"""Tests for services.queries — written BEFORE the implementation (TDD)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from fabric_dw.exceptions import AuthError, PermissionDenied
from fabric_dw.models import RunningQuery
from fabric_dw.services import queries

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TARGET = MagicMock()  # SqlTarget — shape doesn't matter for mocked tests


def _make_sql() -> AsyncMock:
    """Return a fully mocked FabricSqlClient."""
    client = AsyncMock()
    client.execute = AsyncMock()
    client.execute_nonquery = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Fixture rows matching the DMV query output columns
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

_ROW_1: dict[str, object] = {
    "session_id": 42,
    "request_id": "0x0000000000000001",
    "status": "running",
    "start_time": _NOW,
    "total_elapsed_time": 1500,
    "login_name": "user@example.com",
    "command": "SELECT",
    "query_text": "SELECT TOP 10 * FROM dbo.sales",
}

_ROW_2: dict[str, object] = {
    "session_id": 99,
    "request_id": "0x0000000000000002",
    "status": "suspended",
    "start_time": _NOW,
    "total_elapsed_time": 3000,
    "login_name": "admin@example.com",
    "command": "UPDATE",
    "query_text": None,
}


# ---------------------------------------------------------------------------
# list_running — SQL shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_running_sql_references_dm_exec_sessions() -> None:
    """list_running should query sys.dm_exec_sessions."""
    sql = _make_sql()
    sql.execute.return_value = []

    await queries.list_running(sql, _TARGET)

    sql.execute.assert_called_once()
    call_sql: str = sql.execute.call_args[0][1]
    assert "sys.dm_exec_sessions" in call_sql


@pytest.mark.asyncio
async def test_list_running_sql_references_dm_exec_requests() -> None:
    """list_running should query sys.dm_exec_requests."""
    sql = _make_sql()
    sql.execute.return_value = []

    await queries.list_running(sql, _TARGET)

    call_sql: str = sql.execute.call_args[0][1]
    assert "sys.dm_exec_requests" in call_sql


@pytest.mark.asyncio
async def test_list_running_sql_filters_by_status() -> None:
    """list_running SQL must include a status filter with the three relevant states."""
    sql = _make_sql()
    sql.execute.return_value = []

    await queries.list_running(sql, _TARGET)

    call_sql: str = sql.execute.call_args[0][1]
    assert "r.status IN" in call_sql


@pytest.mark.asyncio
async def test_list_running_passes_target_to_execute() -> None:
    """list_running should forward the target to sql.execute."""
    sql = _make_sql()
    sql.execute.return_value = []

    await queries.list_running(sql, _TARGET)

    call_target = sql.execute.call_args[0][0]
    assert call_target is _TARGET


# ---------------------------------------------------------------------------
# list_running — parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_running_returns_empty_list_when_no_rows() -> None:
    """list_running returns [] when the DMV returns no rows."""
    sql = _make_sql()
    sql.execute.return_value = []

    result = await queries.list_running(sql, _TARGET)

    assert result == []


@pytest.mark.asyncio
async def test_list_running_returns_running_query_instances() -> None:
    """list_running should return a list of RunningQuery objects."""
    sql = _make_sql()
    sql.execute.return_value = [_ROW_1]

    result = await queries.list_running(sql, _TARGET)

    assert len(result) == 1
    assert isinstance(result[0], RunningQuery)


@pytest.mark.asyncio
async def test_list_running_parses_fields_correctly() -> None:
    """list_running should map all DMV columns to RunningQuery fields."""
    sql = _make_sql()
    sql.execute.return_value = [_ROW_1]

    result = await queries.list_running(sql, _TARGET)
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
    """list_running should accept rows where query_text is None."""
    sql = _make_sql()
    sql.execute.return_value = [_ROW_2]

    result = await queries.list_running(sql, _TARGET)

    assert result[0].query_text is None


@pytest.mark.asyncio
async def test_list_running_returns_all_rows() -> None:
    """list_running should return one RunningQuery per result row."""
    sql = _make_sql()
    sql.execute.return_value = [_ROW_1, _ROW_2]

    result = await queries.list_running(sql, _TARGET)

    assert len(result) == 2
    assert result[0].session_id == 42
    assert result[1].session_id == 99


# ---------------------------------------------------------------------------
# kill — valid session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_calls_execute_nonquery_with_kill_statement() -> None:
    """kill should execute KILL '<id>' via execute_nonquery."""
    sql = _make_sql()
    sql.execute_nonquery.return_value = 0

    await queries.kill(sql, _TARGET, 42)

    sql.execute_nonquery.assert_called_once()
    call_sql: str = sql.execute_nonquery.call_args[0][1]
    assert "KILL" in call_sql
    assert "42" in call_sql


@pytest.mark.asyncio
async def test_kill_passes_target_to_execute_nonquery() -> None:
    """kill should forward the target to sql.execute_nonquery."""
    sql = _make_sql()
    sql.execute_nonquery.return_value = 0

    await queries.kill(sql, _TARGET, 1)

    call_target = sql.execute_nonquery.call_args[0][0]
    assert call_target is _TARGET


@pytest.mark.asyncio
async def test_kill_returns_none_on_success() -> None:
    """kill should return None on a successful KILL."""
    sql = _make_sql()
    sql.execute_nonquery.return_value = 0

    await queries.kill(sql, _TARGET, 5)


# ---------------------------------------------------------------------------
# kill — invalid session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_raises_value_error_for_zero() -> None:
    """kill(0) should raise ValueError before any SQL is executed."""
    sql = _make_sql()

    with pytest.raises(ValueError, match="session_id"):
        await queries.kill(sql, _TARGET, 0)

    sql.execute_nonquery.assert_not_called()


@pytest.mark.asyncio
async def test_kill_raises_value_error_for_negative() -> None:
    """kill(-1) should raise ValueError before any SQL is executed."""
    sql = _make_sql()

    with pytest.raises(ValueError, match="session_id"):
        await queries.kill(sql, _TARGET, -1)

    sql.execute_nonquery.assert_not_called()


@pytest.mark.asyncio
async def test_kill_raises_value_error_for_large_negative() -> None:
    """kill(-999) should raise ValueError."""
    sql = _make_sql()

    with pytest.raises(ValueError, match="session_id"):
        await queries.kill(sql, _TARGET, -999)

    sql.execute_nonquery.assert_not_called()


# ---------------------------------------------------------------------------
# kill — permission / auth error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_maps_auth_error_to_permission_denied() -> None:
    """kill should map AuthError from execute_nonquery to PermissionDenied."""
    sql = _make_sql()
    sql.execute_nonquery.side_effect = AuthError("login failed for user")

    with pytest.raises(PermissionDenied):
        await queries.kill(sql, _TARGET, 42)


@pytest.mark.asyncio
async def test_kill_permission_denied_message_contains_session_id() -> None:
    """The PermissionDenied raised by kill should mention the session_id."""
    sql = _make_sql()
    sql.execute_nonquery.side_effect = AuthError("access denied")

    with pytest.raises(PermissionDenied, match="42"):
        await queries.kill(sql, _TARGET, 42)
