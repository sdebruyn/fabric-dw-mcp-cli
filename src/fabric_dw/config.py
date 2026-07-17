"""User-level configuration for fabric-dw CLI.

Stores persistent defaults (workspace, warehouse, HTTP retry knobs, SQL
retry knobs, and auth mode) in a TOML file at
``$XDG_CONFIG_HOME/fabric-dw/config.toml``
(falling back to ``~/.config/fabric-dw/config.toml``).

The TOML shape supports multiple named sections:

.. code-block:: toml

    [defaults]
    workspace = "Sales Workspace"
    warehouse = "Sales-DW"
    max_429_retries = 10
    retry_deadline_s = 300
    sql_retry_deadline_s = 120
    sql_retry_executes = false
    auth_mode = "default"

    [telemetry]
    disabled = true

    [mcp]
    workspace_allowlist = ["Sales Workspace", "Finance Workspace"]

    [logging]
    level = "DEBUG"

    [auth]
    tenant_id = "00000000-0000-0000-0000-000000000000"
    client_id = "00000000-0000-0000-0000-000000000001"

Reads are done with :mod:`tomllib` (stdlib, Python 3.11+).
Writes use :mod:`tomli_w` for spec-compliant serialisation (handles newlines,
control characters, non-BMP unicode, etc.) and are atomic:
``tempfile.mkstemp + os.replace``.
The file is protected by a :class:`filelock.FileLock` so concurrent CLI
invocations do not corrupt each other.

How to add a new config section/key
------------------------------------
1. Add a new frozen dataclass (e.g. ``FooConfig``) with ``None`` defaults.
2. Add a field to :class:`UserConfig` with ``field(default_factory=FooConfig)``.
3. In :func:`load_config`, parse the new section permissively (unknown keys
   silently ignored; type mismatches fall back to ``None``).
4. Add a ``_foo_to_dict`` helper mirroring :func:`_defaults_to_dict` (omit
   ``None`` values).
5. In :func:`save_config`, serialise the section via ``_foo_to_dict`` and
   include it only when non-empty.
6. In :func:`_read_all_sections_locked`, also read and return the new section.
7. In :func:`set_config`, add entries to ``_SET_CONFIG_DISPATCH`` for each
   ``("foo", "key")`` pair.
8. Export the new dataclass from ``__all__``.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import tempfile
import tomllib
import typing
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import filelock
import tomli_w

__all__ = [
    "VALID_AUTH_MODES",
    "VALID_LOG_LEVELS",
    "AuthConfig",
    "ConfigError",
    "Defaults",
    "LoggingConfig",
    "McpConfig",
    "TelemetryConfig",
    "UserConfig",
    "clear_config",
    "default_path",
    "load_config",
    "save_config",
    "set_config",
    "set_default",
]

_log = logging.getLogger(__name__)

_LOCK_TIMEOUT = 5  # seconds

# Falsy string values for boolean-like config keys (e.g. [telemetry] disabled).
# Matches the convention in telemetry._FALSY_VALUES / consoledonottrack.com.
_FALSY_STRINGS = frozenset({"", "0", "false", "no", "off"})


class ConfigError(RuntimeError):
    """Raised when a config write operation cannot complete (e.g. lock timeout)."""


@dataclass(frozen=True)
class Defaults:
    """Persistent workspace / warehouse defaults, HTTP + SQL retry knobs, and auth mode."""

    workspace: str | None = None
    warehouse: str | None = None
    max_429_retries: int | None = None
    retry_deadline_s: int | None = None
    sql_retry_deadline_s: int | None = None
    sql_retry_executes: bool | None = None
    auth_mode: str | None = None


@dataclass(frozen=True)
class TelemetryConfig:
    """Configuration for the ``[telemetry]`` section."""

    disabled: bool | None = None


@dataclass(frozen=True)
class McpConfig:
    """Configuration for the ``[mcp]`` section."""

    workspace_allowlist: list[str] | None = None


@dataclass(frozen=True)
class LoggingConfig:
    """Configuration for the ``[logging]`` section."""

    level: str | None = None


@dataclass(frozen=True)
class AuthConfig:
    """Configuration for the ``[auth]`` section."""

    tenant_id: str | None = None
    client_id: str | None = None


@dataclass(frozen=True)
class UserConfig:
    """Top-level user configuration object."""

    defaults: Defaults = field(default_factory=Defaults)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)


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


def _parse_defaults_section(data: dict[str, object]) -> Defaults:
    """Parse the ``[defaults]`` section from *data*."""
    raw = data.get("defaults", {})
    if not isinstance(raw, dict):
        raw = {}
    workspace = raw.get("workspace")
    warehouse = raw.get("warehouse")
    raw_retries = raw.get("max_429_retries")
    raw_deadline = raw.get("retry_deadline_s")
    raw_sql_deadline = raw.get("sql_retry_deadline_s")
    raw_sql_executes = raw.get("sql_retry_executes")
    raw_auth_mode = raw.get("auth_mode")
    auth_mode: str | None = None
    if isinstance(raw_auth_mode, str):
        normalised = raw_auth_mode.strip().lower()
        if normalised in VALID_AUTH_MODES:
            auth_mode = normalised
        else:
            _log.warning(
                "[defaults] auth_mode %r is not a recognised credential mode "
                "(valid: %s); ignoring.",
                raw_auth_mode,
                ", ".join(sorted(VALID_AUTH_MODES)),
            )
    return Defaults(
        workspace=workspace if isinstance(workspace, str) else None,
        warehouse=warehouse if isinstance(warehouse, str) else None,
        max_429_retries=int(raw_retries) if isinstance(raw_retries, int) else None,
        retry_deadline_s=int(raw_deadline)
        if isinstance(raw_deadline, (int, float)) and math.isfinite(raw_deadline)
        else None,
        sql_retry_deadline_s=int(raw_sql_deadline)
        if isinstance(raw_sql_deadline, (int, float)) and math.isfinite(raw_sql_deadline)
        else None,
        sql_retry_executes=raw_sql_executes if isinstance(raw_sql_executes, bool) else None,
        auth_mode=auth_mode,
    )


def _parse_telemetry_section(data: dict[str, object]) -> TelemetryConfig:
    """Parse the ``[telemetry]`` section from *data*.

    Accepted ``disabled`` value types:

    - TOML bool (``true`` / ``false``) → direct bool coercion.
    - TOML int (``1`` / ``0``) → bool via ``bool()``.
    - TOML string (``"true"`` / ``"false"`` etc.) → falsy-set check:
      strings in ``_FALSY_STRINGS`` map to ``False``; any other
      non-empty string maps to ``True``.  This avoids ``bool("false") is True``
      which would silently re-enable telemetry for a user who wrote
      ``disabled = "false"``.
    - Any other type → ``None`` (unknown, treated as not opted out).
    """
    raw = data.get("telemetry", {})
    if not isinstance(raw, dict):
        raw = {}
    raw_disabled = raw.get("disabled")
    disabled: bool | None
    if isinstance(raw_disabled, bool):
        disabled = raw_disabled
    elif isinstance(raw_disabled, int):
        disabled = bool(raw_disabled)
    elif isinstance(raw_disabled, str):
        stripped = raw_disabled.strip().lower()
        disabled = stripped not in _FALSY_STRINGS
    else:
        disabled = None
    return TelemetryConfig(disabled=disabled)


def _parse_mcp_section(data: dict[str, object]) -> McpConfig:
    """Parse the ``[mcp]`` section from *data*."""
    raw = data.get("mcp", {})
    if not isinstance(raw, dict):
        raw = {}
    raw_allowlist = raw.get("workspace_allowlist")
    if isinstance(raw_allowlist, list) and all(isinstance(x, str) for x in raw_allowlist):
        allowlist: list[str] | None = typing.cast("list[str]", raw_allowlist)
    else:
        allowlist = None
    return McpConfig(workspace_allowlist=allowlist)


def _parse_logging_section(data: dict[str, object]) -> LoggingConfig:
    """Parse the ``[logging]`` section from *data*.

    The ``level`` value is validated case-insensitively against
    :data:`VALID_LOG_LEVELS`.  An unrecognised value is discarded (treated as
    ``None``) and a :func:`logging.warning` is emitted so users notice the
    misconfiguration.
    """
    raw = data.get("logging", {})
    if not isinstance(raw, dict):
        raw = {}
    raw_level = raw.get("level")
    level: str | None = None
    if isinstance(raw_level, str):
        normalised = raw_level.strip().upper()
        if normalised in VALID_LOG_LEVELS:
            level = normalised
        else:
            _log.warning(
                "[logging] level %r is not a recognised log level (valid: %s); ignoring.",
                raw_level,
                ", ".join(sorted(VALID_LOG_LEVELS)),
            )
    return LoggingConfig(level=level)


def _parse_auth_section(data: dict[str, object]) -> AuthConfig:
    """Parse the ``[auth]`` section from *data*."""
    raw = data.get("auth", {})
    if not isinstance(raw, dict):
        raw = {}
    raw_tenant_id = raw.get("tenant_id")
    raw_client_id = raw.get("client_id")
    return AuthConfig(
        tenant_id=raw_tenant_id if isinstance(raw_tenant_id, str) else None,
        client_id=raw_client_id if isinstance(raw_client_id, str) else None,
    )


def _parse_sections(data: dict[str, object]) -> UserConfig:
    """Parse all config sections from *data* and return a :class:`UserConfig`."""
    return UserConfig(
        defaults=_parse_defaults_section(data),
        telemetry=_parse_telemetry_section(data),
        mcp=_parse_mcp_section(data),
        logging=_parse_logging_section(data),
        auth=_parse_auth_section(data),
    )


def load_config(path: Path | None = None) -> UserConfig:
    """Load :class:`UserConfig` from *path*.

    Returns an empty :class:`UserConfig` when the file is missing or corrupt
    (never raises).  Unknown sections and keys are silently ignored; type
    mismatches fall back to ``None``.
    """
    resolved = path if path is not None else default_path()
    if not resolved.exists():
        return UserConfig()

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
        return UserConfig()
    except OSError:
        _log.warning(
            "Could not read config file %s; using empty defaults",
            resolved,
            exc_info=True,
        )
        return UserConfig()

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        _log.warning("Config file %s is corrupt (invalid TOML); using empty defaults", resolved)
        return UserConfig()

    return _parse_sections(data)


def save_config(config: UserConfig, path: Path | None = None) -> None:
    """Atomically write *config* to *path*.

    Creates parent directories as needed.
    Serialises with :mod:`tomli_w` for full TOML-spec compliance (handles
    newlines, control characters, non-BMP unicode in workspace/warehouse names).
    Sections whose keys are all ``None`` are omitted (keeps files compact).
    """
    resolved = path if path is not None else default_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    data = _config_to_data(config)

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
    if d.sql_retry_deadline_s is not None:
        out["sql_retry_deadline_s"] = d.sql_retry_deadline_s
    if d.sql_retry_executes is not None:
        out["sql_retry_executes"] = d.sql_retry_executes
    if d.auth_mode is not None:
        out["auth_mode"] = d.auth_mode
    return out


def _telemetry_to_dict(t: TelemetryConfig) -> dict[str, object]:
    """Serialise *t* to a TOML-compatible dict, omitting ``None`` values."""
    out: dict[str, object] = {}
    if t.disabled is not None:
        out["disabled"] = t.disabled
    return out


def _mcp_to_dict(m: McpConfig) -> dict[str, object]:
    """Serialise *m* to a TOML-compatible dict, omitting ``None`` values."""
    out: dict[str, object] = {}
    if m.workspace_allowlist is not None:
        out["workspace_allowlist"] = m.workspace_allowlist
    return out


def _logging_to_dict(lc: LoggingConfig) -> dict[str, object]:
    """Serialise *lc* to a TOML-compatible dict, omitting ``None`` values."""
    out: dict[str, object] = {}
    if lc.level is not None:
        out["level"] = lc.level
    return out


def _auth_to_dict(a: AuthConfig) -> dict[str, object]:
    """Serialise *a* to a TOML-compatible dict, omitting ``None`` values."""
    out: dict[str, object] = {}
    if a.tenant_id is not None:
        out["tenant_id"] = a.tenant_id
    if a.client_id is not None:
        out["client_id"] = a.client_id
    return out


def _config_to_data(config: UserConfig) -> dict[str, object]:
    """Serialise *config* to a TOML-compatible dict, omitting empty sections."""
    data: dict[str, object] = {}
    defaults_dict = _defaults_to_dict(config.defaults)
    if defaults_dict:
        data["defaults"] = defaults_dict
    telemetry_dict = _telemetry_to_dict(config.telemetry)
    if telemetry_dict:
        data["telemetry"] = telemetry_dict
    mcp_dict = _mcp_to_dict(config.mcp)
    if mcp_dict:
        data["mcp"] = mcp_dict
    logging_dict = _logging_to_dict(config.logging)
    if logging_dict:
        data["logging"] = logging_dict
    auth_dict = _auth_to_dict(config.auth)
    if auth_dict:
        data["auth"] = auth_dict
    return data


def _read_all_sections_locked(resolved: Path) -> UserConfig:
    """Read all config sections from *resolved* — must be called inside a FileLock.

    Returns a fully-populated :class:`UserConfig` (empty sections for missing data).

    Raises:
        OSError: When the file exists but cannot be read (e.g. permission denied).
            Re-raised so that :func:`set_config` can abort rather than silently
            clobber the existing config with an empty object.
        tomllib.TOMLDecodeError: When the file exists but contains invalid TOML.
            Also re-raised so :func:`set_config` can surface a clear error.
    """
    if not resolved.exists():
        return UserConfig()
    raw = resolved.read_text(encoding="utf-8")
    return _parse_sections(tomllib.loads(raw))


# ---------------------------------------------------------------------------
# Legacy helper for the existing set_default read path
# ---------------------------------------------------------------------------


def _read_defaults_locked(resolved: Path) -> Defaults:
    """Read :class:`Defaults` from *resolved* — must be called inside a FileLock.

    Raises:
        OSError: When the file exists but cannot be read (e.g. permission denied).
            Re-raised so that :func:`set_default` can abort rather than silently
            clobber the existing config with an empty object.
        tomllib.TOMLDecodeError: When the file exists but contains invalid TOML.
            Also re-raised so :func:`set_default` can surface a clear error.
    """
    return _read_all_sections_locked(resolved).defaults


_MIN_RETRY_DEADLINE_S: int = 1


def _coerce_defaults_key(key: str, value: str | None) -> tuple[int | None, bool | None]:
    """Coerce and validate a numeric/boolean defaults key.

    Returns ``(coerced_int, coerced_bool)`` — at most one will be non-``None``
    for typed keys; both are ``None`` for string keys (no coercion needed) or
    when *value* is ``None``.

    Raises:
        ValueError: If the value is not coercible or is out of range.
    """
    coerced_int: int | None = None
    coerced_bool: bool | None = None
    if value is not None:
        if key in {"max_429_retries", "retry_deadline_s", "sql_retry_deadline_s"}:
            try:
                coerced_int = int(float(value))
            except (ValueError, OverflowError) as exc:
                # Catches non-numeric strings, nan (ValueError), and inf (OverflowError).
                raise ValueError(
                    f"{key} {value!r} cannot be converted to an integer: {exc}"
                ) from exc
            if coerced_int < _MIN_RETRY_DEADLINE_S:
                raise ValueError(f"{key} must be >= {_MIN_RETRY_DEADLINE_S}, got {coerced_int}")
        elif key == "sql_retry_executes":
            if value.lower() in {"true", "1", "yes", "on"}:
                coerced_bool = True
            elif value.lower() in {"false", "0", "no", "off"}:
                coerced_bool = False
            else:
                raise ValueError(f"{key} {value!r} must be one of: true/1/yes/on or false/0/no/off")
        elif key == "auth_mode":
            normalised = value.strip().lower()
            if normalised not in VALID_AUTH_MODES:
                valid_sorted = ", ".join(sorted(VALID_AUTH_MODES))
                raise ValueError(
                    f"auth_mode {value!r} is not a valid credential mode; "
                    f"must be one of {valid_sorted}"
                )
    return coerced_int, coerced_bool


# ---------------------------------------------------------------------------
# Dispatch table for set_config
# ---------------------------------------------------------------------------
# Each entry is a callable that takes (current: UserConfig, value: str | None)
# and returns an updated UserConfig.  Unknown section/key combos are not in
# the table → ValueError.


def _make_defaults_setter(
    key: str,
) -> typing.Callable[[UserConfig, str | None], UserConfig]:
    """Return a UserConfig updater for the given defaults key."""

    def _set(current: UserConfig, value: str | None) -> UserConfig:
        coerced_int, coerced_bool = _coerce_defaults_key(key, value)
        # For auth_mode, normalise to lowercase when setting (None clears the key).
        auth_mode_value: str | None
        if key == "auth_mode":
            auth_mode_value = value.strip().lower() if value is not None else None
        else:
            auth_mode_value = current.defaults.auth_mode
        new_defaults = Defaults(
            workspace=value if key == "workspace" else current.defaults.workspace,
            warehouse=value if key == "warehouse" else current.defaults.warehouse,
            max_429_retries=coerced_int
            if key == "max_429_retries"
            else current.defaults.max_429_retries,
            retry_deadline_s=coerced_int
            if key == "retry_deadline_s"
            else current.defaults.retry_deadline_s,
            sql_retry_deadline_s=coerced_int
            if key == "sql_retry_deadline_s"
            else current.defaults.sql_retry_deadline_s,
            sql_retry_executes=coerced_bool
            if key == "sql_retry_executes"
            else current.defaults.sql_retry_executes,
            auth_mode=auth_mode_value,
        )
        return UserConfig(
            defaults=new_defaults,
            telemetry=current.telemetry,
            mcp=current.mcp,
            logging=current.logging,
            auth=current.auth,
        )

    return _set


def _set_telemetry_disabled(current: UserConfig, value: str | None) -> UserConfig:
    if value is None:
        new_telemetry = TelemetryConfig(disabled=None)
    else:
        lower = value.strip().lower()
        if lower in ("true", "1", "yes", "on"):
            new_telemetry = TelemetryConfig(disabled=True)
        elif lower in ("false", "0", "no", "off"):
            new_telemetry = TelemetryConfig(disabled=False)
        else:
            raise ValueError(
                f"telemetry.disabled {value!r} cannot be interpreted as a boolean; "
                "use 'true' or 'false'"
            )
    return UserConfig(
        defaults=current.defaults,
        telemetry=new_telemetry,
        mcp=current.mcp,
        logging=current.logging,
        auth=current.auth,
    )


def _set_mcp_workspace_allowlist(current: UserConfig, value: str | None) -> UserConfig:
    if value is None:
        new_mcp = McpConfig(workspace_allowlist=None)
    else:
        # value is a comma-separated list of workspace names
        names = [n.strip() for n in value.split(",") if n.strip()]
        new_mcp = McpConfig(workspace_allowlist=names or None)
    return UserConfig(
        defaults=current.defaults,
        telemetry=current.telemetry,
        mcp=new_mcp,
        logging=current.logging,
        auth=current.auth,
    )


VALID_LOG_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

# Valid credential modes — literal copy of :class:`~fabric_dw.auth.CredentialMode` values.
# Kept as a literal rather than derived at import time (``frozenset(m.value for m in
# CredentialMode)``) to avoid pulling the azure-identity / msal import chain into
# config.py's startup path — config.py is imported very early (CLI boot, test fixtures)
# and should remain a lightweight leaf.  The drift-guard test in tests/unit/test_config.py
# asserts ``VALID_AUTH_MODES == {m.value for m in CredentialMode}`` and will fail fast
# if a new mode is added to the enum but not mirrored here.
VALID_AUTH_MODES: frozenset[str] = frozenset({"default", "sp", "interactive"})


def _set_logging_level(current: UserConfig, value: str | None) -> UserConfig:
    if value is not None:
        normalised = value.strip().upper()
        if normalised not in VALID_LOG_LEVELS:
            valid_sorted = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ValueError(
                f"logging.level {value!r} is not a valid log level; must be one of {valid_sorted}"
            )
        value = normalised
    new_logging = LoggingConfig(level=value)
    return UserConfig(
        defaults=current.defaults,
        telemetry=current.telemetry,
        mcp=current.mcp,
        logging=new_logging,
        auth=current.auth,
    )


def _validate_uuid(key: str, value: str) -> None:
    """Raise ValueError if *value* is not a valid UUID string."""
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        # uuid.UUID raises TypeError/AttributeError for non-str inputs; normalise
        # them all to ValueError so callers always see a consistent exception type.
        raise ValueError(
            f"{key} {value!r} is not a valid UUID; "
            "expected a standard UUID format (e.g. xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)"
        ) from None


def _set_auth_tenant_id(current: UserConfig, value: str | None) -> UserConfig:
    if value is not None:
        _validate_uuid("tenant_id", value)
    new_auth = AuthConfig(tenant_id=value, client_id=current.auth.client_id)
    return UserConfig(
        defaults=current.defaults,
        telemetry=current.telemetry,
        mcp=current.mcp,
        logging=current.logging,
        auth=new_auth,
    )


def _set_auth_client_id(current: UserConfig, value: str | None) -> UserConfig:
    if value is not None:
        _validate_uuid("client_id", value)
    new_auth = AuthConfig(tenant_id=current.auth.tenant_id, client_id=value)
    return UserConfig(
        defaults=current.defaults,
        telemetry=current.telemetry,
        mcp=current.mcp,
        logging=current.logging,
        auth=new_auth,
    )


_SET_CONFIG_DISPATCH: dict[
    tuple[str, str],
    typing.Callable[[UserConfig, str | None], UserConfig],
] = {
    ("defaults", "workspace"): _make_defaults_setter("workspace"),
    ("defaults", "warehouse"): _make_defaults_setter("warehouse"),
    ("defaults", "max_429_retries"): _make_defaults_setter("max_429_retries"),
    ("defaults", "retry_deadline_s"): _make_defaults_setter("retry_deadline_s"),
    ("defaults", "sql_retry_deadline_s"): _make_defaults_setter("sql_retry_deadline_s"),
    ("defaults", "sql_retry_executes"): _make_defaults_setter("sql_retry_executes"),
    ("defaults", "auth_mode"): _make_defaults_setter("auth_mode"),
    ("telemetry", "disabled"): _set_telemetry_disabled,
    ("mcp", "workspace_allowlist"): _set_mcp_workspace_allowlist,
    ("logging", "level"): _set_logging_level,
    ("auth", "tenant_id"): _set_auth_tenant_id,
    ("auth", "client_id"): _set_auth_client_id,
}


def set_config(
    section: str,
    key: str,
    value: str | None,
    path: Path | None = None,
) -> None:
    """Atomically update a single key under *section* without touching other keys.

    The read-modify-write is performed under a single :class:`filelock.FileLock`
    held for the full duration, preventing lost-update races between concurrent
    CLI invocations (C20).

    Args:
        section: The config section name (e.g. ``"defaults"``, ``"telemetry"``).
        key: The key within the section.
        value: The new value (as a string; numeric/bool keys are coerced), or
               *None* to clear (unset) the key.
        path: Optional override for the config file path.

    Raises:
        ValueError: If *section*/*key* is not a recognised combination.
        ValueError: If a typed key receives a value that cannot be coerced or
            is out of the valid range.
        OSError: If the existing config file cannot be read (re-raised to prevent
            silent data loss).
        ConfigError: On lock acquisition timeout.
    """
    updater = _SET_CONFIG_DISPATCH.get((section, key))
    if updater is None:
        valid = sorted(f"{s}.{k}" for s, k in _SET_CONFIG_DISPATCH)
        raise ValueError(f"Unknown config section/key {section!r}/{key!r}; must be one of {valid}")

    # Coerce/validate before acquiring the lock to surface errors early.
    # (The updater itself also validates, but calling it twice is harmless.)
    if section == "defaults":
        _coerce_defaults_key(key, value)

    resolved = path if path is not None else default_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(resolved) + ".lock", timeout=_LOCK_TIMEOUT)

    try:
        with lock:
            # Read inside the lock so the full read-modify-write is atomic.
            current = _read_all_sections_locked(resolved)
            # Call the updater to produce the new config (may raise ValueError).
            new_config = updater(current, value)
            _write_config_unlocked(resolved, _config_to_data(new_config))
    except filelock.Timeout:
        raise ConfigError(
            f"Could not acquire lock for {resolved} within {_LOCK_TIMEOUT}s; "
            "another process may be holding it."
        ) from None


def set_default(key: str, value: str | None, path: Path | None = None) -> None:
    """Atomically update a single key under ``[defaults]`` without touching other keys.

    Thin wrapper around :func:`set_config` for the ``"defaults"`` section,
    preserving backward compatibility for all existing callers.

    The read-modify-write is performed under a single :class:`filelock.FileLock`
    held for the full duration, preventing lost-update races between concurrent
    CLI invocations (C20).

    Args:
        key: One of ``"workspace"``, ``"warehouse"``, ``"max_429_retries"``,
             ``"retry_deadline_s"``, ``"sql_retry_deadline_s"``,
             or ``"sql_retry_executes"``.
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
    allowed = {
        "workspace",
        "warehouse",
        "max_429_retries",
        "retry_deadline_s",
        "sql_retry_deadline_s",
        "sql_retry_executes",
        "auth_mode",
    }
    if key not in allowed:
        raise ValueError(f"Unknown config key {key!r}; must be one of {sorted(allowed)}")
    set_config("defaults", key, value, path)


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
