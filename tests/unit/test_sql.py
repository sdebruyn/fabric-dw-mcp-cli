"""Tests for fabric_dw.sql — stateless SQL helper (TDD, written before implementation)."""

from __future__ import annotations

import threading
from contextlib import closing
from unittest.mock import MagicMock

import pytest

import fabric_dw.sql as _sql_module
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import AuthError, PermissionDenied
from fabric_dw.sql import SqlTarget, build_connection_string, map_driver_error

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
        assert isinstance(result, PermissionDenied)

    def test_denied_the_right_to_fragment_returns_permission_denied(self) -> None:
        exc = Exception("denied the right to execute")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDenied)

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
        assert isinstance(result, PermissionDenied)

    def test_case_insensitive_matching(self) -> None:
        exc = Exception("PERMISSION WAS DENIED on the object")
        result = map_driver_error(exc)
        assert isinstance(result, PermissionDenied)

    def test_result_message_contains_original(self) -> None:
        original_msg = "permission was denied on SELECT"
        exc = Exception(original_msg)
        result = map_driver_error(exc)
        assert result is not None
        assert original_msg in str(result)
