"""User-level configuration for fabric-dw CLI.

Stores persistent defaults (workspace, warehouse) in a TOML file at
``$XDG_CONFIG_HOME/fabric-dw/config.toml`` (falling back to
``~/.config/fabric-dw/config.toml``).

The TOML shape is intentionally tiny:

.. code-block:: toml

    [defaults]
    workspace = "Sales Workspace"
    warehouse = "Sales-DW"

Reads are done with :mod:`tomllib` (stdlib, Python 3.11+).
Writes use :mod:`tomli_w` for spec-compliant serialisation (handles newlines,
control characters, non-BMP unicode, etc.) and are atomic:
``tempfile.mkstemp + os.replace``.
The file is protected by a :class:`filelock.FileLock` so concurrent CLI
invocations do not corrupt each other.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

import filelock
import tomli_w

__all__ = [
    "Defaults",
    "UserConfig",
    "clear_config",
    "default_path",
    "load_config",
    "save_config",
    "set_default",
]

_log = logging.getLogger(__name__)

_LOCK_TIMEOUT = 5  # seconds


@dataclass(frozen=True)
class Defaults:
    """Persistent workspace / warehouse defaults."""

    workspace: str | None = None
    warehouse: str | None = None


@dataclass(frozen=True)
class UserConfig:
    """Top-level user configuration object."""

    defaults: Defaults


def default_path() -> Path:
    """Return the platform-appropriate config file path.

    Respects ``$XDG_CONFIG_HOME``; falls back to ``~/.config``.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "fabric-dw" / "config.toml"


def load_config(path: Path | None = None) -> UserConfig:
    """Load :class:`UserConfig` from *path*.

    Returns an empty :class:`UserConfig` when the file is missing or corrupt
    (never raises).
    """
    resolved = path if path is not None else default_path()
    if not resolved.exists():
        return UserConfig(defaults=Defaults())

    lock = filelock.FileLock(str(resolved) + ".lock", timeout=_LOCK_TIMEOUT)
    # C18: separate read errors (lock timeout, permission denied) from parse
    # errors (corrupt TOML) so each is handled with the right semantics.
    try:
        with lock:
            raw = resolved.read_text(encoding="utf-8")
    except filelock.Timeout:
        _log.warning(
            "Could not acquire lock for config file %s (timeout); using empty defaults",
            resolved,
        )
        return UserConfig(defaults=Defaults())
    except OSError:
        _log.warning(
            "Could not read config file %s; using empty defaults",
            resolved,
            exc_info=True,
        )
        return UserConfig(defaults=Defaults())

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        _log.warning("Config file %s is corrupt (invalid TOML); using empty defaults", resolved)
        return UserConfig(defaults=Defaults())

    defaults_raw = data.get("defaults", {})
    if not isinstance(defaults_raw, dict):
        return UserConfig(defaults=Defaults())

    workspace = defaults_raw.get("workspace")
    warehouse = defaults_raw.get("warehouse")
    return UserConfig(
        defaults=Defaults(
            workspace=workspace if isinstance(workspace, str) else None,
            warehouse=warehouse if isinstance(warehouse, str) else None,
        )
    )


def save_config(config: UserConfig, path: Path | None = None) -> None:
    """Atomically write *config* to *path*.

    Creates parent directories as needed.
    Serialises with :mod:`tomli_w` for full TOML-spec compliance (handles
    newlines, control characters, non-BMP unicode in workspace/warehouse names).
    """
    resolved = path if path is not None else default_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    defaults_dict: dict[str, str] = {}
    if config.defaults.workspace is not None:
        defaults_dict["workspace"] = config.defaults.workspace
    if config.defaults.warehouse is not None:
        defaults_dict["warehouse"] = config.defaults.warehouse

    data: dict[str, object] = {"defaults": defaults_dict}

    lock = filelock.FileLock(str(resolved) + ".lock", timeout=_LOCK_TIMEOUT)
    with lock:
        fd, tmp_name = tempfile.mkstemp(
            dir=resolved.parent,
            prefix=".config_tmp_",
            suffix=".toml",
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                tomli_w.dump(data, fh)
            os.replace(tmp_name, resolved)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise


def set_default(key: str, value: str | None, path: Path | None = None) -> None:
    """Atomically update a single key under ``[defaults]`` without touching other keys.

    The read-modify-write is performed under a single :class:`filelock.FileLock`
    held for the full duration, preventing lost-update races between concurrent
    CLI invocations (C20).

    Args:
        key: One of ``"workspace"`` or ``"warehouse"``.
        value: The new value, or *None* to clear (unset) the key.
        path: Optional override for the config file path.

    Raises:
        ValueError: If *key* is not a recognised defaults field.
    """
    allowed = {"workspace", "warehouse"}
    if key not in allowed:
        raise ValueError(f"Unknown config key {key!r}; must be one of {sorted(allowed)}")

    resolved = path if path is not None else default_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(resolved) + ".lock", timeout=_LOCK_TIMEOUT)

    with lock:
        # Read inside the lock so the full read-modify-write is atomic.
        if not resolved.exists():
            current = Defaults()
        else:
            try:
                raw = resolved.read_text(encoding="utf-8")
                data = tomllib.loads(raw)
                defaults_raw = data.get("defaults", {})
                if isinstance(defaults_raw, dict):
                    ws = defaults_raw.get("workspace")
                    wh = defaults_raw.get("warehouse")
                    current = Defaults(
                        workspace=ws if isinstance(ws, str) else None,
                        warehouse=wh if isinstance(wh, str) else None,
                    )
                else:
                    current = Defaults()
            except (OSError, tomllib.TOMLDecodeError):
                current = Defaults()

        new_defaults = Defaults(
            workspace=value if key == "workspace" else current.workspace,
            warehouse=value if key == "warehouse" else current.warehouse,
        )
        # save_config acquires its own lock internally, so call the internal
        # write logic directly to stay within our single lock cycle.
        defaults_dict: dict[str, str] = {}
        if new_defaults.workspace is not None:
            defaults_dict["workspace"] = new_defaults.workspace
        if new_defaults.warehouse is not None:
            defaults_dict["warehouse"] = new_defaults.warehouse
        toml_data: dict[str, object] = {"defaults": defaults_dict}

        fd, tmp_name = tempfile.mkstemp(
            dir=resolved.parent,
            prefix=".config_tmp_",
            suffix=".toml",
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                tomli_w.dump(toml_data, fh)
            os.replace(tmp_name, resolved)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise


def clear_config(path: Path | None = None) -> None:
    """Delete the config file if it exists.

    Acquires the same :class:`filelock.FileLock` used by
    :func:`load_config` / :func:`save_config` so concurrent CLI
    invocations do not interleave.  Never raises even when the file is
    already absent.
    """
    resolved = path if path is not None else default_path()
    lock = filelock.FileLock(str(resolved) + ".lock", timeout=_LOCK_TIMEOUT)
    with lock, contextlib.suppress(FileNotFoundError):
        resolved.unlink()
