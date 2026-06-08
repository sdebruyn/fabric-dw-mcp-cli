"""Tests for services.queries — stateless SQL helper (TDD, written before implementation)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, PermissionDenied
from fabric_dw.models import RunningQuery
from fabric_dw.services import queries

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_target() -> MagicMock:
    """Return a mock SqlTarget."""
    return MagicMock()


def _make_conn(rows: list[tuple[object, ...]], columns: list[str]) -> MagicMock:
    """Return a mock connection whose cursor returns the given rows."""
    cursor = MagicMock()
    cursor.description = [(c, None) for c in columns]
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# Fixture rows matching the DMV query output columns
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

_COLS = [
    "session_id",
    "request_id",
    "status",
    "start_time",
    "total_elapsed_time",
    "login_name",
    "command",
    "query_text",
]

_ROW_1_TUPLE = (
    42,
    "0x0000000000000001",
    "running",
    _NOW,
    1500,
    "user@example.com",
    "SELECT",
    "SELECT TOP 10 * FROM dbo.sales",
)

_ROW_2_TUPLE = (
    99,
    "0x0000000000000002",
    "suspended",
    _NOW,
    3000,
    "admin@example.com",
    "UPDATE",
    None,
)


# ---------------------------------------------------------------------------
# list_running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_running_returns_empty_when_no_rows() -> None:
    target = _make_target()
    conn = _make_conn([], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    assert result == []


@pytest.mark.asyncio
async def test_list_running_returns_running_query_instances() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_1_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    assert len(result) == 1
    assert isinstance(result[0], RunningQuery)


@pytest.mark.asyncio
async def test_list_running_parses_fields_correctly() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_1_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    q = result[0]
    assert q.session_id == 42
    assert q.request_id == "0x0000000000000001"
    assert q.status == "running"
    assert q.start_time == _NOW
    assert q.total_elapsed_time_ms == 1500
    assert q.login_name == "user@example.com"
    assert q.command == "SELECT"
    assert q.query_text == "SELECT TOP 10 * FROM dbo.sales"


@pytest.mark.asyncio
async def test_list_running_handles_null_query_text() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_2_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    assert result[0].query_text is None


@pytest.mark.asyncio
async def test_list_running_returns_all_rows() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_1_TUPLE, _ROW_2_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    assert len(result) == 2
    assert result[0].session_id == 42
    assert result[1].session_id == 99


@pytest.mark.asyncio
async def test_list_running_sql_references_dm_exec_sessions() -> None:
    target = _make_target()
    conn = _make_conn([], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_running(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "sys.dm_exec_sessions" in call_sql


@pytest.mark.asyncio
async def test_list_running_sql_references_dm_exec_requests() -> None:
    target = _make_target()
    conn = _make_conn([], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_running(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "sys.dm_exec_requests" in call_sql


@pytest.mark.asyncio
async def test_list_running_sql_filters_by_status() -> None:
    target = _make_target()
    conn = _make_conn([], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_running(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "r.status IN" in call_sql


@pytest.mark.asyncio
async def test_list_running_closes_connection_after_success() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_1_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_running(target)

    conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_list_running_maps_permission_denied() -> None:
    """cursor.execute raising a 'permission was denied' fragment → PermissionDenied."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object X")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDenied),
    ):
        await queries.list_running(target)


@pytest.mark.asyncio
async def test_list_running_maps_auth_error() -> None:
    """cursor.execute raising an auth fragment → AuthError."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Authentication failed for user '' (token)")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(AuthError),
    ):
        await queries.list_running(target)


@pytest.mark.asyncio
async def test_list_running_unrelated_error_propagates() -> None:
    """Non-mapped driver errors propagate unchanged."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("deadlock detected")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(RuntimeError, match="deadlock detected"),
    ):
        await queries.list_running(target)


# ---------------------------------------------------------------------------
# kill — valid session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_issues_kill_statement() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.kill(target, 42)

    call_sql: str = cursor.execute.call_args[0][0]
    assert "KILL" in call_sql
    assert "42" in call_sql


@pytest.mark.asyncio
async def test_kill_commits_after_execute() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.kill(target, 42)

    conn.commit.assert_called_once()


@pytest.mark.asyncio
async def test_kill_closes_connection_after_success() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.kill(target, 42)

    conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_kill_returns_none_on_success() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.kill(target, 5)  # should not raise


# ---------------------------------------------------------------------------
# kill — invalid session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_raises_value_error_for_zero() -> None:
    target = _make_target()
    conn = MagicMock()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="session_id"),
    ):
        await queries.kill(target, 0)

    conn.cursor.assert_not_called()


@pytest.mark.asyncio
async def test_kill_raises_value_error_for_negative() -> None:
    target = _make_target()
    conn = MagicMock()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="session_id"),
    ):
        await queries.kill(target, -1)

    conn.cursor.assert_not_called()


@pytest.mark.asyncio
async def test_kill_raises_value_error_for_large_negative() -> None:
    target = _make_target()
    conn = MagicMock()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="session_id"),
    ):
        await queries.kill(target, -999)

    conn.cursor.assert_not_called()


# ---------------------------------------------------------------------------
# kill — permission / auth error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_maps_permission_denied_from_cursor() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on KILL")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDenied),
    ):
        await queries.kill(target, 42)


@pytest.mark.asyncio
async def test_kill_maps_auth_error_to_permission_denied() -> None:
    """kill should map AuthError → PermissionDenied."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Authentication failed for user ''")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDenied),
    ):
        await queries.kill(target, 42)


@pytest.mark.asyncio
async def test_kill_permission_denied_message_contains_session_id() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Authentication failed for user ''")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDenied, match="42"),
    ):
        await queries.kill(target, 42)
