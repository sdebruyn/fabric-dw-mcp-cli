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
:class:`~fabric_dw.exceptions.PermissionDeniedError` with a documentation link.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, TypeVar

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import PermissionDeniedError
from fabric_dw.models import (
    ExecRequestHistory,
    ExecSessionHistory,
    FrequentlyRunQuery,
    LongRunningQuery,
    SqlPoolInsight,
)
from fabric_dw.sql import SqlTarget, run_query

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = [
    "EXEC_REQUESTS_HISTORY_COLUMNS",
    "EXEC_SESSIONS_HISTORY_COLUMNS",
    "FREQUENTLY_RUN_QUERIES_COLUMNS",
    "LONG_RUNNING_QUERIES_COLUMNS",
    "SQL_POOL_INSIGHTS_COLUMNS",
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

# ---------------------------------------------------------------------------
# Canonical column lists (MS Learn Fabric variant, verified 2026-06)
# ---------------------------------------------------------------------------

#: Columns projected from ``queryinsights.exec_requests_history``.
EXEC_REQUESTS_HISTORY_COLUMNS: tuple[str, ...] = (
    "distributed_statement_id",
    "database_name",
    "submit_time",
    "start_time",
    "end_time",
    "is_distributed",
    "statement_type",
    "total_elapsed_time_ms",
    "login_name",
    "row_count",
    "status",
    "session_id",
    "connection_id",
    "program_name",
    "batch_id",
    "root_batch_id",
    "query_hash",
    "label",
    "result_cache_hit",
    "sql_pool_name",
    "allocated_cpu_time_ms",
    "data_scanned_remote_storage_mb",
    "data_scanned_memory_mb",
    "data_scanned_disk_mb",
    "command",
    "error_code",
)

#: Columns projected from ``queryinsights.exec_sessions_history``.
EXEC_SESSIONS_HISTORY_COLUMNS: tuple[str, ...] = (
    "session_id",
    "connection_id",
    "session_start_time",
    "session_end_time",
    "program_name",
    "login_name",
    "status",
    "context_info",
    "total_query_elapsed_time_ms",
    "last_request_start_time",
    "last_request_end_time",
    "is_user_process",
    "prev_error",
    "group_id",
    "database_id",
    "authenticating_database_id",
    "open_transaction_count",
    "text_size",
    "language",
    "date_format",
    "date_first",
    "quoted_identifier",
    "arithabort",
    "ansi_null_dflt_on",
    "ansi_defaults",
    "ansi_warnings",
    "ansi_padding",
    "ansi_nulls",
    "concat_null_yields_null",
    "transaction_isolation_level",
    "lock_timeout",
    "deadlock_priority",
    "original_security_id",
    "database_name",
)

#: Columns projected from ``queryinsights.frequently_run_queries``.
#: ``last_run_session_id`` is documented on MS Learn but absent from the live
#: Fabric service — omitted to prevent ``Invalid column name`` errors (#195).
FREQUENTLY_RUN_QUERIES_COLUMNS: tuple[str, ...] = (
    "last_run_start_time",
    "last_run_command",
    "number_of_runs",
    "avg_total_elapsed_time_ms",
    "last_run_total_elapsed_time_ms",
    "last_dist_statement_id",
    "min_run_total_elapsed_time_ms",
    "max_run_total_elapsed_time_ms",
    "number_of_successful_runs",
    "number_of_failed_runs",
    "number_of_canceled_runs",
    "query_hash",
)

#: Columns projected from ``queryinsights.long_running_queries``.
#: ``last_run_session_id`` is documented on MS Learn but absent from the live
#: Fabric service — omitted to prevent ``Invalid column name`` errors (#195).
LONG_RUNNING_QUERIES_COLUMNS: tuple[str, ...] = (
    "last_run_start_time",
    "last_run_command",
    "median_total_elapsed_time_ms",
    "number_of_runs",
    "last_run_total_elapsed_time_ms",
    "last_dist_statement_id",
    "query_hash",
)

#: Columns projected from ``queryinsights.sql_pool_insights``.
SQL_POOL_INSIGHTS_COLUMNS: tuple[str, ...] = (
    "sql_pool_name",
    "timestamp",
    "max_resource_percentage",
    "is_optimized_for_reads",
    "current_workspace_capacity",
    "is_pool_under_pressure",
)


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, _MAX_LIMIT))


def _build_where(
    *,
    since: datetime | None,
    until: datetime | None,
    column: str,
) -> tuple[str, list[object]]:
    """Return a ``(where_clause, params)`` pair for the given time window.

    The ``where_clause`` is either an empty string (no filter) or a
    ``\\nWHERE <fragment>`` string ready to splice into the SQL template.
    The datetime values are returned as *params* for ``?``-style driver
    binding — they are **never** interpolated into the SQL text.

    Args:
        since: Optional inclusive lower bound (``column >= ?``).
        until: Optional inclusive upper bound (``column <= ?``).
        column: The pre-validated column name to filter on.  Must be a
            trusted constant — it is interpolated directly into the SQL
            fragment (identifier, not a value).

    Returns:
        A ``(where_clause, params)`` tuple where ``params`` holds the
        datetime values to be passed via ``run_query(params=...)``.
    """
    fragments: list[str] = []
    params: list[object] = []
    if since is not None:
        fragments.append(f"{column} >= ?")
        params.append(since)
    if until is not None:
        fragments.append(f"{column} <= ?")
        params.append(until)
    if not fragments:
        return "", []
    return "\nWHERE " + " AND ".join(fragments), params


def _execute_sql(
    target: SqlTarget,
    sql_text: str,
    mode: CredentialMode,
    params: list[object] | None = None,
) -> list[dict[str, object]]:
    """Execute *sql_text* synchronously and return rows as list of dicts.

    Args:
        target: The warehouse or SQL analytics endpoint to query.
        sql_text: The SQL statement to execute.  Use ``?`` placeholders for
            any values; pass the corresponding values via *params*.
        mode: The credential mode for Entra authentication.
        params: Optional bound parameters for ``?`` placeholders.

    Raises:
        PermissionDeniedError: If the driver reports a 403 / permission error.
            Re-raised with a documentation hint pointing to the Fabric
            Query Insights permissions page.
    """
    try:
        cols, rows = run_query(target, sql_text, params=params, mode=mode)
    except PermissionDeniedError as exc:
        raise PermissionDeniedError(str(exc), hint=_PERMISSION_DENIED_DOCS) from exc
    return [dict(zip(cols, r, strict=True)) for r in rows]


_M = TypeVar("_M", bound="BaseModel")


def _build_sql(
    view: str,
    columns: tuple[str, ...],
    order_by: str,
    limit: int,
    where: str,
) -> str:
    """Build the SELECT TOP … FROM queryinsights.<view> SQL string.

    All arguments are trusted constants (not user input) — the generated SQL
    is safe to format directly.

    Args:
        view: The unqualified view name under the ``queryinsights`` schema.
        columns: Ordered column names to project.
        order_by: Column name for the ORDER BY clause.
        limit: Clamped row limit (already validated via :func:`_clamp_limit`).
        where: Optional WHERE clause string (from :func:`_build_where`).

    Returns:
        A complete SELECT statement ready to execute.
    """
    col_list = ",\n    ".join(columns)
    return (
        f"SELECT TOP ({limit})\n    {col_list}\n"
        f"FROM queryinsights.{view}{where}\n"
        f"ORDER BY {order_by} DESC;\n"
    )


async def _list_insights(
    target: SqlTarget,
    *,
    view: str,
    columns: tuple[str, ...],
    order_by: str,
    filter_col: str,
    model: type[_M],
    limit: int,
    since: datetime | None,
    until: datetime | None,
    mode: CredentialMode,
) -> list[_M]:
    """Generic implementation shared by all five ``list_*`` functions.

    Clamps *limit*, builds the time-window WHERE clause (binding datetime
    values as ``?`` params — never interpolating them), formats the SELECT,
    executes via :func:`_execute_sql`, and validates each row into *model*.

    Args:
        target: The warehouse or SQL analytics endpoint to query.
        view: Unqualified view name under the ``queryinsights`` schema.
        columns: Canonical column tuple for the SELECT list.
        order_by: Column name for the ORDER BY clause.
        filter_col: Column name for the WHERE time-window filter.
        model: Pydantic model class to validate each row dict into.
        limit: Raw (un-clamped) row limit from the caller.
        since: Optional inclusive lower bound on *filter_col*.
        until: Optional inclusive upper bound on *filter_col*.
        mode: Credential mode for Entra authentication.

    Returns:
        A list of *model* instances.

    Raises:
        PermissionDeniedError: If the caller lacks Contributor or above permission.
    """
    clamped = _clamp_limit(limit)
    where_clause, where_params = _build_where(since=since, until=until, column=filter_col)
    sql_text = _build_sql(view, columns, order_by, clamped, where_clause)
    rows = await asyncio.to_thread(_execute_sql, target, sql_text, mode, where_params)
    return [model.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
        PermissionDeniedError: If the caller lacks Contributor or above permission.
    """
    return await _list_insights(
        target,
        view="exec_requests_history",
        columns=EXEC_REQUESTS_HISTORY_COLUMNS,
        order_by="submit_time",
        filter_col="submit_time",
        model=ExecRequestHistory,
        limit=limit,
        since=since,
        until=until,
        mode=mode,
    )


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
        PermissionDeniedError: If the caller lacks Contributor or above permission.
    """
    return await _list_insights(
        target,
        view="exec_sessions_history",
        columns=EXEC_SESSIONS_HISTORY_COLUMNS,
        order_by="session_start_time",
        filter_col="session_start_time",
        model=ExecSessionHistory,
        limit=limit,
        since=since,
        until=until,
        mode=mode,
    )


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
        PermissionDeniedError: If the caller lacks Contributor or above permission.
    """
    return await _list_insights(
        target,
        view="frequently_run_queries",
        columns=FREQUENTLY_RUN_QUERIES_COLUMNS,
        order_by="number_of_runs",
        filter_col="last_run_start_time",
        model=FrequentlyRunQuery,
        limit=limit,
        since=since,
        until=until,
        mode=mode,
    )


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
        PermissionDeniedError: If the caller lacks Contributor or above permission.
    """
    return await _list_insights(
        target,
        view="long_running_queries",
        columns=LONG_RUNNING_QUERIES_COLUMNS,
        order_by="median_total_elapsed_time_ms",
        filter_col="last_run_start_time",
        model=LongRunningQuery,
        limit=limit,
        since=since,
        until=until,
        mode=mode,
    )


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
        PermissionDeniedError: If the caller lacks Contributor or above permission.
    """
    return await _list_insights(
        target,
        view="sql_pool_insights",
        columns=SQL_POOL_INSIGHTS_COLUMNS,
        order_by="timestamp",
        filter_col="timestamp",
        model=SqlPoolInsight,
        limit=limit,
        since=since,
        until=until,
        mode=mode,
    )
