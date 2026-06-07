"""Tests for fabric_dw.sql_client — written before implementation (TDD)."""

import threading
from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest

import fabric_dw.sql_client as _sql_client_module
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import AuthError, PermissionDenied
from fabric_dw.sql_client import FabricSqlClient, SqlTarget

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_target(
    workspace_id: str = "ws-1",
    database: str = "db-1",
    connection_string: str = "Server=myserver.database.fabric.microsoft.com",
) -> SqlTarget:
    return SqlTarget(
        workspace_id=workspace_id,
        database=database,
        connection_string=connection_string,
    )


def _make_mock_mssql() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Return (mssql_module, mock_connection, mock_cursor)."""
    mock_cursor = MagicMock()
    mock_cursor.description = [("col1", None), ("col2", None)]
    mock_cursor.fetchall.return_value = [(1, "hello"), (2, "world")]
    mock_cursor.rowcount = 2

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    mock_mssql = MagicMock()
    mock_mssql.connect.return_value = mock_conn

    return mock_mssql, mock_conn, mock_cursor


def _patch_mssql(monkeypatch: pytest.MonkeyPatch, mock_mssql: MagicMock) -> None:
    """Replace the _mssql attribute on sql_client with the mock."""
    monkeypatch.setattr(_sql_client_module, "_mssql", mock_mssql)


# ---------------------------------------------------------------------------
# SqlTarget dataclass
# ---------------------------------------------------------------------------


class TestSqlTarget:
    def test_is_frozen(self) -> None:
        target = _make_target()
        with pytest.raises((AttributeError, TypeError)):
            target.workspace_id = "other"  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        t1 = _make_target()
        t2 = _make_target()
        assert t1 == t2

    def test_hashable(self) -> None:
        target = _make_target()
        assert hash(target) is not None
        _ = {target}  # usable as dict key / set member


# ---------------------------------------------------------------------------
# Connection-string augmenter
# ---------------------------------------------------------------------------


class TestConnectionStringAugmenter:
    """Test _augment_connection_string (accessed indirectly via client behaviour)."""

    def _get_augment_fn(self) -> Any:
        return _sql_client_module._augment_connection_string  # noqa: SLF001,RUF100

    def test_default_mode_adds_active_directory_default(self) -> None:
        augment = self._get_augment_fn()
        result = augment("Server=srv", "mydb", CredentialMode.DEFAULT)
        assert "Authentication=ActiveDirectoryDefault" in result
        assert ";;" not in result

    def test_sp_mode_adds_active_directory_service_principal(self) -> None:
        augment = self._get_augment_fn()
        result = augment("Server=srv", "mydb", CredentialMode.SERVICE_PRINCIPAL)
        assert "Authentication=ActiveDirectoryServicePrincipal" in result
        assert ";;" not in result

    def test_interactive_mode_adds_active_directory_interactive(self) -> None:
        augment = self._get_augment_fn()
        result = augment("Server=srv", "mydb", CredentialMode.INTERACTIVE)
        assert "Authentication=ActiveDirectoryInteractive" in result
        assert ";;" not in result

    def test_adds_encrypt_yes(self) -> None:
        augment = self._get_augment_fn()
        result = augment("Server=srv", "mydb", CredentialMode.DEFAULT)
        assert "Encrypt=yes" in result
        assert ";;" not in result

    def test_adds_trust_server_certificate_no(self) -> None:
        augment = self._get_augment_fn()
        result = augment("Server=srv", "mydb", CredentialMode.DEFAULT)
        assert "TrustServerCertificate=no" in result
        assert ";;" not in result

    def test_adds_database_when_not_present(self) -> None:
        augment = self._get_augment_fn()
        result = augment("Server=srv", "mydb", CredentialMode.DEFAULT)
        assert "Database=mydb" in result
        assert ";;" not in result

    def test_does_not_double_add_database_when_already_present(self) -> None:
        augment = self._get_augment_fn()
        cs = "Server=srv;Database=existing-db"
        result = augment(cs, "different-db", CredentialMode.DEFAULT)
        # Database= must appear exactly once in the string
        assert result.count("Database=") == 1
        assert ";;" not in result

    def test_no_double_semicolons_in_full_augmented_string(self) -> None:
        augment = self._get_augment_fn()
        result = augment(
            "Server=myserver.database.fabric.microsoft.com", "mydb", CredentialMode.DEFAULT
        )
        assert ";;" not in result

    def test_idempotent_same_output_on_second_call(self) -> None:
        augment = self._get_augment_fn()
        first = augment("Server=srv", "mydb", CredentialMode.DEFAULT)
        second = augment(first, "mydb", CredentialMode.DEFAULT)
        assert first == second
        assert ";;" not in first

    def test_idempotent_all_modes(self) -> None:
        augment = self._get_augment_fn()
        for mode in CredentialMode:
            first = augment("Server=srv", "mydb", mode)
            second = augment(first, "mydb", mode)
            assert first == second, f"Not idempotent for mode={mode}"
            assert ";;" not in first, f"Double semicolons for mode={mode}"


# ---------------------------------------------------------------------------
# execute — returns list[dict]
# ---------------------------------------------------------------------------


class TestExecute:
    async def test_returns_list_of_dicts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        rows = await client.execute(_make_target(), "SELECT 1")
        await client.close()

        assert rows == [{"col1": 1, "col2": "hello"}, {"col1": 2, "col2": "world"}]

    async def test_uses_cursor_description_for_column_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_mssql, _conn, mock_cursor = _make_mock_mssql()
        mock_cursor.description = [("name", None), ("value", None)]
        mock_cursor.fetchall.return_value = [("alice", 42)]
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        rows = await client.execute(_make_target(), "SELECT name, value FROM t")
        await client.close()

        assert rows == [{"name": "alice", "value": 42}]

    async def test_passes_params_to_cursor_execute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _conn, mock_cursor = _make_mock_mssql()
        mock_cursor.description = [("id", None)]
        mock_cursor.fetchall.return_value = [(7,)]
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        await client.execute(_make_target(), "SELECT id FROM t WHERE id = ?", (7,))
        await client.close()

        mock_cursor.execute.assert_called_once_with("SELECT id FROM t WHERE id = ?", (7,))

    async def test_empty_result_set_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_mssql, _conn, mock_cursor = _make_mock_mssql()
        mock_cursor.fetchall.return_value = []
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        rows = await client.execute(_make_target(), "SELECT 1 WHERE 1=0")
        await client.close()

        assert rows == []


# ---------------------------------------------------------------------------
# execute_nonquery — commits and returns rowcount
# ---------------------------------------------------------------------------


class TestExecuteNonQuery:
    async def test_returns_rowcount(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _conn, mock_cursor = _make_mock_mssql()
        mock_cursor.rowcount = 5
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        count = await client.execute_nonquery(_make_target(), "DELETE FROM t WHERE id > 0")
        await client.close()

        assert count == 5

    async def test_commits_after_execute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, mock_conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        await client.execute_nonquery(_make_target(), "DELETE FROM t")
        await client.close()

        mock_conn.commit.assert_called_once()

    async def test_passes_params_to_cursor_execute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _conn, mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        await client.execute_nonquery(_make_target(), "DELETE FROM t WHERE id = ?", (42,))
        await client.close()

        mock_cursor.execute.assert_called_once_with("DELETE FROM t WHERE id = ?", (42,))


# ---------------------------------------------------------------------------
# Thread isolation — blocking driver call must run off the event-loop thread
# ---------------------------------------------------------------------------


class TestAsyncThreadIsolation:
    async def test_execute_runs_cursor_on_non_event_loop_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        event_loop_thread_id = threading.get_ident()
        captured_ids: list[int] = []

        mock_mssql, _conn, mock_cursor = _make_mock_mssql()

        def capturing_execute(_sql: str, _params: Sequence[Any] = ()) -> None:
            captured_ids.append(threading.get_ident())

        mock_cursor.execute.side_effect = capturing_execute
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        await client.execute(_make_target(), "SELECT 1")
        await client.close()

        assert len(captured_ids) >= 1
        assert all(tid != event_loop_thread_id for tid in captured_ids)

    async def test_execute_nonquery_runs_cursor_on_non_event_loop_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        event_loop_thread_id = threading.get_ident()
        captured_ids: list[int] = []

        mock_mssql, _conn, mock_cursor = _make_mock_mssql()

        def capturing_execute(_sql: str, _params: Sequence[Any] = ()) -> None:
            captured_ids.append(threading.get_ident())

        mock_cursor.execute.side_effect = capturing_execute
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        await client.execute_nonquery(_make_target(), "DELETE FROM t")
        await client.close()

        assert len(captured_ids) >= 1
        assert all(tid != event_loop_thread_id for tid in captured_ids)


# ---------------------------------------------------------------------------
# Connection caching
# ---------------------------------------------------------------------------


class TestConnectionCaching:
    async def test_two_execute_calls_same_target_open_one_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        target = _make_target()
        client = FabricSqlClient()
        await client.execute(target, "SELECT 1")
        await client.execute(target, "SELECT 2")
        await client.close()

        assert mock_mssql.connect.call_count == 1

    async def test_execute_calls_on_different_targets_open_two_connections(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        target1 = _make_target(workspace_id="ws-1", database="db-1")
        target2 = _make_target(workspace_id="ws-2", database="db-2")
        client = FabricSqlClient()
        await client.execute(target1, "SELECT 1")
        await client.execute(target2, "SELECT 2")
        await client.close()

        assert mock_mssql.connect.call_count == 2

    async def test_different_database_same_workspace_opens_two_connections(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        target1 = _make_target(workspace_id="ws-1", database="db-A")
        target2 = _make_target(workspace_id="ws-1", database="db-B")
        client = FabricSqlClient()
        await client.execute(target1, "SELECT 1")
        await client.execute(target2, "SELECT 1")
        await client.close()

        assert mock_mssql.connect.call_count == 2


# ---------------------------------------------------------------------------
# close()  # noqa: ERA001
# ---------------------------------------------------------------------------


class TestClose:
    async def test_close_closes_all_cached_connections(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn1 = MagicMock()
        conn2 = MagicMock()
        cursor = MagicMock()
        cursor.description = [("x", None)]
        cursor.fetchall.return_value = [(1,)]
        cursor.rowcount = 1
        conn1.cursor.return_value = cursor
        conn2.cursor.return_value = cursor

        mock_mssql = MagicMock()
        mock_mssql.connect.side_effect = [conn1, conn2]
        _patch_mssql(monkeypatch, mock_mssql)

        target1 = _make_target(workspace_id="ws-1", database="db-1")
        target2 = _make_target(workspace_id="ws-1", database="db-2")

        client = FabricSqlClient()
        await client.execute(target1, "SELECT 1")
        await client.execute(target2, "SELECT 1")
        await client.close()

        conn1.close.assert_called_once()
        conn2.close.assert_called_once()

    async def test_close_still_closes_second_connection_when_first_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If one conn.close() raises, the other connection is still closed."""
        conn1 = MagicMock()
        conn2 = MagicMock()
        cursor = MagicMock()
        cursor.description = [("x", None)]
        cursor.fetchall.return_value = [(1,)]
        cursor.rowcount = 1
        conn1.cursor.return_value = cursor
        conn2.cursor.return_value = cursor
        first_error = RuntimeError("close failed on conn1")
        conn1.close.side_effect = first_error

        mock_mssql = MagicMock()
        mock_mssql.connect.side_effect = [conn1, conn2]
        _patch_mssql(monkeypatch, mock_mssql)

        target1 = _make_target(workspace_id="ws-1", database="db-1")
        target2 = _make_target(workspace_id="ws-1", database="db-2")

        client = FabricSqlClient()
        await client.execute(target1, "SELECT 1")
        await client.execute(target2, "SELECT 1")

        with pytest.raises(ExceptionGroup) as exc_info:
            await client.close()

        # second connection must have been closed regardless
        conn2.close.assert_called_once()
        # the error from conn1 is wrapped in ExceptionGroup
        assert exc_info.value.exceptions[0] is first_error

    async def test_close_clears_cache_so_next_execute_reopens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        target = _make_target()
        client = FabricSqlClient()
        await client.execute(target, "SELECT 1")
        await client.close()
        await client.execute(target, "SELECT 2")
        await client.close()

        assert mock_mssql.connect.call_count == 2


# ---------------------------------------------------------------------------
# Async context manager
# ---------------------------------------------------------------------------


class TestAsyncContextManager:
    async def test_aenter_returns_self(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        async with client as ctx:
            assert ctx is client

    async def test_aexit_calls_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, mock_conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        target = _make_target()
        async with FabricSqlClient() as client:
            await client.execute(target, "SELECT 1")

        mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# AuthError mapping
# ---------------------------------------------------------------------------


class TestAuthErrorMapping:
    async def test_driver_auth_error_raises_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exception from the driver that looks like an auth error → AuthError."""
        mock_mssql, _conn, _cursor = _make_mock_mssql()

        # Simulate a driver-level exception on connect; use "authentication failed"
        # which is the exact phrase real Entra-token errors use.
        driver_exc = Exception("Authentication failed for user '' (token-based)")
        mock_mssql.connect.side_effect = driver_exc
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        with pytest.raises(AuthError):
            await client.execute(_make_target(), "SELECT 1")

    async def test_non_auth_error_not_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Generic driver errors are not silently swallowed or re-wrapped."""
        mock_mssql, _conn, _cursor = _make_mock_mssql()

        driver_exc = RuntimeError("connection timed out")
        mock_mssql.connect.side_effect = driver_exc
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        with pytest.raises(RuntimeError):
            await client.execute(_make_target(), "SELECT 1")


# ---------------------------------------------------------------------------
# execute / execute_nonquery — cursor.execute error mapping
# ---------------------------------------------------------------------------


class TestCursorExecuteErrorMapping:
    async def test_execute_nonquery_permission_denied_from_cursor_execute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cursor.execute raising 'permission was denied' → PermissionDenied."""
        mock_mssql, _conn, mock_cursor = _make_mock_mssql()
        driver_exc = Exception(
            "The server principal does not have permission was denied on the object"
        )
        mock_cursor.execute.side_effect = driver_exc
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        with pytest.raises(PermissionDenied):
            await client.execute_nonquery(_make_target(), "KILL '42'")

    async def test_execute_cursor_auth_error_raises_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cursor.execute raising 'authentication failed' → AuthError."""
        mock_mssql, _conn, mock_cursor = _make_mock_mssql()
        driver_exc = Exception("Authentication failed for user '' (token-based)")
        mock_cursor.execute.side_effect = driver_exc
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        with pytest.raises(AuthError):
            await client.execute(_make_target(), "SELECT 1")

    async def test_cursor_execute_unrelated_error_propagates_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A driver exception that doesn't match any fragments propagates as-is."""
        mock_mssql, _conn, mock_cursor = _make_mock_mssql()
        driver_exc = RuntimeError("deadlock detected")
        mock_cursor.execute.side_effect = driver_exc
        _patch_mssql(monkeypatch, mock_mssql)

        client = FabricSqlClient()
        with pytest.raises(RuntimeError, match="deadlock detected"):
            await client.execute(_make_target(), "SELECT 1")
