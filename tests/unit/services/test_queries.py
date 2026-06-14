"""Tests for services.queries — stateless SQL helper (TDD, written before implementation)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, NotFoundError, PermissionDeniedError
from fabric_dw.models import Connection, RunningQuery
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


async def test_list_running_returns_empty_when_no_rows() -> None:
    target = _make_target()
    conn = _make_conn([], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    assert result == []


async def test_list_running_returns_running_query_instances() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_1_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    assert len(result) == 1
    assert isinstance(result[0], RunningQuery)


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


async def test_list_running_handles_null_query_text() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_2_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    assert result[0].query_text is None


async def test_list_running_returns_all_rows() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_1_TUPLE, _ROW_2_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    assert len(result) == 2
    assert result[0].session_id == 42
    assert result[1].session_id == 99


async def test_list_running_sql_references_dm_exec_sessions() -> None:
    target = _make_target()
    conn = _make_conn([], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_running(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "sys.dm_exec_sessions" in call_sql


async def test_list_running_sql_references_dm_exec_requests() -> None:
    target = _make_target()
    conn = _make_conn([], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_running(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "sys.dm_exec_requests" in call_sql


async def test_list_running_sql_filters_by_status() -> None:
    target = _make_target()
    conn = _make_conn([], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_running(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "r.status IN" in call_sql


async def test_list_running_closes_connection_after_success() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_1_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_running(target)

    conn.close.assert_called_once()


async def test_list_running_maps_permission_denied() -> None:
    """cursor.execute raising a 'permission was denied' fragment → PermissionDeniedError."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object X")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDeniedError),
    ):
        await queries.list_running(target)


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


async def test_kill_commits_after_execute() -> None:
    """run_query with commit=True issues commit via the shared execution path."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.kill(target, 42)

    conn.commit.assert_called_once()


async def test_kill_closes_connection_after_success() -> None:
    """run_query always closes the connection in its finally block."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.kill(target, 42)

    conn.close.assert_called_once()


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


async def test_kill_raises_value_error_for_zero() -> None:
    target = _make_target()
    conn = MagicMock()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="session_id"),
    ):
        await queries.kill(target, 0)

    conn.cursor.assert_not_called()


async def test_kill_raises_value_error_for_negative() -> None:
    target = _make_target()
    conn = MagicMock()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="session_id"),
    ):
        await queries.kill(target, -1)

    conn.cursor.assert_not_called()


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


async def test_kill_maps_permission_denied_from_cursor() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on KILL")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDeniedError),
    ):
        await queries.kill(target, 42)


async def test_kill_maps_auth_error_faithfully() -> None:
    """kill should surface AuthError as AuthError, not as PermissionDeniedError."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Authentication failed for user ''")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(AuthError),
    ):
        await queries.kill(target, 42)


async def test_kill_maps_not_found_faithfully() -> None:
    """kill surfaces NotFoundError (e.g. session already gone) as NotFoundError."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Invalid object name 'session'")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(NotFoundError),
    ):
        await queries.kill(target, 42)


async def test_kill_uses_run_query_not_direct_connection() -> None:
    """kill must go through run_query (shared path), not open_connection directly."""
    target = _make_target()

    with patch("fabric_dw.services.queries.run_query") as mock_run_query:
        mock_run_query.return_value = ([], [])
        await queries.kill(target, 99)

    mock_run_query.assert_called_once()
    call_kwargs = mock_run_query.call_args
    # Verify the SQL contains KILL and the session id
    sql_arg: str = call_kwargs.args[1]
    assert "KILL" in sql_arg
    assert "99" in sql_arg
    # Verify commit=True and fetch="none" are set
    assert call_kwargs.kwargs.get("commit") is True
    assert call_kwargs.kwargs.get("fetch") == "none"


# ---------------------------------------------------------------------------
# list_connections
# ---------------------------------------------------------------------------

_CONN_COLS = [
    "session_id",
    "connect_time",
    "client_net_address",
    "auth_scheme",
    "encrypt_option",
    "net_transport",
    "most_recent_session_id",
]

_CONN_ROW_1 = (
    10,
    _NOW,
    "192.168.1.100",
    "NTLM",
    "TRUE",
    "TCP",
    10,
)

_CONN_ROW_2 = (
    20,
    _NOW,
    None,
    "KERBEROS",
    "FALSE",
    "TCP",
    20,
)

# Pre-login pooled connection: session_id is NULL (IS NULLABLE per MS docs)
_CONN_ROW_NULL_SESSION = (
    None,
    _NOW,
    "10.0.0.5",
    "NTLM",
    "TRUE",
    "TCP",
    99,
)


async def test_list_connections_returns_empty_when_no_rows() -> None:
    target = _make_target()
    conn = _make_conn([], _CONN_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_connections(target)

    assert result == []


async def test_list_connections_returns_connection_instances() -> None:
    target = _make_target()
    conn = _make_conn([_CONN_ROW_1], _CONN_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_connections(target)

    assert len(result) == 1
    assert isinstance(result[0], Connection)


async def test_list_connections_parses_fields_correctly() -> None:
    target = _make_target()
    conn = _make_conn([_CONN_ROW_1], _CONN_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_connections(target)

    c = result[0]
    assert c.session_id == 10
    assert c.connect_time == _NOW
    assert c.client_net_address == "192.168.1.100"
    assert c.auth_scheme == "NTLM"
    assert c.encrypt_option == "TRUE"
    assert c.net_transport == "TCP"
    assert c.most_recent_session_id == 10


async def test_list_connections_handles_null_client_net_address() -> None:
    target = _make_target()
    conn = _make_conn([_CONN_ROW_2], _CONN_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_connections(target)

    assert result[0].client_net_address is None


async def test_list_connections_returns_all_rows() -> None:
    target = _make_target()
    conn = _make_conn([_CONN_ROW_1, _CONN_ROW_2], _CONN_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_connections(target)

    assert len(result) == 2
    assert result[0].session_id == 10
    assert result[1].session_id == 20


async def test_list_connections_sql_references_dm_exec_connections() -> None:
    target = _make_target()
    conn = _make_conn([], _CONN_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_connections(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "sys.dm_exec_connections" in call_sql


async def test_list_connections_sql_selects_expected_columns() -> None:
    target = _make_target()
    conn = _make_conn([], _CONN_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_connections(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    expected_cols = (
        "session_id",
        "connect_time",
        "client_net_address",
        "auth_scheme",
        "encrypt_option",
        "net_transport",
        "most_recent_session_id",
    )
    for col in expected_cols:
        assert col in call_sql


async def test_list_connections_closes_connection_after_success() -> None:
    target = _make_target()
    conn = _make_conn([_CONN_ROW_1], _CONN_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_connections(target)

    conn.close.assert_called_once()


async def test_list_connections_maps_permission_denied() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception(
        "permission was denied on object sys.dm_exec_connections"
    )
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDeniedError),
    ):
        await queries.list_connections(target)


async def test_list_connections_maps_auth_error() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Authentication failed for user '' (token)")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(AuthError),
    ):
        await queries.list_connections(target)


async def test_list_connections_unrelated_error_propagates() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("network timeout")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(RuntimeError, match="network timeout"),
    ):
        await queries.list_connections(target)


async def test_list_connections_handles_null_session_id() -> None:
    """Pre-login pooled connections return NULL for session_id (IS NULLABLE per MS docs)."""
    target = _make_target()
    conn = _make_conn([_CONN_ROW_NULL_SESSION], _CONN_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_connections(target)

    assert len(result) == 1
    assert result[0].session_id is None
    assert result[0].most_recent_session_id == 99


async def test_list_connections_parses_most_recent_session_id() -> None:
    """most_recent_session_id is parsed from the DMV result (disambiguates pooled connections)."""
    target = _make_target()
    conn = _make_conn([_CONN_ROW_1, _CONN_ROW_2], _CONN_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_connections(target)

    assert result[0].most_recent_session_id == 10
    assert result[1].most_recent_session_id == 20


async def test_list_connections_most_recent_session_id_can_be_none() -> None:
    """most_recent_session_id defaults to None when not present in result row."""
    target = _make_target()
    # Use a row without most_recent_session_id to confirm None default
    cols_without = _CONN_COLS[:-1]  # drop most_recent_session_id
    row_without = _CONN_ROW_1[:-1]
    conn = _make_conn([row_without], cols_without)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_connections(target)

    assert result[0].most_recent_session_id is None


# ---------------------------------------------------------------------------
# list_running — INNER JOIN correctness
# ---------------------------------------------------------------------------


async def test_list_running_sql_uses_inner_join() -> None:
    """The SQL must use INNER JOIN (not LEFT JOIN) so that the WHERE predicate
    on the right-side table is semantically correct rather than implied.
    A LEFT JOIN with a right-side WHERE predicate behaves as INNER JOIN but
    misleads the reader."""
    target = _make_target()
    conn = _make_conn([], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_running(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "INNER JOIN" in call_sql.upper()
    assert "LEFT JOIN" not in call_sql.upper()


# ---------------------------------------------------------------------------
# kill — integer-only injection guard
# ---------------------------------------------------------------------------


async def test_kill_sql_contains_bare_integer() -> None:
    """KILL statement must embed a bare integer, not a quoted or f-string value."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.kill(target, 123)

    call_sql: str = cursor.execute.call_args[0][0]
    # Must be exactly "KILL 123" (no quotes, no extra tokens)
    assert call_sql.strip() == "KILL 123"


async def test_kill_sql_session_id_is_integer_not_string() -> None:
    """The embedded session_id must be a plain integer (not a quoted string)."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.kill(target, 7)

    call_sql: str = cursor.execute.call_args[0][0]
    # Must not contain quotes around the number
    assert "'7'" not in call_sql
    assert '"7"' not in call_sql
    assert "7" in call_sql


async def test_kill_sql_no_params_bound() -> None:
    """KILL embeds the session id as a literal integer — no ? placeholders, no params."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.kill(target, 55)

    # cursor.execute must be called with exactly one positional arg (the SQL),
    # not with a second params argument, because KILL does not support binding.
    assert cursor.execute.call_args.args == ("KILL 55",)
