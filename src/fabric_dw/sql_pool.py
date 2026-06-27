"""Connection pool and prerequisites for Microsoft Fabric Data Warehouse SQL connections.

This module holds the stateless helpers, config resolution, connection pool
implementation, and ``open_connection`` that are shared by or exposed via
:mod:`fabric_dw.sql` (query runner, ``SqlTarget``).

Module-global mutable state:
- ``_sql_config_cache`` — SQL config cache; thread-safe, cleared by ``_sql_config_cache_clear()``.
- ``_pool`` / ``_pool_lock`` — per-key LIFO connection pool; thread-safe.

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

import contextlib
import functools
import importlib
import logging
import os
import re
import threading
import time
import types
from typing import TYPE_CHECKING, Any, Never

from fabric_dw.auth import CredentialMode, get_sql_token_struct
from fabric_dw.config import UserConfig as _UserConfig
from fabric_dw.exceptions import CapacityInactiveError, FabricCliError, FabricError

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

# Truthy/falsy string sets for _resolve_sql_retry_executes and _pool_enabled.
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
# Connection pool
# ---------------------------------------------------------------------------

# Pool configuration constants — override at module level before first use.
# Pooling is controlled by _pool_enabled() which resolves: env var FABRIC_CONN_POOLING
# (falsy: "0"/"false"/"no"/"off"; empty/whitespace → absent) > config.toml
# [defaults].conn_pooling > built-in default on.
POOL_MAX_IDLE: int = 4
"""Maximum number of idle connections kept per ``(workspace_id, database, mode)`` key."""

POOL_MAX_IDLE_SECS: float = 300.0
"""Maximum age (seconds) of an idle connection before eviction on next checkout."""

# Pool key type: (workspace_id, database, mode_value)
_PoolKey = tuple[str, str, str]

# Each slot stores (underlying_connection, last_used_monotonic_timestamp).
_PoolSlot = tuple[Any, float]

# The pool: key -> LIFO stack of idle slots (top = last element).
_pool: dict[_PoolKey, list[_PoolSlot]] = {}
_pool_lock = threading.Lock()


def _pool_enabled() -> bool:
    """Return True when connection pooling is active.

    Resolution order (3-layer):
    1. ``FABRIC_CONN_POOLING`` env var — read at call-time so tests can toggle
       it without reimporting the module.  Only a non-empty, non-whitespace
       value is honoured; an empty or whitespace-only value (e.g. a Docker
       ``ENV FABRIC_CONN_POOLING=`` placeholder) is treated as absent and falls
       through to the next layer.  Accepted disable values: ``"0"``,
       ``"false"``, ``"no"``, ``"off"`` (case-insensitive).  Any other
       non-empty string keeps pooling enabled.
    2. ``config.toml`` ``[defaults].conn_pooling`` — consulted via the existing
       memoised :func:`_load_sql_config` cache (never calls load_config()
       per call).
    3. Built-in default: ``True`` (pooling on).
    """
    raw_env = os.environ.get("FABRIC_CONN_POOLING")
    if raw_env is not None:
        stripped = raw_env.strip()
        if stripped:
            # Non-empty value — honour it (falsy set disables, anything else enables).
            return stripped.lower() not in _FALSY_STRINGS
        # Empty / whitespace-only — treat as absent; fall through to config/default.

    cfg_val = _load_sql_config().defaults.conn_pooling
    if cfg_val is not None:
        return cfg_val

    return True


def _pool_time() -> float:
    """Return the current monotonic clock value.

    Isolated so tests can monkeypatch it to inject a deterministic clock
    without affecting ``time.monotonic`` globally.
    """
    return time.monotonic()


def _is_alive(conn: Any) -> bool:  # noqa: ANN401
    """Return True if *conn* appears usable.

    Checks the ``closed`` attribute when it is an ``int`` or ``bool`` (the
    DB-API convention: 0 = open).  Ignores the attribute when it is not a
    plain numeric value so that mock objects without an explicit ``closed``
    attribute are treated as alive.
    """
    closed = getattr(conn, "closed", None)
    # Only trust closed when it is a real int/bool; ignore Mock objects, etc.
    if not isinstance(closed, (int, bool)):
        return True
    return not bool(closed)


def reset_pool() -> None:
    """Drain and physically close every pooled connection.

    Call this on graceful shutdown (e.g. from the MCP server lifespan teardown,
    a CLI ``finally`` block, or a pytest fixture teardown).  Safe to call
    multiple times and from any thread.
    """
    with _pool_lock:
        to_close: list[Any] = []
        for slots in _pool.values():
            while slots:
                conn, _ts = slots.pop()
                to_close.append(conn)
        _pool.clear()
    # Close outside the lock so slow I/O does not block other threads.
    for conn in to_close:
        with contextlib.suppress(Exception):
            conn.close()


def _pool_checkout(key: _PoolKey) -> Any | None:  # noqa: ANN401
    """Pop and return a live, non-expired connection from the pool, or ``None``.

    Expired or dead connections are discarded (physically closed) during the
    search.  The pop is from the end of the list (LIFO - most recently used).
    """
    now = _pool_time()
    deadline = POOL_MAX_IDLE_SECS
    to_discard: list[Any] = []
    result: Any = None

    with _pool_lock:
        slots = _pool.get(key)
        if not slots:
            return None
        # Pop from the top (LIFO) until we find a usable slot.
        while slots:
            conn, last_used = slots.pop()
            if now - last_used > deadline or not _is_alive(conn):
                to_discard.append(conn)
                continue
            result = conn
            break

    # Close discarded connections outside the lock (may be slow I/O).
    for dead in to_discard:
        with contextlib.suppress(Exception):
            dead.close()

    return result


def _pool_checkin(key: _PoolKey, conn: Any) -> None:  # noqa: ANN401
    """Return *conn* to the pool if there is room, else physically close it.

    Also evicts expired or dead connections from the entire slot list on
    every checkin (D06 fix) so stale bottom-layer connections do not linger
    indefinitely even when only the top of the LIFO stack is ever reused.
    """
    now = _pool_time()
    deadline = POOL_MAX_IDLE_SECS
    to_close: list[Any] = []

    with _pool_lock:
        slots = _pool.setdefault(key, [])
        # Sweep the whole list for expired / dead entries before deciding
        # whether to add the incoming connection.  Iterate in reverse to
        # pop-in-place safely; build a new list to avoid O(n²) pops.
        live: list[_PoolSlot] = []
        for slot_conn, last_used in slots:
            if now - last_used > deadline or not _is_alive(slot_conn):
                to_close.append(slot_conn)
            else:
                live.append((slot_conn, last_used))
        slots[:] = live

        if len(slots) >= POOL_MAX_IDLE:
            to_close.append(conn)
        else:
            slots.append((conn, now))

    for dead in to_close:
        with contextlib.suppress(Exception):
            dead.close()


# ---------------------------------------------------------------------------
# Pooled connection wrapper
# ---------------------------------------------------------------------------


class _PooledConnection:
    """Thin wrapper that intercepts ``.close()`` to return the connection to the pool.

    When ``.close()`` is called:
    - If ``_discard`` is ``True`` (set after a failed query), the underlying
      connection is physically closed and NOT returned to the pool.
    - If pooling is disabled (``_pool_enabled()`` returns ``False``), the
      underlying connection is physically closed.
    - Otherwise the underlying connection is returned to the pool for reuse.

    All other method calls are forwarded verbatim to the underlying connection,
    so callers need not change any code.
    """

    def __init__(self, underlying: Any, key: _PoolKey) -> None:  # noqa: ANN401
        self._underlying = underlying
        self._key = key
        # Set to True after an exception during execute so that the connection
        # is NOT returned to the pool (unknown transaction state).
        self._discard: bool = False
        # Guard against double-close (e.g. explicit close in retry path + outer
        # finally).  Once True, subsequent close() calls are no-ops.
        self._closed: bool = False

    # ------------------------------------------------------------------ #
    # Forward _Connection protocol methods to the underlying object.      #
    # ------------------------------------------------------------------ #

    def cursor(self) -> Any:  # noqa: ANN401
        return self._underlying.cursor()

    def commit(self) -> None:
        self._underlying.commit()

    def rollback(self) -> None:
        self._underlying.rollback()

    def mark_discard(self) -> None:
        """Mark this connection for physical close instead of pool return.

        Call after any error that leaves the connection in an unknown state
        (e.g. mid-execute failure, open transaction with unknown content).
        Once marked, ``.close()`` physically closes the underlying socket and
        does NOT return it to the pool.
        """
        self._discard = True

    def close(self) -> None:
        """Return to pool or physically close, depending on state and config.

        Idempotent: a second call is a no-op.  This prevents the underlying TDS
        socket from receiving ``close()`` twice when the execute-retry path
        calls ``conn.close()`` explicitly and the outer ``finally`` also closes.

        Before returning to the pool, any open implicit transaction is rolled
        back so the next caller starts with a clean transaction state.
        """
        if self._closed:
            return
        self._closed = True
        if self._discard or not _pool_enabled():
            self._underlying.close()
        else:
            # Roll back any open implicit transaction before pool return so
            # the next lender starts with a clean state (D23).
            with contextlib.suppress(Exception):
                self._underlying.rollback()
            _pool_checkin(self._key, self._underlying)

    # Convenience accessor used by pool-specific tests.
    @property
    def _raw(self) -> Any:  # noqa: ANN401
        return self._underlying


def _make_pool_key(target: SqlTarget, mode: CredentialMode) -> _PoolKey:
    return (target.workspace_id, target.database, mode.value)


# ---------------------------------------------------------------------------
# Connection-error translation
# ---------------------------------------------------------------------------

# Case-insensitive substring that identifies a paused/inactive Fabric capacity
# in the driver message returned at connect time.
_CAPACITY_INACTIVE_FRAGMENT: str = "capacity is currently not active"

# Actionable message surfaced to the caller when the capacity is paused.
_CAPACITY_INACTIVE_MSG: str = (
    "The Fabric capacity for this workspace is paused or inactive. "
    "Resume it before running SQL, see "
    "https://learn.microsoft.com/fabric/data-warehouse/pause-resume"
)


def _translate_connect_error(exc: Exception) -> Never:
    """Translate a driver-level connect failure into a clean FabricError.

    Called exclusively from the ``_get_mssql().connect(...)`` catch block in
    ``open_connection``.  Always raises -- never returns normally.

    If *exc* is already a :class:`~fabric_dw.exceptions.FabricCliError` it is
    re-raised unchanged.  This avoids double-wrapping when a test injects a
    pre-typed exception (e.g. :class:`~fabric_dw.exceptions.AuthError`) as the
    connect side-effect, and ensures that any future typed error raised inside
    the pre-connect preparation steps propagates with its original type.

    Raises:
        FabricCliError: Re-raised unchanged if *exc* is already a
            :class:`~fabric_dw.exceptions.FabricCliError`.
        CapacityInactiveError: When the message indicates the Fabric capacity
            is paused or inactive.
        FabricError: For all other driver-level connection failures, preserving
            the original driver message.
    """
    if isinstance(exc, FabricCliError):
        raise exc
    msg = str(exc).lower()
    if _CAPACITY_INACTIVE_FRAGMENT in msg:
        err: FabricError = CapacityInactiveError(_CAPACITY_INACTIVE_MSG)
        err.__cause__ = exc
        raise err
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
) -> _PooledConnection:
    """Return a connection to the target warehouse, reusing a pooled one when available.

    The returned object satisfies the :class:`~fabric_dw.sql._Connection` protocol.
    Its ``.close()`` method returns the connection to the pool (when pooling is
    enabled and the connection is healthy) rather than physically closing the
    socket.  Callers do **not** need to change — the ``contextlib.closing``
    pattern works unchanged.

    When pooling is disabled (``_pool_enabled()`` returns ``False``, controlled
    via the ``FABRIC_CONN_POOLING`` env var, ``config.toml [defaults] conn_pooling``,
    or the built-in default on) every call opens a fresh physical connection
    and ``.close()`` physically closes it.

    When ``autocommit=True``, the ODBC driver does **not** wrap each statement
    in an explicit ``BEGIN TRANSACTION`` / ``COMMIT`` pair.  This is required
    for DDL statements that SQL Server disallows inside a transaction (e.g.
    ``ALTER DATABASE``).  Autocommit connections are **never** pooled — they
    are always opened fresh and physically closed on ``.close()``.

    This function is intentionally synchronous.  Callers that need to keep the
    event loop free should wrap the entire sync block in ``asyncio.to_thread``.

    Args:
        target: The :class:`~fabric_dw.sql.SqlTarget` identifying the warehouse.
        mode: The credential mode for Entra authentication.
        autocommit: When ``True``, open the connection with ODBC-level
            autocommit enabled.  Defaults to ``False``.  Autocommit connections
            bypass the pool entirely.

    Returns:
        A :class:`_PooledConnection` wrapping a DB-API 2.0 connection from
        the ``mssql_python`` driver.
    """
    # Autocommit connections bypass the pool entirely to avoid cross-contaminating
    # a pooled autocommit=False connection with autocommit=True semantics or vice
    # versa.  They are always opened fresh and physically closed after use.
    if autocommit:
        # Acquire a token struct when running under GitHub OIDC.  This bypasses the
        # mssql-python driver's own DefaultAzureCredential chain (which uses
        # AzureCliCredential whose GitHub OIDC assertion expires after ~5 min) in
        # favour of our self-refreshing credential whose _fetch_github_oidc_jwt
        # always obtains a fresh assertion.
        token_struct = get_sql_token_struct(mode)
        use_token = token_struct is not None
        cs = build_connection_string(target, mode=mode, use_access_token=use_token)
        attrs: dict[int, bytes] | None = (
            {SQL_COPT_SS_ACCESS_TOKEN: token_struct} if use_token else None
        )
        try:
            raw_conn: Any = _get_mssql().connect(
                cs, autocommit=True, attrs_before=attrs, timeout=SQL_LOGIN_TIMEOUT_S
            )
        except Exception as _exc:  # translate driver connect failures only
            _translate_connect_error(_exc)
        # Sets query timeout on all *future* cursors (Connection.timeout.setter stores
        # the value; each cursor.__init__ reads it via _set_timeout()).  Safe here
        # because every cursor in this codebase is acquired after open_connection()
        # returns — no caller holds a cursor across connection open.
        raw_conn.timeout = SQL_QUERY_TIMEOUT_S
        # Wrap in _PooledConnection with a sentinel key; mark_discard() ensures
        # .close() physically closes the connection rather than pooling it.
        key = _make_pool_key(target, mode)
        wrapped = _PooledConnection(raw_conn, key)
        wrapped.mark_discard()
        return wrapped

    key = _make_pool_key(target, mode)

    if _pool_enabled():
        cached = _pool_checkout(key)
        if cached is not None:
            # Pool HIT — return the cached connection without acquiring a token.
            # Checked-out connections are already authenticated; acquiring a token
            # here would invoke credential.get_token() (holding azure-identity's
            # token-cache lock) and then discard the result — pure waste.
            return _PooledConnection(cached, key)

    # Pool MISS (or pooling disabled) — open a new physical connection.
    # Only now do we acquire the OIDC token struct, so the credential is never
    # consulted on pool-hit paths.
    token_struct = get_sql_token_struct(mode)
    use_token = token_struct is not None
    cs = build_connection_string(target, mode=mode, use_access_token=use_token)
    attrs = {SQL_COPT_SS_ACCESS_TOKEN: token_struct} if use_token else None
    try:
        raw_conn = _get_mssql().connect(cs, attrs_before=attrs, timeout=SQL_LOGIN_TIMEOUT_S)
    except Exception as _exc:  # translate driver connect failures only
        _translate_connect_error(_exc)
    # Sets query timeout on all *future* cursors (Connection.timeout.setter stores
    # the value; each cursor.__init__ reads it via _set_timeout()).  Safe here
    # because every cursor in this codebase is acquired after open_connection()
    # returns — no caller holds a cursor across connection open.
    raw_conn.timeout = SQL_QUERY_TIMEOUT_S
    return _PooledConnection(raw_conn, key)
