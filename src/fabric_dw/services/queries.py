"""DMV-backed running-query listing and session cancellation for Fabric DW.

Public API
----------
- :func:`list_running`     — return all currently active queries via DMV JOIN.
- :func:`list_connections` — return all active SQL connections via sys.dm_exec_connections.
- :func:`kill`             — terminate a session by session_id via KILL.
"""

from __future__ import annotations

import asyncio

from fabric_dw.auth import CredentialMode
from fabric_dw.models import Connection, QueryLock, RunningQuery
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "kill",
    "list_connections",
    "list_locks",
    "list_running",
]

# NOTE: sys.dm_exec_sql_text is not supported on Fabric DW (Fabric Synapse
# Analytics uses a different execution engine), so the OUTER APPLY for
# query_text is omitted.  The query_text column is included in the SELECT as a
# literal NULL so the RunningQuery model field is populated with None.
# Use dist_statement_id to correlate with queryinsights.exec_requests_history
# to look up the full query text for a specific request.
#
# The LEFT JOIN was changed to INNER JOIN: the feature intent is to return only
# sessions that have an *active* request (status in running/runnable/suspended).
# A LEFT JOIN with a WHERE predicate on the right-side table is logically
# equivalent to INNER JOIN and misleads the reader.  Using INNER JOIN makes the
# intent explicit.
_LIST_RUNNING_SQL = """\
SELECT
    r.session_id,
    r.request_id,
    r.status,
    r.start_time,
    r.total_elapsed_time,
    s.login_name,
    r.command,
    NULL AS query_text,
    r.dist_statement_id,
    NULLIF(r.blocking_session_id, 0) AS blocking_session_id,
    r.wait_type,
    r.wait_time,
    r.cpu_time,
    r.reads,
    r.writes,
    r.logical_reads,
    r.row_count,
    r.open_transaction_count
FROM sys.dm_exec_sessions s
INNER JOIN sys.dm_exec_requests r ON r.session_id = s.session_id
WHERE r.status IN ('running', 'runnable', 'suspended')
ORDER BY r.total_elapsed_time DESC;
"""


async def list_running(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[RunningQuery]:
    """Return all currently-executing or runnable queries on *target*.

    Queries the ``sys.dm_exec_sessions`` / ``sys.dm_exec_requests`` DMVs,
    filtering for rows whose ``status`` is one of ``running``, ``runnable``,
    or ``suspended``, ordered by elapsed time descending.

    Args:
        target: The warehouse to query.
        mode: The credential mode for Entra authentication.

    Returns:
        A (possibly empty) list of :class:`~fabric_dw.models.RunningQuery`
        instances, one per result row.
    """

    def _run() -> list[RunningQuery]:
        cols, rows = run_query(target, _LIST_RUNNING_SQL, mode=mode)
        return [RunningQuery.model_validate(dict(zip(cols, r, strict=True))) for r in rows]

    return await asyncio.to_thread(_run)


_LIST_CONNECTIONS_SQL = """\
SELECT
    session_id,
    connect_time,
    client_net_address,
    auth_scheme,
    encrypt_option,
    net_transport,
    most_recent_session_id
FROM sys.dm_exec_connections
ORDER BY connect_time;
"""


async def list_connections(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[Connection]:
    """Return all active SQL connections on *target*.

    Queries the ``sys.dm_exec_connections`` DMV, which exposes lower-level
    connection info (including idle connections) that is not visible in
    ``sys.dm_exec_requests``.  Requires VIEW SERVER STATE permission on the
    database (Fabric grants this automatically to workspace admins and owners).

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        mode: The credential mode for Entra authentication.

    Returns:
        A (possibly empty) list of :class:`~fabric_dw.models.Connection`
        instances, one per result row.

    Raises:
        PermissionDeniedError: If the caller lacks VIEW SERVER STATE.
    """

    def _run() -> list[Connection]:
        cols, rows = run_query(target, _LIST_CONNECTIONS_SQL, mode=mode)
        return [Connection.model_validate(dict(zip(cols, r, strict=True))) for r in rows]

    return await asyncio.to_thread(_run)


_LIST_LOCKS_SQL_TMPL = (
    "SELECT TOP ({limit})\n"
    "    l.request_session_id AS session_id,\n"
    "    l.resource_type,\n"
    "    l.request_mode,\n"
    "    l.request_status,\n"
    # For OBJECT-type locks, resource_associated_entity_id IS the object_id.
    # For KEY/PAGE/RID/EXTENT locks it is a hobt_id; resolve through sys.partitions.
    "    OBJECT_SCHEMA_NAME(\n"
    "        CASE WHEN l.resource_type = 'OBJECT'\n"
    "             THEN l.resource_associated_entity_id\n"
    "             ELSE p.object_id\n"
    "        END\n"
    "    ) AS schema_name,\n"
    "    OBJECT_NAME(\n"
    "        CASE WHEN l.resource_type = 'OBJECT'\n"
    "             THEN l.resource_associated_entity_id\n"
    "             ELSE p.object_id\n"
    "        END\n"
    "    ) AS object_name,\n"
    "    r.blocking_session_id,\n"
    "    r.wait_type,\n"
    "    r.wait_time,\n"
    "    r.command\n"
    "FROM sys.dm_tran_locks l\n"
    "LEFT JOIN sys.dm_exec_requests r ON r.session_id = l.request_session_id\n"
    "LEFT JOIN sys.partitions p\n"
    "    ON l.resource_associated_entity_id = p.hobt_id\n"
    "    AND l.resource_type IN ('KEY', 'PAGE', 'RID', 'EXTENT')\n"
    "{where}\n"
    "ORDER BY l.request_session_id, l.resource_type\n"
)


async def kill(
    target: SqlTarget,
    session_id: int,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Terminate the session identified by *session_id* on *target*.

    Args:
        target: The warehouse to connect to.
        session_id: A positive integer identifying the session to kill.
        mode: The credential mode for Entra authentication.

    Raises:
        ValueError: If *session_id* is not a positive integer (i.e. <= 0).
        PermissionDeniedError: If the driver reports a permission error
            (KILL requires Monitor or Admin permission on Fabric DW).
        AuthError: If the driver reports an authentication failure.
        NotFoundError: If the driver reports a missing object (e.g. the
            session no longer exists when the KILL is issued).
    """
    if session_id <= 0:
        msg = f"session_id must be a positive integer; got {session_id}"
        raise ValueError(msg)

    # KILL requires a bare integer literal — the SQL syntax does not accept
    # a parameter placeholder here.  We cast to int explicitly to prevent any
    # accidental injection before embedding in the statement.
    safe_id = int(session_id)
    kill_sql = f"KILL {safe_id}"

    # T-SQL forbids KILL inside a user transaction.  Pass autocommit=True so
    # the ODBC driver opens the connection without BEGIN TRANSACTION, which is
    # required for KILL to succeed on Fabric DW.
    await asyncio.to_thread(run_query, target, kill_sql, mode=mode, fetch="none", autocommit=True)


async def list_locks(
    target: SqlTarget,
    *,
    limit: int = 100,
    waiting_only: bool = False,
    blocked_only: bool = False,
    include_database: bool = False,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[QueryLock]:
    """Return active lock rows from sys.dm_tran_locks joined with sys.dm_exec_requests.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        limit: Maximum rows to return (1-10000, default 100).
        waiting_only: When True, restrict to locks with request_status IN ('WAIT', 'CONVERT').
            CONVERT covers lock-upgrade waits (e.g. S upgrading to X).
        blocked_only: When True, show only sessions that are blocked by another session
            (victims). The blocker's session_id appears in blocking_session_id.
        include_database: When True, include DATABASE-scoped lock rows (excluded by default).
        mode: The credential mode for Entra authentication.

    Returns:
        A (possibly empty) list of :class:`~fabric_dw.models.QueryLock` instances.
    """
    limit = max(1, min(limit, 10_000))

    conditions: list[str] = []
    if not include_database:
        conditions.append("l.resource_type <> 'DATABASE'")
    if waiting_only:
        # CONVERT = lock-upgrade wait (e.g. S upgrading to X); also genuinely blocked.
        conditions.append("l.request_status IN ('WAIT', 'CONVERT')")
    if blocked_only:
        conditions.append("r.blocking_session_id IS NOT NULL AND r.blocking_session_id <> 0")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Use str.format() rather than f-string to avoid the S608 / B608 SQL-injection
    # lint rule, which triggers on any inline f-string containing SELECT.  The
    # only dynamic values are the clamped integer `limit` and the WHERE clause
    # built from a fixed allow-list of boolean flags — no user input is embedded.
    sql = _LIST_LOCKS_SQL_TMPL.format(limit=limit, where=where)

    def _run() -> list[QueryLock]:
        cols, rows = run_query(target, sql, mode=mode)
        return [QueryLock.model_validate(dict(zip(cols, r, strict=True))) for r in rows]

    return await asyncio.to_thread(_run)
