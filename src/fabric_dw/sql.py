"""Stateless SQL helper for connecting to Microsoft Fabric Data Warehouses.

Public API
----------
- :class:`SqlTarget`          — frozen dataclass identifying a warehouse.
- :func:`build_connection_string` — augment the raw API connection string.
- :func:`open_connection`     — checkout a pooled (or fresh) connection; caller closes.
- :func:`map_driver_error`    — classify a driver exception → high-level error.
- :func:`is_transient_connection_error` — True when an exception is a retryable TDS drop.
- :func:`run_query`           — open connection, execute, fetch, map errors.
- :func:`run_statements`      — execute multiple DDL statements on ONE connection.
- :func:`reset_pool`          — drain and physically close all pooled connections.

__all__
-------
Only the public names listed below are part of the stable API.  Internal
helpers (``_pool``, ``_driver``, ``_PooledConnection``, ...) are not exported.

Connection Pool
---------------
``open_connection`` returns a thin wrapper whose ``.close()`` method returns
the underlying connection to a per-key LIFO pool instead of physically closing
it.  The pool is keyed on ``(workspace_id, database, mode)`` and bounded by two
module-level constants:

``POOL_MAX_IDLE``       — maximum idle connections per key (default 4).
``POOL_MAX_IDLE_SECS``  — maximum idle age in seconds before eviction (default 300).

Disable pooling by setting the environment variable ``FABRIC_CONN_POOLING``
to a falsy value (``"0"``, ``"false"``, ``"no"``, or ``"off"``) before
process startup, or at runtime by assigning the same value and then calling
:func:`reset_pool` to drain existing connections.  Pooling can also be
disabled via ``config.toml`` ``[defaults] conn_pooling = false`` (resolution
order: env var > config > built-in default on).  An empty or whitespace-only
``FABRIC_CONN_POOLING`` is treated as absent and falls through to the config/default
layer.  When disabled every ``open_connection`` call opens a fresh physical
connection and ``.close()`` physically closes it.

Call :func:`reset_pool` on graceful shutdown to close all idle connections.  The
MCP server lifespan calls ``reset_pool`` in its ``finally`` block so pooled TDS
connections are drained on server shutdown.
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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from fabric_dw.auth import CredentialMode, get_sql_token_struct
from fabric_dw.exceptions import AuthError, NotFoundError, PermissionDeniedError

_log = logging.getLogger(__name__)

__all__ = [
    "POOL_MAX_IDLE",
    "POOL_MAX_IDLE_SECS",
    "SQL_COPT_SS_ACCESS_TOKEN",
    "SQL_LOGIN_TIMEOUT_S",
    "SQL_QUERY_TIMEOUT_S",
    "SqlTarget",
    "build_connection_string",
    "is_auth_failed_message",
    "is_snapshot_not_ready_error",
    "is_transient_connection_error",
    "map_driver_error",
    "open_connection",
    "reset_pool",
    "run_query",
    "run_statements",
    "tenant_from_connection_string_host",
]

# ---------------------------------------------------------------------------
# Transient-retry configuration
# ---------------------------------------------------------------------------

# Backoff delays (seconds) for the *execute-phase* retry loop.
# The delay before attempt N+1 is _EXECUTE_RETRY_DELAYS[min(N, len-1)],
# so the sequence is:
#   attempt 1 → fails → sleep 2 s → attempt 2
#   attempt 2 → fails → sleep 5 s → attempt 3
#   attempt 3+ → fails → sleep 10 s → attempt 4 … (capped at 10 s)
#
# The loop retries up to len(_EXECUTE_RETRY_DELAYS) + 1 attempts total
# (initial attempt + 3 retries = 4 attempts max) AND stops early if the
# wall-clock deadline (sql_retry_deadline_s, default 120 s) is exceeded.
# Both guards are applied so the worst-case bound is clearly bounded:
#   worst case <= (len(_EXECUTE_RETRY_DELAYS) + 1) attempts
#              x (effective deadline + SQL_QUERY_TIMEOUT_S)
#
# With the built-in default deadline (120 s):
#   = 4 x (120 s + 300 s) = ~1680 s (~28 minutes)
# With FABRIC_SQL_RETRY_TIMEOUT_S=300 (integration CI):
#   = 4 x (300 s + 300 s) = ~2400 s (~40 minutes)
#
# The deadline is configurable via FABRIC_SQL_RETRY_TIMEOUT_S env var or
# ``fdw config set sql-retry-deadline``.
_EXECUTE_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0, 10.0)

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

# Backoff delays for the connect-phase retry loop.  The delay before attempt
# N+1 is _CONNECT_RETRY_DELAYS[min(N, len-1)], so the sequence is:
#   attempt 1 → fails → sleep 5 s → attempt 2
#   attempt 2 → fails → sleep 10 s → attempt 3
#   attempt 3 → fails → sleep 15 s → attempt 4
#   attempt 4+ → fails → sleep 15 s → attempt 5 … (capped at 15 s)
_CONNECT_RETRY_DELAYS: tuple[float, ...] = (5.0, 10.0, 15.0)

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
# SQL retry config resolution — 3-layer precedence
# ---------------------------------------------------------------------------
# Both knobs resolve at call-time via the 3-layer rule:
#   env var (highest) > config.toml [defaults] > built-in fallback
#
# A module-level cache avoids re-reading the config file on every query.
# The cache is protected by a threading.Lock (threading is already imported).
# _sql_config_cache_clear() is a test-only hook to reset the cache between
# tests that mutate env vars or the config.

from fabric_dw.config import UserConfig as _UserConfig  # noqa: E402

_sql_config_cache: _UserConfig | None = None
_sql_config_lock: threading.Lock = threading.Lock()


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


def _resolve_sql_retry_deadline_s() -> int:
    """Return the effective SQL retry deadline in seconds.

    Resolution order (3-layer):
    1. ``FABRIC_SQL_RETRY_TIMEOUT_S`` env var — must be an integer (or float-formatted
       integer like ``"120.0"``) >= 1.  Invalid values are ignored (warning logged)
       and fall through to next layer.
    2. ``config.toml`` ``[defaults].sql_retry_deadline_s``.
    3. Built-in fallback: :data:`_SQL_RETRY_DEADLINE_S_DEFAULT` (120 s).
    """
    raw_env = os.environ.get("FABRIC_SQL_RETRY_TIMEOUT_S")
    if raw_env is not None:
        try:
            v = int(float(raw_env))
        except (ValueError, OverflowError):
            _log.warning("FABRIC_SQL_RETRY_TIMEOUT_S=%r is not a valid integer; ignoring", raw_env)
        else:
            if v >= _MIN_SQL_RETRY_DEADLINE_S:
                return v
            _log.warning(
                "FABRIC_SQL_RETRY_TIMEOUT_S=%r must be >= %s; ignoring",
                raw_env,
                _MIN_SQL_RETRY_DEADLINE_S,
            )

    cfg_val = _load_sql_config().defaults.sql_retry_deadline_s
    if cfg_val is not None:
        return cfg_val

    return _SQL_RETRY_DEADLINE_S_DEFAULT


# Truthy/falsy string sets for _resolve_sql_retry_executes.
# Kept inline to avoid importing telemetry's private helpers.
_FALSY_STRINGS: frozenset[str] = frozenset({"", "0", "false", "no", "off"})


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
# Lazy driver import — @functools.cache avoids the module-level global and the
# PLW0603 noqa suppression.
# ---------------------------------------------------------------------------


@functools.cache
def _driver() -> types.ModuleType:
    """Return the ``mssql_python`` module, importing it on first call.

    The result is cached so the import happens at most once per process.
    Tests can monkeypatch :func:`_get_mssql` instead (kept as alias below).
    """
    return importlib.import_module("mssql_python")


# Legacy shim used by existing tests / callers that monkeypatch ``_mssql``.
# We keep it so tests that do ``monkeypatch.setattr(_sql_module, "_mssql", ...)``
# still work — they write to the module-level name which is checked first.
_mssql: types.ModuleType | None = None


def _get_mssql() -> types.ModuleType:
    """Return the mssql_python module, preferring the monkeypatched stub.

    Tests that use ``monkeypatch.setattr(_sql_module, "_mssql", mock)`` set
    ``_mssql`` to a non-None value.  Production code (where ``_mssql`` is
    ``None``) falls through to the cached :func:`_driver`.
    """
    return _mssql if _mssql is not None else _driver()


# ---------------------------------------------------------------------------
# Sentinel strings for error classification
# ---------------------------------------------------------------------------

# SQL permission-denial failures in driver error messages.
_PERMISSION_DENIED_FRAGMENTS = (
    "permission was denied",
    "denied the right to",
)

# Entra authentication failures in driver error messages.
# "could not login" covers the bare SQL Server 18456 message form
# ("Could not login (18456)") that does NOT embed "authentication failed".
_AUTH_FAILED_FRAGMENTS = ("authentication failed", "could not login")

# SQL Server native error numbers that indicate permission denied.
# 229: SELECT permission denied; 230: INSERT; 297: execute permission denied.
_PERMISSION_DENIED_ERROR_NUMBERS = frozenset({229, 230, 297})

# SQL Server native error number for authentication failure (login failed).
_AUTH_FAILED_ERROR_NUMBERS = frozenset({18456})

# SQL Server native error numbers that indicate a missing object.
# 208:  Invalid object name (table/view not found).
# 2812: Could not find stored procedure '<name>'.
_NOT_FOUND_ERROR_NUMBERS = frozenset({208, 2812})

# Message fragments that indicate a missing database object.
_NOT_FOUND_FRAGMENTS = (
    "invalid object name",
    "base table or view not found",
)

# Fragments that indicate a freshly-created snapshot database has not yet
# finished provisioning at the SQL layer ("eventual consistency" lag).  The
# full error from the Fabric TDS endpoint reads:
#   "User does not have permission to alter database '<name>', the database
#    does not exist, or the database is not in a state that allows access checks."
# All three clauses are surfaced as a single PermissionDeniedError (the driver
# maps them via the permission-denied fragment path), so we detect them by
# matching the unique sub-phrase that distinguishes provisioning lag from a real
# permission denial.
_SNAPSHOT_NOT_READY_FRAGMENTS = ("not in a state that allows access checks",)

# Fragments that indicate a transient connection-level drop (TCP tear-down,
# server-side restart, or fabric warm-up).  These are safe to retry because
# the statement has NOT been executed on the server yet (connection failed).
# Keep this list tight — we must NOT retry real SQL or auth-config errors.
_TRANSIENT_FRAGMENTS = (
    # mssql_python / ODBC Driver 18 wording:
    "communication link failure",
    "connection was forcibly closed",
    "a transport-level error",
    "tcp provider",
    # Generic socket/timeout seen during heavy transient:
    "connection timed out",
    "connection reset by peer",
    # NOTE: "database was not found" is intentionally NOT listed here.  The real
    # Fabric TDS driver embeds native error number 18456 alongside this message, so
    # map_driver_error() converts it to AuthError before is_transient_connection_error
    # is consulted — making a "database was not found" entry dead code in the
    # run_query / run_statements retry paths.  Including it would also incorrectly
    # retry a genuine wrong-database-name error for the full backoff window.
    # _wait_for_sql_readiness in tests/integration/conftest.py handles the warm-up
    # case correctly by inspecting the AuthError message directly.
)

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

# Regex to extract the native SQL Server error number from a DDBC error string.
# The ODBC driver embeds it in two forms:
#   - "Error: 18456" or "error 229"   → captured by the first alternative.
#   - "[SQL Server] ... (229)" where the parenthesised number is anchored to an
#     explicit SQL-Server/Msg/Error context word so that incidental numbers in
#     unrelated text (port numbers, byte counts, row counts) are not matched.
#
# Note: the second alternative deliberately reuses the word "Error" (distinct
# from "Error:" with the colon+space in alt-1) to match patterns like
# "Error (229)".  Since re.finditer processes left-to-right and the code
# returns on the first recognised number, first-match-wins is intentional —
# there is no ambiguity between the two alternatives in practice.
_NATIVE_ERROR_RE = re.compile(
    r"\b(?:Error:\s*|error\s+)(\d+)\b"
    r"|(?:SQL\s+Server|Msg|Error)\b[^(]*\((\d+)\)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Minimal DB-API 2.0 Protocols (for type checking only)
# ---------------------------------------------------------------------------


class _Cursor(Protocol):
    description: list[tuple[str, Any]] | None
    rowcount: int

    def execute(self, sql: str, params: Sequence[object] | None = None) -> None: ...

    def fetchone(self) -> tuple[Any, ...] | None: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]: ...

    def nextset(self) -> bool | None: ...

    def close(self) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# SqlTarget
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SqlTarget:
    """Identifies a specific Fabric warehouse to connect to.

    Attributes:
        workspace_id: The Fabric workspace GUID.
        database: The warehouse / database name.
        connection_string: The raw ODBC connection string from the Fabric API;
            no augmentation should be applied before passing it here.
    """

    workspace_id: str
    database: str
    connection_string: str


# ---------------------------------------------------------------------------
# Fabric host → tenant GUID helper
# ---------------------------------------------------------------------------

# The Fabric DW / SQL-analytics-endpoint connection-string hostname encodes
# both the tenant and workspace IDs in its first label:
#
#   <b32(tenantId)>-<b32(workspaceId)>.datawarehouse.fabric.microsoft.com
#
# where b32(guid) = RFC-4648 base32 (lowercase, no padding) of the GUID's
# .NET little-endian byte order (Python: uuid.UUID(...).bytes_le).
#
# Each encoded GUID is exactly 26 base32 characters (128 bits / 5 bits per
# char = 25.6 → rounded up to 26 with one trailing padding position stripped).
_FABRIC_DW_SUFFIX = ".datawarehouse.fabric.microsoft.com"
_B32_GUID_LEN = 26


def tenant_from_connection_string_host(host: object) -> str | None:
    """Decode the tenant GUID from a Fabric DW connection-string hostname.

    The Fabric connection-string hostname encodes both the tenant and workspace
    IDs in its first DNS label:

        <b32(tenantId)>-<b32(workspaceId)>.datawarehouse.fabric.microsoft.com

    Each encoded GUID is exactly 26 base32 characters.  This function decodes
    the first segment (tenant) and returns it as a UUID string.

    The function is entirely fail-safe: any non-matching or garbage input
    returns ``None`` and never raises.

    Args:
        host: A Fabric connection-string hostname, or any other string/value.
            A full ODBC connection string (``Server=<host>``) is also accepted;
            the host is extracted from it first.

    Returns:
        The tenant UUID string (e.g. ``"9064c167-4885-40ef-9f34-1853218aea86"``),
        or ``None`` if *host* does not match the expected Fabric DW shape.
    """
    import base64  # noqa: PLC0415 (stdlib, always available)
    import uuid  # noqa: PLC0415

    try:
        if not isinstance(host, str):
            return None

        # Strip an optional "Server=" prefix that the raw API value may carry.
        # Also strip any whitespace between '=' and the hostname (e.g. "Server= host").
        raw = host.strip()
        if raw.lower().startswith("server="):
            raw = raw[len("server=") :].strip()

        # Validate the *.datawarehouse.fabric.microsoft.com suffix.
        if not raw.lower().endswith(_FABRIC_DW_SUFFIX):
            return None

        # The first DNS label is everything before the first '.'.
        first_label = raw[: raw.index(".")]

        # The label has the form "<tenant_b32>-<workspace_b32>".
        # Split on the last '-' separator between the two 26-char segments.
        sep_pos = first_label.find("-")
        if sep_pos < 0:
            return None

        tenant_b32 = first_label[:sep_pos]
        workspace_b32 = first_label[sep_pos + 1 :]

        # Both segments must be exactly 26 characters.
        if len(tenant_b32) != _B32_GUID_LEN or len(workspace_b32) != _B32_GUID_LEN:
            return None

        # Pad to a multiple of 8 and base32-decode (RFC 4648; uppercase alphabet).
        def _b32_to_uuid(segment: str) -> str:
            padded = segment.upper() + "=" * ((8 - len(segment) % 8) % 8)
            raw_bytes = base64.b32decode(padded)
            return str(uuid.UUID(bytes_le=raw_bytes))

        tenant_id = _b32_to_uuid(tenant_b32)

        # Validate the workspace segment decodes to a well-formed UUID too.
        # A 26-char segment with garbage content would otherwise let a host with
        # a valid tenant prefix but garbage workspace slip through as a match.
        _b32_to_uuid(workspace_b32)

        # Round-trip validate: the decoded tenant must parse as a UUID.
        # uuid.UUID() above already does this, but be explicit.
        uuid.UUID(tenant_id)
    except Exception:
        return None
    else:
        return tenant_id


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
# Public API
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


def open_connection(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
    autocommit: bool = False,
) -> _Connection:
    """Return a connection to the target warehouse, reusing a pooled one when available.

    The returned object satisfies the :class:`_Connection` protocol.  Its
    ``.close()`` method returns the connection to the pool (when pooling is
    enabled and the connection is healthy) rather than physically closing the
    socket.  Callers do **not** need to change - the ``contextlib.closing``
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
        target: The :class:`SqlTarget` identifying the warehouse.
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
        raw_conn: Any = _get_mssql().connect(
            cs, autocommit=True, attrs_before=attrs, timeout=SQL_LOGIN_TIMEOUT_S
        )
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
    raw_conn = _get_mssql().connect(cs, attrs_before=attrs, timeout=SQL_LOGIN_TIMEOUT_S)
    # Sets query timeout on all *future* cursors (Connection.timeout.setter stores
    # the value; each cursor.__init__ reads it via _set_timeout()).  Safe here
    # because every cursor in this codebase is acquired after open_connection()
    # returns — no caller holds a cursor across connection open.
    raw_conn.timeout = SQL_QUERY_TIMEOUT_S
    return _PooledConnection(raw_conn, key)


def map_driver_error(exc: BaseException) -> Exception | None:
    """Return a mapped exception for known driver error categories, or ``None``.

    Matching strategy (in priority order):

    1. **Native SQL Server error numbers** - inspect ``exc.ddbc_error`` for
       embedded error numbers (e.g. ``Error: 229``, ``(18456)``).  This is the
       most reliable signal and survives locale / driver-version changes.
    2. **Message-fragment fallback** - scan the stringified exception for known
       English substrings.  Kept so that behaviour never regresses when error
       numbers are unavailable (e.g. mock exceptions in tests).

    Permission-denied is checked before auth-failure in both strategies so a
    message containing both fragments resolves to
    :class:`~fabric_dw.exceptions.PermissionDeniedError`.

    Args:
        exc: The raw exception raised by the driver.

    Returns:
        A :class:`~fabric_dw.exceptions.PermissionDeniedError`,
        :class:`~fabric_dw.exceptions.AuthError`, or
        :class:`~fabric_dw.exceptions.NotFoundError` instance if the error
        message matches a known fragment or error number, otherwise ``None``.
    """
    # --- Strategy 1: native error number (primary, locale-independent) ---
    ddbc_error = getattr(exc, "ddbc_error", None)
    if ddbc_error:
        for match in _NATIVE_ERROR_RE.finditer(str(ddbc_error)):
            raw_num = match.group(1) or match.group(2)
            if raw_num:
                err_num = int(raw_num)
                if err_num in _PERMISSION_DENIED_ERROR_NUMBERS:
                    return PermissionDeniedError(str(exc))
                if err_num in _AUTH_FAILED_ERROR_NUMBERS:
                    return AuthError(str(exc))
                if err_num in _NOT_FOUND_ERROR_NUMBERS:
                    return NotFoundError(str(exc))

    # --- Strategy 2: message-fragment fallback (locale-dependent, documented) ---
    msg = str(exc).lower()
    for cls, fragments in (
        (PermissionDeniedError, _PERMISSION_DENIED_FRAGMENTS),
        (AuthError, _AUTH_FAILED_FRAGMENTS),
        (NotFoundError, _NOT_FOUND_FRAGMENTS),
    ):
        if any(fragment in msg for fragment in fragments):
            return cls(str(exc))
    return None


def is_transient_connection_error(exc: BaseException) -> bool:
    """Return True when *exc* represents a retryable TDS connection-level drop.

    Matches only transport / warm-up errors, NOT auth failures or SQL errors.
    Used by :func:`run_query` and :func:`run_statements` to gate the small
    bounded retry loop that guards against transient Fabric TDS drops.

    Args:
        exc: The raw exception raised by the driver.

    Returns:
        ``True`` when the exception message matches a known transient fragment,
        ``False`` for all other errors (auth, permission, SQL syntax, etc.).
    """
    msg = str(exc).lower()
    return any(fragment in msg for fragment in _TRANSIENT_FRAGMENTS)


def is_snapshot_not_ready_error(exc: BaseException) -> bool:
    """Return True when *exc* indicates a snapshot DB is still provisioning.

    A freshly-created Fabric warehouse snapshot database is not immediately
    accessible at the SQL layer.  During the provisioning window the TDS
    endpoint returns a message of the form:

        "User does not have permission to alter database '<name>', the database
         does not exist, or the database is not in a state that allows access
         checks."

    This is surfaced as a :class:`~fabric_dw.exceptions.PermissionDeniedError`
    by :func:`map_driver_error` because the message contains the
    "permission was denied" / "permission" fragment.  However, retrying is
    safe here — the statement was rejected *before* it could execute, and once
    provisioning finishes the same ``ALTER DATABASE`` will succeed.

    Args:
        exc: The exception raised by the driver or by :func:`map_driver_error`.

    Returns:
        ``True`` when the message matches a known snapshot-not-ready fragment,
        ``False`` for all other errors.
    """
    msg = str(exc).lower()
    return any(fragment in msg for fragment in _SNAPSHOT_NOT_READY_FRAGMENTS)


def is_auth_failed_message(msg: str) -> bool:
    """Return True when *msg* contains a known Entra authentication-failure fragment.

    This is the public counterpart to the private :data:`_AUTH_FAILED_FRAGMENTS`
    tuple.  It centralises the check so that callers outside this module (e.g.
    integration test readiness probes) do not need to import the private constant.

    Matching is case-insensitive.  Covered fragments include:

    * ``"authentication failed"`` — the common Entra/ODBC wording.
    * ``"could not login"`` — the bare SQL Server 18456 form that does **not**
      embed the "authentication failed" substring (e.g. "Could not login (18456)").

    Args:
        msg: A string to test, typically ``str(exc)`` from a driver exception.

    Returns:
        ``True`` when *msg* contains at least one auth-failure fragment,
        ``False`` otherwise.
    """
    lower = msg.lower()
    return any(fragment in lower for fragment in _AUTH_FAILED_FRAGMENTS)


# ---------------------------------------------------------------------------
# TDS runner helpers
# ---------------------------------------------------------------------------


def _is_connect_retryable(exc: BaseException) -> bool:
    """Return True when *exc* is retryable on the connect/login path.

    In addition to the standard transient TDS transport errors, authentication
    failures (error 18456 / SQLSTATE 28000) are also treated as retryable here
    because a freshly-created or warming-up Fabric warehouse may reject the
    login with "authentication failed" / "could not login" until the TDS
    endpoint finishes provisioning — even when the credentials are correct.

    **Warm-up window**: :func:`_with_connect_retry` retries retryable errors
    for up to ``_CONNECT_RETRY_TIMEOUT_S`` seconds (~120 s by default), which
    is enough margin to cover observed Fabric warehouse warm-up durations
    (~60-90 s).

    **Trade-off**: a genuinely-wrong credential will now hang up to ~120 s
    before the AuthError is surfaced to the caller, because the retry loop
    cannot distinguish "wrong credential" from "warehouse still warming up".
    This is intentional and accepted: the warm-up case is far more common in
    production usage.

    Scope: this helper is ONLY used by :func:`_with_connect_retry`.  It is NOT
    used in the execute-phase retry logic — auth errors there are still mapped
    to :class:`~fabric_dw.exceptions.AuthError` and raised immediately.
    """
    if is_transient_connection_error(exc):
        return True
    # Also retry auth-failed errors on the connect/login path.
    # Use the same constants as map_driver_error() for consistency.
    ddbc_error = getattr(exc, "ddbc_error", None)
    if ddbc_error:
        for match in _NATIVE_ERROR_RE.finditer(str(ddbc_error)):
            raw_num = match.group(1) or match.group(2)
            if raw_num and int(raw_num) in _AUTH_FAILED_ERROR_NUMBERS:
                return True
    msg = str(exc).lower()
    return any(fragment in msg for fragment in _AUTH_FAILED_FRAGMENTS)


def _with_connect_retry(
    target: SqlTarget,
    mode: CredentialMode,
    autocommit: bool,  # noqa: FBT001
) -> tuple[_Connection, int, int, BaseException | None]:
    """Attempt to open a connection, retrying on transient connect failures.

    This is a shared helper for :func:`run_query` and :func:`run_statements`
    (D08 - DRY extraction).  It encapsulates the time-bounded transient-retry
    loop for the **connect phase only** — the execute phase is NOT covered here.

    Retry boundary (D10)
    --------------------
    Transient errors raised by ``open_connection`` are always safe to retry
    because no statement has been sent to the server yet.  Transient errors
    raised *after* ``cursor.execute`` has been called may indicate that the
    server already received and applied the statement — retrying such errors
    for non-idempotent DML would risk duplicate execution.

    Auth-failed retries / warm-up window
    -------------------------------------
    Authentication failures (error 18456) are retried here because a
    warming-up Fabric warehouse may reject the login until provisioning
    completes — even with correct credentials.  The retry window is governed
    by ``_CONNECT_RETRY_TIMEOUT_S`` (~120 s by default) so that the full
    warehouse warm-up duration is covered.

    **Trade-off**: a genuinely-wrong credential will now hang for up to ~120 s
    before the AuthError is surfaced to the caller.  See
    :func:`_is_connect_retryable` for a fuller discussion.

    Clock / sleep are referenced through the ``time`` module object so that
    unit tests can substitute a fake clock without real delays:
    ``monkeypatch.setattr(_sql_module, "time", fake_time_module)``.

    Args:
        target: The :class:`SqlTarget` to connect to.
        mode: The credential mode for Entra authentication.
        autocommit: Whether to open with ODBC-level autocommit.

    Returns:
        A tuple ``(conn, attempt, max_attempts, last_exc)`` where:
        - ``conn`` is the open connection (ready to use).
        - ``attempt`` is the zero-based attempt index that succeeded (0 on the
          first attempt, 1 on the first retry, etc.).
        - ``max_attempts`` is always ``attempt + 2`` (a sentinel value retained
          for API compatibility; callers no longer use it as a retry gate).
        - ``last_exc`` is the last transient exception seen before success
          (``None`` on first-attempt success).

    Raises:
        Any non-retryable exception from ``open_connection`` immediately.
        The last retryable exception when the wall-clock deadline passes.
    """
    deadline = time.monotonic() + _resolve_sql_retry_deadline_s()
    last_exc: BaseException | None = None
    attempt = 0

    while True:
        try:
            conn = open_connection(target, mode=mode, autocommit=autocommit)
        except Exception as exc:
            if not _is_connect_retryable(exc):
                # Non-retryable error — raise immediately without any delay.
                raise
            last_exc = exc
            if time.monotonic() >= deadline:
                # Budget exhausted — surface the last retryable error.
                raise
            # Back off before the next attempt.  _CONNECT_RETRY_DELAYS is
            # indexed from 0 (= delay before attempt 2); clamp to last value.
            delay = _CONNECT_RETRY_DELAYS[min(attempt, len(_CONNECT_RETRY_DELAYS) - 1)]
            time.sleep(delay)
            attempt += 1
            continue
        else:
            # Connection succeeded.
            # ``max_attempts`` is returned as ``attempt + 2`` purely for API
            # compatibility; ``run_query`` ignores it (prefixed ``_max_attempts``)
            # and gates execute-phase retries on ``_max_execute_attempts`` instead.
            return conn, attempt, attempt + 2, last_exc


def run_query(  # noqa: PLR0913, PLR0915
    target: SqlTarget,
    statement: str,
    *,
    params: Sequence[object] | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
    commit: bool = False,
    fetch: Literal["all", "none", "one", "rowcount"] = "all",
    autocommit: bool = False,
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Open a connection, execute *statement*, fetch rows, close, and map errors.

    This is the single TDS execute-and-fetch helper that replaces ~20 copies of
    the open-connection / cursor / execute / map-error pattern across services.

    The ``mssql_python`` driver accepts a sequence of parameter values via
    ``execute(sql, params)`` where *params* is a ``Sequence`` (list or tuple).
    Use ``?`` placeholders in *statement*.

    Retry boundary — commit-phase safety
    -------------------------------------
    The retry loop covers ONLY the **execute + fetch** phase (up to and
    including reading ``cursor.rowcount`` or calling ``fetchall``/``fetchone``).
    The **commit** is performed ONCE, outside the retry loop, after a
    successful execute+fetch.

    This split is the key safety guarantee for COPY INTO / DML:

    * If a transient TDS error occurs **during execute**, the statement has NOT
      been sent to (or committed on) the server — it is safe to discard the
      connection and retry on a fresh one.
    * If a transient error occurs **during or after commit**, the load may
      already have been committed server-side — retrying the statement could
      cause a double-load.  Such errors are therefore **re-raised immediately**
      without retrying the statement.

    Transient execute-phase errors are retried only when ``fetch != "none"``
    (i.e. for ``"all"``, ``"one"``, and ``"rowcount"``).  DML / DDL
    (``fetch="none"``) is never retried after ``cursor.execute`` has begun.

    Attempt cap and time budget
    ---------------------------
    The loop retries at most ``len(_EXECUTE_RETRY_DELAYS)`` times (3 retries =
    4 total attempts: initial + 3) **in addition** to the wall-clock deadline
    (``_CONNECT_RETRY_TIMEOUT_S``, ~120 s).  Both guards are applied so the
    worst case is clearly bounded.  Between retries the loop sleeps with
    exponential backoff (starting at ~2 s, capped at ~10 s — see
    ``_EXECUTE_RETRY_DELAYS``).

    If the attempt cap or the time budget is exhausted before any attempt
    succeeds, the last transient error is re-raised.

    Args:
        target: The :class:`SqlTarget` identifying the warehouse.
        statement: The SQL statement to execute.
        params: Optional sequence of parameter values to bind.  Use ``?``
            placeholders in *statement*.  Identifiers (schema/table names) MUST
            be bracket-quoted via :func:`~fabric_dw.identifiers.quote_identifier`
            and validated via :func:`~fabric_dw.identifiers.validate_identifier`
            — they cannot be bound as parameters.
        mode: The credential mode for Entra authentication.
        commit: When ``True``, call ``conn.commit()`` after execute (for DDL/DML).
            Ignored when ``autocommit=True`` (the driver commits automatically).
        fetch: One of:
            - ``"all"`` (default) — call ``fetchall()`` and return
              ``(columns, rows)``; columns are derived from ``cursor.description``.
              If ``cursor.description`` is ``None`` (no result set), returns
              ``([], [])`` without calling ``fetchall()`` (defensive guard against
              ``ProgrammingError: Invalid cursor state``).
            - ``"one"`` — call ``fetchone()``; returns ``(columns, [row])``
              or ``(columns, [])`` when no row is found.  Same ``description``
              guard applies.
            - ``"none"`` — do not fetch; returns ``([], [])``.
            - ``"rowcount"`` — read ``cursor.rowcount`` instead of fetching a
              result set.  Returns ``([], [(rowcount,)])`` so callers read
              ``rows[0][0]``.  Commits (when requested) AFTER reading rowcount.
              Use for statements that produce no result set but report an affected-
              row count via the ODBC ``SQLRowCount`` API (e.g. ``COPY INTO`` on
              mssql-python ≥ 1.9.0 — ``cursor.description`` is ``None`` and
              ``fetchall()`` raises ``ProgrammingError: Invalid cursor state``,
              but ``cursor.rowcount`` correctly equals the rows loaded).

        autocommit: When ``True``, open the connection with ODBC-level autocommit
            so the driver does not wrap the statement in an explicit transaction.
            Use this for DDL that SQL Server disallows inside transactions
            (e.g. ``ALTER DATABASE``).  When ``True``, ``commit`` is ignored.

    Returns:
        A ``(columns, rows)`` tuple where *columns* is a list of column-name
        strings and *rows* is a list of row tuples.

    Raises:
        PermissionDeniedError: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
        NotFoundError: If the driver reports a missing-object error (SQL Server
            error 208, invalid object name / base table or view not found).
        Exception: Any other driver error is propagated unchanged.
    """
    # Whether execute-phase transient errors are safe to retry.
    # Default: only non-DML fetches (fetch != "none") can safely be retried
    # after execute has started — DML could have already been applied server-side.
    # When sql_retry_executes is enabled (opt-in), fetch="none" statements are
    # also retried; callers must ensure idempotency of those statements.
    execute_retry_allowed = fetch != "none" or _resolve_sql_retry_executes()

    # Maximum number of execute-phase attempts: initial + len(_EXECUTE_RETRY_DELAYS) retries.
    # Both this cap AND the wall-clock deadline must be satisfied for a retry to fire.
    _max_execute_attempts = len(_EXECUTE_RETRY_DELAYS) + 1  # = 4

    def _execute_and_fetch(c: _Connection) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Run the statement on *c*, fetch results, and return (cols, rows).

        This helper covers ONLY the execute + fetch phase.  It deliberately
        does NOT call c.commit() — the caller is responsible for committing
        after this function returns successfully.  This split ensures that
        the retry loop never re-executes a statement that may already have
        been committed server-side.
        """
        cur = c.cursor()
        if params:
            cur.execute(statement, params)
        else:
            cur.execute(statement)

        if fetch == "none":
            # No result set to read.  Return without committing — commit is
            # the caller's responsibility (or suppressed for fetch="none").
            return [], []

        if fetch == "rowcount":
            # Read cursor.rowcount immediately — COPY INTO (and similar
            # statements) produce no result set on mssql-python ≥ 1.9.0:
            # cursor.description is None and fetchall() raises
            # ProgrammingError: Invalid cursor state.
            # cursor.rowcount is correctly set to the number of affected rows.
            return [], [(cur.rowcount,)]

        # Defensive guard: if the driver returns no result set (description is
        # None) for an "all" or "one" fetch, return empty results instead of
        # calling fetchall()/fetchone() which would raise
        # "ProgrammingError: Invalid cursor state".
        if cur.description is None:
            return [], []

        # Fetch the result set BEFORE the caller commits.  Committing first
        # invalidates the cursor's prepared statement on mssql-python, which
        # causes "Associated statement is not prepared" on the subsequent
        # fetchall/fetchone call.
        cols = [col[0] for col in cur.description]

        if fetch == "one":
            row = cur.fetchone()
            result: tuple[list[str], list[tuple[Any, ...]]] = (
                cols,
                ([row] if row is not None else []),
            )
        else:
            fetched_rows: list[tuple[Any, ...]] = cur.fetchall()
            result = cols, fetched_rows

        return result

    conn, _attempt, _max_attempts, _ = _with_connect_retry(target, mode, autocommit)
    # execute_attempt counts execute-phase attempts so far (for backoff indexing
    # and the attempt cap).  Starts at 1 for the first attempt.
    execute_attempt = 1
    # deadline is set on the first execute-phase transient failure.  The total
    # wall-clock budget for execute-phase retries matches the connect-phase
    # budget (sql_retry_deadline_s, default 120 s), resolved at call-time.
    execute_deadline: float | None = None

    while True:
        try:
            result = _execute_and_fetch(conn)
        except Exception as exc:
            # Mark tainted so close() physically closes instead of pooling.
            if isinstance(conn, _PooledConnection):
                conn.mark_discard()
            mapped = map_driver_error(exc)
            if mapped:
                # Auth/permission/not-found errors are deterministic —
                # raise immediately without retrying.
                conn.close()
                raise mapped from exc
            # Only retry execute-phase transient errors when the statement
            # is safe to re-run (fetch != "none").  Non-idempotent DML must
            # not be re-executed (D10 retry boundary).
            if not (execute_retry_allowed and is_transient_connection_error(exc)):
                conn.close()
                raise
            # Set the deadline on the first execute-phase transient failure.
            if execute_deadline is None:
                execute_deadline = time.monotonic() + _resolve_sql_retry_deadline_s()
            # Close the tainted connection before sleeping.  _PooledConnection
            # is idempotent so a later close() call is a no-op.
            conn.close()
            # Check both guards: attempt cap and wall-clock deadline.
            if execute_attempt >= _max_execute_attempts or time.monotonic() >= execute_deadline:
                raise
            # Sleep with exponential backoff before opening a fresh connection.
            # Backoff index is 0-based from the first failure (attempt 1 failed
            # → delay index 0, attempt 2 failed → delay index 1, etc.).
            delay_idx = min(execute_attempt - 1, len(_EXECUTE_RETRY_DELAYS) - 1)
            time.sleep(_EXECUTE_RETRY_DELAYS[delay_idx])
            execute_attempt += 1
            # Open a fresh connection for the next execute attempt.
            conn, _attempt, _max_attempts, _ = _with_connect_retry(target, mode, autocommit)
        else:
            # Execute + fetch succeeded.  Now commit (if requested) OUTSIDE the
            # retry loop.  If commit raises, the error propagates directly to
            # the caller — we do NOT retry here.  Retrying after a commit
            # failure would risk re-executing the statement and causing a
            # double-load (e.g. double COPY INTO).  The connection is marked
            # for discard on commit failure (unknown transaction state).
            try:
                if commit and not autocommit:
                    conn.commit()
            except Exception:
                if isinstance(conn, _PooledConnection):
                    conn.mark_discard()
                conn.close()
                raise
            conn.close()
            return result


def run_statements(
    target: SqlTarget,
    statements: Sequence[str],
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
    autocommit: bool = False,
    commit_per_statement: bool = True,
) -> None:
    """Execute multiple DDL/DML *statements* on a **single** connection.

    Opens ONE connection, executes each statement in sequence, then closes the
    connection.  This avoids the N x TCP+TLS+TDS handshake overhead that arises
    when opening a new connection per statement (relevant for
    ``_drop_schema_objects`` with many tables/views).

    Retry boundary
    --------------
    Transient errors during the **connect** phase (``open_connection``) are
    retried automatically (for up to ``_CONNECT_RETRY_TIMEOUT_S`` seconds, ~120 s by default).
    Transient errors that occur *after* a statement has begun executing are
    **not** retried — the connection state is unknown and re-executing DDL/DML
    could cause duplicates or inconsistency.

    Atomicity
    ---------
    When ``commit_per_statement=True`` (default) each statement is committed
    individually.  If an error occurs midway, previously committed statements
    are permanent and the schema is left in a partial state.

    When ``commit_per_statement=False`` all statements are executed inside a
    **single transaction** that is committed only after the last statement
    succeeds.  Any failure rolls back the entire batch, providing all-or-nothing
    semantics.  Use this when the full sequence must be atomic (e.g. cascade
    drop that includes the containing schema).  Has no effect when
    ``autocommit=True``.

    Args:
        target: The :class:`SqlTarget` identifying the warehouse.
        statements: Sequence of SQL strings to execute in order.
        mode: The credential mode for Entra authentication.
        autocommit: When ``True``, open the connection with ODBC-level autocommit
            so the driver does not wrap statements in explicit transactions.
            Use this for DDL that SQL Server disallows inside transactions
            (e.g. ``ALTER DATABASE``).  When ``True``, ``conn.commit()`` is
            not called (the driver commits each statement automatically) and
            ``commit_per_statement`` has no effect.
        commit_per_statement: When ``True`` (default) commit after each
            statement (best-effort, partial-failure mode).  When ``False``
            defer the commit until all statements have executed, giving
            all-or-nothing transaction semantics.  Ignored when
            ``autocommit=True``.

    Raises:
        PermissionDeniedError: If the driver reports a permission error on any statement.
        AuthError: If the driver reports an authentication failure.
        Exception: Any other driver error is propagated unchanged.
    """
    conn, _attempt, _max, _ = _with_connect_retry(target, mode, autocommit)

    cursor = conn.cursor()
    try:
        for stmt in statements:
            try:
                cursor.execute(stmt)
                if not autocommit and commit_per_statement:
                    conn.commit()
            except Exception as exc:
                if isinstance(conn, _PooledConnection):
                    conn.mark_discard()
                mapped = map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise
        # All statements executed — commit once if deferred.
        if not autocommit and not commit_per_statement:
            conn.commit()
    finally:
        conn.close()
