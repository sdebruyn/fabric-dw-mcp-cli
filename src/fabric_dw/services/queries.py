"""DMV-backed running-query listing and session cancellation for Fabric DW.

Public API
----------
- :func:`list_running`     — return all currently active queries via DMV JOIN.
- :func:`list_connections` — return all active SQL connections via sys.dm_exec_connections.
- :func:`kill`             — terminate a session by session_id via KILL.
"""

from __future__ import annotations

import asyncio
from contextlib import closing

from fabric_dw import sql
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import AuthError, PermissionDeniedError
from fabric_dw.models import Connection, RunningQuery
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "kill",
    "list_connections",
    "list_running",
]

# NOTE: sys.dm_exec_sql_text is not supported on Fabric DW (Fabric Synapse
# Analytics uses a different execution engine), so the OUTER APPLY for
# query_text is omitted.  The query_text column is included in the SELECT as a
# literal NULL so the RunningQuery model field is populated with None.
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
    NULL AS query_text
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
        PermissionDeniedError: If the driver raises a permission or auth error
            (KILL requires Monitor or Admin permission on Fabric DW).
    """
    if session_id <= 0:
        msg = f"session_id must be a positive integer; got {session_id}"
        raise ValueError(msg)

    # KILL requires a bare integer (no quotes, no parameter binding).
    # We validate and cast to int to prevent injection before embedding.
    safe_id = int(session_id)
    kill_sql = f"KILL {safe_id}"

    def _run() -> None:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(kill_sql)
                conn.commit()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    msg = f"Permission denied when trying to KILL session {session_id}: {mapped}"
                    raise PermissionDeniedError(
                        msg,
                        hint="KILL requires Monitor or Admin permission on Fabric DW.",
                    ) from exc
                if isinstance(exc, (PermissionDeniedError, AuthError)):
                    msg = f"Permission denied when trying to KILL session {session_id}: {exc}"
                    raise PermissionDeniedError(msg) from exc
                raise

    await asyncio.to_thread(_run)
