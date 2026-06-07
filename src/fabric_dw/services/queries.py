"""DMV-backed running-query listing and session cancellation for Fabric DW.

Public API
----------
- :func:`list_running` — return all currently active queries via DMV JOIN.
- :func:`kill`         — terminate a session by session_id via KILL.
"""

from __future__ import annotations

from fabric_dw.exceptions import AuthError, PermissionDenied
from fabric_dw.models import RunningQuery
from fabric_dw.sql_client import FabricSqlClient, SqlTarget

__all__ = [
    "kill",
    "list_running",
]

# NOTE: sys.dm_exec_sql_text is not supported on Fabric DW (Fabric Synapse
# Analytics uses a different execution engine), so the OUTER APPLY for
# query_text is omitted.  The query_text column is included in the SELECT as a
# literal NULL so the RunningQuery model field is populated with None.
_LIST_RUNNING_SQL = """\
SELECT
    r.session_id,
    r.request_id,
    r.status,
    r.start_time,
    r.total_elapsed_time,
    s.login_name,
    r.command,
    NULL AS query_text
FROM sys.dm_exec_sessions s
LEFT JOIN sys.dm_exec_requests r ON r.session_id = s.session_id
WHERE r.status IN ('running', 'runnable', 'suspended')
ORDER BY r.total_elapsed_time DESC;
"""


def _build_kill_sql(session_id: int) -> str:
    """Return a safe KILL statement for the given session id.

    The ``session_id`` is cast to ``int`` explicitly so no arbitrary string
    can be injected through this path.
    """
    safe_id = int(session_id)
    return f"KILL '{safe_id}'"


async def list_running(sql: FabricSqlClient, target: SqlTarget) -> list[RunningQuery]:
    """Return all currently-executing or runnable queries on *target*.

    Queries the ``sys.dm_exec_sessions`` / ``sys.dm_exec_requests`` DMVs,
    filtering for rows whose ``status`` is one of ``running``, ``runnable``,
    or ``suspended``, ordered by elapsed time descending.

    Args:
        sql: An authenticated :class:`~fabric_dw.sql_client.FabricSqlClient`.
        target: The warehouse to query.

    Returns:
        A (possibly empty) list of :class:`~fabric_dw.models.RunningQuery`
        instances, one per result row.
    """
    rows = await sql.execute(target, _LIST_RUNNING_SQL)
    return [RunningQuery.model_validate(row) for row in rows]


async def kill(sql: FabricSqlClient, target: SqlTarget, session_id: int) -> None:
    """Terminate the session identified by *session_id* on *target*.

    Args:
        sql: An authenticated :class:`~fabric_dw.sql_client.FabricSqlClient`.
        target: The warehouse to connect to.
        session_id: A positive integer identifying the session to kill.

    Raises:
        ValueError: If *session_id* is not a positive integer (i.e. <= 0).
        PermissionDenied: If the driver raises an :class:`~fabric_dw.exceptions.AuthError`
            (KILL requires Monitor or Admin permission on Fabric DW).
    """
    if session_id <= 0:
        msg = f"session_id must be a positive integer; got {session_id}"
        raise ValueError(msg)

    stmt = _build_kill_sql(session_id)
    try:
        await sql.execute_nonquery(target, stmt)
    except AuthError as exc:
        msg = f"Permission denied when trying to KILL session {session_id}: {exc}"
        raise PermissionDenied(msg) from exc
