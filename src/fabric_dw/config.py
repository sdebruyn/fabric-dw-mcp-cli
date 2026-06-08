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
Writes are hand-generated TOML (no third-party serialiser needed for this
simple shape) and are atomic: ``tempfile.mkstemp + os.replace``.
The file is protected by a :class:`filelock.FileLock` so concurrent CLI
invocations do not corrupt each other.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

if True:  # pragma: no branch  # tomllib is stdlib >= 3.11
    import tomllib

import filelock

__all__ = [
    "Defaults",
    "UserConfig",
    "clear_config",
    "default_path",
    "load_config",
    "save_config",
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
    try:
        with lock:
            raw = resolved.read_text(encoding="utf-8")
    except Exception:
        _log.warning("Could not read config file %s; using empty defaults", resolved)
        return UserConfig(defaults=Defaults())

    try:
        data = tomllib.loads(raw)
    except Exception:
        _log.warning("Config file %s is corrupt; using empty defaults", resolved)
        return UserConfig(defaults=Defaults())

    defaults_raw = data.get("defaults", {})
    if not isinstance(defaults_raw, dict):
        return UserConfig(defaults=Defaults())

    workspace = defaults_raw.get("workspace")
    warehouse = defaults_raw.get("warehouse")
    return UserConfig(
        defaults=Defaults(
            workspace=str(workspace) if isinstance(workspace, str) else None,
            warehouse=str(warehouse) if isinstance(warehouse, str) else None,
        )
    )


def save_config(config: UserConfig, path: Path | None = None) -> None:
    """Atomically write *config* to *path*.

    Creates parent directories as needed.
    """
    resolved = path if path is not None else default_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    # Hand-write minimal TOML — no third-party serialiser required.
    lines: list[str] = ["[defaults]\n"]
    if config.defaults.workspace is not None:
        escaped = config.defaults.workspace.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'workspace = "{escaped}"\n')
    if config.defaults.warehouse is not None:
        escaped = config.defaults.warehouse.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'warehouse = "{escaped}"\n')
    content = "".join(lines)

    lock = filelock.FileLock(str(resolved) + ".lock", timeout=_LOCK_TIMEOUT)
    with lock:
        fd, tmp_name = tempfile.mkstemp(
            dir=resolved.parent,
            prefix=".config_tmp_",
            suffix=".toml",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
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
