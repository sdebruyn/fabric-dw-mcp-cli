"""Tests for fabric_dw.sql — stateless SQL helper (TDD, written before implementation)."""

from __future__ import annotations

import threading
from contextlib import closing
from unittest.mock import MagicMock

import pytest

import fabric_dw.sql as _sql_module
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import AuthError, PermissionDeniedError
from fabric_dw.sql import (
    SqlTarget,
    build_connection_string,
    map_driver_error,
    run_query,
    run_statements,
)

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
    """Replace the _mssql attribute on sql module with the mock."""
    monkeypatch.setattr(_sql_module, "_mssql", mock_mssql)


# ---------------------------------------------------------------------------
# SqlTarget dataclass
# ---------------------------------------------------------------------------


class TestSqlTarget:
    def test_is_frozen(self) -> None:
        target = _make_target()
        with pytest.raises((AttributeError, TypeError)):
            target.workspace_id = "other"  # ty: ignore[invalid-assignment]

    def test_equality_by_value(self) -> None:
        t1 = _make_target()
        t2 = _make_target()
        assert t1 == t2

    def test_hashable(self) -> None:
        target = _make_target()
        assert hash(target) is not None
        _ = {target}  # usable as dict key / set member


# ---------------------------------------------------------------------------
# build_connection_string — augmenter
# ---------------------------------------------------------------------------


class TestBuildConnectionString:
    """Test build_connection_string."""

    def test_default_mode_adds_active_directory_default(self) -> None:
        result = build_connection_string(_make_target(), mode=CredentialMode.DEFAULT)
        assert "Authentication=ActiveDirectoryDefault" in result
        assert ";;" not in result

    def test_sp_mode_adds_active_directory_service_principal(self) -> None:
        result = build_connection_string(_make_target(), mode=CredentialMode.SERVICE_PRINCIPAL)
        assert "Authentication=ActiveDirectoryServicePrincipal" in result
        assert ";;" not in result

    def test_interactive_mode_adds_active_directory_interactive(self) -> None:
        result = build_connection_string(_make_target(), mode=CredentialMode.INTERACTIVE)
        assert "Authentication=ActiveDirectoryInteractive" in result
        assert ";;" not in result

    def test_adds_encrypt_yes(self) -> None:
        result = build_connection_string(_make_target())
        assert "Encrypt=yes" in result
        assert ";;" not in result

    def test_adds_trust_server_certificate_no(self) -> None:
        result = build_connection_string(_make_target())
        assert "TrustServerCertificate=no" in result
        assert ";;" not in result

    def test_adds_database_from_target(self) -> None:
        target = _make_target(database="mydb")
        result = build_connection_string(target)
        assert "Database=mydb" in result
        assert ";;" not in result

    def test_does_not_double_add_database_when_already_present(self) -> None:
        target = _make_target(
            database="mydb",
            connection_string="Server=srv;Database=existing-db",
        )
        result = build_connection_string(target)
        # Database= must appear exactly once in the string
        assert result.count("Database=") == 1
        assert ";;" not in result

    def test_no_double_semicolons_in_full_augmented_string(self) -> None:
        result = build_connection_string(_make_target())
        assert ";;" not in result

    def test_idempotent_same_output_on_second_call(self) -> None:
        target = _make_target()
        first = build_connection_string(target)
        # Build a new target with the augmented connection string and same database
        target2 = SqlTarget(
            workspace_id=target.workspace_id,
            database=target.database,
            connection_string=first,
        )
        second = build_connection_string(target2)
        assert first == second
        assert ";;" not in first

    def test_idempotent_all_modes(self) -> None:
        target = _make_target()
        for mode in CredentialMode:
            first = build_connection_string(target, mode=mode)
            target2 = SqlTarget(
                workspace_id=target.workspace_id,
                database=target.database,
                connection_string=first,
            )
            second = build_connection_string(target2, mode=mode)
            assert first == second, f"Not idempotent for mode={mode}"
            assert ";;" not in first, f"Double semicolons for mode={mode}"

    def test_uses_target_connection_string_as_base(self) -> None:
        target = _make_target(connection_string="Server=custom.host.com")
        result = build_connection_string(target)
        assert "Server=custom.host.com" in result

    def test_bare_fqdn_gets_server_prefix(self) -> None:
        target = _make_target(connection_string="myhost.datawarehouse.fabric.microsoft.com")
        result = build_connection_string(target)
        assert result.startswith("Server=myhost.datawarehouse.fabric.microsoft.com")

    def test_bare_fqdn_server_prefix_idempotent(self) -> None:
        target = _make_target(connection_string="myhost.datawarehouse.fabric.microsoft.com")
        first = build_connection_string(target)
        target2 = SqlTarget(
            workspace_id=target.workspace_id,
            database=target.database,
            connection_string=first,
        )
        second = build_connection_string(target2)
        assert first == second
        assert ";;" not in first

    def test_already_has_server_prefix_not_duplicated(self) -> None:
        target = _make_target(connection_string="Server=myhost.datawarehouse.fabric.microsoft.com")
        result = build_connection_string(target)
        assert result.count("Server=") == 1


# ---------------------------------------------------------------------------
# open_connection — sync, caller closes
# ---------------------------------------------------------------------------


class TestOpenConnection:
    def test_returns_connection_from_driver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        conn = open_connection(_make_target())
        assert conn is mock_conn

    def test_driver_called_with_augmented_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        target = _make_target(database="testdb")
        open_connection(target)

        called_cs: str = mock_mssql.connect.call_args[0][0]
        assert "Database=testdb" in called_cs
        assert "Authentication=" in called_cs

    def test_connection_usable_with_closing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """open_connection result works inside contextlib.closing."""
        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        with closing(open_connection(_make_target())):
            pass

        mock_conn.close.assert_called_once()

    def test_each_call_opens_new_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """open_connection is stateless — no caching."""
        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        target = _make_target()
        c1 = open_connection(target)
        c2 = open_connection(target)
        assert mock_mssql.connect.call_count == 2
        c1.close()
        c2.close()

    def test_blocking_call_off_event_loop_thread_is_callers_responsibility(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """open_connection is sync — it runs on whichever thread calls it.

        Services wrap it in asyncio.to_thread. This test just checks that
        the function itself runs synchronously (call count increments synchronously).
        """
        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        thread_ids: list[int] = []

        def _capture_connect(_cs: str) -> MagicMock:
            thread_ids.append(threading.get_ident())
            return MagicMock()

        mock_mssql.connect.side_effect = _capture_connect

        open_connection(_make_target())
        assert len(thread_ids) == 1
        assert thread_ids[0] == threading.get_ident()


# ---------------------------------------------------------------------------
# map_driver_error — error classifier
# ---------------------------------------------------------------------------


class TestMapDriverError:
    def test_permission_denied_fragment_returns_permission_denied(self) -> None:
        exc = Exception("The principal does not have permission was denied on object X")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_denied_the_right_to_fragment_returns_permission_denied(self) -> None:
        exc = Exception("denied the right to execute")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_auth_failed_fragment_returns_auth_error(self) -> None:
        exc = Exception("Authentication failed for user '' (token-based)")
        result = map_driver_error(exc)
        assert isinstance(result, AuthError)

    def test_unrelated_error_returns_none(self) -> None:
        exc = Exception("connection timed out")
        result = map_driver_error(exc)
        assert result is None

    def test_deadlock_returns_none(self) -> None:
        exc = RuntimeError("deadlock detected")
        result = map_driver_error(exc)
        assert result is None

    def test_permission_denied_wins_over_auth_when_both_match(self) -> None:
        # A contrived message that contains both a permission fragment and auth fragment.
        # Permission-denied check must come first (as in the issue spec).
        exc = Exception("permission was denied authentication failed")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_case_insensitive_matching(self) -> None:
        exc = Exception("PERMISSION WAS DENIED on the object")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_result_message_contains_original(self) -> None:
        original_msg = "permission was denied on SELECT"
        exc = Exception(original_msg)
        result = map_driver_error(exc)
        assert result is not None
        assert original_msg in str(result)

    # --- Native error number tests (strategy 1: locale-independent) ---

    @staticmethod
    def _make_driver_exc(msg: str, ddbc_error: str) -> BaseException:
        """Build a mock driver exception with a ``ddbc_error`` attribute."""
        exc = MagicMock(spec=Exception)
        exc.__str__ = MagicMock(return_value=msg)
        exc.ddbc_error = ddbc_error
        return exc  # type: ignore[return-value]

    def test_native_error_229_returns_permission_denied(self) -> None:
        """Error number 229 (SELECT permission denied) → PermissionDeniedError."""
        exc = self._make_driver_exc("some driver error", "Error: 229 SELECT permission denied")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_native_error_230_returns_permission_denied(self) -> None:
        """Error number 230 (INSERT permission denied) → PermissionDeniedError."""
        exc = self._make_driver_exc("some driver error", "(230) INSERT permission denied")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_native_error_297_returns_permission_denied(self) -> None:
        """Error number 297 (execute permission denied) → PermissionDeniedError."""
        exc = self._make_driver_exc("some driver error", "Error: 297 execute permission denied")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_native_error_18456_returns_auth_error(self) -> None:
        """Error number 18456 (login failed) → AuthError."""
        exc = self._make_driver_exc("login failed", "[SQL Server]Login failed. Error: 18456")
        result = map_driver_error(exc)
        assert isinstance(result, AuthError)

    def test_native_permission_wins_over_fragment_auth(self) -> None:
        """Native error 229 beats an auth fragment in the message string."""
        exc = self._make_driver_exc("authentication failed error", "Error: 229 permission")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_unrecognised_native_error_falls_through_to_fragment(self) -> None:
        """An unrecognised native error number falls through to fragment matching."""
        exc = self._make_driver_exc("permission was denied for this object", "Error: 9999 unknown")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)


# ---------------------------------------------------------------------------
# run_query — param binding
# ---------------------------------------------------------------------------


class TestRunQuery:
    """Tests for run_query: param forwarding, commit, fetch modes, error mapping."""

    def test_params_forwarded_to_cursor_execute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When params are given, cursor.execute is called with the params sequence."""
        mock_mssql, _mock_conn, mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "SELECT * FROM t WHERE id = ?", params=["abc"])

        mock_cursor.execute.assert_called_once_with("SELECT * FROM t WHERE id = ?", ["abc"])

    def test_no_params_calls_execute_without_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When params is None, cursor.execute is called with the SQL only."""
        mock_mssql, _mock_conn, mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "SELECT 1")

        mock_cursor.execute.assert_called_once_with("SELECT 1")

    def test_returns_columns_and_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _mock_conn, mock_cursor = _make_mock_mssql()
        mock_cursor.description = [("col1", None), ("col2", None)]
        mock_cursor.fetchall.return_value = [(1, "a"), (2, "b")]
        _patch_mssql(monkeypatch, mock_mssql)

        cols, rows = run_query(_make_target(), "SELECT col1, col2 FROM t")

        assert cols == ["col1", "col2"]
        assert rows == [(1, "a"), (2, "b")]

    def test_commit_true_calls_commit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, mock_conn, _mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "INSERT INTO t VALUES (1)", commit=True, fetch="none")

        mock_conn.commit.assert_called_once()

    def test_commit_false_does_not_call_commit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, mock_conn, _mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "SELECT 1", commit=False)

        mock_conn.commit.assert_not_called()

    def test_fetch_none_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        cols, rows = run_query(_make_target(), "DELETE FROM t", fetch="none")

        assert cols == []
        assert rows == []

    def test_connection_closed_after_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "SELECT 1")

        mock_conn.close.assert_called_once()

    def test_connection_closed_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, mock_conn, mock_cursor = _make_mock_mssql()
        mock_cursor.execute.side_effect = Exception("boom")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="boom"):
            run_query(_make_target(), "SELECT 1")

        mock_conn.close.assert_called_once()

    def test_permission_denied_fragment_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        mock_cursor.execute.side_effect = Exception("permission was denied on object X")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(PermissionDeniedError):
            run_query(_make_target(), "SELECT * FROM X")

    def test_auth_error_fragment_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        mock_cursor.execute.side_effect = Exception("Authentication failed for user ''")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(AuthError):
            run_query(_make_target(), "SELECT 1")

    def test_multiple_params_bound_correctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multiple ? placeholders are passed as a sequence."""
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "SELECT * FROM t WHERE a = ? AND b = ?", params=["x", 42])

        mock_cursor.execute.assert_called_once_with(
            "SELECT * FROM t WHERE a = ? AND b = ?", ["x", 42]
        )


# ---------------------------------------------------------------------------
# run_statements — single connection for all DDL
# ---------------------------------------------------------------------------


class TestRunStatements:
    """run_statements must use ONE connection for all statements."""

    def test_all_statements_use_single_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """connect() is called exactly once regardless of statement count."""
        mock_mssql, _mock_conn, _mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_statements(
            _make_target(),
            ["DROP TABLE [dbo].[t1]", "DROP TABLE [dbo].[t2]", "DROP TABLE [dbo].[t3]"],
        )

        # Only one TCP connection was opened.
        assert mock_mssql.connect.call_count == 1

    def test_each_statement_is_executed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, _mock_conn, mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        stmts = ["DROP VIEW [dbo].[v1]", "DROP TABLE [dbo].[t1]"]
        run_statements(_make_target(), stmts)

        assert mock_cursor.execute.call_count == 2
        mock_cursor.execute.assert_any_call("DROP VIEW [dbo].[v1]")
        mock_cursor.execute.assert_any_call("DROP TABLE [dbo].[t1]")

    def test_commit_called_after_each_statement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, mock_conn, _mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_statements(_make_target(), ["DROP TABLE [a]", "DROP TABLE [b]"])

        assert mock_conn.commit.call_count == 2

    def test_connection_closed_after_all_statements(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_statements(_make_target(), ["DROP TABLE [dbo].[t]"])

        mock_conn.close.assert_called_once()

    def test_error_on_first_statement_closes_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_mssql, mock_conn, mock_cursor = _make_mock_mssql()
        mock_cursor.execute.side_effect = Exception("permission was denied")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(PermissionDeniedError):
            run_statements(_make_target(), ["DROP TABLE [dbo].[t]"])

        mock_conn.close.assert_called_once()

    def test_empty_statements_opens_and_closes_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_statements(_make_target(), [])

        assert mock_mssql.connect.call_count == 1
        mock_conn.close.assert_called_once()
