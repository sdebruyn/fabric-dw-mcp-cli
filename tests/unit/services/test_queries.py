"""Tests for services.queries — stateless SQL helper (TDD, written before implementation)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, NotFoundError, PermissionDeniedError
from fabric_dw.models import Connection, QueryLock, RunningQuery
from fabric_dw.services import queries
from tests.unit.services._helpers import _FakeRow, _make_conn, _make_target

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
    "dist_statement_id",
    "blocking_session_id",
    "wait_type",
    "wait_time",
    "cpu_time",
    "reads",
    "writes",
    "logical_reads",
    "row_count",
    "open_transaction_count",
]

_ROW_1_TUPLE = (
    42,
    "0x0000000000000001",
    "running",
    _NOW,
    1500,
    "user@example.com",
    "SELECT",
    None,
    "A1B2C3D4-1234-5678-ABCD-EF0123456789",
    None,
    None,
    None,
    750,
    100,
    5,
    1000,
    50,
    0,
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
    "B2C3D4E5-2345-6789-BCDE-F01234567890",
    42,
    "LCK_M_S",
    500,
    250,
    50,
    0,
    500,
    0,
    1,
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
    assert q.query_text is None
    assert q.dist_statement_id == "A1B2C3D4-1234-5678-ABCD-EF0123456789"
    assert q.blocking_session_id is None
    assert q.wait_type is None
    assert q.wait_time_ms is None
    assert q.cpu_time_ms == 750
    assert q.reads == 100
    assert q.writes == 5
    assert q.logical_reads == 1000
    assert q.row_count == 50
    assert q.open_transaction_count == 0


async def test_list_running_handles_null_query_text() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_2_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    assert result[0].query_text is None


async def test_list_running_parses_blocking_and_wait_fields() -> None:
    target = _make_target()
    conn = _make_conn([_ROW_2_TUPLE], _COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    q = result[0]
    assert q.blocking_session_id == 42
    assert q.wait_type == "LCK_M_S"
    assert q.wait_time_ms == 500
    assert q.open_transaction_count == 1


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
    """KILL statement embeds the session_id as a bare integer literal."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.kill(target, 42)

    call_sql: str = cursor.execute.call_args[0][0]
    # Assert the full KILL statement — bare "42" could match in GUIDs, comments, etc.
    assert call_sql.strip() == "KILL 42"


async def test_kill_uses_autocommit_mode() -> None:
    """KILL must be issued in autocommit mode, not inside a user transaction.

    T-SQL forbids KILL inside an explicit transaction.  run_query must be
    called with autocommit=True so the ODBC driver does not wrap the KILL
    in BEGIN TRANSACTION / COMMIT.
    """
    target = _make_target()

    with patch("fabric_dw.services.queries.run_query") as mock_run_query:
        mock_run_query.return_value = ([], [])
        await queries.kill(target, 42)

    call_kwargs = mock_run_query.call_args
    assert call_kwargs.kwargs.get("autocommit") is True


async def test_kill_does_not_commit_manually_in_autocommit_mode() -> None:
    """With autocommit, the driver handles commit; run_query must not call conn.commit().

    Also asserts that open_connection was opened with autocommit=True, which is
    the actual guard: without it, this test would pass even if autocommit=True
    were accidentally dropped (because commit defaults to False in run_query).
    """
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn) as mock_open:
        await queries.kill(target, 42)

    conn.commit.assert_not_called()
    mock_open.assert_called_once()
    assert mock_open.call_args.kwargs.get("autocommit") is True


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
    # Verify autocommit=True (KILL is forbidden inside a user transaction) and fetch="none"
    assert call_kwargs.kwargs.get("autocommit") is True
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
    # Assert exact statement — bare "7" could match in other literal values.
    assert call_sql.strip() == "KILL 7"


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


# ---------------------------------------------------------------------------
# Regression: Row→tuple normalisation in queries DMV path (#719)
# ---------------------------------------------------------------------------
# _FakeRow is imported from tests.unit.services._helpers (shared definition).


async def test_list_running_row_objects_produce_populated_model_fields() -> None:
    """Regression for #719: Row-like driver objects are normalised to real tuples.

    Feeds _FakeRow instances through the full stack (via open_connection so
    run_query's central normalisation fires) and asserts that list_running
    returns RunningQuery instances with all fields correctly populated —
    not just the first column.
    """
    target = _make_target()
    fake_row = _FakeRow(*_ROW_1_TUPLE)
    conn = _make_conn([fake_row], _COLS)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_running(target)

    assert len(result) == 1
    q = result[0]
    # Verify that fields beyond the first column are populated.
    assert q.session_id == 42  # col 1
    assert q.status == "running"  # col 3
    assert q.login_name == "user@example.com"  # col 6
    assert q.total_elapsed_time_ms == 1500  # col 5


async def test_list_running_run_query_output_is_real_tuples() -> None:
    """Genuine regression guard for #719: rows from run_query must be real tuples.

    The dict-path test above documents intent but does NOT fail on pre-fix code
    because _FakeRow.__iter__ yields values (not keys), so dict(zip(cols, row))
    is always correct.  This test adds the actual guard: it intercepts the rows
    that run_query delivers to list_running and asserts each is a genuine tuple.
    A pre-fix codebase would deliver _FakeRow objects here, failing the
    ``type(row) is tuple`` check.
    """
    from fabric_dw.sql import run_query as _run_query  # noqa: PLC0415

    target = _make_target()
    fake_row = _FakeRow(*_ROW_1_TUPLE)
    conn = _make_conn([fake_row], _COLS)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    captured_rows: list[object] = []

    def _spy(t, sql, **kw):  # type: ignore[return]
        cols, rows = _run_query(t, sql, **kw)
        captured_rows.extend(rows)
        return cols, rows

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        patch("fabric_dw.services.queries.run_query", side_effect=_spy),
    ):
        await queries.list_running(target)

    assert len(captured_rows) == 1
    assert type(captured_rows[0]) is tuple, (
        f"run_query must return real tuples, got {type(captured_rows[0])}"
    )


# ---------------------------------------------------------------------------
# list_locks
# ---------------------------------------------------------------------------

_LOCK_COLS = [
    "session_id",
    "resource_type",
    "request_mode",
    "request_status",
    "schema_name",
    "object_name",
    "blocking_session_id",
    "wait_type",
    "wait_time",
    "command",
]

_LOCK_ROW_1 = (
    42,
    "OBJECT",
    "S",
    "GRANT",
    "dbo",
    "sales",
    None,
    None,
    None,
    "SELECT",
)

_LOCK_ROW_2 = (
    99,
    "KEY",
    "X",
    "WAIT",
    "dbo",
    "orders",
    42,
    "LCK_M_X",
    1500,
    "INSERT",
)


async def test_list_locks_returns_empty_when_no_rows() -> None:
    target = _make_target()
    conn = _make_conn([], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_locks(target)

    assert result == []


async def test_list_locks_returns_query_lock_instances() -> None:
    target = _make_target()
    conn = _make_conn([_LOCK_ROW_1], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_locks(target)

    assert len(result) == 1
    assert isinstance(result[0], QueryLock)


async def test_list_locks_parses_fields_correctly() -> None:
    target = _make_target()
    conn = _make_conn([_LOCK_ROW_1], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_locks(target)

    lock = result[0]
    assert lock.session_id == 42
    assert lock.resource_type == "OBJECT"
    assert lock.request_mode == "S"
    assert lock.request_status == "GRANT"
    assert lock.schema_name == "dbo"
    assert lock.object_name == "sales"
    assert lock.blocking_session_id is None
    assert lock.wait_type is None
    assert lock.wait_time_ms is None
    assert lock.command == "SELECT"


async def test_list_locks_parses_blocking_fields() -> None:
    target = _make_target()
    conn = _make_conn([_LOCK_ROW_2], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_locks(target)

    lock = result[0]
    assert lock.session_id == 99
    assert lock.blocking_session_id == 42
    assert lock.wait_type == "LCK_M_X"
    assert lock.wait_time_ms == 1500


async def test_list_locks_returns_all_rows() -> None:
    target = _make_target()
    conn = _make_conn([_LOCK_ROW_1, _LOCK_ROW_2], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await queries.list_locks(target)

    assert len(result) == 2
    assert result[0].session_id == 42
    assert result[1].session_id == 99


async def test_list_locks_sql_references_dm_tran_locks() -> None:
    target = _make_target()
    conn = _make_conn([], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_locks(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "sys.dm_tran_locks" in call_sql


async def test_list_locks_sql_uses_left_join_exec_requests() -> None:
    target = _make_target()
    conn = _make_conn([], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_locks(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "LEFT JOIN" in call_sql.upper()
    assert "sys.dm_exec_requests" in call_sql


async def test_list_locks_default_excludes_database_rows() -> None:
    target = _make_target()
    conn = _make_conn([], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_locks(target)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "<> 'DATABASE'" in call_sql


async def test_list_locks_include_database_removes_filter() -> None:
    target = _make_target()
    conn = _make_conn([], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_locks(target, include_database=True)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "<> 'DATABASE'" not in call_sql


async def test_list_locks_waiting_only_adds_where_clause() -> None:
    target = _make_target()
    conn = _make_conn([], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_locks(target, waiting_only=True)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "IN ('WAIT', 'CONVERT')" in call_sql


async def test_list_locks_blocked_only_adds_where_clause() -> None:
    target = _make_target()
    conn = _make_conn([], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_locks(target, blocked_only=True)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    # Verify the WHERE predicate specifically, not just the SELECT column name
    assert "blocking_session_id IS NOT NULL" in call_sql


async def test_list_locks_limit_clamped_to_minimum() -> None:
    target = _make_target()
    conn = _make_conn([], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_locks(target, limit=0)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (1)" in call_sql


async def test_list_locks_limit_clamped_to_maximum() -> None:
    target = _make_target()
    conn = _make_conn([], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_locks(target, limit=99999)

    cursor = conn.cursor.return_value
    call_sql: str = cursor.execute.call_args[0][0]
    assert "TOP (10000)" in call_sql


async def test_list_locks_closes_connection_after_success() -> None:
    target = _make_target()
    conn = _make_conn([_LOCK_ROW_1], _LOCK_COLS)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await queries.list_locks(target)

    conn.close.assert_called_once()
