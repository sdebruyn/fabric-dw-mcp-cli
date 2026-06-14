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

Connection Pool
---------------
``open_connection`` returns a thin wrapper whose ``.close()`` method returns
the underlying connection to a per-key LIFO pool instead of physically closing
it.  The pool is keyed on ``(workspace_id, database, mode)`` and bounded by two
module-level constants:

``POOL_MAX_IDLE``       — maximum idle connections per key (default 4).
``POOL_MAX_IDLE_SECS``  — maximum idle age in seconds before eviction (default 300).

Disable pooling entirely by setting the environment variable
``FABRIC_SQL_POOL=0`` before process startup, or at runtime by setting
``os.environ["FABRIC_SQL_POOL"] = "0"`` and then calling :func:`reset_pool` to
drain existing connections.  When disabled every ``open_connection`` call opens a
fresh physical connection and ``.close()`` physically closes it.

Call :func:`reset_pool` on graceful shutdown to close all idle connections.  The
MCP server lifespan calls ``reset_pool`` in its ``finally`` block so pooled TDS
connections are drained on server shutdown.
"""

from __future__ import annotations

import contextlib
import functools
import importlib
import os
import re
import threading
import time
import types
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import AuthError, NotFoundError, PermissionDeniedError

# ---------------------------------------------------------------------------
# Transient-retry configuration
# ---------------------------------------------------------------------------

# Maximum number of retry attempts for transient TDS connection drops.
# Set to 0 to disable retries entirely.  Unit tests can monkeypatch this.
SQL_TRANSIENT_MAX_RETRIES: int = 3

# Backoff delays (seconds) between retry attempts.  Index 0 = delay before
# attempt 2, index 1 = delay before attempt 3, etc.  Extend if needed.
_TRANSIENT_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)

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
_AUTH_FAILED_FRAGMENTS = ("authentication failed",)

# SQL Server native error numbers that indicate permission denied.
# 229: SELECT permission denied; 230: INSERT; 297: execute permission denied.
_PERMISSION_DENIED_ERROR_NUMBERS = frozenset({229, 230, 297})

# SQL Server native error number for authentication failure (login failed).
_AUTH_FAILED_ERROR_NUMBERS = frozenset({18456})

# SQL Server native error numbers that indicate a missing object.
# 208: Invalid object name (table/view/proc not found).
_NOT_FOUND_ERROR_NUMBERS = frozenset({208})

# Message fragments that indicate a missing database object.
_NOT_FOUND_FRAGMENTS = (
    "invalid object name",
    "base table or view not found",
)

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

# Mapping from CredentialMode to the ActiveDirectory auth type suffix.
_MODE_TO_AD_AUTH: dict[CredentialMode, str] = {
    CredentialMode.DEFAULT: "ActiveDirectoryDefault",
    CredentialMode.SERVICE_PRINCIPAL: "ActiveDirectoryServicePrincipal",
    CredentialMode.INTERACTIVE: "ActiveDirectoryInteractive",
}

# Regex to extract the native SQL Server error number from a DDBC error string.
# The ODBC driver embeds it as e.g. "[SQL Server]Login failed ... Error: 18456"
# or "[SQL Server] ... (229)".
_NATIVE_ERROR_RE = re.compile(r"\b(?:Error:\s*|error\s+)(\d+)\b|\((\d+)\)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Minimal DB-API 2.0 Protocols (for type checking only)
# ---------------------------------------------------------------------------


class _Cursor(Protocol):
    description: list[tuple[str, Any]] | None
    rowcount: int

    def execute(self, sql: str, params: Sequence[object] | None = None) -> None: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]: ...

    def nextset(self) -> bool | None: ...

    def close(self) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

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
# Env var FABRIC_SQL_POOL=0 disables pooling entirely at any time.
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

    Reads ``FABRIC_SQL_POOL`` from the environment at call-time so tests can
    toggle it without reimporting the module.  Any value other than ``"0"``
    keeps pooling enabled (the default).
    """
    return os.environ.get("FABRIC_SQL_POOL", "1") != "0"


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
    """Return *conn* to the pool if there is room, else physically close it."""
    do_close = False
    with _pool_lock:
        slots = _pool.setdefault(key, [])
        if len(slots) >= POOL_MAX_IDLE:
            do_close = True
        else:
            slots.append((conn, _pool_time()))
    if do_close:
        with contextlib.suppress(Exception):
            conn.close()


# ---------------------------------------------------------------------------
# Pooled connection wrapper
# ---------------------------------------------------------------------------


class _PooledConnection:
    """Thin wrapper that intercepts ``.close()`` to return the connection to the pool.

    When ``.close()`` is called:
    - If ``_discard`` is ``True`` (set after a failed query), the underlying
      connection is physically closed and NOT returned to the pool.
    - If pooling is disabled (``FABRIC_SQL_POOL=0``), the underlying connection
      is physically closed.
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

    # ------------------------------------------------------------------ #
    # Forward _Connection protocol methods to the underlying object.      #
    # ------------------------------------------------------------------ #

    def cursor(self) -> Any:  # noqa: ANN401
        return self._underlying.cursor()

    def commit(self) -> None:
        self._underlying.commit()

    def close(self) -> None:
        """Return to pool or physically close, depending on state and config."""
        if self._discard or not _pool_enabled():
            self._underlying.close()
        else:
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
) -> str:
    """Augment the API-provided connection string with auth, encryption and database settings.

    The operation is idempotent: calling it twice with the same target and mode
    returns the identical string.

    Args:
        target: The :class:`SqlTarget` whose ``connection_string`` and ``database``
            are used as inputs.
        mode: The credential mode, used to select the ActiveDirectory auth variant.

    Returns:
        The augmented ODBC connection string, ready to pass to the driver.
    """
    # The Fabric API returns the warehouse FQDN as a bare hostname with no
    # "Server=" prefix.  The mssql_python driver requires a proper ODBC key=value
    # format, so prepend "Server=" when the raw string has no Server key.
    raw = target.connection_string
    if not _has_key(raw, "Server"):
        raw = f"Server={raw}"
    cs = _set_key(raw, "Authentication", _MODE_TO_AD_AUTH[mode])
    cs = _set_key(cs, "Encrypt", "yes")
    cs = _set_key(cs, "TrustServerCertificate", "no")
    return _set_key(cs, "Database", target.database)


def open_connection(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> _Connection:
    """Return a connection to the target warehouse, reusing a pooled one when available.

    The returned object satisfies the :class:`_Connection` protocol.  Its
    ``.close()`` method returns the connection to the pool (when pooling is
    enabled and the connection is healthy) rather than physically closing the
    socket.  Callers do **not** need to change - the ``contextlib.closing``
    pattern works unchanged.

    When pooling is disabled (``FABRIC_SQL_POOL=0``) every call opens a fresh
    physical connection and ``.close()`` physically closes it.

    This function is intentionally synchronous.  Callers that need to keep the
    event loop free should wrap the entire sync block in ``asyncio.to_thread``.

    Args:
        target: The :class:`SqlTarget` identifying the warehouse.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`_PooledConnection` wrapping a DB-API 2.0 connection from
        the ``mssql_python`` driver.
    """
    key = _make_pool_key(target, mode)

    if _pool_enabled():
        cached = _pool_checkout(key)
        if cached is not None:
            return _PooledConnection(cached, key)

    cs = build_connection_string(target, mode=mode)
    raw_conn: Any = _get_mssql().connect(cs)
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


# ---------------------------------------------------------------------------
# TDS runner helpers
# ---------------------------------------------------------------------------


def run_query(  # noqa: PLR0912,PLR0913
    target: SqlTarget,
    statement: str,
    *,
    params: Sequence[object] | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
    commit: bool = False,
    fetch: Literal["all", "none", "one"] = "all",
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Open a connection, execute *statement*, fetch rows, close, and map errors.

    This is the single TDS execute-and-fetch helper that replaces ~20 copies of
    the open-connection / cursor / execute / map-error pattern across services.

    The ``mssql_python`` driver uses ``pyformat`` paramstyle (``%(name)s``) but
    also accepts qmark (``?``) style.  *params* is unpacked as variadic positional
    arguments because the driver's ``execute`` signature is
    ``execute(sql, *parameters)``.

    Args:
        target: The :class:`SqlTarget` identifying the warehouse.
        statement: The SQL statement to execute.
        params: Optional sequence of parameter values to bind.  Use ``?``
            placeholders in *statement*.  Identifiers (schema/table names) MUST
            be bracket-quoted via :func:`~fabric_dw.identifiers.quote_identifier`
            and validated via :func:`~fabric_dw.identifiers.validate_identifier`
            - they cannot be bound as parameters.
        mode: The credential mode for Entra authentication.
        commit: When ``True``, call ``conn.commit()`` after execute (for DDL/DML).
        fetch: One of:
            - ``"all"`` (default) - call ``fetchall()`` and return
              ``(columns, rows)``; columns are derived from ``cursor.description``.
            - ``"one"`` - call ``fetchone()`` (not yet used but available for
              future point-lookup helpers); returns ``(columns, [row])`` or
              ``([], [])`` when no row.
            - ``"none"`` - do not fetch; returns ``([], [])``.

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
    # Bounded transient retry: retry ONLY on connection-level TDS drops (not on
    # real SQL/auth errors).  SQL_TRANSIENT_MAX_RETRIES=0 disables the loop.
    max_attempts = 1 + max(0, SQL_TRANSIENT_MAX_RETRIES)
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        if attempt > 0:
            # Back off before the next attempt.  _TRANSIENT_RETRY_DELAYS is
            # indexed from 0 (= delay before attempt 2); clamp to last value.
            delay = _TRANSIENT_RETRY_DELAYS[min(attempt - 1, len(_TRANSIENT_RETRY_DELAYS) - 1)]
            time.sleep(delay)

        try:
            conn = open_connection(target, mode=mode)
        except Exception as exc:
            # open_connection itself may raise on connect failure (TCP timeout,
            # TLS handshake error, etc.).  Retry on transient errors, just as
            # run_statements does — the dead connection is never put into the
            # pool when open_connection raises, so there is nothing to discard.
            if is_transient_connection_error(exc) and attempt < max_attempts - 1:
                last_exc = exc
                continue
            raise

        try:
            cursor = conn.cursor()
            try:
                if params:
                    cursor.execute(statement, params)
                else:
                    cursor.execute(statement)
                if commit:
                    conn.commit()

                if fetch == "none":
                    return [], []

                cols = [c[0] for c in (cursor.description or [])]

                if fetch == "one":
                    row = cursor.fetchone()  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
                    return cols, ([row] if row is not None else [])

                # Default: fetch all rows.
                rows: list[tuple[Any, ...]] = cursor.fetchall()
            except Exception as exc:
                # Mark tainted so close() physically closes instead of pooling.
                # setattr is safe for both _PooledConnection and mock objects.
                if isinstance(conn, _PooledConnection):
                    conn._discard = True
                mapped = map_driver_error(exc)
                if mapped:
                    # Auth/permission errors are never transient — raise immediately.
                    raise mapped from exc
                # Retry only on recognised transient connection drops; re-raise
                # everything else (SQL syntax, deadlocks, programming errors …).
                if is_transient_connection_error(exc) and attempt < max_attempts - 1:
                    last_exc = exc
                    continue
                raise
            else:
                return cols, rows
        finally:
            conn.close()

    # Unreachable in normal flow but satisfies the type-checker and raises the
    # last transient error if somehow the loop exits without returning.
    if last_exc is not None:
        raise last_exc  # pragma: no cover
    return [], []  # pragma: no cover


def run_statements(
    target: SqlTarget,
    statements: Sequence[str],
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Execute multiple DDL/DML *statements* on a **single** connection.

    Opens ONE connection, executes each statement in sequence (committing after
    each), then closes the connection.  This avoids the N x TCP+TLS+TDS handshake
    overhead that arises when opening a new connection per statement (relevant
    for ``_drop_schema_objects`` with many tables/views).

    Args:
        target: The :class:`SqlTarget` identifying the warehouse.
        statements: Sequence of SQL strings to execute in order.
        mode: The credential mode for Entra authentication.

    Raises:
        PermissionDeniedError: If the driver reports a permission error on any statement.
        AuthError: If the driver reports an authentication failure.
        Exception: Any other driver error is propagated unchanged.
    """
    # Bounded transient retry: retry ONLY on connection-level TDS drops that
    # occur during the *connect* phase (before any statement executes).
    # Per-statement errors after a successful connect are NOT retried here
    # because the connection state is unknown once a statement has started.
    max_attempts = 1 + max(0, SQL_TRANSIENT_MAX_RETRIES)
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        if attempt > 0:
            delay = _TRANSIENT_RETRY_DELAYS[min(attempt - 1, len(_TRANSIENT_RETRY_DELAYS) - 1)]
            time.sleep(delay)

        try:
            conn = open_connection(target, mode=mode)
        except Exception as exc:
            # open_connection itself may raise on connect failure.
            if is_transient_connection_error(exc) and attempt < max_attempts - 1:
                last_exc = exc
                continue
            raise

        cursor = conn.cursor()
        try:
            for stmt in statements:
                try:
                    cursor.execute(stmt)
                    conn.commit()
                except Exception as exc:
                    if isinstance(conn, _PooledConnection):
                        conn._discard = True
                    mapped = map_driver_error(exc)
                    if mapped:
                        raise mapped from exc
                    raise
            # All statements succeeded — return (outer loop exits naturally).
            return
        finally:
            conn.close()

    if last_exc is not None:
        raise last_exc  # pragma: no cover
