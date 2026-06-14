"""Tests for fabric_dw.sql — stateless SQL helper (TDD, written before implementation)."""

from __future__ import annotations

import threading
from contextlib import closing
from unittest.mock import MagicMock

import pytest

import fabric_dw.sql as _sql_module
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import AuthError, NotFoundError, PermissionDeniedError
from fabric_dw.sql import (
    SqlTarget,
    build_connection_string,
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
    monkeypatch.setenv("FABRIC_SQL_POOL", "0")
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
        """Error number 230 (INSERT permission denied) -> PermissionDeniedError."""
        exc = self._make_driver_exc("some driver error", "(230) INSERT permission denied")
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
        """Error number 208 in parenthesised form -> NotFoundError."""
        exc = self._make_driver_exc("some driver error", "(208) Invalid object name")
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
        monkeypatch.setenv("FABRIC_SQL_POOL", "1")
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
        """FABRIC_SQL_POOL=0 disables the pool — every call opens+closes."""
        monkeypatch.setenv("FABRIC_SQL_POOL", "0")

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


def _make_fake_time_module() -> MagicMock:
    """Return a fake ``time`` module whose ``sleep`` is a no-op."""
    import time as _time  # noqa: PLC0415

    fake = MagicMock()
    fake.sleep = MagicMock()  # no-op
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

    def test_run_query_raises_after_all_retries_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When all retry attempts hit transient errors the last error is re-raised."""
        original_retries = _sql_module.SQL_TRANSIENT_MAX_RETRIES
        _sql_module.SQL_TRANSIENT_MAX_RETRIES = 2
        try:
            mock_mssql, _, _ = _make_mock_mssql()
            bad_cursor = MagicMock()
            bad_cursor.execute.side_effect = Exception("communication link failure")
            bad_conn = MagicMock()
            bad_conn.cursor.return_value = bad_cursor
            mock_mssql.connect.return_value = bad_conn
            _patch_mssql(monkeypatch, mock_mssql)

            with pytest.raises(Exception, match="communication link failure"):
                run_query(_make_target(), "SELECT 1")

            # 1 original + 2 retries = 3 connect calls
            assert mock_mssql.connect.call_count == 3
        finally:
            _sql_module.SQL_TRANSIENT_MAX_RETRIES = original_retries

    def test_run_query_does_not_retry_when_retries_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SQL_TRANSIENT_MAX_RETRIES=0 disables retry entirely."""
        original_retries = _sql_module.SQL_TRANSIENT_MAX_RETRIES
        _sql_module.SQL_TRANSIENT_MAX_RETRIES = 0
        try:
            mock_mssql, _, _ = _make_mock_mssql()
            bad_cursor = MagicMock()
            bad_cursor.execute.side_effect = Exception("communication link failure")
            bad_conn = MagicMock()
            bad_conn.cursor.return_value = bad_cursor
            mock_mssql.connect.return_value = bad_conn
            _patch_mssql(monkeypatch, mock_mssql)

            with pytest.raises(Exception, match="communication link failure"):
                run_query(_make_target(), "SELECT 1")

            assert mock_mssql.connect.call_count == 1
        finally:
            _sql_module.SQL_TRANSIENT_MAX_RETRIES = original_retries

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
        # attrs_before must be None (falsy) — no token injected.
        assert not call_kwargs.get("attrs_before")
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
