"""User-level configuration for fabric-dw CLI.

Stores persistent defaults (workspace, warehouse, and HTTP retry knobs)
in a TOML file at ``$XDG_CONFIG_HOME/fabric-dw/config.toml`` (falling
back to ``~/.config/fabric-dw/config.toml``).

The TOML shape is intentionally tiny:

.. code-block:: toml

    [defaults]
    workspace = "Sales Workspace"
    warehouse = "Sales-DW"
    max_429_retries = 10
    retry_deadline_s = 300.0

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
import math
import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

import filelock
import tomli_w

__all__ = [
    "ConfigError",
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


class ConfigError(RuntimeError):
    """Raised when a config write operation cannot complete (e.g. lock timeout)."""


@dataclass(frozen=True)
class Defaults:
    """Persistent workspace / warehouse defaults and HTTP retry knobs."""

    workspace: str | None = None
    warehouse: str | None = None
    max_429_retries: int | None = None
    retry_deadline_s: float | None = None


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


def _write_config_unlocked(resolved: Path, data: dict[str, object]) -> None:
    """Atomically write *data* to *resolved* — caller must hold the FileLock.

    Uses ``tempfile.mkstemp`` + ``os.replace`` for crash-safe atomic writes.
    On any I/O error the temp file is cleaned up before re-raising.
    """
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
    raw_retries = defaults_raw.get("max_429_retries")
    raw_deadline = defaults_raw.get("retry_deadline_s")
    return UserConfig(
        defaults=Defaults(
            workspace=workspace if isinstance(workspace, str) else None,
            warehouse=warehouse if isinstance(warehouse, str) else None,
            max_429_retries=int(raw_retries) if isinstance(raw_retries, int) else None,
            retry_deadline_s=float(raw_deadline)
            if isinstance(raw_deadline, (int, float)) and math.isfinite(raw_deadline)
            else None,
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

    data: dict[str, object] = {"defaults": _defaults_to_dict(config.defaults)}

    lock = filelock.FileLock(str(resolved) + ".lock", timeout=_LOCK_TIMEOUT)
    with lock:
        _write_config_unlocked(resolved, data)


def _defaults_to_dict(d: Defaults) -> dict[str, object]:
    """Serialise *d* to a TOML-compatible dict, omitting ``None`` values."""
    out: dict[str, object] = {}
    if d.workspace is not None:
        out["workspace"] = d.workspace
    if d.warehouse is not None:
        out["warehouse"] = d.warehouse
    if d.max_429_retries is not None:
        out["max_429_retries"] = d.max_429_retries
    if d.retry_deadline_s is not None:
        out["retry_deadline_s"] = d.retry_deadline_s
    return out


def _read_defaults_locked(resolved: Path) -> Defaults:
    """Read :class:`Defaults` from *resolved* — must be called inside a FileLock.

    Raises:
        OSError: When the file exists but cannot be read (e.g. permission denied).
            Re-raised so that :func:`set_default` can abort rather than silently
            clobber the existing config with an empty object.
        tomllib.TOMLDecodeError: When the file exists but contains invalid TOML.
            Also re-raised so :func:`set_default` can surface a clear error.
    """
    if not resolved.exists():
        return Defaults()
    raw = resolved.read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    defaults_raw = data.get("defaults", {})
    if not isinstance(defaults_raw, dict):
        return Defaults()
    ws = defaults_raw.get("workspace")
    wh = defaults_raw.get("warehouse")
    rr = defaults_raw.get("max_429_retries")
    rd = defaults_raw.get("retry_deadline_s")
    return Defaults(
        workspace=ws if isinstance(ws, str) else None,
        warehouse=wh if isinstance(wh, str) else None,
        max_429_retries=int(rr) if isinstance(rr, int) else None,
        retry_deadline_s=float(rd) if isinstance(rd, (int, float)) and math.isfinite(rd) else None,
    )


_MIN_RETRY_DEADLINE_S: float = 0.1


def set_default(key: str, value: str | None, path: Path | None = None) -> None:
    """Atomically update a single key under ``[defaults]`` without touching other keys.

    The read-modify-write is performed under a single :class:`filelock.FileLock`
    held for the full duration, preventing lost-update races between concurrent
    CLI invocations (C20).

    Args:
        key: One of ``"workspace"``, ``"warehouse"``, ``"max_429_retries"``,
             or ``"retry_deadline_s"``.
        value: The new value (as a string for numeric keys it is coerced), or
               *None* to clear (unset) the key.
        path: Optional override for the config file path.

    Raises:
        ValueError: If *key* is not a recognised defaults field.
        ValueError: If a numeric key receives a value that cannot be coerced,
            is out of the valid range, or is non-finite.
        OSError: If the existing config file cannot be read (re-raised to prevent
            silent data loss — set_default will never overwrite a file it could
            not read).
    """
    allowed = {"workspace", "warehouse", "max_429_retries", "retry_deadline_s"}
    if key not in allowed:
        raise ValueError(f"Unknown config key {key!r}; must be one of {sorted(allowed)}")

    resolved = path if path is not None else default_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(resolved) + ".lock", timeout=_LOCK_TIMEOUT)

    # Coerce and validate numeric keys before acquiring the lock so errors
    # surface early without any lock or I/O.
    coerced_int: int | None = None
    coerced_float: float | None = None
    if value is not None:
        if key == "max_429_retries":
            try:
                coerced_int = int(float(value))
            except (ValueError, OverflowError) as exc:
                raise ValueError(
                    f"max_429_retries {value!r} cannot be converted to an integer: {exc}"
                ) from exc
            if coerced_int < 1:
                raise ValueError(f"max_429_retries must be >= 1, got {coerced_int}")
        elif key == "retry_deadline_s":
            try:
                coerced_float = float(value)
            except (ValueError, OverflowError) as exc:
                raise ValueError(
                    f"retry_deadline_s {value!r} cannot be converted to a float: {exc}"
                ) from exc
            if not math.isfinite(coerced_float):
                raise ValueError(f"retry_deadline_s must be a finite number, got {coerced_float!r}")
            if coerced_float < _MIN_RETRY_DEADLINE_S:
                raise ValueError(
                    f"retry_deadline_s must be >= {_MIN_RETRY_DEADLINE_S}, got {coerced_float}"
                )

    try:
        with lock:
            # Read inside the lock so the full read-modify-write is atomic.
            # OSError is intentionally NOT caught here: if the file exists but
            # cannot be read, we must not proceed to write an empty config and
            # silently erase the user's existing workspace/warehouse defaults.
            current = _read_defaults_locked(resolved)
            new_defaults = Defaults(
                workspace=value if key == "workspace" else current.workspace,
                warehouse=value if key == "warehouse" else current.warehouse,
                max_429_retries=coerced_int
                if key == "max_429_retries"
                else current.max_429_retries,
                retry_deadline_s=coerced_float
                if key == "retry_deadline_s"
                else current.retry_deadline_s,
            )
            # _write_config_unlocked is called inside the lock so the full
            # read-modify-write stays within a single lock cycle (C20).
            toml_data: dict[str, object] = {"defaults": _defaults_to_dict(new_defaults)}
            _write_config_unlocked(resolved, toml_data)
    except filelock.Timeout:
        raise ConfigError(
            f"Could not acquire lock for {resolved} within {_LOCK_TIMEOUT}s; "
            "another process may be holding it."
        ) from None


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
