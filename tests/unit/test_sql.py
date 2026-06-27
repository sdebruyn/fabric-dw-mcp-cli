"""Tests for fabric_dw.sql — stateless SQL helper (TDD, written before implementation)."""

from __future__ import annotations

import threading
from contextlib import closing
from unittest.mock import MagicMock

import pytest

import fabric_dw.sql as _sql_module
from fabric_dw.auth import CredentialMode
from fabric_dw.config import Defaults, UserConfig
from fabric_dw.exceptions import AuthError, FabricServerError, NotFoundError, PermissionDeniedError
from fabric_dw.sql import (
    SqlTarget,
    build_connection_string,
    is_auth_failed_message,
    is_transient_connection_error,
    map_driver_error,
    reset_pool,
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
# Autouse fixture: disable pooling for legacy tests that check physical closes
#
# The connection pool intercepts .close() calls and returns connections to the
# pool instead of physically closing them.  Legacy tests that assert
# mock_conn.close.assert_called_once() were written before pooling existed.
# This autouse fixture disables the pool globally for every test in this
# module and drains any leftover pool state, so those tests remain intact.
# Pool-specific tests below re-enable pooling locally with monkeypatch.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_pool_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the connection pool for all tests unless they opt in."""
    monkeypatch.setenv("FABRIC_CONN_POOLING", "0")
    reset_pool()


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
        # The returned object is a _PooledConnection wrapper; its _raw is the mock_conn.
        assert conn._raw is mock_conn  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

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

        def _capture_connect(_cs: str, **_kwargs: object) -> MagicMock:
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
        """Error number 229 (SELECT permission denied) -> PermissionDeniedError."""
        exc = self._make_driver_exc("some driver error", "Error: 229 SELECT permission denied")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_native_error_230_returns_permission_denied(self) -> None:
        """Error number 230 (INSERT permission denied) -> PermissionDeniedError.

        D03: parenthesised form requires an anchor word (SQL Server/Msg/Error)
        before the parenthesised number so that incidental numbers in port or
        byte-count text are not mis-matched.
        """
        exc = self._make_driver_exc(
            "some driver error",
            "[SQL Server]INSERT permission denied. Error (230)",
        )
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_native_error_297_returns_permission_denied(self) -> None:
        """Error number 297 (execute permission denied) -> PermissionDeniedError."""
        exc = self._make_driver_exc("some driver error", "Error: 297 execute permission denied")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_native_error_18456_returns_auth_error(self) -> None:
        """Error number 18456 (login failed) -> AuthError."""
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

    # --- NotFoundError mapping (BUG 2 regression) ---

    def test_native_error_208_returns_not_found(self) -> None:
        """Error number 208 (Invalid object name) -> NotFoundError."""
        exc = self._make_driver_exc("some driver error", "Error: 208 Invalid object name 'dbo.x'")
        result = map_driver_error(exc)
        assert isinstance(result, NotFoundError)

    def test_native_error_208_parenthesised_returns_not_found(self) -> None:
        """Error number 208 in parenthesised form -> NotFoundError.

        D03: parenthesised form requires a SQL Server/Msg/Error anchor word
        to avoid matching incidental numbers (port numbers, row counts, etc.).
        """
        exc = self._make_driver_exc(
            "some driver error",
            "[SQL Server] Error (208) Invalid object name",
        )
        result = map_driver_error(exc)
        assert isinstance(result, NotFoundError)

    def test_native_error_3701_returns_not_found(self) -> None:
        """Error number 3701 (Cannot drop … because it does not exist) -> NotFoundError.

        3701 is emitted by DROP FUNCTION / DROP PROCEDURE / DROP VIEW when the
        named object does not exist.  Adding it to _NOT_FOUND_ERROR_NUMBERS
        means drop_function (and other drop operations) can use the
        NotFoundError mapping instead of a catalog pre-check.
        """
        exc = self._make_driver_exc(
            "Cannot drop the function 'dbo.fn_nope' because it does not exist",
            "Error: 3701 Cannot drop the function 'dbo.fn_nope'",
        )
        result = map_driver_error(exc)
        assert isinstance(result, NotFoundError)

    def test_fragment_cannot_drop_the_returns_not_found(self) -> None:
        """Message containing 'cannot drop the' -> NotFoundError (fragment fallback)."""
        exc = Exception("Cannot drop the function 'fn_nope' because it does not exist")
        result = map_driver_error(exc)
        assert isinstance(result, NotFoundError)

    def test_fragment_invalid_object_name_returns_not_found(self) -> None:
        """Message containing 'invalid object name' -> NotFoundError."""
        exc = Exception("Invalid object name 'dbo.missing_view'")
        result = map_driver_error(exc)
        assert isinstance(result, NotFoundError)

    def test_fragment_base_table_or_view_not_found_returns_not_found(self) -> None:
        """Message containing 'base table or view not found' -> NotFoundError."""
        exc = Exception("Base table or view not found: 'dbo.missing_table'")
        result = map_driver_error(exc)
        assert isinstance(result, NotFoundError)

    def test_permission_denied_wins_over_not_found(self) -> None:
        """Permission-denied is checked before not-found in both strategies."""
        exc = Exception("permission was denied on invalid object name 'x'")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_auth_error_wins_over_not_found_fragment(self) -> None:
        """Auth is checked before not-found in strategy 2."""
        exc = Exception("authentication failed invalid object name 'x'")
        result = map_driver_error(exc)
        assert isinstance(result, AuthError)

    def test_unrelated_error_still_returns_none(self) -> None:
        """Unrelated errors are still None after adding NotFound checks."""
        exc = Exception("Incorrect syntax near 'SELCT'")
        result = map_driver_error(exc)
        assert result is None

    def test_could_not_login_bare_returns_auth_error(self) -> None:
        """Bare 'Could not login (18456)' without 'authentication failed' -> AuthError."""
        exc = Exception("Could not login (18456)")
        result = map_driver_error(exc)
        assert isinstance(result, AuthError)

    def test_could_not_login_case_insensitive(self) -> None:
        """'COULD NOT LOGIN' matches case-insensitively -> AuthError."""
        exc = Exception("COULD NOT LOGIN because the login failed")
        result = map_driver_error(exc)
        assert isinstance(result, AuthError)


# ---------------------------------------------------------------------------
# is_auth_failed_message — public helper
# ---------------------------------------------------------------------------


class TestIsAuthFailedMessage:
    """Tests for the is_auth_failed_message public helper."""

    def test_authentication_failed_returns_true(self) -> None:
        assert is_auth_failed_message("Authentication failed for user '' (token-based)")

    def test_could_not_login_returns_true(self) -> None:
        assert is_auth_failed_message("Could not login (18456)")

    def test_could_not_login_upper_case_returns_true(self) -> None:
        assert is_auth_failed_message("COULD NOT LOGIN because the login failed")

    def test_unrelated_message_returns_false(self) -> None:
        assert not is_auth_failed_message("connection timed out")

    def test_permission_denied_returns_false(self) -> None:
        assert not is_auth_failed_message("permission was denied")

    def test_empty_string_returns_false(self) -> None:
        assert not is_auth_failed_message("")


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


# ---------------------------------------------------------------------------
# Connection pool tests
# ---------------------------------------------------------------------------


class TestConnectionPool:
    """Tests for the LIFO connection pool integrated into open_connection."""

    @pytest.fixture(autouse=True)
    def _enable_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Enable the pool for all tests in this class and drain it after."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "1")
        reset_pool()

    def test_pool_reuse_same_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two sequential open_connection calls with the same key reuse one underlying conn."""
        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        target = _make_target()
        # First checkout: opens a fresh physical connection.
        conn1 = open_connection(target)
        assert mock_mssql.connect.call_count == 1
        conn1.close()  # returns underlying to pool

        # Second checkout: reuses the pooled connection (connect not called again).
        conn2 = open_connection(target)
        assert mock_mssql.connect.call_count == 1
        assert conn2._raw is mock_conn  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        conn2.close()

    def test_different_keys_do_not_share(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connections for different targets/modes are never shared."""
        mock_mssql, _, _ = _make_mock_mssql()

        conn_a = MagicMock()
        conn_b = MagicMock()
        mock_mssql.connect.side_effect = [conn_a, conn_b]
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        target_a = _make_target(workspace_id="ws-A")
        target_b = _make_target(workspace_id="ws-B")

        ca = open_connection(target_a)
        ca.close()
        cb = open_connection(target_b)
        # Each key always opened its own fresh connection.
        assert mock_mssql.connect.call_count == 2
        assert cb._raw is conn_b  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        cb.close()

    def test_idle_eviction_on_checkout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A connection older than POOL_MAX_IDLE_SECS is discarded on checkout."""
        mock_mssql, mock_conn_old, _ = _make_mock_mssql()
        mock_conn_fresh = MagicMock()
        mock_mssql.connect.side_effect = [mock_conn_old, mock_conn_fresh]
        _patch_mssql(monkeypatch, mock_mssql)

        import fabric_dw.sql as sql_mod  # noqa: PLC0415
        from fabric_dw.sql import open_connection  # noqa: PLC0415

        # Patch _pool_time BEFORE checkin so the stored timestamp is deterministic.
        # _pool_time is called in order:
        #   1. first open_connection → _pool_checkout (pool empty, doesn't use the time)
        #   2. conn1.close() → _pool_checkin stores t=0.0
        #   3. second open_connection → _pool_checkout sees now=idle_limit+1.0
        #      → age = idle_limit+1.0 - 0.0 = idle_limit+1.0 > idle_limit → eviction
        idle_limit = sql_mod.POOL_MAX_IDLE_SECS
        # Provide enough values:
        #   42.0 → first checkout (pool empty, value consumed but irrelevant)
        #   0.0  → checkin timestamp stored for conn_old
        #   idle_limit+1.0 → now during second checkout, triggers eviction
        #   999.0 → checkin timestamp for conn2 after the test
        fake_times = iter([42.0, 0.0, idle_limit + 1.0, 999.0])

        def _fake_time() -> float:
            return next(fake_times)

        monkeypatch.setattr(sql_mod, "_pool_time", _fake_time)

        # Put the first conn into the pool (checkin stores t=0.0).
        conn1 = open_connection(_make_target())
        conn1.close()
        assert mock_mssql.connect.call_count == 1

        # Checkout sees t=idle_limit+1, age=idle_limit+1 > idle_limit — evicts.
        conn2 = open_connection(_make_target())
        assert mock_mssql.connect.call_count == 2
        assert conn2._raw is mock_conn_fresh  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        # The old connection must have been physically closed.
        mock_conn_old.close.assert_called_once()
        conn2.close()

    def test_dead_connection_discarded_on_checkout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A connection whose .closed attribute is truthy is discarded and a fresh one opened."""
        mock_mssql, mock_conn_dead, _ = _make_mock_mssql()
        mock_conn_fresh = MagicMock()
        mock_mssql.connect.side_effect = [mock_conn_dead, mock_conn_fresh]
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        target = _make_target()
        conn1 = open_connection(target)
        conn1.close()  # conn_dead is now pooled

        # Mark the pooled connection as closed (simulates server-side closure).
        mock_conn_dead.closed = 1

        conn2 = open_connection(target)
        assert mock_mssql.connect.call_count == 2, "fresh conn should have been opened"
        assert conn2._raw is mock_conn_fresh  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        conn2.close()

    def test_failed_query_does_not_pool_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After a query exception the tainted connection is NOT returned to the pool."""
        mock_mssql, mock_conn, mock_cursor = _make_mock_mssql()
        mock_cursor.execute.side_effect = Exception("network error")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="network error"):
            run_query(_make_target(), "SELECT 1")

        # The tainted connection must have been physically closed.
        mock_conn.close.assert_called_once()

        # Pool should be empty — the next call must open a fresh connection.
        mock_cursor2 = MagicMock()
        mock_cursor2.description = []
        mock_cursor2.fetchall.return_value = []
        mock_conn2 = MagicMock()
        mock_conn2.cursor.return_value = mock_cursor2
        mock_mssql.connect.return_value = mock_conn2
        mock_cursor2.execute.side_effect = None  # no error this time

        run_query(_make_target(), "SELECT 1", fetch="none")
        assert mock_mssql.connect.call_count == 2

    def test_pool_disabled_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_CONN_POOLING=0 disables the pool — every call opens+closes."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "0")

        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "SELECT 1")
        run_query(_make_target(), "SELECT 1")

        # With pool disabled two calls must open two physical connections.
        assert mock_mssql.connect.call_count == 2
        # Both connections must be physically closed (not pooled).
        assert mock_conn.close.call_count == 2

    def test_reset_pool_physically_closes_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """reset_pool() drains the pool and physically closes every connection."""
        mock_mssql, _, _ = _make_mock_mssql()
        conns = [MagicMock() for _ in range(3)]
        mock_mssql.connect.side_effect = list(conns)
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        target = _make_target()
        # Check out all 3 concurrently (hold them open), then return all to pool.
        # Sequential open/close would reuse the pooled conn each time.
        checked_out = [open_connection(target) for _ in range(3)]
        for c in checked_out:
            c.close()  # return each to pool

        assert mock_mssql.connect.call_count == 3

        reset_pool()

        for conn in conns:
            conn.close.assert_called_once()

    def test_max_idle_cap_per_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pool never grows beyond POOL_MAX_IDLE per key; excess are physically closed."""
        import fabric_dw.sql as sql_mod  # noqa: PLC0415

        original_max = sql_mod.POOL_MAX_IDLE
        sql_mod.POOL_MAX_IDLE = 2
        try:
            mock_mssql, _, _ = _make_mock_mssql()
            conns = [MagicMock() for _ in range(4)]
            mock_mssql.connect.side_effect = list(conns)
            _patch_mssql(monkeypatch, mock_mssql)

            from fabric_dw.sql import _pool, _pool_lock, open_connection  # noqa: PLC0415

            target = _make_target()
            checked_out = [open_connection(target) for _ in range(4)]
            for c in checked_out:
                c.close()  # attempt to pool all 4

            with _pool_lock:
                key = ("ws-1", "db-1", "default")
                pool_size = len(_pool.get(key, []))

            assert pool_size == 2, f"expected 2 idle, got {pool_size}"
            # The two excess connections must have been physically closed.
            closed_count = sum(1 for c in conns if c.close.called)
            assert closed_count == 2
        finally:
            sql_mod.POOL_MAX_IDLE = original_max

    def test_run_statements_checks_out_once_checks_in_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_statements opens exactly one connection for multiple statements."""
        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_statements(_make_target(), ["DROP TABLE [a]", "DROP TABLE [b]", "DROP TABLE [c]"])

        assert mock_mssql.connect.call_count == 1

    def test_fetchall_error_discards_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A fetchall() error marks the connection as discarded (not pooled)."""
        mock_mssql, mock_conn, mock_cursor = _make_mock_mssql()
        mock_cursor.description = [("col1",)]
        mock_cursor.fetchall.side_effect = Exception("stream interrupted")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="stream interrupted"):
            run_query(_make_target(), "SELECT col1 FROM t")

        # The tainted connection must have been physically closed.
        mock_conn.close.assert_called_once()

        # Pool must be empty — the next call must open a fresh connection.
        mock_cursor2 = MagicMock()
        mock_cursor2.description = []
        mock_cursor2.fetchall.return_value = []
        mock_conn2 = MagicMock()
        mock_conn2.cursor.return_value = mock_cursor2
        mock_mssql.connect.return_value = mock_conn2

        run_query(_make_target(), "SELECT 1", fetch="none")
        assert mock_mssql.connect.call_count == 2

    def test_fetchone_error_discards_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A fetchone() error marks the connection as discarded (not pooled)."""
        mock_mssql, mock_conn, mock_cursor = _make_mock_mssql()
        mock_cursor.description = [("col1",)]
        mock_cursor.fetchone.side_effect = Exception("stream interrupted")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="stream interrupted"):
            run_query(_make_target(), "SELECT col1 FROM t", fetch="one")

        # The tainted connection must have been physically closed.
        mock_conn.close.assert_called_once()

        # Pool must be empty — the next call must open a fresh connection.
        mock_cursor2 = MagicMock()
        mock_cursor2.description = []
        mock_cursor2.fetchone.return_value = None
        mock_conn2 = MagicMock()
        mock_conn2.cursor.return_value = mock_cursor2
        mock_mssql.connect.return_value = mock_conn2

        run_query(_make_target(), "SELECT 1", fetch="none")
        assert mock_mssql.connect.call_count == 2


# ---------------------------------------------------------------------------
# is_transient_connection_error — error classifier
# ---------------------------------------------------------------------------


class TestIsTransientConnectionError:
    """Tests for the transient-error detector used by the retry loop."""

    def test_communication_link_failure_is_transient(self) -> None:
        exc = Exception("Communication link failure")
        assert is_transient_connection_error(exc) is True

    def test_connection_forcibly_closed_is_transient(self) -> None:
        exc = Exception("An existing connection was forcibly closed by the remote host")
        assert is_transient_connection_error(exc) is True

    def test_transport_level_error_is_transient(self) -> None:
        exc = Exception(
            "A transport-level error has occurred when receiving results from the server"
        )
        assert is_transient_connection_error(exc) is True

    def test_tcp_provider_is_transient(self) -> None:
        exc = Exception("TCP Provider: Error code 0x68")
        assert is_transient_connection_error(exc) is True

    def test_database_was_not_found_is_not_transient(self) -> None:
        # "database was not found" was removed from _TRANSIENT_FRAGMENTS because
        # the real Fabric driver wraps it with native error 18456, so
        # map_driver_error() converts it to AuthError before the transient
        # classifier is ever consulted.  A bare message-only exception (no
        # ddbc_error) is therefore also NOT classified as transient.
        exc = Exception(
            "Login failed for user '<token-identified principal>'. Reason: "
            "Authentication was successful, but the database was not found "
            "or you have insufficient permissions to connect to it"
        )
        assert is_transient_connection_error(exc) is False

    def test_auth_error_is_not_transient(self) -> None:
        exc = Exception("Authentication failed for user ''")
        assert is_transient_connection_error(exc) is False

    def test_permission_denied_is_not_transient(self) -> None:
        exc = Exception("permission was denied on object X")
        assert is_transient_connection_error(exc) is False

    def test_syntax_error_is_not_transient(self) -> None:
        exc = Exception("Incorrect syntax near 'SELCT'")
        assert is_transient_connection_error(exc) is False

    def test_generic_boom_is_not_transient(self) -> None:
        exc = Exception("boom")
        assert is_transient_connection_error(exc) is False

    def test_case_insensitive(self) -> None:
        exc = Exception("COMMUNICATION LINK FAILURE")
        assert is_transient_connection_error(exc) is True


# ---------------------------------------------------------------------------
# Transient retry behaviour in run_query / run_statements
# ---------------------------------------------------------------------------


def _make_fake_time_module(*, monotonic_values: list[float] | None = None) -> MagicMock:
    """Return a fake ``time`` module whose ``sleep`` is a no-op.

    Args:
        monotonic_values: If given, ``time.monotonic`` returns successive values
            from this list (then wraps around).  When ``None`` (default), the
            real ``time.monotonic`` is used — suitable for tests that only care
            about sleep call counts, not deadline behaviour.
    """
    import time as _time  # noqa: PLC0415

    fake = MagicMock()
    fake.sleep = MagicMock()  # no-op
    if monotonic_values is not None:
        values_iter = iter(monotonic_values)

        def _next_mono() -> float:
            return next(values_iter)

        fake.monotonic = MagicMock(side_effect=_next_mono)
    else:
        fake.monotonic = MagicMock(side_effect=_time.monotonic)
    return fake


class TestTransientRetry:
    """run_query and run_statements retry on transient errors but not on real errors."""

    @pytest.fixture(autouse=True)
    def _disable_sleep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace the sql module's time reference to avoid actual delays."""
        monkeypatch.setattr(_sql_module, "time", _make_fake_time_module())

    def test_run_query_retries_transient_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A transient error on attempt 1 is retried; attempt 2 succeeds."""
        mock_mssql, _, _ = _make_mock_mssql()

        # First cursor/execute raises transient; second succeeds.
        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor

        good_cursor = MagicMock()
        good_cursor.description = [("n",)]
        good_cursor.fetchall.return_value = [(1,)]
        good_conn = MagicMock()
        good_conn.cursor.return_value = good_cursor

        mock_mssql.connect.side_effect = [bad_conn, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        _cols, rows = run_query(_make_target(), "SELECT 1")

        assert mock_mssql.connect.call_count == 2
        assert rows == [(1,)]

    def test_run_query_does_not_retry_non_transient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-transient error is raised immediately without retrying."""
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        mock_cursor.execute.side_effect = Exception("Incorrect syntax near 'SELCT'")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="Incorrect syntax near"):
            run_query(_make_target(), "SELCT 1")

        # Only one connect attempt — no retry.
        assert mock_mssql.connect.call_count == 1

    def test_run_query_raises_after_budget_exhausted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the time budget is exhausted the last transient error is re-raised.

        The execute-phase retry loop is time-budgeted: it keeps retrying until
        _SQL_RETRY_DEADLINE_S_DEFAULT seconds have elapsed since the first failure.
        With the clock advanced past the deadline immediately after the first
        failure, the attempt-cap/deadline check fires before sleeping and the
        loop stops after the first attempt — no retry connection is opened.

        Clock sequence (injected via _make_fake_time_module):
          - connect-phase _with_connect_retry: call 0 → deadline base
          - first execute fails → call 1 → sets execute_deadline (t=0)
          - call 2 → time.monotonic() check: t > deadline → stop, raise

        Total connect calls: 1 (deadline already expired after first failure).
        """
        # Monotonic values consumed in order:
        #   [0] deadline base inside _with_connect_retry
        #   [1] sets execute_deadline = 0 + _SQL_RETRY_DEADLINE_S_DEFAULT
        #   [2] first deadline check after sleep: returns value past deadline
        # The connect-phase _with_connect_retry for the retry conn also needs
        # a value for its own deadline calculation.
        timeout = _sql_module._SQL_RETRY_DEADLINE_S_DEFAULT  # type: ignore[attr-defined]
        fake_time = _make_fake_time_module(
            monotonic_values=[
                0.0,  # _with_connect_retry deadline base (initial conn)
                0.0,  # execute_deadline = 0 + timeout
                timeout + 1,  # deadline check after first execute failure → expired
            ]
        )
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, _, _ = _make_mock_mssql()
        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor
        mock_mssql.connect.return_value = bad_conn
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="communication link failure"):
            run_query(_make_target(), "SELECT 1")

        # Only 1 connect call: deadline already expired after first failure.
        assert mock_mssql.connect.call_count == 1

    def test_run_query_retries_multiple_times_before_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The retry loop keeps retrying transient execute failures until one succeeds.

        Scenario (3 failures then success):
          connect 1 → execute fails (transient)
          connect 2 → execute fails (transient)
          connect 3 → execute fails (transient)
          connect 4 → execute succeeds
        Total connect calls: 4.  Clock stays within budget throughout.
        """
        timeout = _sql_module._SQL_RETRY_DEADLINE_S_DEFAULT  # type: ignore[attr-defined]
        # Monotonic values:
        #  [0]  _with_connect_retry deadline base (initial conn)
        #  [1]  execute_deadline = 0 + timeout (set on first failure)
        #  [2]  deadline check after failure 1 → within budget
        #  [3]  _with_connect_retry deadline base (retry conn 2)
        #  [4]  deadline check after failure 2 → within budget
        #  [5]  _with_connect_retry deadline base (retry conn 3)
        #  [6]  deadline check after failure 3 → within budget
        #  [7]  _with_connect_retry deadline base (retry conn 4)
        fake_time = _make_fake_time_module(
            monotonic_values=[
                0.0,
                0.0,
                timeout / 2,
                timeout / 2,
                timeout / 2,
                timeout / 2,
                timeout / 2,
                timeout / 2,
            ]
        )
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, _, _ = _make_mock_mssql()

        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor

        good_cursor = MagicMock()
        good_cursor.description = [("n",)]
        good_cursor.fetchall.return_value = [(7,)]
        good_conn = MagicMock()
        good_conn.cursor.return_value = good_cursor

        mock_mssql.connect.side_effect = [bad_conn, bad_conn, bad_conn, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        _cols, rows = run_query(_make_target(), "SELECT 1")

        assert rows == [(7,)]
        assert mock_mssql.connect.call_count == 4

    def test_run_statements_retries_transient_connect_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_statements retries when open_connection itself raises a transient error."""
        mock_mssql, good_conn, _ = _make_mock_mssql()

        # First connect call raises a transient error; second succeeds.
        mock_mssql.connect.side_effect = [
            Exception("TCP Provider: connection failed"),
            good_conn,
        ]
        _patch_mssql(monkeypatch, mock_mssql)

        run_statements(_make_target(), ["SELECT 1"])

        assert mock_mssql.connect.call_count == 2

    def test_run_query_retries_transient_connect_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_query retries when open_connection itself raises a transient error."""
        mock_mssql, good_conn, good_cursor = _make_mock_mssql()
        good_cursor.description = [("n",)]
        good_cursor.fetchall.return_value = [(1,)]

        # First connect call raises a transient error; second succeeds.
        mock_mssql.connect.side_effect = [
            Exception("TCP Provider: connection failed"),
            good_conn,
        ]
        _patch_mssql(monkeypatch, mock_mssql)

        _cols, rows = run_query(_make_target(), "SELECT 1")

        assert mock_mssql.connect.call_count == 2
        assert rows == [(1,)]

    def test_run_query_does_not_retry_wrong_database_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A "database was not found" error with error 18456 maps to AuthError and is NOT retried.

        map_driver_error converts 18456 to AuthError before is_transient_connection_error
        is consulted, so the error surfaces immediately instead of being retried.
        """
        mock_mssql, _, mock_cursor = _make_mock_mssql()

        # Simulate the Fabric TDS driver: message contains "database was not found"
        # AND ddbc_error embeds native error 18456.  Must be a real exception
        # subclass so that mock can raise it.
        class _FakeDriverError(Exception):
            ddbc_error: str = "[SQL Server]Login failed. Error: 18456"

            def __str__(self) -> str:
                return (
                    "Login failed for user '<token-identified principal>'. "
                    "Reason: Authentication was successful, but the database was not found "
                    "or you have insufficient permissions to connect to it"
                )

        mock_cursor.execute.side_effect = _FakeDriverError()
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(AuthError):
            run_query(_make_target(), "SELECT 1")

        # Must NOT retry — only one connect attempt.
        assert mock_mssql.connect.call_count == 1

    # ------------------------------------------------------------------
    # fetch="rowcount" (COPY INTO) execute-phase transient retry
    # ------------------------------------------------------------------

    def test_rowcount_transient_retries_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """COPY INTO (fetch='rowcount'): transient execute error is retried on a fresh connection.

        Sequence:
          1. _with_connect_retry -> bad_conn (connect call 1)
          2. _execute_once(bad_conn) raises transient "Communication link failure"
          3. execute_retry_allowed=True (rowcount != none) -> retry on fresh conn
          4. _with_connect_retry -> good_conn (connect call 2)
          5. _execute_once(good_conn) succeeds; rowcount == 5
        Result: ([], [(5,)]) — the COPY INTO rowcount path succeeds.
        """
        mock_mssql, _, _ = _make_mock_mssql()

        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception(
            "Driver Error: Communication link failure; "
            "DDBC Error: [Microsoft]Communication link failure"
        )
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor

        good_cursor = MagicMock()
        good_cursor.description = None  # COPY INTO produces no result set
        good_cursor.rowcount = 5
        good_conn = MagicMock()
        good_conn.cursor.return_value = good_cursor

        mock_mssql.connect.side_effect = [bad_conn, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        cols, rows = run_query(
            _make_target(),
            "COPY INTO [dbo].[t] FROM 'https://onelake.dfs.fabric.microsoft.com/...' WITH (...);",
            fetch="rowcount",
            commit=True,
        )

        assert mock_mssql.connect.call_count == 2
        assert cols == []
        assert rows == [(5,)]

    def test_rowcount_deterministic_error_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """COPY INTO (fetch='rowcount'): a deterministic (non-transient) error is NOT retried.

        A syntax / permission / programming error raised during execute must
        surface immediately without opening a second connection.
        """
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        mock_cursor.execute.side_effect = Exception("Incorrect syntax near 'COPY'")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="Incorrect syntax near"):
            run_query(
                _make_target(),
                "COPY BADSTATEMENT",
                fetch="rowcount",
            )

        # Only one connection should have been opened — no retry.
        assert mock_mssql.connect.call_count == 1

    def test_rowcount_transient_budget_exhausted_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """COPY INTO (fetch='rowcount'): when the time budget is exhausted the error is re-raised.

        The execute-phase retry is time-budgeted.  With the clock advanced past the
        deadline after the first execute-phase failure, no second attempt is made
        and the transient error propagates.

        Clock sequence:
          [0] _with_connect_retry deadline base (initial conn)
          [1] execute_deadline = 0 + timeout (first failure)
          [2] deadline check: past deadline → stop, raise
        Total connect calls: 1.
        """
        timeout = _sql_module._SQL_RETRY_DEADLINE_S_DEFAULT  # type: ignore[attr-defined]
        fake_time = _make_fake_time_module(
            monotonic_values=[
                0.0,  # _with_connect_retry deadline base
                0.0,  # execute_deadline = 0 + timeout
                timeout + 1,  # deadline check after first failure → expired
            ]
        )
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, _, _ = _make_mock_mssql()

        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("Communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor
        mock_mssql.connect.return_value = bad_conn
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="Communication link failure"):
            run_query(
                _make_target(),
                "COPY INTO [dbo].[t] FROM 'https://...' WITH (...);",
                fetch="rowcount",
            )

        # Deadline expired immediately after first failure — only 1 connect call.
        assert mock_mssql.connect.call_count == 1

    def test_fetch_none_does_not_retry_transient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetch='none' (DML/DDL): a transient execute error is NOT retried.

        Non-idempotent statements (INSERT / UPDATE / DDL) must never be
        re-executed after cursor.execute has been called, because the server
        may have already applied the change.
        """
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        mock_cursor.execute.side_effect = Exception("Communication link failure")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="Communication link failure"):
            run_query(_make_target(), "INSERT INTO t VALUES (1)", fetch="none")

        # fetch="none" → execute_retry_allowed=False → only one connect.
        assert mock_mssql.connect.call_count == 1

    # ------------------------------------------------------------------
    # Commit-phase safety: transient error during COMMIT must NOT retry
    # ------------------------------------------------------------------

    def test_commit_phase_transient_not_retried_statement_executed_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CRITICAL: a transient error during COMMIT is NOT retried; the statement executes once.

        This is the key safety guarantee for COPY INTO / bulk-load operations.
        If a TDS drop occurs after the statement has executed but during the
        commit, the load may already be committed server-side.  Retrying the
        statement would risk a double-load.

        The expected behaviour:
          1. connect → execute COPY INTO succeeds (cursor.rowcount read)
          2. commit() raises transient "Communication link failure"
          3. run_query re-raises the error immediately — NO second execute

        Assertions:
          - cursor.execute called exactly once (no double-load)
          - the transient error propagates to the caller
          - connect called exactly once (no retry connection opened)
        """
        mock_mssql, _, _ = _make_mock_mssql()

        # Cursor that succeeds on execute but commit raises transient.
        execute_cursor = MagicMock()
        execute_cursor.description = None  # COPY INTO: no result set
        execute_cursor.rowcount = 100

        execute_conn = MagicMock()
        execute_conn.cursor.return_value = execute_cursor
        execute_conn.commit.side_effect = Exception("Communication link failure")

        mock_mssql.connect.return_value = execute_conn
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="Communication link failure"):
            run_query(
                _make_target(),
                "COPY INTO [dbo].[t] FROM 'https://onelake.dfs.fabric.microsoft.com/...'",
                fetch="rowcount",
                commit=True,
            )

        # The statement must have been executed exactly once — no retry.
        execute_cursor.execute.assert_called_once()
        # Only one connect attempt — the retry loop never fires for commit errors.
        assert mock_mssql.connect.call_count == 1

    def test_execute_phase_retried_commit_called_once_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Execute-phase retry: multiple connects, but commit is called exactly once.

        Sequence:
          1. connect 1 → execute fails (transient)
          2. connect 2 → execute succeeds (rowcount=7)
          3. commit() called once (outside the retry loop)

        Asserts: connect called twice, commit called exactly once.
        """
        mock_mssql, _, _ = _make_mock_mssql()

        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor

        good_cursor = MagicMock()
        good_cursor.description = None  # COPY INTO: no result set
        good_cursor.rowcount = 7
        good_conn = MagicMock()
        good_conn.cursor.return_value = good_cursor

        mock_mssql.connect.side_effect = [bad_conn, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        cols, rows = run_query(
            _make_target(),
            "COPY INTO [dbo].[t] FROM 'https://onelake.dfs.fabric.microsoft.com/...' WITH (...);",
            fetch="rowcount",
            commit=True,
        )

        assert cols == []
        assert rows == [(7,)]
        # Two connections were opened (initial + 1 retry).
        assert mock_mssql.connect.call_count == 2
        # The bad connection's execute was never committed.
        bad_conn.commit.assert_not_called()
        # Commit was called exactly once — on the successful connection.
        good_conn.commit.assert_called_once()

    def test_execute_retry_bounded_by_attempt_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Persistent transient execute errors give up after the attempt cap (4 connects).

        The attempt cap is len(_EXECUTE_RETRY_DELAYS) + 1 = 4 attempts max.
        With the clock staying well within the wall-clock deadline, the loop
        must stop after exactly 4 connect+execute attempts (initial + 3 retries)
        regardless of the deadline.

        Clock sequence: all deadline checks return a time within budget so that
        only the attempt cap terminates the loop.
        """
        delays = _sql_module._EXECUTE_RETRY_DELAYS  # type: ignore[attr-defined]
        max_attempts = len(delays) + 1  # = 4

        # Provide enough monotonic values for the retry loop:
        # _with_connect_retry is called once per attempt (4 times), each call
        # consumes one monotonic value for its own deadline base.
        # The execute deadline is set on the first failure (1 monotonic call).
        # Each subsequent failure does an attempt-cap check first (before the
        # time check), so we need time values that are within budget for the
        # deadline checks that do fire.
        mono_values = [0.0] * 20  # all within budget (t=0 << deadline)

        fake_time = _make_fake_time_module(monotonic_values=mono_values)
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, _, _ = _make_mock_mssql()
        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor
        mock_mssql.connect.return_value = bad_conn
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="communication link failure"):
            run_query(_make_target(), "SELECT 1")

        # Must have stopped at exactly max_attempts connects.
        assert mock_mssql.connect.call_count == max_attempts, (
            f"expected {max_attempts} connect calls (attempt cap), "
            f"got {mock_mssql.connect.call_count}"
        )


# ---------------------------------------------------------------------------
# GitHub OIDC SQL token injection — build_connection_string + open_connection
# ---------------------------------------------------------------------------


class TestBuildConnectionStringAccessToken:
    """Tests for build_connection_string(use_access_token=True)."""

    def test_use_access_token_omits_authentication_key(self) -> None:
        """When use_access_token=True, Authentication= must NOT appear in the cs."""
        result = build_connection_string(_make_target(), use_access_token=True)
        assert "Authentication=" not in result

    def test_use_access_token_still_adds_encrypt_and_tls_keys(self) -> None:
        """Encryption settings must be present even when injecting a token."""
        result = build_connection_string(_make_target(), use_access_token=True)
        assert "Encrypt=yes" in result
        assert "TrustServerCertificate=no" in result

    def test_use_access_token_still_adds_database(self) -> None:
        """Database= must be present even when injecting a token."""
        target = _make_target(database="mydb")
        result = build_connection_string(target, use_access_token=True)
        assert "Database=mydb" in result

    def test_default_use_access_token_false_keeps_authentication(self) -> None:
        """Default (use_access_token=False) must keep Authentication=."""
        result = build_connection_string(_make_target())
        assert "Authentication=ActiveDirectoryDefault" in result

    def test_use_access_token_false_explicitly_keeps_authentication(self) -> None:
        """Explicit use_access_token=False must keep Authentication=."""
        result = build_connection_string(_make_target(), use_access_token=False)
        assert "Authentication=ActiveDirectoryDefault" in result

    def test_use_access_token_no_double_semicolons(self) -> None:
        """Token-injection path must not produce double semicolons."""
        result = build_connection_string(_make_target(), use_access_token=True)
        assert ";;" not in result


class TestOpenConnectionOidcTokenInjection:
    """Tests for OIDC token injection in open_connection.

    The mssql_python driver is never imported: _mssql is monkeypatched with a
    MagicMock.  get_sql_token_struct is patched at the fabric_dw.sql module level
    so that the mock controls the OIDC environment independently of env-vars.
    """

    def test_non_oidc_connect_called_without_attrs_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Outside OIDC, connect must be called WITHOUT attrs_before (or attrs_before=None)
        and the connection string must contain Authentication=."""
        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)
        # Simulate non-OIDC: get_sql_token_struct returns None.
        monkeypatch.setattr(_sql_module, "get_sql_token_struct", lambda *_a, **_kw: None)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        open_connection(_make_target())

        call_kwargs = mock_mssql.connect.call_args.kwargs
        # attrs_before must be explicitly None — not merely absent (falsy).
        # The implementation always passes attrs_before=None on the non-OIDC path.
        assert call_kwargs.get("attrs_before") is None
        # Connection string must contain Authentication=.
        called_cs: str = mock_mssql.connect.call_args.args[0]
        assert "Authentication=" in called_cs

    def test_oidc_connect_called_with_attrs_before_containing_key_1256(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under OIDC, connect must receive attrs_before={1256: <bytes>}."""
        import struct  # noqa: PLC0415

        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        # Build a known token struct the same way the real code does.
        known_token = "eyJhbGciOiJSUzI1NiJ9.mock-sql-token"  # noqa: S105
        token_bytes = known_token.encode("UTF-16-LE")
        fake_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

        monkeypatch.setattr(_sql_module, "get_sql_token_struct", lambda *_a, **_kw: fake_struct)

        from fabric_dw.sql import SQL_COPT_SS_ACCESS_TOKEN, open_connection  # noqa: PLC0415

        open_connection(_make_target())

        call_kwargs = mock_mssql.connect.call_args.kwargs
        attrs = call_kwargs.get("attrs_before")
        assert attrs is not None
        assert SQL_COPT_SS_ACCESS_TOKEN in attrs
        assert attrs[SQL_COPT_SS_ACCESS_TOKEN] == fake_struct

    def test_oidc_connect_called_without_authentication_in_cs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under OIDC, the connection string must NOT contain Authentication=."""
        import struct  # noqa: PLC0415

        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        token_bytes = b"fake"
        fake_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
        monkeypatch.setattr(_sql_module, "get_sql_token_struct", lambda *_a, **_kw: fake_struct)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        open_connection(_make_target())

        called_cs: str = mock_mssql.connect.call_args.args[0]
        assert "Authentication=" not in called_cs

    def test_oidc_autocommit_connect_called_with_attrs_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Under OIDC, autocommit=True connections must also receive attrs_before."""
        import struct  # noqa: PLC0415

        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        token_bytes = b"tok"
        fake_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
        monkeypatch.setattr(_sql_module, "get_sql_token_struct", lambda *_a, **_kw: fake_struct)

        from fabric_dw.sql import SQL_COPT_SS_ACCESS_TOKEN, open_connection  # noqa: PLC0415

        open_connection(_make_target(), autocommit=True)

        call_kwargs = mock_mssql.connect.call_args.kwargs
        attrs = call_kwargs.get("attrs_before")
        assert attrs is not None
        assert SQL_COPT_SS_ACCESS_TOKEN in attrs

    def test_sql_copt_ss_access_token_constant_is_1256(self) -> None:
        """SQL_COPT_SS_ACCESS_TOKEN must equal 1256 (the ODBC attribute number)."""
        from fabric_dw.sql import SQL_COPT_SS_ACCESS_TOKEN  # noqa: PLC0415

        assert SQL_COPT_SS_ACCESS_TOKEN == 1256

    def test_oidc_pool_hit_does_not_call_connect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pool HIT under OIDC must NOT call connect and must NOT invoke get_sql_token_struct.

        On the first call a new physical connection is opened (pool miss).
        After .close() returns the connection to the pool, the second call must
        return the cached connection without acquiring a token — verifying that
        the token acquisition is gated behind the pool-miss path.
        """
        import struct  # noqa: PLC0415

        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        # Enable the pool for this test.
        monkeypatch.setenv("FABRIC_CONN_POOLING", "1")
        reset_pool()

        # Track how many times the token stub is invoked.
        token_call_count = 0

        token_bytes = b"fake-oidc-token"
        fake_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

        def _counting_token_stub(*_a: object, **_kw: object) -> bytes:
            nonlocal token_call_count
            token_call_count += 1
            return fake_struct

        monkeypatch.setattr(_sql_module, "get_sql_token_struct", _counting_token_stub)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        target = _make_target()

        # First call: pool miss → opens a new physical connection, acquires token.
        conn1 = open_connection(target)
        assert mock_mssql.connect.call_count == 1
        assert token_call_count == 1
        conn1.close()  # return to pool

        # Second call: pool HIT → must reuse cached connection, not call connect,
        # and must NOT invoke get_sql_token_struct at all.
        conn2 = open_connection(target)
        assert mock_mssql.connect.call_count == 1, "connect must NOT be called on pool hit"
        assert token_call_count == 1, "get_sql_token_struct must NOT be called on pool hit"
        assert conn2._raw is mock_conn  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        conn2.close()


# ---------------------------------------------------------------------------
# D01 — params contract: sequence passed as second positional arg, not unpacked
# ---------------------------------------------------------------------------


class TestParamsContract:
    """D01: run_query must pass params as a sequence (not *params) to execute()."""

    def test_empty_params_sequence_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty sequence is treated the same as None -- execute called with SQL only."""
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "SELECT 1", params=[])

        # Empty sequence is falsy -- execute must be called WITHOUT params.
        mock_cursor.execute.assert_called_once_with("SELECT 1")

    def test_tuple_params_passed_as_sequence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tuple params are accepted and forwarded verbatim (not unpacked)."""
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "SELECT * FROM t WHERE id = ?", params=("abc",))

        # The tuple must arrive as the second argument, not as *args.
        mock_cursor.execute.assert_called_once_with("SELECT * FROM t WHERE id = ?", ("abc",))

    def test_list_params_passed_as_sequence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """List params are accepted and forwarded verbatim."""
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "SELECT * FROM t WHERE a=? AND b=?", params=[1, 2])

        mock_cursor.execute.assert_called_once_with("SELECT * FROM t WHERE a=? AND b=?", [1, 2])


# ---------------------------------------------------------------------------
# D03 -- _NATIVE_ERROR_RE: incidental numbers must not be matched
# ---------------------------------------------------------------------------


class TestNativeErrorRegex:
    """D03: the tightened regex must not match incidental numbers in error messages."""

    @staticmethod
    def _make_driver_exc(msg: str, ddbc_error: str) -> BaseException:
        exc = MagicMock(spec=Exception)
        exc.__str__ = MagicMock(return_value=msg)
        exc.ddbc_error = ddbc_error
        return exc  # type: ignore[return-value]

    def test_port_number_in_ddbc_not_matched(self) -> None:
        """A bare port number like '(1433)' must NOT trigger error-number matching."""
        exc = self._make_driver_exc(
            "connection failed",
            "TCP Provider: Error code 0x68 (port 1433)",
        )
        # 1433 is not in any error-number set; result should be None (no false match).
        # But even if 1433 were in a set, we test the regex doesn't match it here
        # because '(port 1433)' contains text inside the parens.
        result = map_driver_error(exc)
        assert result is None

    def test_row_count_in_parentheses_not_matched(self) -> None:
        """A row count like '(100 rows affected)' must not match."""
        exc = self._make_driver_exc(
            "query completed",
            "(100 rows affected)",
        )
        result = map_driver_error(exc)
        assert result is None

    def test_anchored_sql_server_parens_form_matches(self) -> None:
        """The tightened regex still matches '[SQL Server] ... Error (229)'."""
        exc = self._make_driver_exc(
            "permission error",
            "[SQL Server] SELECT permission denied. Error (229)",
        )
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_bare_error_colon_form_matches(self) -> None:
        """'Error: 229' first-alternative form still matches."""
        exc = self._make_driver_exc("some error", "Error: 229 SELECT permission denied")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDeniedError)

    def test_bare_parenthesised_number_not_matched(self) -> None:
        """'(230) text' with no anchor word must NOT match the tightened regex."""
        exc = self._make_driver_exc("some driver error", "(230) INSERT permission denied")
        # D03: bare (N) without SQL Server/Msg/Error anchor is rejected.
        # However the string fallback is NOT invoked here because ddbc_error is set.
        # The native-number strategy finds no match; fragment strategy uses str(exc)
        # which says "some driver error" -- no permission fragment -- so result is None.
        result = map_driver_error(exc)
        assert result is None


# ---------------------------------------------------------------------------
# D06 -- pool checkin evicts stale entries from the full slot list
# ---------------------------------------------------------------------------


class TestPoolCheckinEvictsStale:
    """D06: _pool_checkin must sweep and evict expired connections from the entire slot list."""

    @pytest.fixture(autouse=True)
    def _enable_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_CONN_POOLING", "1")
        reset_pool()

    def test_stale_bottom_layer_evicted_on_checkin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A connection buried below the top of the LIFO stack is evicted on next checkin.

        Scenario:
        1. Open conn_a and conn_b (both from the same key).
        2. Close conn_a (checkin at t=0) -> slot list = [(conn_a, 0)].
        3. Close conn_b (checkin at t=expired+5) -> sweep evicts conn_a;
           adds conn_b -> slot list = [(conn_b, expired+5)].
        """
        import fabric_dw.sql as sql_mod  # noqa: PLC0415
        from fabric_dw.sql import _pool, _pool_lock, open_connection  # noqa: PLC0415

        mock_mssql, _, _ = _make_mock_mssql()
        conn_a = MagicMock()
        conn_b = MagicMock()
        mock_mssql.connect.side_effect = [conn_a, conn_b]
        _patch_mssql(monkeypatch, mock_mssql)

        idle_limit = sql_mod.POOL_MAX_IDLE_SECS
        # _pool_time is called for:
        #   checkout 1 (pool empty): t=1.0 (irrelevant -- no stored timestamp)
        #   checkout 2 (pool empty): t=2.0 (irrelevant)
        #   checkin conn_a:          t=0.0  <- stored as last_used
        #   checkin conn_b (now):    t=idle_limit+5.0
        #     -> age of conn_a = idle_limit+5.0 > idle_limit => evict
        fake_times = iter([1.0, 2.0, 0.0, idle_limit + 5.0])
        monkeypatch.setattr(sql_mod, "_pool_time", lambda: next(fake_times))

        target = _make_target()
        wrap_a = open_connection(target)
        wrap_b = open_connection(target)

        wrap_a.close()  # checkin at t=0.0
        wrap_b.close()  # checkin at t=idle_limit+5 -> sweeps and evicts conn_a

        with _pool_lock:
            key = ("ws-1", "db-1", "default")
            slots = _pool.get(key, [])
            pool_conns = [s[0] for s in slots]

        assert conn_a not in pool_conns, "stale conn_a should have been evicted on checkin"
        assert conn_b in pool_conns, "fresh conn_b should remain in pool"
        conn_a.close.assert_called_once()  # physically closed


# ---------------------------------------------------------------------------
# D10 -- retry boundary: DML (fetch="none") must NOT be retried on execute error
# ---------------------------------------------------------------------------


class TestD10RetryBoundary:
    """D10: execute-phase transient errors for DML must not trigger a retry."""

    @pytest.fixture(autouse=True)
    def _disable_sleep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_sql_module, "time", _make_fake_time_module())

    def test_dml_not_retried_on_transient_execute_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fetch='none' (DML/DDL) must NOT be retried after cursor.execute raises transient.

        If the server received the INSERT before the connection dropped, retrying
        would cause a duplicate row.  The error must be re-raised immediately.
        """
        mock_mssql, _, _ = _make_mock_mssql()
        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor
        mock_mssql.connect.return_value = bad_conn
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="communication link failure"):
            run_query(_make_target(), "INSERT INTO t VALUES (1)", fetch="none")

        # Must NOT retry -- exactly 1 connect attempt.
        assert mock_mssql.connect.call_count == 1, (
            "DML (fetch='none') must not retry on execute-phase transient errors"
        )

    def test_select_retried_once_on_transient_execute_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fetch='all' (SELECT) IS allowed a single execute-phase retry."""
        mock_mssql, _, _ = _make_mock_mssql()

        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor

        good_cursor = MagicMock()
        good_cursor.description = [("n",)]
        good_cursor.fetchall.return_value = [(42,)]
        good_conn = MagicMock()
        good_conn.cursor.return_value = good_cursor

        mock_mssql.connect.side_effect = [bad_conn, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        _cols, rows = run_query(_make_target(), "SELECT 1")

        assert rows == [(42,)]
        assert mock_mssql.connect.call_count == 2
        # _PooledConnection.close() is idempotent; despite being called once
        # explicitly (retry path) and once in the outer finally, the underlying
        # bad_conn must receive close() exactly once.
        bad_conn.close.assert_called_once()

    def test_fetch_one_retried_once_on_transient_execute_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fetch='one' is also a read-only path and gets the single execute-phase retry."""
        mock_mssql, _, _ = _make_mock_mssql()

        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor

        good_cursor = MagicMock()
        good_cursor.description = [("n",)]
        good_cursor.fetchone.return_value = (99,)
        good_conn = MagicMock()
        good_conn.cursor.return_value = good_cursor

        mock_mssql.connect.side_effect = [bad_conn, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        _cols, rows = run_query(_make_target(), "SELECT TOP 1 n FROM t", fetch="one")

        assert rows == [(99,)]
        assert mock_mssql.connect.call_count == 2


# ---------------------------------------------------------------------------
# D23 -- pool reset: rollback called before checkin so next caller starts clean
# ---------------------------------------------------------------------------


class TestD23PoolRollbackOnReturn:
    """D23: _PooledConnection.close() must call rollback() before returning to pool."""

    @pytest.fixture(autouse=True)
    def _enable_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_CONN_POOLING", "1")
        reset_pool()

    def test_rollback_called_before_checkin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Closing a healthy pooled connection must call rollback() on the underlying conn."""
        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        conn = open_connection(_make_target())
        conn.close()

        mock_conn.rollback.assert_called_once()
        # The underlying must NOT have been physically closed (it went to pool).
        mock_conn.close.assert_not_called()

    def test_rollback_not_called_when_discarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """mark_discard() must skip rollback and physically close instead."""
        mock_mssql, mock_conn, mock_cursor = _make_mock_mssql()
        mock_cursor.execute.side_effect = Exception("network error")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="network error"):
            run_query(_make_target(), "SELECT 1")

        # Tainted connection must be physically closed, not rolled back then pooled.
        mock_conn.close.assert_called_once()
        mock_conn.rollback.assert_not_called()

    def test_successful_run_query_rolls_back_before_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A successful SELECT must roll back before returning connection to pool."""
        mock_mssql, mock_conn, mock_cursor = _make_mock_mssql()
        mock_cursor.description = [("n",)]
        mock_cursor.fetchall.return_value = [(1,)]
        _patch_mssql(monkeypatch, mock_mssql)

        run_query(_make_target(), "SELECT 1")

        # rollback called on pool return; underlying NOT physically closed.
        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #405 — longer timeouts + auth-failed retry on connect path
# ---------------------------------------------------------------------------


class TestConnectionStringTimeouts:
    """Timeout constants must have correct values; timeouts must NOT appear in the
    connection string (they are injected via the driver API, not keyword syntax)."""

    def test_login_timeout_constant_value(self) -> None:
        """SQL_LOGIN_TIMEOUT_S must be 60 (generous vs ~0s driver default)."""
        import fabric_dw.sql as sql_mod  # noqa: PLC0415

        assert sql_mod.SQL_LOGIN_TIMEOUT_S == 60

    def test_query_timeout_constant_value(self) -> None:
        """SQL_QUERY_TIMEOUT_S must be 300 (generous for long-running admin queries)."""
        import fabric_dw.sql as sql_mod  # noqa: PLC0415

        assert sql_mod.SQL_QUERY_TIMEOUT_S == 300

    def test_connection_timeout_absent_from_connection_string(self) -> None:
        """'Connection Timeout' must NOT appear in the connection string.

        mssql-python 1.8.0 rejects 'Connection Timeout' as an unknown keyword.
        The login timeout is passed via connect(timeout=...) instead.
        """
        result = build_connection_string(_make_target())
        assert "Connection Timeout" not in result

    def test_command_timeout_absent_from_connection_string(self) -> None:
        """'Command Timeout' must NOT appear in the connection string.

        mssql-python 1.8.0 rejects 'Command Timeout' as an unknown keyword.
        The query timeout is set via connection.timeout = ... instead.
        """
        result = build_connection_string(_make_target())
        assert "Command Timeout" not in result

    def test_no_timeout_keywords_leak_into_connection_string(self) -> None:
        """No *Timeout* keyword of any form must appear in the connection string."""
        result = build_connection_string(_make_target())
        assert "Timeout" not in result

    def test_connection_string_valid_per_real_driver_parser_without_token(self) -> None:
        """build_connection_string output must be accepted by the real mssql-python
        connection-string parser (validate_keywords=True) — regression guard for #415.

        Uses the non-token path (Authentication= key present).
        Skipped when the mssql_python native binary is not available (e.g. CI unit
        runner without ODBC driver or incompatible platform).
        """
        parser_mod = pytest.importorskip(
            "mssql_python.connection_string_parser",
            reason="mssql_python native binary unavailable — skipping parser regression guard",
            exc_type=ImportError,
        )
        connection_string_parser_cls = parser_mod._ConnectionStringParser  # type: ignore[attr-defined]
        cs = build_connection_string(_make_target(), use_access_token=False)
        # Must NOT raise ConnectionStringParseError.
        connection_string_parser_cls(validate_keywords=True)._parse(cs)

    def test_connection_string_valid_per_real_driver_parser_with_token(self) -> None:
        """build_connection_string output (use_access_token=True) must be accepted
        by the real mssql-python parser — regression guard for #415.

        Uses the token path (Authentication= key absent).
        Skipped when the mssql_python native binary is not available.
        """
        parser_mod = pytest.importorskip(
            "mssql_python.connection_string_parser",
            reason="mssql_python native binary unavailable — skipping parser regression guard",
            exc_type=ImportError,
        )
        connection_string_parser_cls = parser_mod._ConnectionStringParser  # type: ignore[attr-defined]
        cs = build_connection_string(_make_target(), use_access_token=True)
        # Must NOT raise ConnectionStringParseError.
        connection_string_parser_cls(validate_keywords=True)._parse(cs)


class TestOpenConnectionTimeoutAPI:
    """open_connection must pass login timeout via connect(timeout=) and set
    query timeout via connection.timeout — NOT via connection-string keywords."""

    def test_fresh_normal_connect_passes_login_timeout_kwarg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-autocommit fresh open must pass timeout=SQL_LOGIN_TIMEOUT_S to connect()."""
        import fabric_dw.sql as sql_mod  # noqa: PLC0415

        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        open_connection(_make_target())

        kwargs = mock_mssql.connect.call_args.kwargs
        assert kwargs.get("timeout") == sql_mod.SQL_LOGIN_TIMEOUT_S

    def test_fresh_normal_connect_sets_query_timeout_property(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-autocommit fresh open must set raw_conn.timeout = SQL_QUERY_TIMEOUT_S."""
        import fabric_dw.sql as sql_mod  # noqa: PLC0415

        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        open_connection(_make_target())

        assert mock_conn.timeout == sql_mod.SQL_QUERY_TIMEOUT_S

    def test_autocommit_connect_passes_login_timeout_kwarg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Autocommit fresh open must pass timeout=SQL_LOGIN_TIMEOUT_S to connect()."""
        import fabric_dw.sql as sql_mod  # noqa: PLC0415

        mock_mssql, _, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)
        # No OIDC token — straightforward path.
        monkeypatch.setattr(_sql_module, "get_sql_token_struct", lambda *_a, **_kw: None)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        open_connection(_make_target(), autocommit=True)

        kwargs = mock_mssql.connect.call_args.kwargs
        assert kwargs.get("timeout") == sql_mod.SQL_LOGIN_TIMEOUT_S

    def test_autocommit_connect_sets_query_timeout_property(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Autocommit fresh open must set raw_conn.timeout = SQL_QUERY_TIMEOUT_S."""
        import fabric_dw.sql as sql_mod  # noqa: PLC0415

        mock_mssql, mock_conn, _ = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)
        monkeypatch.setattr(_sql_module, "get_sql_token_struct", lambda *_a, **_kw: None)

        from fabric_dw.sql import open_connection  # noqa: PLC0415

        open_connection(_make_target(), autocommit=True)

        assert mock_conn.timeout == sql_mod.SQL_QUERY_TIMEOUT_S


class TestAuthFailedConnectRetry:
    """_with_connect_retry must retry auth-failed / 18456 errors on the connect path.

    The connect-phase retry is time-bounded (``_SQL_RETRY_DEADLINE_S_DEFAULT``, ~120 s)
    rather than attempt-count-bounded.  Tests inject a fake monotonic clock so
    that deadlines are exercised deterministically without any real wall-clock
    delay.

    Fake-clock convention
    ---------------------
    ``time.monotonic()`` is called once at the *start* of ``_with_connect_retry``
    to compute the deadline, and once *after each failed attempt* to check whether
    the deadline has passed.  For N connect failures the call sequence is:

      monotonic() → deadline base        (call index 0)
      connect → fail
      sleep(d)
      monotonic() → check deadline       (call index 1)
      connect → fail
      sleep(d)
      monotonic() → check deadline       (call index 2)
      …

    A ``monotonic_values`` list passed to :func:`_make_fake_time_module` must
    supply at least 1 + N values to cover the whole sequence.
    """

    # ------------------------------------------------------------------ #
    # Helper: fake driver exception with ddbc_error containing 18456      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _make_auth_exc() -> Exception:
        """Return a simulated 18456 auth exception with a ``ddbc_error`` attribute."""

        class _AuthDriverError(Exception):
            ddbc_error: str = "[SQL Server]Login failed. Error: 18456"

            def __str__(self) -> str:
                return (
                    "Login failed for user '<token-identified principal>'. "
                    "Could not login because the authentication failed."
                )

        return _AuthDriverError()

    @staticmethod
    def _fake_time_within_deadline(n_failures: int) -> MagicMock:
        """Return a fake time module where all deadline checks are within budget.

        The deadline is set to ``t0 + _SQL_RETRY_DEADLINE_S_DEFAULT``.  We use t0=0
        and all post-failure monotonic() calls return 1.0 (well within budget).
        This simulates the warehouse recovering before the deadline.
        """
        # call 0: deadline base (t0=0)
        # calls 1..n_failures: each post-failure check returns 1.0 < deadline
        mono_values = [0.0] + [1.0] * n_failures
        return _make_fake_time_module(monotonic_values=mono_values)

    @staticmethod
    def _fake_time_past_deadline() -> MagicMock:
        """Return a fake time module where the first post-failure check exceeds budget.

        This simulates a genuinely-wrong credential / warehouse that never recovers
        within the ~120 s window.  After the single failure the monotonic clock
        jumps past the deadline so the loop raises immediately.
        """
        timeout = _sql_module._SQL_RETRY_DEADLINE_S_DEFAULT
        # call 0: t0=0  →  deadline = 0 + timeout
        # call 1: t0 + timeout + 1.0  →  past deadline
        mono_values = [0.0, timeout + 1.0]
        return _make_fake_time_module(monotonic_values=mono_values)

    def test_auth_failed_connect_retried_twice_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An 18456 error on attempts 1 and 2 is retried; attempt 3 succeeds.

        time.sleep must be called twice (once before each retry) using the
        bounded backoff delays (5 s then 10 s).
        """
        fake_time = self._fake_time_within_deadline(n_failures=2)
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, good_conn, good_cursor = _make_mock_mssql()
        good_cursor.description = [("n",)]
        good_cursor.fetchall.return_value = [(1,)]

        auth_exc = self._make_auth_exc()
        # Fail twice, succeed on the third attempt.
        mock_mssql.connect.side_effect = [auth_exc, auth_exc, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        _cols, rows = run_query(_make_target(), "SELECT 1")

        assert rows == [(1,)]
        assert mock_mssql.connect.call_count == 3

        # time.sleep must have been called twice (before attempt 2 and 3).
        assert fake_time.sleep.call_count == 2
        # Delays follow the bounded backoff schedule: 5 s, then 10 s.
        delays = [call.args[0] for call in fake_time.sleep.call_args_list]
        assert delays == [
            _sql_module._CONNECT_RETRY_DELAYS[0],
            _sql_module._CONNECT_RETRY_DELAYS[1],
        ]

    def test_auth_failed_connect_persistent_raises_after_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the deadline expires while auth-failed errors persist, the exception surfaces.

        The fake clock is arranged so that the first post-failure monotonic()
        check already exceeds the deadline, causing the loop to raise after the
        very first attempt — i.e. exactly 1 connect call and 1 sleep call.
        """
        fake_time = self._fake_time_past_deadline()
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, _, _ = _make_mock_mssql()
        auth_exc = self._make_auth_exc()
        mock_mssql.connect.side_effect = auth_exc
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="authentication failed"):
            run_query(_make_target(), "SELECT 1")

        # The deadline check fires after the first failure → re-raise immediately.
        # sleep was called once (before the deadline check), then raise fires.
        assert mock_mssql.connect.call_count == 1
        # No sleep happens because we raise before sleeping when deadline is exceeded.
        assert fake_time.sleep.call_count == 0

    def test_auth_failed_fragment_only_also_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Auth-failed detected via message fragment only (no ddbc_error) is also retried."""
        fake_time = self._fake_time_within_deadline(n_failures=1)
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, good_conn, good_cursor = _make_mock_mssql()
        good_cursor.description = [("n",)]
        good_cursor.fetchall.return_value = [(1,)]

        # Plain Exception has no ddbc_error; detection falls through to fragment path.
        frag_exc = Exception("authentication failed for user ''")
        mock_mssql.connect.side_effect = [frag_exc, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        _cols, rows = run_query(_make_target(), "SELECT 1")

        assert rows == [(1,)]
        assert mock_mssql.connect.call_count == 2

    def test_could_not_login_fragment_also_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'could not login' fragment (without ddbc_error) is retried via _AUTH_FAILED_FRAGMENTS."""
        fake_time = self._fake_time_within_deadline(n_failures=1)
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, good_conn, good_cursor = _make_mock_mssql()
        good_cursor.description = [("n",)]
        good_cursor.fetchall.return_value = [(1,)]

        # Some Fabric TDS error paths surface "could not login" without a native error number.
        frag_exc = Exception("could not login because the authentication failed")
        mock_mssql.connect.side_effect = [frag_exc, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        _cols, rows = run_query(_make_target(), "SELECT 1")

        assert rows == [(1,)]
        assert mock_mssql.connect.call_count == 2

    def test_transient_connect_retried_with_bounded_backoff_first_delay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Standard transient connect errors use the first backoff delay (5 s) on retry 1."""
        fake_time = self._fake_time_within_deadline(n_failures=1)
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, good_conn, good_cursor = _make_mock_mssql()
        good_cursor.description = [("n",)]
        good_cursor.fetchall.return_value = [(1,)]

        transient_exc = Exception("TCP Provider: connection failed")
        mock_mssql.connect.side_effect = [transient_exc, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        _cols, rows = run_query(_make_target(), "SELECT 1")

        assert rows == [(1,)]
        assert fake_time.sleep.call_count == 1
        # First backoff interval is _CONNECT_RETRY_DELAYS[0] = 5 s.
        assert fake_time.sleep.call_args.args[0] == _sql_module._CONNECT_RETRY_DELAYS[0]

    def test_auth_failed_on_execute_phase_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Auth-failed raised during EXECUTE (not connect) must NOT be retried.

        The execute-phase retry only covers transient transport errors for
        read-only queries; mapped AuthError is raised immediately.
        """
        monkeypatch.setattr(_sql_module, "time", _make_fake_time_module())

        mock_mssql, _, mock_cursor = _make_mock_mssql()

        class _AuthDriverError(Exception):
            ddbc_error: str = "[SQL Server]Login failed. Error: 18456"

            def __str__(self) -> str:
                return "Login failed: authentication failed"

        mock_cursor.execute.side_effect = _AuthDriverError()
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(AuthError):
            run_query(_make_target(), "SELECT 1")

        # map_driver_error converts the 18456 to AuthError and raises immediately.
        assert mock_mssql.connect.call_count == 1

    def test_retry_policy_constants(self) -> None:
        """Connect+execute-phase default deadline is 120 s (int); backoff arrays are set."""
        assert _sql_module._SQL_RETRY_DEADLINE_S_DEFAULT == 120
        assert isinstance(_sql_module._SQL_RETRY_DEADLINE_S_DEFAULT, int)
        assert _sql_module._CONNECT_RETRY_DELAYS == (5.0, 10.0, 15.0)
        assert _sql_module._EXECUTE_RETRY_DELAYS == (2.0, 5.0, 10.0)

    def test_dml_not_retried_execute_phase_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetch='none' DML must still NOT be retried after execute raises transient (D10).

        This test re-validates the idempotency boundary is unaffected by the
        auth-failed-on-connect change.
        """
        monkeypatch.setattr(_sql_module, "time", _make_fake_time_module())

        mock_mssql, _, _ = _make_mock_mssql()
        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor
        mock_mssql.connect.return_value = bad_conn
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="communication link failure"):
            run_query(_make_target(), "INSERT INTO t VALUES (1)", fetch="none")

        # Only the initial connect attempt — no execute-phase retry for DML.
        assert mock_mssql.connect.call_count == 1

    def test_run_statements_retries_auth_failed_connect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_statements retries 18456 / auth-failed on the connect phase.

        Mirrors the run_query case: a warming-up Fabric warehouse may reject the
        login with error 18456 until provisioning completes.  _with_connect_retry
        is shared by both run_query and run_statements, so both must retry.
        """
        fake_time = self._fake_time_within_deadline(n_failures=1)
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, good_conn, _ = _make_mock_mssql()

        auth_exc = self._make_auth_exc()
        # First connect raises 18456; second succeeds.
        mock_mssql.connect.side_effect = [auth_exc, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        run_statements(_make_target(), ["SELECT 1"])

        assert mock_mssql.connect.call_count == 2
        # Exactly one sleep before the retry, using the first backoff interval (5 s).
        assert fake_time.sleep.call_count == 1
        assert fake_time.sleep.call_args.args[0] == _sql_module._CONNECT_RETRY_DELAYS[0]

    def test_happy_path_no_sleep_no_deadline_exceeded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the first connect attempt succeeds, sleep is never called.

        This guarantees that happy-path execution has zero overhead from the
        retry loop.
        """
        fake_time = self._fake_time_within_deadline(n_failures=0)
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, good_conn, good_cursor = _make_mock_mssql()
        good_cursor.description = [("n",)]
        good_cursor.fetchall.return_value = [(1,)]
        mock_mssql.connect.return_value = good_conn
        _patch_mssql(monkeypatch, mock_mssql)

        _cols, rows = run_query(_make_target(), "SELECT 1")

        assert rows == [(1,)]
        assert mock_mssql.connect.call_count == 1
        assert fake_time.sleep.call_count == 0

    def test_non_retryable_error_raises_immediately_no_sleep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-retryable connect error is raised immediately without sleep or deadline check."""
        fake_time = self._fake_time_within_deadline(n_failures=0)
        monkeypatch.setattr(_sql_module, "time", fake_time)

        mock_mssql, _, _ = _make_mock_mssql()
        non_retryable = Exception("Incorrect syntax near 'SELCT'")
        mock_mssql.connect.side_effect = non_retryable
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="Incorrect syntax"):
            run_query(_make_target(), "SELECT 1")

        assert mock_mssql.connect.call_count == 1
        assert fake_time.sleep.call_count == 0


# ---------------------------------------------------------------------------
# D12 — fetch-before-commit ordering (COPY INTO fix)
# ---------------------------------------------------------------------------


class _CommitInvalidatingCursor:
    """Fake cursor that mimics mssql-python's "Associated statement is not prepared" error.

    After ``commit()`` is called on the owning connection, any access to
    ``fetchall()``, ``fetchone()``, or ``description`` raises ``_ProgrammingError``
    with that message — exactly what mssql-python does when a committed cursor is
    subsequently read.
    """

    _PREPARED_ERROR = "Associated statement is not prepared"

    def __init__(
        self,
        description: list[tuple[str, None]],
        rows: list[tuple[object, ...]],
        committed_flag: list[bool],
    ) -> None:
        self._description = description
        self._rows = rows
        self._committed = committed_flag

    def execute(self, _sql: str, _params: object = None) -> None:
        pass

    @property
    def description(self) -> list[tuple[str, None]]:
        if self._committed[0]:
            raise _ProgrammingError(self._PREPARED_ERROR)
        return self._description

    def fetchall(self) -> list[tuple[object, ...]]:
        if self._committed[0]:
            raise _ProgrammingError(self._PREPARED_ERROR)
        return self._rows

    def fetchone(self) -> tuple[object, ...] | None:
        if self._committed[0]:
            raise _ProgrammingError(self._PREPARED_ERROR)
        return self._rows[0] if self._rows else None


class _CommitInvalidatingConnection:
    """Fake _Connection whose ``commit()`` invalidates the cursor."""

    def __init__(
        self,
        description: list[tuple[str, None]],
        rows: list[tuple[object, ...]],
    ) -> None:
        self._committed: list[bool] = [False]
        self._cursor = _CommitInvalidatingCursor(description, rows, self._committed)

    def cursor(self) -> _CommitInvalidatingCursor:
        return self._cursor

    def commit(self) -> None:
        self._committed[0] = True

    def close(self) -> None:
        pass


class _ProgrammingError(Exception):
    """Minimal stand-in for mssql_python.exceptions.ProgrammingError."""


class TestFetchBeforeCommitOrdering:
    """D12: result set must be fetched BEFORE commit() is called.

    These tests use a fake cursor/connection that raises once ``commit()`` has
    been called, mirroring the mssql-python "Associated statement is not
    prepared" behaviour that broke COPY INTO.
    """

    def _patch_with_invalidating_conn(
        self,
        monkeypatch: pytest.MonkeyPatch,
        description: list[tuple[str, None]],
        rows: list[tuple[object, ...]],
    ) -> _CommitInvalidatingConnection:
        fake_conn = _CommitInvalidatingConnection(description, rows)
        mock_mssql = MagicMock()
        mock_mssql.connect.return_value = fake_conn
        monkeypatch.setattr(_sql_module, "_mssql", mock_mssql)
        return fake_conn

    def test_fetch_all_commit_true_returns_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetch='all' + commit=True must return rows even if commit invalidates cursor."""
        description = [("rows_loaded", None), ("rows_rejected", None)]
        rows: list[tuple[object, ...]] = [(1000, 0)]
        self._patch_with_invalidating_conn(monkeypatch, description, rows)

        cols, result_rows = run_query(
            _make_target(),
            "COPY INTO [dbo].[t] FROM '...' WITH (FILE_TYPE='PARQUET')",
            commit=True,
            fetch="all",
        )

        assert cols == ["rows_loaded", "rows_rejected"]
        assert result_rows == [(1000, 0)]

    def test_fetch_one_commit_true_returns_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetch='one' + commit=True must return the single row before the commit fires."""
        description = [("rows_loaded", None), ("rows_rejected", None)]
        rows: list[tuple[object, ...]] = [(500, 2)]
        self._patch_with_invalidating_conn(monkeypatch, description, rows)

        cols, result_rows = run_query(
            _make_target(),
            "COPY INTO [dbo].[t] FROM '...' WITH (FILE_TYPE='PARQUET')",
            commit=True,
            fetch="one",
        )

        assert cols == ["rows_loaded", "rows_rejected"]
        assert result_rows == [(500, 2)]

    def test_fetch_none_commit_true_still_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetch='none' + commit=True (DDL/DML path) must still call commit()."""
        description: list[tuple[str, None]] = []
        rows: list[tuple[object, ...]] = []
        fake_conn = self._patch_with_invalidating_conn(monkeypatch, description, rows)

        cols, result_rows = run_query(
            _make_target(),
            "INSERT INTO [dbo].[t] VALUES (1)",
            commit=True,
            fetch="none",
        )

        assert cols == []
        assert result_rows == []
        # Verify commit was actually called.
        assert fake_conn._committed[0] is True


# ---------------------------------------------------------------------------
# D13 — fetch="rowcount" mode (COPY INTO cursor.rowcount, no result set)
# ---------------------------------------------------------------------------


class _RowcountCursor:
    """Fake cursor that mimics COPY INTO on mssql-python ≥ 1.9.0.

    - description is None (no result set returned).
    - fetchall() raises ProgrammingError (Invalid cursor state).
    - rowcount == N (the number of rows loaded).
    """

    def __init__(self, rowcount: int) -> None:
        self.description: None = None
        self.rowcount: int = rowcount
        self._committed: list[bool]  # set by owning connection after construction

    def execute(self, _sql: str, _params: object = None) -> None:
        pass

    def fetchall(self) -> list[tuple[object, ...]]:
        raise _ProgrammingError("Invalid cursor state")

    def fetchone(self) -> tuple[object, ...] | None:
        raise _ProgrammingError("Invalid cursor state")

    def nextset(self) -> bool | None:
        return False

    def close(self) -> None:
        pass


class _RowcountConnection:
    """Fake _Connection for rowcount-based cursor."""

    def __init__(self, rowcount: int) -> None:
        self._committed: list[bool] = [False]
        self._cursor = _RowcountCursor(rowcount)
        self._cursor._committed = self._committed

    def cursor(self) -> _RowcountCursor:
        return self._cursor

    def commit(self) -> None:
        self._committed[0] = True

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class TestFetchRowcountMode:
    """D13: fetch='rowcount' reads cursor.rowcount instead of fetching a result set.

    This covers the COPY INTO case on mssql-python ≥ 1.9.0 where:
    - cursor.description is None
    - fetchall() raises ProgrammingError
    - cursor.rowcount == rows loaded
    """

    def _patch_with_rowcount_conn(
        self,
        monkeypatch: pytest.MonkeyPatch,
        rowcount: int,
    ) -> _RowcountConnection:
        fake_conn = _RowcountConnection(rowcount)
        mock_mssql = MagicMock()
        mock_mssql.connect.return_value = fake_conn
        monkeypatch.setattr(_sql_module, "_mssql", mock_mssql)
        return fake_conn

    def test_rowcount_returns_count_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetch='rowcount' must return ([], [(N,)]) for rowcount=N."""
        fake_conn = self._patch_with_rowcount_conn(monkeypatch, 3)

        cols, rows = run_query(
            _make_target(),
            "COPY INTO [dbo].[t] FROM '...' WITH (FILE_TYPE='PARQUET');",
            commit=True,
            fetch="rowcount",
        )

        assert cols == []
        assert rows == [(3,)]
        # commit must have been called
        assert fake_conn._committed[0] is True

    def test_rowcount_commit_false_does_not_commit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetch='rowcount' + commit=False must NOT call commit()."""
        fake_conn = self._patch_with_rowcount_conn(monkeypatch, 5)

        _cols, rows = run_query(
            _make_target(),
            "COPY INTO [dbo].[t] FROM '...' WITH (FILE_TYPE='PARQUET');",
            commit=False,
            fetch="rowcount",
        )

        assert rows == [(5,)]
        assert fake_conn._committed[0] is False

    def test_rowcount_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetch='rowcount' must return ([], [(0,)]) when rowcount=0."""
        self._patch_with_rowcount_conn(monkeypatch, 0)

        _cols, rows = run_query(
            _make_target(),
            "COPY INTO [dbo].[t] FROM '...' WITH (FILE_TYPE='PARQUET');",
            fetch="rowcount",
        )

        assert rows == [(0,)]

    def test_rowcount_does_not_call_fetchall(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetch='rowcount' must NOT call fetchall() (which would raise Invalid cursor state)."""
        fake_conn = self._patch_with_rowcount_conn(monkeypatch, 7)
        # Verify that the cursor's fetchall would indeed raise
        with pytest.raises(_ProgrammingError, match="Invalid cursor state"):
            fake_conn._cursor.fetchall()

        # But run_query with fetch='rowcount' must NOT raise despite that
        _cols, rows = run_query(
            _make_target(),
            "COPY INTO [dbo].[t] FROM '...' WITH (FILE_TYPE='PARQUET');",
            fetch="rowcount",
        )
        assert rows == [(7,)]


# ---------------------------------------------------------------------------
# D14 — defensive guard: fetch="all"/"one" with description=None returns ([], [])
# ---------------------------------------------------------------------------


class _NoResultSetCursor:
    """Fake cursor that returns description=None and raises on fetch (no result set)."""

    def __init__(self) -> None:
        self.description: None = None
        self.rowcount: int = -1

    def execute(self, _sql: str, _params: object = None) -> None:
        pass

    def fetchall(self) -> list[tuple[object, ...]]:
        raise _ProgrammingError("Invalid cursor state")

    def fetchone(self) -> tuple[object, ...] | None:
        raise _ProgrammingError("Invalid cursor state")

    def close(self) -> None:
        pass


class _NoResultSetConnection:
    def __init__(self) -> None:
        self._committed: list[bool] = [False]
        self._cursor = _NoResultSetCursor()

    def cursor(self) -> _NoResultSetCursor:
        return self._cursor

    def commit(self) -> None:
        self._committed[0] = True

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class TestFetchAllDescriptionNoneGuard:
    """D14: fetch='all'/'one' with description=None must return ([], []) without raising."""

    def _patch(self, monkeypatch: pytest.MonkeyPatch) -> _NoResultSetConnection:
        fake_conn = _NoResultSetConnection()
        mock_mssql = MagicMock()
        mock_mssql.connect.return_value = fake_conn
        monkeypatch.setattr(_sql_module, "_mssql", mock_mssql)
        return fake_conn

    def test_fetch_all_description_none_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fetch='all' with description=None must return ([], []) and NOT raise."""
        fake_conn = self._patch(monkeypatch)

        cols, rows = run_query(
            _make_target(),
            "COPY INTO [dbo].[t] FROM '...' WITH (FILE_TYPE='PARQUET');",
            commit=True,
            fetch="all",
        )

        assert cols == []
        assert rows == []
        assert fake_conn._committed[0] is True

    def test_fetch_one_description_none_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fetch='one' with description=None must return ([], []) and NOT raise."""
        fake_conn = self._patch(monkeypatch)

        cols, rows = run_query(
            _make_target(),
            "COPY INTO [dbo].[t] FROM '...' WITH (FILE_TYPE='PARQUET');",
            commit=True,
            fetch="one",
        )

        assert cols == []
        assert rows == []
        assert fake_conn._committed[0] is True


# ---------------------------------------------------------------------------
# SQL retry config resolution — 3-layer precedence
# ---------------------------------------------------------------------------


class TestSqlRetryConfig:
    """_resolve_sql_retry_deadline_s and _resolve_sql_retry_executes follow the 3-layer rule."""

    def test_deadline_env_wins_over_config_and_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FABRIC_SQL_RETRY_TIMEOUT_S takes precedence over config and built-in default."""
        monkeypatch.setenv("FABRIC_SQL_RETRY_TIMEOUT_S", "999")
        assert _sql_module._resolve_sql_retry_deadline_s() == 999

    def test_deadline_float_formatted_int_env_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FABRIC_SQL_RETRY_TIMEOUT_S='999.0' is accepted as 999 (Docker float-int)."""
        monkeypatch.setenv("FABRIC_SQL_RETRY_TIMEOUT_S", "999.0")
        assert _sql_module._resolve_sql_retry_deadline_s() == 999

    def test_deadline_config_wins_over_default(self) -> None:
        """Config sql_retry_deadline_s beats the built-in 120 default."""
        cfg = UserConfig(defaults=Defaults(sql_retry_deadline_s=250))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._resolve_sql_retry_deadline_s() == 250

    def test_deadline_falls_back_to_default(self) -> None:
        """When no env var and no config value, the default 120 is returned."""
        assert _sql_module._resolve_sql_retry_deadline_s() == 120

    def test_deadline_return_type_is_int(self) -> None:
        """_resolve_sql_retry_deadline_s always returns an int."""
        assert isinstance(_sql_module._resolve_sql_retry_deadline_s(), int)

    def test_deadline_invalid_env_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-numeric FABRIC_SQL_RETRY_TIMEOUT_S is ignored; default used instead."""
        monkeypatch.setenv("FABRIC_SQL_RETRY_TIMEOUT_S", "not-an-int")
        assert _sql_module._resolve_sql_retry_deadline_s() == 120

    def test_deadline_below_min_env_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A value < 1 in FABRIC_SQL_RETRY_TIMEOUT_S is ignored; default used instead."""
        monkeypatch.setenv("FABRIC_SQL_RETRY_TIMEOUT_S", "0")
        assert _sql_module._resolve_sql_retry_deadline_s() == 120

    def test_deadline_invalid_env_falls_through_to_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid env var falls through to config value."""
        monkeypatch.setenv("FABRIC_SQL_RETRY_TIMEOUT_S", "bad")
        cfg = UserConfig(defaults=Defaults(sql_retry_deadline_s=88))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._resolve_sql_retry_deadline_s() == 88

    def test_deadline_config_zero_floored_to_default(self) -> None:
        """A config.toml sql_retry_deadline_s of 0 is rejected and falls through to default."""
        cfg = UserConfig(defaults=Defaults(sql_retry_deadline_s=0))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._resolve_sql_retry_deadline_s() == 120

    def test_deadline_config_below_min_floored_to_default(self) -> None:
        """A config.toml sql_retry_deadline_s below the minimum is rejected and falls through."""
        cfg = UserConfig(defaults=Defaults(sql_retry_deadline_s=-5))
        _sql_module._sql_config_cache = cfg
        # -5 < _MIN_SQL_RETRY_DEADLINE_S, so the built-in default is used
        assert _sql_module._resolve_sql_retry_deadline_s() == 120

    def test_deadline_valid_config_value_returned(self) -> None:
        """A config.toml sql_retry_deadline_s at or above the minimum is returned as-is."""
        min_val = _sql_module._MIN_SQL_RETRY_DEADLINE_S
        cfg = UserConfig(defaults=Defaults(sql_retry_deadline_s=min_val))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._resolve_sql_retry_deadline_s() == min_val

    def test_executes_env_wins_over_config_and_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FABRIC_SQL_RETRY_EXECUTES=1 takes precedence over config and built-in default."""
        monkeypatch.setenv("FABRIC_SQL_RETRY_EXECUTES", "1")
        assert _sql_module._resolve_sql_retry_executes() is True

    def test_executes_env_false_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_SQL_RETRY_EXECUTES=false → False."""
        monkeypatch.setenv("FABRIC_SQL_RETRY_EXECUTES", "false")
        cfg = UserConfig(defaults=Defaults(sql_retry_executes=True))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._resolve_sql_retry_executes() is False

    def test_executes_config_wins_over_default(self) -> None:
        """Config sql_retry_executes=True beats the built-in False default."""
        cfg = UserConfig(defaults=Defaults(sql_retry_executes=True))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._resolve_sql_retry_executes() is True

    def test_executes_falls_back_to_false(self) -> None:
        """When no env var and no config value, False is returned."""
        assert _sql_module._resolve_sql_retry_executes() is False

    def test_executes_truthy_strings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Various truthy env values are all recognised."""
        for v in ("1", "true", "TRUE", "True", "yes", "on", "anything-else"):
            monkeypatch.setenv("FABRIC_SQL_RETRY_EXECUTES", v)
            _sql_module._sql_config_cache_clear()
            assert _sql_module._resolve_sql_retry_executes() is True, f"expected True for {v!r}"

    def test_executes_falsy_strings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All recognised falsy env values map to False."""
        for v in ("0", "false", "FALSE", "False", "no", "off", ""):
            monkeypatch.setenv("FABRIC_SQL_RETRY_EXECUTES", v)
            _sql_module._sql_config_cache_clear()
            assert _sql_module._resolve_sql_retry_executes() is False, f"expected False for {v!r}"


# ---------------------------------------------------------------------------
# SQL retry execute-widening — fetch="none" opt-in
# ---------------------------------------------------------------------------


class TestSqlRetryExecutes:
    """sql_retry_executes=True widens execute retry to cover fetch='none' statements."""

    @pytest.fixture(autouse=True)
    def _disable_sleep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_sql_module, "time", _make_fake_time_module())

    def test_fetch_none_retried_when_sql_retry_executes_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With FABRIC_SQL_RETRY_EXECUTES=1, a transient error on fetch='none' IS retried."""
        monkeypatch.setenv("FABRIC_SQL_RETRY_EXECUTES", "1")
        _sql_module._sql_config_cache_clear()

        mock_mssql, _, _ = _make_mock_mssql()

        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor

        good_cursor = MagicMock()
        good_cursor.description = None
        good_conn = MagicMock()
        good_conn.cursor.return_value = good_cursor

        mock_mssql.connect.side_effect = [bad_conn, good_conn]
        _patch_mssql(monkeypatch, mock_mssql)

        cols, rows = run_query(_make_target(), "INSERT INTO t VALUES (1)", fetch="none")

        # Retry fired — two connect calls.
        assert mock_mssql.connect.call_count == 2
        assert cols == []
        assert rows == []

    def test_fetch_none_not_retried_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default (sql_retry_executes=False): fetch='none' transient error is NOT retried.

        This is the D10 idempotency guarantee — re-validates it with the new
        _resolve_sql_retry_executes() path.
        """
        # env var is already unset by _clear_sql_config_cache autouse fixture.
        mock_mssql, _, _ = _make_mock_mssql()

        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = Exception("communication link failure")
        bad_conn = MagicMock()
        bad_conn.cursor.return_value = bad_cursor
        mock_mssql.connect.return_value = bad_conn
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="communication link failure"):
            run_query(_make_target(), "INSERT INTO t VALUES (1)", fetch="none")

        # Must NOT retry — exactly 1 connect attempt.
        assert mock_mssql.connect.call_count == 1, (
            "DML (fetch='none') must not retry on execute-phase transient errors by default"
        )


# ---------------------------------------------------------------------------
# _pool_enabled — 3-layer precedence (env > config > default True)
# ---------------------------------------------------------------------------


class TestPoolEnabledConfig:
    """_pool_enabled follows the 3-layer rule: env > config > built-in True.

    Note: the module-level ``_disable_pool_by_default`` autouse fixture sets
    ``FABRIC_CONN_POOLING=0`` for every test in this module.  Tests that need to
    remove the env var entirely (to test config or default fall-through) call
    ``monkeypatch.delenv("FABRIC_CONN_POOLING", raising=False)`` to undo it.
    """

    def test_env_zero_disables_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_CONN_POOLING=0 disables pooling even when config says True."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "0")
        cfg = UserConfig(defaults=Defaults(conn_pooling=True))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._pool_enabled() is False

    def test_env_one_enables_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_CONN_POOLING=1 enables pooling even when config says False."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "1")
        cfg = UserConfig(defaults=Defaults(conn_pooling=False))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._pool_enabled() is True

    def test_env_false_string_disables_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_CONN_POOLING=false (string) disables pooling."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "false")
        assert _sql_module._pool_enabled() is False

    def test_env_off_string_disables_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_CONN_POOLING=off (string) disables pooling."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "off")
        assert _sql_module._pool_enabled() is False

    def test_env_no_string_disables_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_CONN_POOLING=no (string) disables pooling."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "no")
        assert _sql_module._pool_enabled() is False

    def test_env_empty_string_falls_through_to_default_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FABRIC_CONN_POOLING='' (empty string) is treated as absent — pooling stays ON.

        An empty placeholder (e.g. Docker ``ENV FABRIC_CONN_POOLING=``) must not
        silently disable pooling.  Only a non-empty falsy value disables it.
        """
        monkeypatch.setenv("FABRIC_CONN_POOLING", "")
        assert _sql_module._pool_enabled() is True

    def test_env_whitespace_only_falls_through_to_default_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FABRIC_CONN_POOLING='  ' (whitespace only) is treated as absent — pooling stays ON."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "   ")
        assert _sql_module._pool_enabled() is True

    def test_env_true_string_enables_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_CONN_POOLING=true enables pooling."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "true")
        assert _sql_module._pool_enabled() is True

    def test_env_any_non_falsy_string_enables_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any non-falsy FABRIC_CONN_POOLING value enables pooling."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "yes")
        assert _sql_module._pool_enabled() is True

    def test_config_false_disables_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config conn_pooling=False disables pooling when env var is absent."""
        monkeypatch.delenv("FABRIC_CONN_POOLING", raising=False)
        cfg = UserConfig(defaults=Defaults(conn_pooling=False))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._pool_enabled() is False

    def test_config_true_enables_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config conn_pooling=True enables pooling when env var is absent."""
        monkeypatch.delenv("FABRIC_CONN_POOLING", raising=False)
        cfg = UserConfig(defaults=Defaults(conn_pooling=True))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._pool_enabled() is True

    def test_falls_back_to_true_when_no_env_no_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When neither env var nor config is set, pooling is enabled (default True)."""
        monkeypatch.delenv("FABRIC_CONN_POOLING", raising=False)
        # _isolate_sql_config autouse fixture already cleared the cache.
        assert _sql_module._pool_enabled() is True

    def test_config_none_falls_back_to_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config conn_pooling=None (unset) falls through to the built-in True default."""
        monkeypatch.delenv("FABRIC_CONN_POOLING", raising=False)
        cfg = UserConfig(defaults=Defaults(conn_pooling=None))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._pool_enabled() is True

    def test_env_wins_over_config_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_CONN_POOLING=1 wins over config conn_pooling=False."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "1")
        cfg = UserConfig(defaults=Defaults(conn_pooling=False))
        _sql_module._sql_config_cache = cfg
        assert _sql_module._pool_enabled() is True


# ---------------------------------------------------------------------------
# Row normalisation — run_query must return real tuples (#718 / #719)
# ---------------------------------------------------------------------------


class _FakeRow:
    """Sequence-compatible non-tuple stand-in for mssql_python.row.Row.

    mssql_python returns Row objects from fetchall() / fetchone().  They are
    iterable and index-accessible but are NOT tuple subclasses.  This minimal
    class reproduces that contract so the tests are driver-independent.
    """

    def __init__(self, *values: object) -> None:
        self._values = values

    def __iter__(self):  # type: ignore[return]
        return iter(self._values)

    def __getitem__(self, idx: int) -> object:
        return self._values[idx]

    def __len__(self) -> int:
        return len(self._values)


class TestRunQueryRowNormalisation:
    """run_query must normalise driver Row objects to real Python tuples."""

    def _make_mssql_with_rows(
        self,
        rows: list,
        fetchone_row: object | None = None,
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        """Return (mssql_module, conn, cursor) with controlled fetchall/fetchone."""
        cursor = MagicMock()
        cursor.description = [("col1", None), ("col2", None)]
        cursor.fetchall.return_value = rows
        cursor.fetchone.return_value = fetchone_row
        cursor.rowcount = len(rows)
        conn = MagicMock()
        conn.cursor.return_value = cursor
        mssql = MagicMock()
        mssql.connect.return_value = conn
        return mssql, conn, cursor

    def test_fetchall_row_objects_become_tuples(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetchall returning Row objects must be normalised to real tuples."""
        fake_rows = [_FakeRow(1, "alpha"), _FakeRow(2, "beta")]
        mssql, _, _ = self._make_mssql_with_rows(fake_rows)
        _patch_mssql(monkeypatch, mssql)

        _, rows = run_query(_make_target(), "SELECT col1, col2 FROM t")

        assert len(rows) == 2
        for row in rows:
            assert type(row) is tuple, f"expected tuple, got {type(row)}"
        assert rows[0] == (1, "alpha")
        assert rows[1] == (2, "beta")

    def test_fetchall_plain_tuples_pass_through_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Normalisation is idempotent: real tuples stay real tuples."""
        plain_rows = [(10, "x"), (20, "y")]
        mssql, _, _ = self._make_mssql_with_rows(plain_rows)
        _patch_mssql(monkeypatch, mssql)

        _, rows = run_query(_make_target(), "SELECT col1, col2 FROM t")

        assert rows == [(10, "x"), (20, "y")]
        for row in rows:
            assert type(row) is tuple

    def test_fetchone_row_object_becomes_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetchone returning a Row object must also be normalised to a real tuple."""
        fake_row = _FakeRow(99, "single")
        mssql, _, _ = self._make_mssql_with_rows([], fetchone_row=fake_row)
        _patch_mssql(monkeypatch, mssql)

        _, rows = run_query(_make_target(), "SELECT col1, col2 FROM t", fetch="one")

        assert len(rows) == 1
        assert type(rows[0]) is tuple
        assert rows[0] == (99, "single")

    def test_fetchone_none_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fetchone returning None must produce an empty row list, not a list with None."""
        mssql, _, _ = self._make_mssql_with_rows([], fetchone_row=None)
        _patch_mssql(monkeypatch, mssql)

        _, rows = run_query(_make_target(), "SELECT col1, col2 FROM t", fetch="one")

        assert rows == []

    def test_zip_with_cols_works_after_normalisation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dict(zip(cols, row)) must produce correct column→value mapping after normalisation.

        This is the exact pattern used in query_insights._execute_sql (#719).
        """
        fake_rows = [_FakeRow("sess-1", "conn-1"), _FakeRow("sess-2", "conn-2")]
        mssql, _, cursor = self._make_mssql_with_rows(fake_rows)
        cursor.description = [("session_id", None), ("connection_id", None)]
        _patch_mssql(monkeypatch, mssql)

        cols, rows = run_query(_make_target(), "SELECT session_id, connection_id FROM t")

        dicts = [dict(zip(cols, r, strict=True)) for r in rows]
        assert dicts[0] == {"session_id": "sess-1", "connection_id": "conn-1"}
        assert dicts[1] == {"session_id": "sess-2", "connection_id": "conn-2"}


# ---------------------------------------------------------------------------
# _clean_driver_error_message — noise-stripping helper
# ---------------------------------------------------------------------------


class TestCleanDriverErrorMessage:
    """Tests for _clean_driver_error_message (private helper, accessed via module)."""

    def _clean(self, msg: str) -> str:
        return _sql_module._clean_driver_error_message(msg)  # type: ignore[attr-defined]

    def test_strips_driver_noise_prefix(self) -> None:
        """The driver-noise prefix is removed, leaving only the SQL Server message."""
        raw = (
            "Driver Error: Column not found; DDBC Error: "
            "[Microsoft][SQL Server]Invalid column name 'amount'."
        )
        assert self._clean(raw) == "[Microsoft][SQL Server]Invalid column name 'amount'."

    def test_case_insensitive_prefix_match(self) -> None:
        """Prefix matching is case-insensitive."""
        raw = "driver error: something; ddbc error: [SQL Server]Bad stuff."
        assert self._clean(raw) == "[SQL Server]Bad stuff."

    def test_no_prefix_returns_original(self) -> None:
        """A message without the prefix is returned unchanged."""
        raw = "[Microsoft][SQL Server]Invalid column name 'amount'."
        assert self._clean(raw) == raw

    def test_empty_string_returns_empty(self) -> None:
        assert self._clean("") == ""

    def test_multiple_semicolons_only_strips_first_segment(self) -> None:
        """Only the first 'Driver Error: ...; DDBC Error:' segment is stripped."""
        raw = "Driver Error: Col not found; DDBC Error: [SQL Server]Err; extra stuff."
        assert self._clean(raw) == "[SQL Server]Err; extra stuff."


# ---------------------------------------------------------------------------
# _wrap_unmapped_driver_error — FabricServerError wrapper
# ---------------------------------------------------------------------------


class _DriverExcWithDdbcError(Exception):
    """Minimal stand-in for a driver exception with a ddbc_error attribute."""

    def __init__(self, msg: str, ddbc_error: str) -> None:
        super().__init__(msg)
        self.ddbc_error = ddbc_error


class TestWrapUnmappedDriverError:
    """Tests for _wrap_unmapped_driver_error (private helper, accessed via module)."""

    def _wrap(self, exc: BaseException) -> FabricServerError | None:
        return _sql_module._wrap_unmapped_driver_error(exc)  # type: ignore[attr-defined]

    def _make_driver_exc_with_ddbc(self, msg: str, ddbc_error: str) -> _DriverExcWithDdbcError:
        return _DriverExcWithDdbcError(msg, ddbc_error)

    def test_returns_fabric_server_error_for_ddbc_error(self) -> None:
        """An exception with ddbc_error is wrapped in FabricServerError."""
        exc = self._make_driver_exc_with_ddbc(
            "Driver Error: Column not found; DDBC Error: [SQL Server]Invalid column name 'amount'.",
            "[Microsoft][SQL Server]Invalid column name 'amount'.",
        )
        result = self._wrap(exc)
        assert isinstance(result, FabricServerError)

    def test_message_uses_ddbc_error_content(self) -> None:
        """The wrapped message comes from ddbc_error, not the noisy full string."""
        exc = self._make_driver_exc_with_ddbc(
            "Driver Error: Column not found; DDBC Error: [SQL Server]Invalid column name 'amount'.",
            "[Microsoft][SQL Server]Invalid column name 'amount'.",
        )
        result = self._wrap(exc)
        assert result is not None
        assert "Invalid column name 'amount'" in str(result)
        assert "Driver Error:" not in str(result)
        assert "DDBC Error:" not in str(result)

    def test_returns_none_for_exception_without_ddbc_error(self) -> None:
        """An exception without ddbc_error (e.g. network/cursor error) returns None."""
        exc = Exception("Invalid cursor state")
        assert self._wrap(exc) is None

    def test_returns_none_for_plain_exception(self) -> None:
        """A plain Exception with no ddbc_error attribute returns None."""
        exc = Exception("connection timed out")
        assert self._wrap(exc) is None

    def test_not_found_208_wraps_as_fabric_server_error_in_isolation(self) -> None:
        """_wrap_unmapped_driver_error wraps error-208 as FabricServerError when called alone.

        _wrap_unmapped_driver_error does not know about error numbers — it wraps
        any exception that carries a ddbc_error attribute.  In run_query the
        callers invoke map_driver_error FIRST (which returns NotFoundError for 208),
        so _wrap_unmapped_driver_error is never reached for known error numbers.
        When called in isolation the wrapper returns FabricServerError; the
        prioritisation is enforced by the call order in run_query, not by _wrap.
        """
        exc = self._make_driver_exc_with_ddbc(
            "Invalid object name 'dbo.x'",
            "Error: 208 Invalid object name 'dbo.x'",
        )
        result = self._wrap(exc)
        assert isinstance(result, FabricServerError)


# ---------------------------------------------------------------------------
# run_query — unmapped driver SQL error wrapping (#747)
# ---------------------------------------------------------------------------


class _DriverSqlExecError(Exception):
    """Minimal stand-in for mssql_python.exceptions.ProgrammingError with ddbc_error."""

    def __init__(self, msg: str, ddbc_error: str) -> None:
        super().__init__(msg)
        self.ddbc_error = ddbc_error


class TestRunQueryUnmappedDriverError:
    """run_query must surface unmapped driver SQL errors as FabricServerError."""

    @staticmethod
    def _make_driver_execute_exc(msg: str, ddbc_error: str) -> _DriverSqlExecError:
        """Return a driver-like exception with ddbc_error attribute."""
        return _DriverSqlExecError(msg, ddbc_error)

    def test_invalid_column_raises_fabric_server_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A driver ProgrammingError with ddbc_error raises FabricServerError, not a raw error."""
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        exc = self._make_driver_execute_exc(
            "Driver Error: Column not found; DDBC Error: [SQL Server]Invalid column name 'amount'.",
            "[Microsoft][SQL Server]Invalid column name 'amount'.",
        )
        mock_cursor.execute.side_effect = exc
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(FabricServerError) as exc_info:
            run_query(_make_target(), "CREATE OR ALTER VIEW [dbo].[v] AS SELECT amount FROM t")

        assert "Invalid column name 'amount'" in str(exc_info.value)

    def test_invalid_column_message_no_driver_noise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The FabricServerError message must not contain the driver-noise prefix."""
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        exc = self._make_driver_execute_exc(
            "Driver Error: Column not found; DDBC Error: [SQL Server]Invalid column name 'x'.",
            "[Microsoft][SQL Server]Invalid column name 'x'.",
        )
        mock_cursor.execute.side_effect = exc
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(FabricServerError) as exc_info:
            run_query(_make_target(), "SELECT x FROM t", fetch="none")

        msg = str(exc_info.value)
        assert "Driver Error:" not in msg
        assert "DDBC Error:" not in msg

    def test_fetch_none_invalid_column_raises_fabric_server_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fetch='none' DDL path also wraps unmapped driver SQL errors."""
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        exc = self._make_driver_execute_exc(
            "Driver Error: Column not found; DDBC Error: [SQL Server]Invalid column name 'amount'.",
            "[Microsoft][SQL Server]Invalid column name 'amount'.",
        )
        mock_cursor.execute.side_effect = exc
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(FabricServerError):
            run_query(
                _make_target(),
                "CREATE OR ALTER VIEW [dbo].[v] AS SELECT amount FROM t",
                fetch="none",
                commit=True,
            )

    def test_already_mapped_not_found_still_raises_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error 208 (NotFoundError) is mapped before the FabricServerError fallback."""
        mock_mssql, _, mock_cursor = _make_mock_mssql()

        class _Err208Error(Exception):
            ddbc_error = "Error: 208 Invalid object name 'dbo.missing'"

            def __str__(self) -> str:
                return "Invalid object name 'dbo.missing'"

        mock_cursor.execute.side_effect = _Err208Error()
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(NotFoundError):
            run_query(_make_target(), "SELECT * FROM [dbo].[missing]")

    def test_exception_without_ddbc_error_not_wrapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exception with no ddbc_error (e.g. cursor state error) propagates unchanged."""
        mock_mssql, _, mock_cursor = _make_mock_mssql()
        mock_cursor.execute.side_effect = Exception("Invalid cursor state")
        _patch_mssql(monkeypatch, mock_mssql)

        with pytest.raises(Exception, match="Invalid cursor state") as exc_info:
            run_query(_make_target(), "SELECT 1")

        # Must NOT be wrapped as FabricServerError.
        assert not isinstance(exc_info.value, FabricServerError)


# ---------------------------------------------------------------------------
# SQL DEBUG logging
# ---------------------------------------------------------------------------


class TestSqlDebugLogging:
    """DEBUG-level logging for executed SQL statements."""

    def test_run_query_emits_debug_log_with_sql(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """run_query must emit a DEBUG record containing the SQL text when DEBUG is enabled."""
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        with caplog.at_level("DEBUG", logger="fabric_dw.sql"):
            run_query(_make_target(), "SELECT 1 AS n")

        debug_records = [r for r in caplog.records if r.levelno == 10]  # logging.DEBUG == 10
        sql_seen = any(
            "SELECT 1 AS n" in r.getMessage() or getattr(r, "sql", None) == "SELECT 1 AS n"
            for r in debug_records
        )
        assert sql_seen, (
            f"Expected SQL in DEBUG records; got: {[r.getMessage() for r in debug_records]}"
        )

    def test_run_query_no_debug_log_when_level_is_info(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """run_query must NOT emit any record from fabric_dw.sql when DEBUG is disabled."""
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        with caplog.at_level("INFO", logger="fabric_dw.sql"):
            run_query(_make_target(), "SELECT 1 AS n")

        sql_records = [r for r in caplog.records if r.name == "fabric_dw.sql"]
        assert sql_records == [], (
            f"Expected no fabric_dw.sql records at INFO level; got: {sql_records}"
        )

    def test_run_statements_emits_debug_log_per_statement(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """run_statements must emit a DEBUG record for each executed statement."""
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        stmts = ["DROP TABLE [dbo].[t1]", "DROP VIEW [dbo].[v1]"]
        with caplog.at_level("DEBUG", logger="fabric_dw.sql"):
            run_statements(_make_target(), stmts)

        debug_records = [r for r in caplog.records if r.levelno == 10 and r.name == "fabric_dw.sql"]
        logged_sqls = [getattr(r, "sql", r.getMessage()) for r in debug_records]
        assert any("DROP TABLE [dbo].[t1]" in s for s in logged_sqls), (
            f"Expected first statement in DEBUG records; got: {logged_sqls}"
        )
        assert any("DROP VIEW [dbo].[v1]" in s for s in logged_sqls), (
            f"Expected second statement in DEBUG records; got: {logged_sqls}"
        )

    def test_run_statements_no_debug_log_when_level_is_info(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """run_statements must NOT emit fabric_dw.sql records when DEBUG is disabled."""
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        with caplog.at_level("INFO", logger="fabric_dw.sql"):
            run_statements(_make_target(), ["DROP TABLE [dbo].[t]"])

        sql_records = [r for r in caplog.records if r.name == "fabric_dw.sql"]
        assert sql_records == [], (
            f"Expected no fabric_dw.sql records at INFO level; got: {sql_records}"
        )

    def test_secret_not_logged_raw(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A COPY INTO with SECRET = '<token>' must not log the raw secret value."""
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        raw_secret = "sv=2024&sig=TOPSECRETTOKEN"  # noqa: S105
        copy_sql = (
            f"COPY INTO [dbo].[t] FROM 'https://x.blob.core.windows.net/c/f.parquet' "
            f"WITH (CREDENTIAL = (IDENTITY = 'Shared Access Signature', SECRET = '{raw_secret}'))"
        )

        with caplog.at_level("DEBUG", logger="fabric_dw.sql"):
            run_query(_make_target(), copy_sql)

        # The raw secret must NOT appear anywhere in any log record.
        all_log_text = " ".join(
            str(r.getMessage()) + str(getattr(r, "sql", "")) for r in caplog.records
        )
        assert "TOPSECRETTOKEN" not in all_log_text, (
            "Raw secret token must not appear in any log record"
        )
        assert "***" in all_log_text, "Redacted placeholder '***' must appear in log records"

    def test_bound_param_values_not_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Bound parameter VALUES must not appear in logs — only the count may be logged."""
        mock_mssql, _conn, _cursor = _make_mock_mssql()
        _patch_mssql(monkeypatch, mock_mssql)

        sentinel_secret = "VERYSECRETPARAMVALUE"  # noqa: S105
        sql = "SELECT * FROM [dbo].[t] WHERE col = ?"

        with caplog.at_level("DEBUG", logger="fabric_dw.sql"):
            run_query(_make_target(), sql, params=[sentinel_secret])

        all_log_text = " ".join(
            str(r.getMessage()) + str(getattr(r, "sql", "")) + str(getattr(r, "param_count", ""))
            for r in caplog.records
        )
        assert sentinel_secret not in all_log_text, (
            "Bound parameter value must not appear in any log record"
        )
