"""Tests for services.query_insights — TDD, written before implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, PermissionDeniedError
from fabric_dw.models import (
    ExecRequestHistory,
    ExecSessionHistory,
    FrequentlyRunQuery,
    LongRunningQuery,
    SqlPoolInsight,
)
from fabric_dw.services import query_insights
from fabric_dw.services.query_insights import (
    EXEC_REQUESTS_HISTORY_COLUMNS,
    EXEC_SESSIONS_HISTORY_COLUMNS,
    FREQUENTLY_RUN_QUERIES_COLUMNS,
    LONG_RUNNING_QUERIES_COLUMNS,
    SQL_POOL_INSIGHTS_COLUMNS,
)
from tests.unit.services._helpers import _make_conn, _make_target

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
_CONN_STR = "myhost.datawarehouse.fabric.microsoft.com"


# ---------------------------------------------------------------------------
# exec_requests_history
# ---------------------------------------------------------------------------

_REQ_HIST_COLS = list(EXEC_REQUESTS_HISTORY_COLUMNS)

_REQ_HIST_ROW = (
    "a1b2c3d4-e5f6-7890-abcd-ef1234567890",  # distributed_statement_id
    "MyWarehouse",  # database_name
    _NOW,  # submit_time
    _NOW,  # start_time
    _NOW,  # end_time
    1,  # is_distributed
    "SELECT",  # statement_type
    1500,  # total_elapsed_time_ms
    "user@example.com",  # login_name
    100,  # row_count
    "Succeeded",  # status
    42,  # session_id
    None,  # connection_id
    None,  # program_name
    None,  # batch_id
    None,  # root_batch_id
    "abc123",  # query_hash
    None,  # label
    0,  # result_cache_hit
    None,  # sql_pool_name
    5000,  # allocated_cpu_time_ms
    None,  # data_scanned_remote_storage_mb
    None,  # data_scanned_memory_mb
    None,  # data_scanned_disk_mb
    "SELECT TOP 10 * FROM dbo.sales",  # command
    0,  # error_code
)


async def test_list_request_history_returns_empty_when_no_rows() -> None:
    target = _make_target()
    conn = _make_conn([], _REQ_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_request_history(target)
    assert result == []


async def test_list_request_history_returns_model_instances() -> None:
    target = _make_target()
    conn = _make_conn([_REQ_HIST_ROW], _REQ_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_request_history(target)
    assert len(result) == 1
    assert isinstance(result[0], ExecRequestHistory)


async def test_list_request_history_parses_fields() -> None:
    target = _make_target()
    conn = _make_conn([_REQ_HIST_ROW], _REQ_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_request_history(target)
    row = result[0]
    assert row.database_name == "MyWarehouse"
    assert row.status == "Succeeded"
    assert row.session_id == 42
    assert row.total_elapsed_time_ms == 1500


async def test_list_request_history_sql_references_exec_requests_history() -> None:
    target = _make_target()
    conn = _make_conn([], _REQ_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_request_history(target)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "queryinsights.exec_requests_history" in call_sql


async def test_list_request_history_sql_has_top_clause() -> None:
    target = _make_target()
    conn = _make_conn([], _REQ_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_request_history(target, limit=50)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (50)" in call_sql


async def test_list_request_history_default_limit_100() -> None:
    target = _make_target()
    conn = _make_conn([], _REQ_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_request_history(target)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (100)" in call_sql


async def test_list_request_history_limit_clamped_to_10000() -> None:
    target = _make_target()
    conn = _make_conn([], _REQ_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_request_history(target, limit=99999)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (10000)" in call_sql


async def test_list_request_history_since_adds_where_clause() -> None:
    target = _make_target()
    conn = _make_conn([], _REQ_HIST_COLS)
    since = datetime(2024, 1, 1, tzinfo=UTC)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_request_history(target, since=since)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "WHERE" in call_sql
    assert "submit_time" in call_sql


async def test_list_request_history_maps_permission_denied() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object X")
    conn.cursor.return_value = cursor
    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDeniedError),
    ):
        await query_insights.list_request_history(target)


async def test_list_request_history_permission_denied_message_has_docs_link() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object X")
    conn.cursor.return_value = cursor
    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDeniedError, match="query-insights"),
    ):
        await query_insights.list_request_history(target)


async def test_list_request_history_maps_auth_error() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Authentication failed for user '' (token)")
    conn.cursor.return_value = cursor
    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(AuthError),
    ):
        await query_insights.list_request_history(target)


async def test_list_request_history_closes_connection() -> None:
    target = _make_target()
    conn = _make_conn([_REQ_HIST_ROW], _REQ_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_request_history(target)
    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# exec_sessions_history
# ---------------------------------------------------------------------------

_SESS_HIST_COLS = list(EXEC_SESSIONS_HISTORY_COLUMNS)

_SESS_HIST_ROW = (
    1,  # session_id
    "a1b2c3d4-e5f6-7890-abcd-ef1234567890",  # connection_id
    _NOW,  # session_start_time
    _NOW,  # session_end_time
    "SSMS",  # program_name
    "user@example.com",  # login_name
    "Succeeded",  # status
    None,  # context_info
    2000,  # total_query_elapsed_time_ms
    _NOW,  # last_request_start_time
    _NOW,  # last_request_end_time
    True,  # is_user_process
    0,  # prev_error
    1,  # group_id
    5,  # database_id
    0,  # authenticating_database_id
    0,  # open_transaction_count
    4096,  # text_size
    "us_english",  # language
    "mdy",  # date_format
    7,  # date_first
    True,  # quoted_identifier
    True,  # arithabort
    True,  # ansi_null_dflt_on
    False,  # ansi_defaults
    True,  # ansi_warnings
    True,  # ansi_padding
    True,  # ansi_nulls
    True,  # concat_null_yields_null
    2,  # transaction_isolation_level
    -1,  # lock_timeout
    0,  # deadlock_priority
    b"\x01\x00",  # original_security_id
    "MyWarehouse",  # database_name
)


async def test_list_session_history_returns_empty_when_no_rows() -> None:
    target = _make_target()
    conn = _make_conn([], _SESS_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_session_history(target)
    assert result == []


async def test_list_session_history_returns_model_instances() -> None:
    target = _make_target()
    conn = _make_conn([_SESS_HIST_ROW], _SESS_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_session_history(target)
    assert len(result) == 1
    assert isinstance(result[0], ExecSessionHistory)


async def test_list_session_history_sql_references_view() -> None:
    target = _make_target()
    conn = _make_conn([], _SESS_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_session_history(target)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "queryinsights.exec_sessions_history" in call_sql


async def test_list_session_history_top_default_100() -> None:
    target = _make_target()
    conn = _make_conn([], _SESS_HIST_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_session_history(target)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (100)" in call_sql


async def test_list_session_history_maps_permission_denied() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object X")
    conn.cursor.return_value = cursor
    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDeniedError),
    ):
        await query_insights.list_session_history(target)


async def test_list_session_history_since_adds_where() -> None:
    target = _make_target()
    conn = _make_conn([], _SESS_HIST_COLS)
    since = datetime(2024, 1, 1, tzinfo=UTC)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_session_history(target, since=since)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "session_start_time" in call_sql
    # datetime must be bound as a param, never interpolated into the SQL string
    assert since.isoformat() not in call_sql
    assert since in cursor.execute.call_args[0][1]


# ---------------------------------------------------------------------------
# frequently_run_queries
# ---------------------------------------------------------------------------

_FREQ_COLS = list(FREQUENTLY_RUN_QUERIES_COLUMNS)

_FREQ_ROW = (
    _NOW,  # last_run_start_time
    "SELECT TOP 10 * FROM dbo.sales",  # last_run_command
    42,  # number_of_runs
    1500,  # avg_total_elapsed_time_ms
    1200,  # last_run_total_elapsed_time_ms
    None,  # last_dist_statement_id
    800,  # min_run_total_elapsed_time_ms
    2000,  # max_run_total_elapsed_time_ms
    40,  # number_of_successful_runs
    1,  # number_of_failed_runs
    1,  # number_of_canceled_runs
    "abc123",  # query_hash
)


async def test_list_frequent_queries_returns_empty_when_no_rows() -> None:
    target = _make_target()
    conn = _make_conn([], _FREQ_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_frequent_queries(target)
    assert result == []


async def test_list_frequent_queries_returns_model_instances() -> None:
    target = _make_target()
    conn = _make_conn([_FREQ_ROW], _FREQ_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_frequent_queries(target)
    assert len(result) == 1
    assert isinstance(result[0], FrequentlyRunQuery)


async def test_list_frequent_queries_parses_fields() -> None:
    target = _make_target()
    conn = _make_conn([_FREQ_ROW], _FREQ_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_frequent_queries(target)
    row = result[0]
    assert row.number_of_runs == 42
    assert row.avg_total_elapsed_time_ms == 1500
    assert row.number_of_canceled_runs == 1


async def test_list_frequent_queries_sql_references_view() -> None:
    target = _make_target()
    conn = _make_conn([], _FREQ_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_frequent_queries(target)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "queryinsights.frequently_run_queries" in call_sql


async def test_list_frequent_queries_top_default_100() -> None:
    target = _make_target()
    conn = _make_conn([], _FREQ_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_frequent_queries(target)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (100)" in call_sql


async def test_list_frequent_queries_maps_permission_denied() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied")
    conn.cursor.return_value = cursor
    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDeniedError),
    ):
        await query_insights.list_frequent_queries(target)


async def test_list_frequent_queries_since_adds_where() -> None:
    target = _make_target()
    conn = _make_conn([], _FREQ_COLS)
    since = datetime(2024, 1, 1, tzinfo=UTC)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_frequent_queries(target, since=since)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "last_run_start_time" in call_sql
    # datetime must be bound as a param, never interpolated into the SQL string
    assert since.isoformat() not in call_sql
    assert since in cursor.execute.call_args[0][1]


# ---------------------------------------------------------------------------
# long_running_queries
# ---------------------------------------------------------------------------

_LONG_COLS = list(LONG_RUNNING_QUERIES_COLUMNS)

_LONG_ROW = (
    _NOW,  # last_run_start_time
    "SELECT * FROM bigfact",  # last_run_command
    30000,  # median_total_elapsed_time_ms
    5,  # number_of_runs
    28000,  # last_run_total_elapsed_time_ms
    None,  # last_dist_statement_id
    "def456",  # query_hash
)


async def test_list_long_running_returns_empty_when_no_rows() -> None:
    target = _make_target()
    conn = _make_conn([], _LONG_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_long_running_queries(target)
    assert result == []


async def test_list_long_running_returns_model_instances() -> None:
    target = _make_target()
    conn = _make_conn([_LONG_ROW], _LONG_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_long_running_queries(target)
    assert len(result) == 1
    assert isinstance(result[0], LongRunningQuery)


async def test_list_long_running_parses_fields() -> None:
    target = _make_target()
    conn = _make_conn([_LONG_ROW], _LONG_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_long_running_queries(target)
    row = result[0]
    assert row.median_total_elapsed_time_ms == 30000
    assert row.number_of_runs == 5


async def test_list_long_running_sql_references_view() -> None:
    target = _make_target()
    conn = _make_conn([], _LONG_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_long_running_queries(target)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "queryinsights.long_running_queries" in call_sql


async def test_list_long_running_top_default_100() -> None:
    target = _make_target()
    conn = _make_conn([], _LONG_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_long_running_queries(target)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (100)" in call_sql


async def test_list_long_running_maps_permission_denied() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied")
    conn.cursor.return_value = cursor
    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDeniedError),
    ):
        await query_insights.list_long_running_queries(target)


async def test_list_long_running_since_adds_where() -> None:
    target = _make_target()
    conn = _make_conn([], _LONG_COLS)
    since = datetime(2024, 1, 1, tzinfo=UTC)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_long_running_queries(target, since=since)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "last_run_start_time" in call_sql
    # datetime must be bound as a param, never interpolated into the SQL string
    assert since.isoformat() not in call_sql
    assert since in cursor.execute.call_args[0][1]


# ---------------------------------------------------------------------------
# sql_pool_insights
# ---------------------------------------------------------------------------

_POOL_COLS = list(SQL_POOL_INSIGHTS_COLUMNS)

_POOL_ROW = (
    "SELECT",  # sql_pool_name
    _NOW,  # timestamp
    100,  # max_resource_percentage
    True,  # is_optimized_for_reads
    "F4",  # current_workspace_capacity
    False,  # is_pool_under_pressure
)


async def test_list_sql_pool_insights_returns_empty_when_no_rows() -> None:
    target = _make_target()
    conn = _make_conn([], _POOL_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_sql_pool_insights(target)
    assert result == []


async def test_list_sql_pool_insights_returns_model_instances() -> None:
    target = _make_target()
    conn = _make_conn([_POOL_ROW], _POOL_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_sql_pool_insights(target)
    assert len(result) == 1
    assert isinstance(result[0], SqlPoolInsight)


async def test_list_sql_pool_insights_parses_fields() -> None:
    target = _make_target()
    conn = _make_conn([_POOL_ROW], _POOL_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await query_insights.list_sql_pool_insights(target)
    row = result[0]
    assert row.sql_pool_name == "SELECT"
    assert row.max_resource_percentage == 100
    assert row.is_pool_under_pressure is False


async def test_list_sql_pool_insights_sql_references_view() -> None:
    target = _make_target()
    conn = _make_conn([], _POOL_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_sql_pool_insights(target)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "queryinsights.sql_pool_insights" in call_sql


async def test_list_sql_pool_insights_top_default_100() -> None:
    target = _make_target()
    conn = _make_conn([], _POOL_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_sql_pool_insights(target)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (100)" in call_sql


async def test_list_sql_pool_insights_maps_permission_denied() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied")
    conn.cursor.return_value = cursor
    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDeniedError),
    ):
        await query_insights.list_sql_pool_insights(target)


async def test_list_sql_pool_insights_since_adds_where() -> None:
    target = _make_target()
    conn = _make_conn([], _POOL_COLS)
    since = datetime(2024, 1, 1, tzinfo=UTC)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_sql_pool_insights(target, since=since)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "timestamp" in call_sql
    # datetime must be bound as a param, never interpolated into the SQL string
    assert since.isoformat() not in call_sql
    assert since in cursor.execute.call_args[0][1]


async def test_list_sql_pool_insights_closes_connection() -> None:
    target = _make_target()
    conn = _make_conn([_POOL_ROW], _POOL_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_sql_pool_insights(target)
    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# limit clamping shared across all functions
# ---------------------------------------------------------------------------


async def test_limit_clamped_min_1() -> None:
    """Negative or zero limit is treated as 1."""
    target = _make_target()
    conn = _make_conn([], _POOL_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_sql_pool_insights(target, limit=-5)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (1)" in call_sql


async def test_limit_clamped_max_10000_for_frequent() -> None:
    target = _make_target()
    conn = _make_conn([], _FREQ_COLS)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_frequent_queries(target, limit=50000)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (10000)" in call_sql


# ---------------------------------------------------------------------------
# Both since and until produce compound WHERE clause
# ---------------------------------------------------------------------------


async def test_request_history_since_and_until_produce_and_clause() -> None:
    target = _make_target()
    conn = _make_conn([], _REQ_HIST_COLS)
    since = datetime(2024, 1, 1, tzinfo=UTC)
    until = datetime(2024, 12, 31, tzinfo=UTC)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_request_history(target, since=since, until=until)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "AND" in call_sql


# ---------------------------------------------------------------------------
# Column-alignment tests: every canonical column appears in the generated SQL
# ---------------------------------------------------------------------------


def test_exec_requests_history_sql_contains_all_canonical_columns() -> None:
    """Every column in EXEC_REQUESTS_HISTORY_COLUMNS must appear in the SELECT."""
    sql = query_insights._build_sql(
        "exec_requests_history", EXEC_REQUESTS_HISTORY_COLUMNS, "submit_time", 100, ""
    )
    for col in EXEC_REQUESTS_HISTORY_COLUMNS:
        assert col in sql, f"Column {col!r} missing from exec_requests_history SQL"


def test_exec_sessions_history_sql_contains_all_canonical_columns() -> None:
    """Every column in EXEC_SESSIONS_HISTORY_COLUMNS must appear in the SELECT."""
    sql = query_insights._build_sql(
        "exec_sessions_history",
        EXEC_SESSIONS_HISTORY_COLUMNS,
        "session_start_time",
        100,
        "",
    )
    for col in EXEC_SESSIONS_HISTORY_COLUMNS:
        assert col in sql, f"Column {col!r} missing from exec_sessions_history SQL"


def test_frequently_run_queries_sql_contains_all_canonical_columns() -> None:
    """Every column in FREQUENTLY_RUN_QUERIES_COLUMNS must appear in the SELECT."""
    sql = query_insights._build_sql(
        "frequently_run_queries", FREQUENTLY_RUN_QUERIES_COLUMNS, "number_of_runs", 100, ""
    )
    for col in FREQUENTLY_RUN_QUERIES_COLUMNS:
        assert col in sql, f"Column {col!r} missing from frequently_run_queries SQL"


def test_long_running_queries_sql_contains_all_canonical_columns() -> None:
    """Every column in LONG_RUNNING_QUERIES_COLUMNS must appear in the SELECT."""
    sql = query_insights._build_sql(
        "long_running_queries",
        LONG_RUNNING_QUERIES_COLUMNS,
        "median_total_elapsed_time_ms",
        100,
        "",
    )
    for col in LONG_RUNNING_QUERIES_COLUMNS:
        assert col in sql, f"Column {col!r} missing from long_running_queries SQL"


def test_sql_pool_insights_sql_contains_all_canonical_columns() -> None:
    """Every column in SQL_POOL_INSIGHTS_COLUMNS must appear in the SELECT."""
    sql = query_insights._build_sql(
        "sql_pool_insights", SQL_POOL_INSIGHTS_COLUMNS, "timestamp", 100, ""
    )
    for col in SQL_POOL_INSIGHTS_COLUMNS:
        assert col in sql, f"Column {col!r} missing from sql_pool_insights SQL"


def test_frequently_run_queries_sql_excludes_last_run_session_id() -> None:
    """last_run_session_id must NOT appear — it crashes on live Fabric (#195)."""
    sql = query_insights._build_sql(
        "frequently_run_queries", FREQUENTLY_RUN_QUERIES_COLUMNS, "number_of_runs", 100, ""
    )
    assert "last_run_session_id" not in sql


def test_long_running_queries_sql_excludes_last_run_session_id() -> None:
    """last_run_session_id must NOT appear — it crashes on live Fabric (#195)."""
    sql = query_insights._build_sql(
        "long_running_queries",
        LONG_RUNNING_QUERIES_COLUMNS,
        "median_total_elapsed_time_ms",
        100,
        "",
    )
    assert "last_run_session_id" not in sql


# ---------------------------------------------------------------------------
# V01: _build_where datetime binding — unit tests for the helper directly
# ---------------------------------------------------------------------------


def test_build_where_no_bounds_returns_empty_clause_and_no_params() -> None:
    """No since/until → empty string and empty params."""
    clause, params = query_insights._build_where(since=None, until=None, column="col")
    assert clause == ""
    assert params == []


def test_build_where_since_only_returns_clause_and_one_param() -> None:
    """since-only → WHERE clause with one ? placeholder and the datetime as param."""
    since = datetime(2024, 1, 1, tzinfo=UTC)
    clause, params = query_insights._build_where(since=since, until=None, column="ts")
    assert "WHERE" in clause
    assert "ts >= ?" in clause
    assert params == [since]
    # The datetime must NOT be interpolated as a string literal.
    assert since.isoformat() not in clause


def test_build_where_until_only_returns_clause_and_one_param() -> None:
    """until-only → WHERE clause with one ? placeholder and the datetime as param."""
    until = datetime(2024, 12, 31, tzinfo=UTC)
    clause, params = query_insights._build_where(since=None, until=until, column="ts")
    assert "WHERE" in clause
    assert "ts <= ?" in clause
    assert params == [until]
    assert until.isoformat() not in clause


def test_build_where_since_and_until_returns_two_params_in_order() -> None:
    """since+until → WHERE with two ? placeholders; params in since, until order."""
    since = datetime(2024, 1, 1, tzinfo=UTC)
    until = datetime(2024, 12, 31, tzinfo=UTC)
    clause, params = query_insights._build_where(since=since, until=until, column="ts")
    assert "WHERE" in clause
    assert "AND" in clause
    assert params == [since, until]
    assert since.isoformat() not in clause
    assert until.isoformat() not in clause


def test_build_where_clause_prefixed_with_newline_where() -> None:
    """Non-empty clause starts with \\nWHERE so it splices cleanly into SQL."""
    since = datetime(2024, 6, 1, tzinfo=UTC)
    clause, _ = query_insights._build_where(since=since, until=None, column="ts")
    assert clause.startswith("\nWHERE ")


# ---------------------------------------------------------------------------
# V01: end-to-end binding — datetime params passed to run_query, not in SQL
# ---------------------------------------------------------------------------


async def test_list_request_history_since_passed_as_param_not_interpolated() -> None:
    """since datetime must be in the params tuple, never in the SQL string."""
    target = _make_target()
    conn = _make_conn([], _REQ_HIST_COLS)
    since = datetime(2024, 3, 15, 9, 30, tzinfo=UTC)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_request_history(target, since=since)
    cursor = conn.cursor.return_value
    # SQL string must not contain the ISO-formatted datetime value
    call_sql: str = cursor.execute.call_args[0][0]
    assert since.isoformat() not in call_sql
    # The datetime must appear as a bound parameter (second positional arg to execute)
    call_params = cursor.execute.call_args[0][1]
    assert since in call_params


async def test_list_request_history_until_passed_as_param_not_interpolated() -> None:
    """until datetime must be in the params tuple, never in the SQL string."""
    target = _make_target()
    conn = _make_conn([], _REQ_HIST_COLS)
    until = datetime(2024, 9, 30, 23, 59, tzinfo=UTC)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_request_history(target, until=until)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert until.isoformat() not in call_sql
    call_params = cursor.execute.call_args[0][1]
    assert until in call_params


async def test_list_request_history_both_bounds_passed_as_params() -> None:
    """Both since and until must be bound as params (two ? placeholders in SQL)."""
    target = _make_target()
    conn = _make_conn([], _REQ_HIST_COLS)
    since = datetime(2024, 1, 1, tzinfo=UTC)
    until = datetime(2024, 12, 31, tzinfo=UTC)
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await query_insights.list_request_history(target, since=since, until=until)
    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    # Neither value interpolated
    assert since.isoformat() not in call_sql
    assert until.isoformat() not in call_sql
    # Both present as params
    call_params = cursor.execute.call_args[0][1]
    assert since in call_params
    assert until in call_params
