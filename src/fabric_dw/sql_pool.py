"""SQL connection prerequisites for Microsoft Fabric Data Warehouse connections.

This module holds the stateless helpers, config resolution, and
``open_connection`` that are shared by or exposed via
:mod:`fabric_dw.sql` (query runner, ``SqlTarget``).

Module-global mutable state:
- ``_sql_config_cache`` — SQL config cache; thread-safe, cleared by ``_sql_config_cache_clear()``.

Import graph (no cycle):
    sql_pool <- {fabric_dw.auth (CredentialMode, get_sql_token_struct),
                 fabric_dw.config (UserConfig, load_config)}
    sql      <- sql_pool, sql_errors
    (sql no longer imports fabric_dw.auth directly; get_sql_token_struct lives
    in sql_pool alongside open_connection which calls it)

:class:`~fabric_dw.sql.SqlTarget` is only referenced under ``TYPE_CHECKING``
in this module (safe because ``fabric_dw.sql`` has
``from __future__ import annotations``).
"""

from __future__ import annotations

import functools
import importlib
import logging
import os
import re
import threading
import types
from typing import TYPE_CHECKING, Any, Never

from fabric_dw.auth import CredentialMode, get_sql_token_struct
from fabric_dw.config import UserConfig as _UserConfig
from fabric_dw.exceptions import (
    CAPACITY_INACTIVE_MESSAGE,
    CapacityInactiveError,
    FabricCliError,
    FabricError,
)
from fabric_dw.sql_errors import _is_connect_retryable

if TYPE_CHECKING:
    from fabric_dw.sql import SqlTarget

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timeout configuration
# ---------------------------------------------------------------------------

# Login / connection timeout (seconds) passed as the ``timeout`` keyword
# argument to ``mssql_python.connect()``.  The driver default is 0 (no
# timeout), which is too permissive for a freshly-warming Fabric warehouse.
SQL_LOGIN_TIMEOUT_S: int = 60

# Query / command timeout (seconds) applied to every cursor via the
# ``Connection.timeout`` property setter after a fresh connection is opened.
# A generous value prevents long-running administrative queries from being
# cancelled prematurely.
SQL_QUERY_TIMEOUT_S: int = 300

# ---------------------------------------------------------------------------
# Connect-phase retry configuration
# ---------------------------------------------------------------------------

# Total wall-clock budget (seconds) for the connect-phase retry loop inside
# _with_connect_retry.  The loop keeps retrying while _is_connect_retryable
# returns True and the elapsed time is less than this budget.
#
# The built-in default is 120 s, which covers the observed Fabric warehouse
# warm-up window (~60-90 s) with comfortable margin.  It is configurable via
# the FABRIC_SQL_RETRY_TIMEOUT_S env var or ``fdw config set sql-retry-deadline``.
#
# **Trade-off**: a genuinely-wrong credential will now hang up to ~120 s
# before the AuthError is surfaced to the caller — because the retry loop
# cannot distinguish "wrong credential" from "warehouse still warming up".
# This latency is accepted: the warm-up case is far more common in production.
_SQL_RETRY_DEADLINE_S_DEFAULT: int = 120
_MIN_SQL_RETRY_DEADLINE_S: int = 1  # minimum accepted value for env / config

# Backwards-compatible alias used by integration tests and the smoke-timeout invariant test.
# The old name was _CONNECT_RETRY_TIMEOUT_S; it was renamed to _SQL_RETRY_DEADLINE_S_DEFAULT
# when the value became configurable.  Remove after all callsites are updated.
_CONNECT_RETRY_TIMEOUT_S: int = _SQL_RETRY_DEADLINE_S_DEFAULT

# ---------------------------------------------------------------------------
# SQL config cache — 3-layer config resolution
# ---------------------------------------------------------------------------
# Both knobs resolve at call-time via the 3-layer rule:
#   env var (highest) > config.toml [defaults] > built-in fallback
#
# A module-level cache avoids re-reading the config file on every query.
# The cache is protected by a threading.Lock.
# _sql_config_cache_clear() is a test-only hook to reset the cache between
# tests that mutate env vars or the config.

_sql_config_cache: _UserConfig | None = None
_sql_config_lock: threading.Lock = threading.Lock()

# Truthy/falsy string set for _resolve_sql_retry_executes.
# Kept inline to avoid importing telemetry's private helpers.
_FALSY_STRINGS: frozenset[str] = frozenset({"", "0", "false", "no", "off"})

# ---------------------------------------------------------------------------
# ODBC connection attribute constant
# ---------------------------------------------------------------------------

# ODBC connection attribute number for injecting a pre-acquired SQL access token.
# When set in attrs_before, the driver uses this token instead of its own
# DefaultAzureCredential chain — critical for long-running CI jobs where the
# mssql-python driver's own AzureCliCredential assertion expires after ~5 min.
SQL_COPT_SS_ACCESS_TOKEN: int = 1256

# Mapping from CredentialMode to the ActiveDirectory auth type suffix.
_MODE_TO_AD_AUTH: dict[CredentialMode, str] = {
    CredentialMode.DEFAULT: "ActiveDirectoryDefault",
    CredentialMode.SERVICE_PRINCIPAL: "ActiveDirectoryServicePrincipal",
    CredentialMode.INTERACTIVE: "ActiveDirectoryInteractive",
}

# ---------------------------------------------------------------------------
# Config cache helpers
# ---------------------------------------------------------------------------


def _load_sql_config() -> _UserConfig:
    """Return a cached :class:`~fabric_dw.config.UserConfig`, loading on first call.

    Uses a module-level cache so the config file is read at most once per
    process.  Thread-safe via double-checked locking: the fast path (cache
    already populated) avoids acquiring the lock entirely; the slow path
    (first call) acquires the lock and re-checks before loading.  Safe
    because ``_UserConfig`` is a frozen dataclass — once assigned the
    reference is immutable and visible to all threads after the lock release.
    """
    global _sql_config_cache  # noqa: PLW0603
    # Fast path: check without the lock (common case after first load).
    if _sql_config_cache is not None:
        return _sql_config_cache
    with _sql_config_lock:
        # Re-check inside the lock to handle a concurrent first-loader.
        if _sql_config_cache is None:
            from fabric_dw.config import load_config  # noqa: PLC0415

            _sql_config_cache = load_config()
        return _sql_config_cache


def _sql_config_cache_clear() -> None:
    """Reset the SQL config cache.  For use in tests only."""
    global _sql_config_cache  # noqa: PLW0603
    with _sql_config_lock:
        _sql_config_cache = None


# ---------------------------------------------------------------------------
# SQL retry config resolution
# ---------------------------------------------------------------------------


def _validate_sql_retry_deadline_s(value: int, source: str) -> int | None:
    """Return *value* when it meets the minimum, else log a warning and return None.

    Args:
        value:  Candidate deadline in seconds (already parsed to int).
        source: Human-readable label for the origin (e.g. the env-var name or
                ``"sql_retry_deadline_s (config.toml)"``), used in the warning.
    """
    if value >= _MIN_SQL_RETRY_DEADLINE_S:
        return value
    _log.warning(
        "%s=%r must be >= %s; ignoring",
        source,
        value,
        _MIN_SQL_RETRY_DEADLINE_S,
    )
    return None


def _resolve_sql_retry_deadline_s() -> int:
    """Return the effective SQL retry deadline in seconds.

    Resolution order (3-layer):
    1. ``FABRIC_SQL_RETRY_TIMEOUT_S`` env var — must be an integer (or float-formatted
       integer like ``"120.0"``) >= 1.  Invalid values are ignored (warning logged)
       and fall through to next layer.
    2. ``config.toml`` ``[defaults].sql_retry_deadline_s`` — same >= 1 floor applies;
       values below the minimum are ignored (warning logged) and fall through.
    3. Built-in fallback: :data:`_SQL_RETRY_DEADLINE_S_DEFAULT` (120 s).
    """
    raw_env = os.environ.get("FABRIC_SQL_RETRY_TIMEOUT_S")
    if raw_env is not None:
        try:
            v = int(float(raw_env))
        except (ValueError, OverflowError):
            _log.warning("FABRIC_SQL_RETRY_TIMEOUT_S=%r is not a valid integer; ignoring", raw_env)
        else:
            result = _validate_sql_retry_deadline_s(v, "FABRIC_SQL_RETRY_TIMEOUT_S")
            if result is not None:
                return result

    cfg_val = _load_sql_config().defaults.sql_retry_deadline_s
    if cfg_val is not None:
        result = _validate_sql_retry_deadline_s(cfg_val, "sql_retry_deadline_s (config.toml)")
        if result is not None:
            return result

    return _SQL_RETRY_DEADLINE_S_DEFAULT


def _resolve_sql_retry_executes() -> bool:
    """Return True if execute-phase retry should be widened to include fetch="none".

    Resolution order (3-layer):
    1. ``FABRIC_SQL_RETRY_EXECUTES`` env var — falsy: ``{"","0","false","no","off"}``
       (case-insensitive); anything else is truthy.
    2. ``config.toml`` ``[defaults].sql_retry_executes``.
    3. Built-in fallback: ``False`` (non-idempotent DML is not retried by default).
    """
    raw_env = os.environ.get("FABRIC_SQL_RETRY_EXECUTES")
    if raw_env is not None:
        return raw_env.lower() not in _FALSY_STRINGS

    cfg_val = _load_sql_config().defaults.sql_retry_executes
    if cfg_val is not None:
        return cfg_val

    return False


# ---------------------------------------------------------------------------
# Lazy driver import
# ---------------------------------------------------------------------------


@functools.cache
def _driver() -> types.ModuleType:
    """Return the ``mssql_python`` module, importing it on first call.

    The result is cached so the import happens at most once per process.
    Tests can monkeypatch :func:`_get_mssql` instead (kept as alias below).
    """
    return importlib.import_module("mssql_python")


# Legacy shim used by existing tests / callers that monkeypatch ``_mssql``.
# We keep it so tests that do ``monkeypatch.setattr(_sql_pool_module, "_mssql", ...)``
# still work — they write to the module-level name which is checked first.
_mssql: types.ModuleType | None = None


def _get_mssql() -> types.ModuleType:
    """Return the mssql_python module, preferring the monkeypatched stub.

    Tests that use ``monkeypatch.setattr(_sql_pool_module, "_mssql", mock)`` set
    ``_mssql`` to a non-None value.  Production code (where ``_mssql`` is
    ``None``) falls through to the cached :func:`_driver`.
    """
    return _mssql if _mssql is not None else _driver()


# ---------------------------------------------------------------------------
# Internal connection-string helpers
# ---------------------------------------------------------------------------


def _has_key(connection_string: str, key: str) -> bool:
    """Return True if *key* is already present in the ODBC connection string."""
    pattern = re.compile(r"(?:^|;)\s*" + re.escape(key) + r"\s*=", re.IGNORECASE)
    return bool(pattern.search(connection_string))


def _set_key(connection_string: str, key: str, value: str) -> str:
    """Append *key=value* to *connection_string* if *key* is not already set."""
    if _has_key(connection_string, key):
        return connection_string
    stripped = connection_string.rstrip().rstrip(";")
    sep = ";" if stripped else ""
    return f"{stripped}{sep}{key}={value}"


# ---------------------------------------------------------------------------
# Connection-string builder
# ---------------------------------------------------------------------------


def build_connection_string(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
    use_access_token: bool = False,
) -> str:
    """Augment the API-provided connection string with auth, encryption and database settings.

    The operation is idempotent: calling it twice with the same target and mode
    returns the identical string.

    Args:
        target: The :class:`SqlTarget` whose ``connection_string`` and ``database``
            are used as inputs.
        mode: The credential mode, used to select the ActiveDirectory auth variant.
            Ignored when ``use_access_token`` is ``True``.
        use_access_token: When ``True``, omit the ``Authentication=`` key from the
            connection string.  The caller is responsible for injecting a pre-acquired
            token via ``attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct}``.

    Returns:
        The augmented ODBC connection string, ready to pass to the driver.
    """
    # The Fabric API returns the warehouse FQDN as a bare hostname with no
    # "Server=" prefix.  The mssql_python driver requires a proper ODBC key=value
    # format, so prepend "Server=" when the raw string has no Server key.
    raw = target.connection_string
    if not _has_key(raw, "Server"):
        raw = f"Server={raw}"
    # Only set the Authentication key when we are NOT injecting a pre-acquired token.
    # With a token in attrs_before, the Authentication key must be absent — the driver
    # uses whichever identity source is provided first and having both causes conflicts.
    if not use_access_token:
        raw = _set_key(raw, "Authentication", _MODE_TO_AD_AUTH[mode])
    cs = _set_key(raw, "Encrypt", "yes")
    cs = _set_key(cs, "TrustServerCertificate", "no")
    return _set_key(cs, "Database", target.database)


# ---------------------------------------------------------------------------
# Connection-error translation
# ---------------------------------------------------------------------------

# Case-insensitive substring that identifies a paused/inactive Fabric capacity
# in the driver message returned at connect time.
_CAPACITY_INACTIVE_FRAGMENT: str = "capacity is currently not active"


def _translate_connect_error(exc: Exception) -> Never:
    """Translate a driver-level connect failure into a clean FabricError.

    Called exclusively from the ``_get_mssql().connect(...)`` catch block in
    ``open_connection``.  Always raises -- never returns normally.

    Evaluation order is significant:

    1. Pre-typed :class:`~fabric_dw.exceptions.FabricCliError` -- re-raised bare
       so no extra frame is added and the original type is preserved.  Covers test
       mocks that inject a typed exception (e.g. ``AuthError``) as the connect
       side-effect.
    2. Capacity-inactive fragment -- translated to
       :class:`~fabric_dw.exceptions.CapacityInactiveError` immediately (never
       retried).
    3. Retryable driver exceptions -- re-raised bare so the ``_is_connect_retryable``
       check inside :func:`~fabric_dw.sql._with_connect_retry` still sees the
       original exception with its ``ddbc_error`` attribute intact (needed for
       Strategy 1 native-error matching, e.g. 18456 + "database was not found"
       during warehouse warm-up).
    4. Everything else -- wrapped in a generic
       :class:`~fabric_dw.exceptions.FabricError` that preserves the driver message.

    Raises:
        FabricCliError: Re-raised bare if *exc* is already a
            :class:`~fabric_dw.exceptions.FabricCliError`.
        CapacityInactiveError: When the message indicates the Fabric capacity
            is paused or inactive.
        Exception: Re-raised bare if :func:`~fabric_dw.sql_errors._is_connect_retryable`
            returns ``True`` (so the retry loop retains ``ddbc_error``).
        FabricError: For all other non-retryable driver connect failures,
            preserving the original driver message.
    """
    # Step 1: pass pre-typed errors through unchanged.
    if isinstance(exc, FabricCliError):
        raise  # noqa: PLE0704 -- always called from an except block; bare raise is valid
    # Step 2: paused-capacity -- surface immediately (must precede retryable check
    # so the error is never silently retried for ~120 s).
    msg = str(exc).lower()
    if _CAPACITY_INACTIVE_FRAGMENT in msg:
        err: FabricError = CapacityInactiveError(CAPACITY_INACTIVE_MESSAGE)
        err.__cause__ = exc
        raise err
    # Step 3: retryable driver errors -- re-raise bare to preserve ddbc_error.
    if _is_connect_retryable(exc):
        raise  # noqa: PLE0704 -- preserves ddbc_error attribute for Strategy 1 retry
    # Step 4: non-retryable driver failures -- wrap cleanly.
    wrapped = FabricError(f"SQL connection failed: {exc}")
    wrapped.__cause__ = exc
    raise wrapped


# ---------------------------------------------------------------------------
# open_connection
# ---------------------------------------------------------------------------


def open_connection(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
    autocommit: bool = False,
) -> Any:  # noqa: ANN401 -- native driver connection has no public static type
    """Return a raw mssql-python connection to the target warehouse.

    The mssql-python driver's native pooling is explicitly enabled before
    each connect attempt. Callers own the returned connection and close it
    normally.

    This function is intentionally synchronous.  Callers that need to keep the
    event loop free should wrap the entire sync block in ``asyncio.to_thread``.

    Args:
        target: The :class:`~fabric_dw.sql.SqlTarget` identifying the warehouse.
        mode: The credential mode for Entra authentication.
        autocommit: When ``True``, open the connection with ODBC-level
            autocommit enabled. Defaults to ``False``; native driver pooling
            is enabled before this connection is opened as well.

    Returns:
        A DB-API 2.0 connection from the ``mssql_python`` driver.
    """
    token_struct = get_sql_token_struct(mode)
    use_token = token_struct is not None
    cs = build_connection_string(target, mode=mode, use_access_token=use_token)
    attrs = {SQL_COPT_SS_ACCESS_TOKEN: token_struct} if use_token else None
    try:
        driver = _get_mssql()
        driver.pooling(enabled=True)
        raw_conn = driver.connect(
            cs, autocommit=autocommit, attrs_before=attrs, timeout=SQL_LOGIN_TIMEOUT_S
        )
    except Exception as _exc:  # translate driver connect failures only
        _translate_connect_error(_exc)
    # Sets query timeout on all *future* cursors (Connection.timeout.setter stores
    # the value; each cursor.__init__ reads it via _set_timeout()).  Safe here
    # because every cursor in this codebase is acquired after open_connection()
    # returns — no caller holds a cursor across connection open.
    raw_conn.timeout = SQL_QUERY_TIMEOUT_S
    return raw_conn
