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

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import FabricCliError, FabricError
from fabric_dw.sql_errors import (
    _clean_driver_error_message,  # noqa: F401 (test shim: _sql_module._clean_driver_error_message)
    _is_connect_retryable,
    _wrap_unmapped_driver_error,
    is_auth_failed_message,
    is_snapshot_not_ready_error,
    is_transient_connection_error,
    map_driver_error,
)
from fabric_dw.sql_pool import (
    _CONNECT_RETRY_TIMEOUT_S,  # noqa: F401 (backwards-compat alias; read by integration smoke)
    _FALSY_STRINGS,  # noqa: F401 (backwards-compat alias)
    _MIN_SQL_RETRY_DEADLINE_S,  # noqa: F401 (test shim: _sql_module._MIN_SQL_RETRY_DEADLINE_S)
    _SQL_RETRY_DEADLINE_S_DEFAULT,  # noqa: F401 (test shim: _sql_module._SQL_RETRY_DEADLINE_S_DEFAULT)
    POOL_MAX_IDLE,
    # POOL_MAX_IDLE is read from sql_pool.py's globals by _pool_checkout/_pool_checkin.
    # Tests that SET POOL_MAX_IDLE to change pool capacity MUST target
    # fabric_dw.sql_pool.POOL_MAX_IDLE — setting fabric_dw.sql.POOL_MAX_IDLE is a
    # silent no-op for those callers.
    POOL_MAX_IDLE_SECS,
    SQL_COPT_SS_ACCESS_TOKEN,
    SQL_LOGIN_TIMEOUT_S,
    SQL_QUERY_TIMEOUT_S,
    _driver,  # noqa: F401 (test shim: _sql_module._driver)
    _get_mssql,  # noqa: F401 (test shim)
    _is_alive,  # noqa: F401 (backwards-compat alias)
    _load_sql_config,  # noqa: F401 (test shim)
    _make_pool_key,  # noqa: F401 (backwards-compat alias)
    # _mssql is the import-time value (None). Patching fabric_dw.sql._mssql is a
    # silent no-op because _get_mssql() reads _mssql from sql_pool's own globals,
    # not from sql.py's namespace.  Tests MUST patch fabric_dw.sql_pool._mssql.
    _mssql,  # noqa: F401 (import-time snapshot; do NOT patch via fabric_dw.sql)
    _pool,  # noqa: F401 (test access: from fabric_dw.sql import _pool; same object as sql_pool._pool)
    _pool_checkin,  # noqa: F401 (backwards-compat alias)
    _pool_checkout,  # noqa: F401 (backwards-compat alias)
    # _pool_enabled is called by bare name via _PooledConnection.close() and
    # open_connection — both live in sql_pool.py.  Patching fabric_dw.sql._pool_enabled
    # is a silent no-op for those callers.  Tests that call _pool_enabled() directly
    # via _sql_module._pool_enabled() call the function (which executes in sql_pool
    # context) and get the correct result — the function reference is the same object.
    _pool_enabled,  # noqa: F401 (test access via _sql_module._pool_enabled())
    _pool_lock,  # noqa: F401 (test access: from fabric_dw.sql import _pool_lock; same object)
    # _pool_time is called by bare name inside _pool_checkout/_pool_checkin, both in
    # sql_pool.py.  Tests that patch _pool_time MUST target fabric_dw.sql_pool._pool_time
    # — patching fabric_dw.sql._pool_time is a silent no-op for those callers.
    _pool_time,  # noqa: F401 (import-time snapshot; do NOT patch via fabric_dw.sql)
    _PooledConnection,
    _resolve_sql_retry_deadline_s,
    _resolve_sql_retry_executes,
    _sql_config_cache_clear,  # noqa: F401 (test hook: _sql_module._sql_config_cache_clear())
    _validate_sql_retry_deadline_s,  # noqa: F401 (test shim: _sql_module._validate_sql_retry_deadline_s)
    build_connection_string,
    open_connection,
    reset_pool,
)

# Re-export shims rationale (two groups):
#
# sql_errors shims: bind function names in this module's namespace so that
# `from fabric_dw.sql import map_driver_error` keeps working, so that
# `patch("fabric_dw.sql.<function>")` patches the binding used by the runner
# functions below, and so that test code that accesses private helpers via
# `_sql_module._<name>` can still find them.  The private error-classification
# CONSTANTS are intentionally not re-exported: they are not referenced by bare
# name here, and patching them via fabric_dw.sql.* would be a silent no-op.
#
# sql_pool shims: all names that were defined here but now live in sql_pool.py.
# Importing them preserves the public surface (`from fabric_dw.sql import
# build_connection_string`), bare-name calls inside _with_connect_retry /
# run_query / run_statements, and test accesses such as
# `_sql_module._SQL_RETRY_DEADLINE_S_DEFAULT`.
#
# IMPORTANT — silent no-ops (do NOT patch via fabric_dw.sql):
#   _mssql        — tests MUST patch fabric_dw.sql_pool._mssql
#   _pool_time    — tests MUST patch fabric_dw.sql_pool._pool_time
#   POOL_MAX_IDLE — tests that SET this MUST target fabric_dw.sql_pool.POOL_MAX_IDLE
#
# _sql_config_cache and _sql_config_lock are intentionally NOT re-exported:
# they are mutable state owned by sql_pool; tests that must inject a cached
# config value target fabric_dw.sql_pool._sql_config_cache directly.

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

# Backoff delays for the connect-phase retry loop.  The delay before attempt
# N+1 is _CONNECT_RETRY_DELAYS[min(N, len-1)], so the sequence is:
#   attempt 1 → fails → sleep 5 s → attempt 2
#   attempt 2 → fails → sleep 10 s → attempt 3
#   attempt 3 → fails → sleep 15 s → attempt 4
#   attempt 4+ → fails → sleep 15 s → attempt 5 … (capped at 15 s)
_CONNECT_RETRY_DELAYS: tuple[float, ...] = (5.0, 10.0, 15.0)

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
# TDS runner helpers
# ---------------------------------------------------------------------------


def _wrap_connect_retry_exhausted(exc: BaseException) -> BaseException:
    """Return a clean, renderable exception for a connect-retry budget exhaustion.

    ``exc`` is the last retryable exception seen by :func:`_with_connect_retry`
    when the wall-clock deadline expires.  It is either:

    - Already a :class:`~fabric_dw.exceptions.FabricCliError` (e.g. an
      ``AuthError`` whose message happened to match a retryable fragment) —
      returned unchanged, it is already clean.
    - A raw driver exception (e.g. ``mssql_python.exceptions.OperationalError``
      for a TCP connect/login timeout, DDBC error code 0x102) re-raised bare by
      :func:`~fabric_dw.sql_pool._translate_connect_error` Step 3 *specifically*
      so this loop could retry it — wrapped here in a
      :class:`~fabric_dw.exceptions.FabricError` so a raw driver traceback never
      reaches the CLI/MCP boundary once the retry window is spent (#972).

    ``__cause__`` is set explicitly on the wrap branch (chains to the driver
    exception) and left untouched on the pass-through branch (preserves the
    FabricCliError's own original cause, if any).  Callers must raise the
    return value **without** ``from exc`` — since the pass-through branch
    returns ``exc`` itself, ``raise ... from exc`` would set
    ``exc.__cause__ = exc`` (a self-reference) and discard the real cause.
    """
    if isinstance(exc, FabricCliError):
        return exc
    wrapped = FabricError(f"SQL connection failed: {exc}; please retry")
    wrapped.__cause__ = exc
    return wrapped


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
        A clean, renderable exception (see :func:`_wrap_connect_retry_exhausted`)
        wrapping the last retryable exception when the wall-clock deadline
        passes — never a raw driver exception (#972).
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
                # Budget exhausted — surface the last retryable error, wrapped
                # so a raw driver exception never escapes the connect path (#972).
                # No `from exc` here (deliberately, not an oversight):
                # _wrap_connect_retry_exhausted() already sets __cause__ itself for
                # the wrap branch, and the pass-through branch (exc is already a
                # FabricCliError) must keep its ORIGINAL __cause__ unchanged --
                # `raise ... from exc` unconditionally overwrites __cause__ at the
                # raise site, which for the pass-through branch (same object) would
                # self-reference (`exc.__cause__ = exc`) and clobber the real cause.
                raise _wrap_connect_retry_exhausted(exc)  # noqa: B904
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


def _log_sql_execute(sql: str, *, param_count: int = 0) -> None:
    """Emit a DEBUG record for an SQL statement about to be executed.

    Guards the log emission behind a single :meth:`logging.Logger.isEnabledFor`
    check so there is zero overhead when the ``fabric_dw.sql`` logger is not at
    DEBUG level.  Centralises the log call shared by :func:`run_query` and
    :func:`run_statements` to prevent drift.

    The SQL is logged verbatim at DEBUG level - no redaction is applied.
    Bound parameter VALUES are never logged; only the count appears in the
    log record.  Because verbose/DEBUG output may contain literal SQL
    (including embedded credentials such as SAS tokens or COPY INTO secrets),
    treat ``-v`` log output as sensitive and do not share it.

    Args:
        sql: The raw SQL statement.
        param_count: Number of bound parameters being passed (values are never
            logged; only the count appears in the log record).
    """
    if _log.isEnabledFor(logging.DEBUG):
        _log.debug("sql execute", extra={"sql": sql, "param_count": param_count})


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
        FabricServerError: If the driver reports an unmapped SQL error (e.g.
            "Invalid column name") that carries a ``ddbc_error`` attribute.
            The message is cleaned of driver-noise prefixes so the user sees
            the SQL Server-level message directly.
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
        _debug = _log.isEnabledFor(logging.DEBUG)
        if _debug:
            _log_sql_execute(statement, param_count=len(params) if params else 0)
        _t0 = time.monotonic() if _debug else 0.0
        if params:
            cur.execute(statement, params)
        else:
            cur.execute(statement)
        if _debug:
            _log.debug(
                "sql execute -> done",
                extra={
                    "elapsed_ms": (time.monotonic() - _t0) * 1000,
                    "rowcount": cur.rowcount,
                },
            )

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
            raw_row = cur.fetchone()
            # Normalise to a real tuple so callers' list[tuple[...]] annotations
            # are honest regardless of which driver Row type is returned.
            result: tuple[list[str], list[tuple[Any, ...]]] = (
                cols,
                ([tuple(raw_row)] if raw_row is not None else []),
            )
        else:
            # mssql_python returns Row objects that are sequence-compatible but
            # are not tuple subclasses.  Normalise here once so every caller
            # receives real tuples and dict(zip(cols, row)) / tuple indexing
            # work correctly without per-callsite workarounds.
            fetched_rows: list[tuple[Any, ...]] = [tuple(r) for r in cur.fetchall()]
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
                # Wrap unmapped driver SQL errors (e.g. "Invalid column name")
                # as FabricServerError so the CLI catches them cleanly.
                # Internal cursor-state errors and network errors carry no
                # ddbc_error and pass through unwrapped.
                wrapped = _wrap_unmapped_driver_error(exc)
                if wrapped:
                    raise wrapped from exc
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


def run_statements(  # noqa: PLR0913
    target: SqlTarget,
    statements: Sequence[str],
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
    autocommit: bool = False,
    commit_per_statement: bool = True,
    fetch_last_rowcount: bool = False,
) -> int | None:
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
        fetch_last_rowcount: When ``True``, read ``cursor.rowcount`` after each
            statement and return the value recorded for the LAST statement
            (e.g. the rows loaded by a trailing ``COPY INTO``).  The rowcount is
            read immediately after ``execute`` and before any deferred commit,
            matching the ``fetch="rowcount"`` behaviour of :func:`run_query`.
            When ``False`` (default) the function returns ``None``.

    Returns:
        The ``cursor.rowcount`` of the last executed statement when
        *fetch_last_rowcount* is ``True`` (``None`` if *statements* is empty),
        otherwise ``None``.

    Raises:
        PermissionDeniedError: If the driver reports a permission error on any statement.
        AuthError: If the driver reports an authentication failure.
        FabricServerError: If the driver reports an unmapped SQL error that
            carries a ``ddbc_error`` attribute (e.g. syntax errors, invalid
            column names).  The message is cleaned of driver-noise prefixes.
        Exception: Any other driver error is propagated unchanged.
    """
    conn, _attempt, _max, _ = _with_connect_retry(target, mode, autocommit)

    cursor = conn.cursor()
    last_rowcount: int | None = None
    try:
        for stmt in statements:
            try:
                _log_sql_execute(stmt)
                cursor.execute(stmt)
                if fetch_last_rowcount:
                    # Read rowcount BEFORE any deferred commit — committing first
                    # can invalidate the cursor state on mssql-python.  Mirrors
                    # run_query(fetch="rowcount").
                    last_rowcount = cursor.rowcount
                if not autocommit and commit_per_statement:
                    conn.commit()
            except Exception as exc:
                if isinstance(conn, _PooledConnection):
                    conn.mark_discard()
                mapped = map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                # Wrap unmapped driver SQL errors as FabricServerError so CLI
                # callers see a clean message rather than a raw traceback.
                wrapped = _wrap_unmapped_driver_error(exc)
                if wrapped:
                    raise wrapped from exc
                raise
        # All statements executed — commit once if deferred.
        if not autocommit and not commit_per_statement:
            conn.commit()
    finally:
        conn.close()
    return last_rowcount
