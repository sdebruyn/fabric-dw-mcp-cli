"""DMV-backed Fabric Query Insights views via the ``queryinsights`` schema.

Public API
----------
- :func:`list_request_history`   — ``queryinsights.exec_requests_history``
- :func:`list_session_history`   — ``queryinsights.exec_sessions_history``
- :func:`list_frequent_queries`  — ``queryinsights.frequently_run_queries``
- :func:`list_long_running_queries` — ``queryinsights.long_running_queries``
- :func:`list_sql_pool_insights` — ``queryinsights.sql_pool_insights``

All functions accept an optional *limit* (default 100, cap 10 000) and
ISO-8601 *since* / *until* window where the view has a datetime column to
filter on.  A 403 driver error is surfaced as
:class:`~fabric_dw.exceptions.PermissionDenied` with a documentation link.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime
from typing import TypeVar

from fabric_dw import sql
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import PermissionDenied
from fabric_dw.models import (
    ExecRequestHistory,
    ExecSessionHistory,
    FrequentlyRunQuery,
    LongRunningQuery,
    SqlPoolInsight,
)
from fabric_dw.sql import SqlTarget

__all__ = [
    "list_frequent_queries",
    "list_long_running_queries",
    "list_request_history",
    "list_session_history",
    "list_sql_pool_insights",
]

_PERMISSION_DENIED_DOCS = (
    "https://learn.microsoft.com/fabric/data-warehouse/query-insights"
    " — Contributor or above permission is required."
)

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 10_000

_ModelT = TypeVar(
    "_ModelT",
    ExecRequestHistory,
    ExecSessionHistory,
    FrequentlyRunQuery,
    LongRunningQuery,
    SqlPoolInsight,
)


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, _MAX_LIMIT))


def _build_where(
    *,
    since: datetime | None,
    until: datetime | None,
    column: str,
) -> str:
    """Return a WHERE clause fragment (without the WHERE keyword) or empty string."""
    parts: list[str] = []
    if since is not None:
        parts.append(f"{column} >= '{since.isoformat()}'")
    if until is not None:
        parts.append(f"{column} <= '{until.isoformat()}'")
    return " AND ".join(parts)


def _run_query(
    target: SqlTarget,
    sql_text: str,
    model_cls: type[_ModelT],
    mode: CredentialMode,
) -> list[_ModelT]:
    """Execute *sql_text* synchronously and return a list of *model_cls* instances."""
    with closing(sql.open_connection(target, mode=mode)) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(sql_text)
            cols = [c[0] for c in (cursor.description or [])]
            rows = cursor.fetchall()
        except Exception as exc:
            mapped = sql.map_driver_error(exc)
            if mapped:
                if isinstance(mapped, PermissionDenied):
                    msg = f"{mapped} — {_PERMISSION_DENIED_DOCS}"
                    raise PermissionDenied(msg) from exc
                raise mapped from exc
            raise
        return [
            model_cls.model_validate(dict(zip(cols, r, strict=True)))  # type: ignore[return-value]
            for r in rows
        ]


# ---------------------------------------------------------------------------
# exec_requests_history
# ---------------------------------------------------------------------------

_REQUEST_HISTORY_SQL_TEMPLATE = """\
SELECT TOP ({limit})
    distributed_statement_id,
    database_name,
    submit_time,
    start_time,
    end_time,
    is_distributed,
    statement_type,
    total_elapsed_time_ms,
    login_name,
    row_count,
    status,
    session_id,
    connection_id,
    program_name,
    batch_id,
    root_batch_id,
    query_hash,
    label,
    result_cache_hit,
    sql_pool_name,
    allocated_cpu_time_ms,
    data_scanned_remote_storage_mb,
    data_scanned_memory_mb,
    data_scanned_disk_mb,
    command,
    error_code
FROM queryinsights.exec_requests_history{where}
ORDER BY submit_time DESC;
"""


async def list_request_history(
    target: SqlTarget,
    *,
    limit: int = _DEFAULT_LIMIT,
    since: datetime | None = None,
    until: datetime | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[ExecRequestHistory]:
    """Return completed SQL requests from ``queryinsights.exec_requests_history``.

    Args:
        target: The warehouse or SQL analytics endpoint to query.
        limit: Maximum number of rows to return (default 100, cap 10 000).
        since: Optional ISO-8601 lower bound on ``submit_time`` (inclusive).
        until: Optional ISO-8601 upper bound on ``submit_time`` (inclusive).
        mode: The credential mode for Entra authentication.

    Returns:
        A list of :class:`~fabric_dw.models.ExecRequestHistory` instances,
        ordered by ``submit_time`` descending.

    Raises:
        PermissionDenied: If the caller lacks Contributor or above permission.
    """
    clamped = _clamp_limit(limit)
    where_fragment = _build_where(since=since, until=until, column="submit_time")
    where_clause = f"\nWHERE {where_fragment}" if where_fragment else ""
    sql_text = _REQUEST_HISTORY_SQL_TEMPLATE.format(limit=clamped, where=where_clause)

    return await asyncio.to_thread(
        _run_query, target, sql_text, ExecRequestHistory, mode
    )


# ---------------------------------------------------------------------------
# exec_sessions_history
# ---------------------------------------------------------------------------

_SESSION_HISTORY_SQL_TEMPLATE = """\
SELECT TOP ({limit})
    session_id,
    connection_id,
    session_start_time,
    session_end_time,
    program_name,
    login_name,
    status,
    context_info,
    total_query_elapsed_time_ms,
    last_request_start_time,
    last_request_end_time,
    is_user_process,
    prev_error,
    group_id,
    database_id,
    authenticating_database_id,
    open_transaction_count,
    text_size,
    language,
    date_format,
    date_first,
    quoted_identifier,
    arithabort,
    ansi_null_dflt_on,
    ansi_defaults,
    ansi_warnings,
    ansi_padding,
    ansi_nulls,
    concat_null_yields_null,
    transaction_isolation_level,
    lock_timeout,
    deadlock_priority,
    original_security_id,
    database_name
FROM queryinsights.exec_sessions_history{where}
ORDER BY session_start_time DESC;
"""


async def list_session_history(
    target: SqlTarget,
    *,
    limit: int = _DEFAULT_LIMIT,
    since: datetime | None = None,
    until: datetime | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[ExecSessionHistory]:
    """Return completed sessions from ``queryinsights.exec_sessions_history``.

    Args:
        target: The warehouse or SQL analytics endpoint to query.
        limit: Maximum number of rows to return (default 100, cap 10 000).
        since: Optional ISO-8601 lower bound on ``session_start_time`` (inclusive).
        until: Optional ISO-8601 upper bound on ``session_start_time`` (inclusive).
        mode: The credential mode for Entra authentication.

    Returns:
        A list of :class:`~fabric_dw.models.ExecSessionHistory` instances,
        ordered by ``session_start_time`` descending.

    Raises:
        PermissionDenied: If the caller lacks Contributor or above permission.
    """
    clamped = _clamp_limit(limit)
    where_fragment = _build_where(since=since, until=until, column="session_start_time")
    where_clause = f"\nWHERE {where_fragment}" if where_fragment else ""
    sql_text = _SESSION_HISTORY_SQL_TEMPLATE.format(limit=clamped, where=where_clause)

    return await asyncio.to_thread(
        _run_query, target, sql_text, ExecSessionHistory, mode
    )


# ---------------------------------------------------------------------------
# frequently_run_queries
# ---------------------------------------------------------------------------

_FREQUENT_QUERIES_SQL_TEMPLATE = """\
SELECT TOP ({limit})
    last_run_start_time,
    last_run_command,
    number_of_runs,
    avg_total_elapsed_time_ms,
    last_run_total_elapsed_time_ms,
    last_dist_statement_id,
    last_run_session_id,
    min_run_total_elapsed_time_ms,
    max_run_total_elapsed_time_ms,
    number_of_successful_runs,
    number_of_failed_runs,
    number_of_cancelled_runs,
    query_hash
FROM queryinsights.frequently_run_queries{where}
ORDER BY number_of_runs DESC;
"""


async def list_frequent_queries(
    target: SqlTarget,
    *,
    limit: int = _DEFAULT_LIMIT,
    since: datetime | None = None,
    until: datetime | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[FrequentlyRunQuery]:
    """Return frequently-run queries from ``queryinsights.frequently_run_queries``.

    Args:
        target: The warehouse or SQL analytics endpoint to query.
        limit: Maximum number of rows to return (default 100, cap 10 000).
        since: Optional ISO-8601 lower bound on ``last_run_start_time`` (inclusive).
        until: Optional ISO-8601 upper bound on ``last_run_start_time`` (inclusive).
        mode: The credential mode for Entra authentication.

    Returns:
        A list of :class:`~fabric_dw.models.FrequentlyRunQuery` instances,
        ordered by ``number_of_runs`` descending.

    Raises:
        PermissionDenied: If the caller lacks Contributor or above permission.
    """
    clamped = _clamp_limit(limit)
    where_fragment = _build_where(since=since, until=until, column="last_run_start_time")
    where_clause = f"\nWHERE {where_fragment}" if where_fragment else ""
    sql_text = _FREQUENT_QUERIES_SQL_TEMPLATE.format(limit=clamped, where=where_clause)

    return await asyncio.to_thread(
        _run_query, target, sql_text, FrequentlyRunQuery, mode
    )


# ---------------------------------------------------------------------------
# long_running_queries
# ---------------------------------------------------------------------------

_LONG_RUNNING_SQL_TEMPLATE = """\
SELECT TOP ({limit})
    last_run_start_time,
    last_run_command,
    median_total_elapsed_time_ms,
    number_of_runs,
    last_run_total_elapsed_time_ms,
    last_dist_statement_id,
    last_run_session_id,
    query_hash
FROM queryinsights.long_running_queries{where}
ORDER BY median_total_elapsed_time_ms DESC;
"""


async def list_long_running_queries(
    target: SqlTarget,
    *,
    limit: int = _DEFAULT_LIMIT,
    since: datetime | None = None,
    until: datetime | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[LongRunningQuery]:
    """Return long-running queries from ``queryinsights.long_running_queries``.

    Args:
        target: The warehouse or SQL analytics endpoint to query.
        limit: Maximum number of rows to return (default 100, cap 10 000).
        since: Optional ISO-8601 lower bound on ``last_run_start_time`` (inclusive).
        until: Optional ISO-8601 upper bound on ``last_run_start_time`` (inclusive).
        mode: The credential mode for Entra authentication.

    Returns:
        A list of :class:`~fabric_dw.models.LongRunningQuery` instances,
        ordered by ``median_total_elapsed_time_ms`` descending.

    Raises:
        PermissionDenied: If the caller lacks Contributor or above permission.
    """
    clamped = _clamp_limit(limit)
    where_fragment = _build_where(since=since, until=until, column="last_run_start_time")
    where_clause = f"\nWHERE {where_fragment}" if where_fragment else ""
    sql_text = _LONG_RUNNING_SQL_TEMPLATE.format(limit=clamped, where=where_clause)

    return await asyncio.to_thread(
        _run_query, target, sql_text, LongRunningQuery, mode
    )


# ---------------------------------------------------------------------------
# sql_pool_insights
# ---------------------------------------------------------------------------

_SQL_POOL_INSIGHTS_SQL_TEMPLATE = """\
SELECT TOP ({limit})
    sql_pool_name,
    timestamp,
    max_resource_percentage,
    is_optimized_for_reads,
    current_workspace_capacity,
    is_pool_under_pressure
FROM queryinsights.sql_pool_insights{where}
ORDER BY timestamp DESC;
"""


async def list_sql_pool_insights(
    target: SqlTarget,
    *,
    limit: int = _DEFAULT_LIMIT,
    since: datetime | None = None,
    until: datetime | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[SqlPoolInsight]:
    """Return SQL pool insight events from ``queryinsights.sql_pool_insights``.

    Args:
        target: The warehouse or SQL analytics endpoint to query.
        limit: Maximum number of rows to return (default 100, cap 10 000).
        since: Optional ISO-8601 lower bound on ``timestamp`` (inclusive).
        until: Optional ISO-8601 upper bound on ``timestamp`` (inclusive).
        mode: The credential mode for Entra authentication.

    Returns:
        A list of :class:`~fabric_dw.models.SqlPoolInsight` instances,
        ordered by ``timestamp`` descending.

    Raises:
        PermissionDenied: If the caller lacks Contributor or above permission.
    """
    clamped = _clamp_limit(limit)
    where_fragment = _build_where(since=since, until=until, column="timestamp")
    where_clause = f"\nWHERE {where_fragment}" if where_fragment else ""
    sql_text = _SQL_POOL_INSIGHTS_SQL_TEMPLATE.format(limit=clamped, where=where_clause)

    return await asyncio.to_thread(
        _run_query, target, sql_text, SqlPoolInsight, mode
    )
