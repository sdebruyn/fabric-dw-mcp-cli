"""Tests for services.procedures — DMV-mock tests + identifier-validator tests (TDD)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, NotFoundError, PermissionDeniedError
from fabric_dw.models import StoredProcedure
from fabric_dw.services import procedures
from fabric_dw.services.procedures import validate_identifier
from tests.unit.services._helpers import _make_conn, _make_conn_for_ddl, _make_target

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
_LATER = datetime(2024, 6, 2, 8, 30, 0, tzinfo=UTC)

_LIST_COLS = ["schema_name", "name", "created", "modified"]
_GET_COLS = ["schema_name", "name", "created", "modified", "definition"]

_PROC_ROW_1 = ("dbo", "usp_load", _NOW, _LATER)
_PROC_ROW_2 = ("finance", "usp_monthly", _NOW, _NOW)
_PROC_ROW_GET = ("dbo", "usp_load", _NOW, _LATER, "BEGIN SELECT 1 AS id END")
_PROC_ROW_GET_MOVED = ("archive", "usp_load", _NOW, _LATER, "BEGIN SELECT 1 AS id END")


# ===========================================================================
# identifier validator re-export
# ===========================================================================


class TestValidateIdentifier:
    def test_simple_valid_identifier(self) -> None:
        assert validate_identifier("my_proc") == "my_proc"

    def test_rejects_semicolon(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("proc;injection")

    def test_rejects_bracket(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("proc]name")


# ===========================================================================
# list_procedures
# ===========================================================================


class TestListProcedures:
    async def test_returns_empty_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await procedures.list_procedures(target)
        assert result == []

    async def test_returns_procedure_instances(self) -> None:
        target = _make_target()
        conn = _make_conn([_PROC_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await procedures.list_procedures(target)
        assert len(result) == 1
        assert isinstance(result[0], StoredProcedure)

    async def test_parses_fields_correctly(self) -> None:
        target = _make_target()
        conn = _make_conn([_PROC_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await procedures.list_procedures(target)
        p = result[0]
        assert p.schema_name == "dbo"
        assert p.name == "usp_load"
        assert p.qualified_name == "dbo.usp_load"
        assert p.created == _NOW
        assert p.modified == _LATER
        assert p.definition is None

    async def test_returns_all_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([_PROC_ROW_1, _PROC_ROW_2], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await procedures.list_procedures(target)
        assert len(result) == 2

    async def test_sql_references_sys_procedures(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await procedures.list_procedures(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.procedures" in call_sql

    async def test_sql_references_sys_schemas(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await procedures.list_procedures(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.schemas" in call_sql

    async def test_filters_by_schema_when_provided(self) -> None:
        target = _make_target()
        conn = _make_conn([_PROC_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await procedures.list_procedures(target, schema="dbo")
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        call_sql: str = call_args[0][0]
        assert "s.name = ?" in call_sql
        params = call_args[0][1] if len(call_args[0]) > 1 else []
        assert "dbo" in list(params)

    async def test_schema_filter_validates_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await procedures.list_procedures(target, schema="bad]schema")

    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn([_PROC_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await procedures.list_procedures(target)
        conn.close.assert_called_once()

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sys.procedures")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await procedures.list_procedures(target)

    async def test_maps_auth_error(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Authentication failed for user ''")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(AuthError),
        ):
            await procedures.list_procedures(target)

    async def test_unrelated_error_propagates(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("network timeout")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(RuntimeError, match="network timeout"),
        ):
            await procedures.list_procedures(target)

    async def test_no_endpoint_guard_raises(self) -> None:
        """list_procedures must NOT raise for SQL Analytics Endpoint targets.

        This test asserts the absence of a DW-only guard: the service must
        accept any SqlTarget regardless of target kind.
        """
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        # Build a target that looks like a SQL endpoint (kind is not checked at service level)
        endpoint_target = SqlTarget(
            workspace_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            database="MyLakehouse",
            connection_string="ep.datawarehouse.fabric.microsoft.com",
        )
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            # Must not raise — no endpoint guard
            result = await procedures.list_procedures(endpoint_target)
        assert isinstance(result, list)


# ===========================================================================
# get_procedure
# ===========================================================================


class TestGetProcedure:
    async def test_returns_procedure_with_definition(self) -> None:
        target = _make_target()
        conn = _make_conn([_PROC_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await procedures.get_procedure(target, "dbo", "usp_load")
        assert isinstance(result, StoredProcedure)
        assert result.definition == "BEGIN SELECT 1 AS id END"

    async def test_parses_all_fields(self) -> None:
        target = _make_target()
        conn = _make_conn([_PROC_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await procedures.get_procedure(target, "dbo", "usp_load")
        assert result.schema_name == "dbo"
        assert result.name == "usp_load"
        assert result.qualified_name == "dbo.usp_load"
        assert result.created == _NOW
        assert result.modified == _LATER

    async def test_sql_includes_sys_sql_modules(self) -> None:
        target = _make_target()
        conn = _make_conn([_PROC_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await procedures.get_procedure(target, "dbo", "usp_load")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.sql_modules" in call_sql

    async def test_raises_not_found_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(NotFoundError),
        ):
            await procedures.get_procedure(target, "dbo", "nonexistent")

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await procedures.get_procedure(target, "bad;schema", "usp_load")

    async def test_validates_procedure_name_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await procedures.get_procedure(target, "dbo", "usp--injection")

    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn([_PROC_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await procedures.get_procedure(target, "dbo", "usp_load")
        conn.close.assert_called_once()

    async def test_returns_raw_definition_when_header_is_blank(self) -> None:
        """get_procedure returns the raw Fabric definition without patching the CREATE header."""
        raw_def = "CREATE PROCEDURE . AS BEGIN SELECT 1 END"
        row = ("dbo", "usp_load", _NOW, _LATER, raw_def)
        target = _make_target()
        conn = _make_conn([row], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await procedures.get_procedure(target, "dbo", "usp_load")
        assert result.definition == raw_def

    async def test_returns_raw_definition_bare_dot_form(self) -> None:
        """get_procedure returns bare-dot definitions unchanged (no header-patching)."""
        raw_def = "CREATE PROCEDURE . AS BEGIN SELECT id, label FROM fdw_qa.t_ctas END"
        row = ("fdw_qa", "usp_load", _NOW, _LATER, raw_def)
        target = _make_target()
        conn = _make_conn([row], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await procedures.get_procedure(target, "fdw_qa", "usp_load")
        assert result.definition == raw_def

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on sys.sql_modules")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await procedures.get_procedure(target, "dbo", "usp_load")


# ===========================================================================
# create_procedure
# ===========================================================================


class TestCreateProcedure:
    async def test_emits_create_procedure_ddl(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await procedures.create_procedure(target, "dbo", "usp_load", "BEGIN SELECT 1 END")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "CREATE PROCEDURE" in call_sql.upper()
        assert "[dbo]" in call_sql
        assert "[usp_load]" in call_sql

    async def test_includes_body(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await procedures.create_procedure(target, "dbo", "usp_load", "BEGIN SELECT 1 END")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "BEGIN SELECT 1 END" in call_sql

    async def test_returns_procedure_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await procedures.create_procedure(
                target, "dbo", "usp_load", "BEGIN SELECT 1 END"
            )

        assert isinstance(result, StoredProcedure)
        assert result.schema_name == "dbo"
        assert result.name == "usp_load"

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.create_procedure(target, "bad]schema", "usp_load", "BEGIN END")

    async def test_validates_procedure_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.create_procedure(target, "dbo", "usp;drop", "BEGIN END")

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await procedures.create_procedure(target, "dbo", "usp_load", "BEGIN SELECT 1 END")

        ddl_conn.commit.assert_called_once()

    async def test_maps_permission_denied_on_ddl(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on database")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await procedures.create_procedure(target, "dbo", "usp_load", "BEGIN END")

    async def test_rejects_identifier_injection_via_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.create_procedure(
                target, "x]; DROP TABLE users--", "usp_ok", "BEGIN END"
            )

    async def test_rejects_identifier_injection_via_proc_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.create_procedure(
                target, "dbo", "usp_ok] WITH EXECUTE AS OWNER--", "BEGIN END"
            )

    async def test_create_procedure_returns_raw_definition(self) -> None:
        """create_procedure returns the raw Fabric definition without header-patching."""
        raw_def = "CREATE PROCEDURE . AS BEGIN SELECT id, label FROM fdw_qa.t_ctas END"
        fetch_row = ("fdw_qa", "usp_load", _NOW, _LATER, raw_def)
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([fetch_row], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await procedures.create_procedure(
                target,
                "fdw_qa",
                "usp_load",
                "BEGIN SELECT id, label FROM fdw_qa.t_ctas END",
            )

        assert result.definition == raw_def


# ===========================================================================
# update_procedure
# ===========================================================================


class TestUpdateProcedure:
    async def test_emits_create_or_alter_procedure_ddl(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await procedures.update_procedure(target, "dbo", "usp_load", "BEGIN SELECT 2 END")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "CREATE OR ALTER PROCEDURE" in call_sql

    async def test_uses_brackets_for_schema_and_name(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await procedures.update_procedure(target, "dbo", "usp_load", "BEGIN END")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[usp_load]" in call_sql

    async def test_returns_procedure_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await procedures.update_procedure(target, "dbo", "usp_load", "BEGIN END")

        assert isinstance(result, StoredProcedure)

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.update_procedure(target, "bad--schema", "usp_load", "BEGIN END")

    async def test_validates_procedure_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.update_procedure(target, "dbo", "usp;injection", "BEGIN END")

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await procedures.update_procedure(target, "dbo", "usp_load", "BEGIN END")

        ddl_conn.commit.assert_called_once()

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to alter procedure")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await procedures.update_procedure(target, "dbo", "usp_load", "BEGIN END")


# ===========================================================================
# drop_procedure
# ===========================================================================


class TestDropProcedure:
    async def test_emits_drop_procedure_ddl(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await procedures.drop_procedure(target, "dbo", "usp_load")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP PROCEDURE" in call_sql

    async def test_uses_brackets_for_schema_and_name(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await procedures.drop_procedure(target, "dbo", "usp_load")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[usp_load]" in call_sql

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await procedures.drop_procedure(target, "dbo", "usp_load")
        conn.commit.assert_called_once()

    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await procedures.drop_procedure(target, "dbo", "usp_load")
        conn.close.assert_called_once()

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.drop_procedure(target, "bad]schema", "usp_load")

    async def test_validates_procedure_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.drop_procedure(target, "dbo", "usp--bad")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to drop procedure")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await procedures.drop_procedure(target, "dbo", "usp_load")

    async def test_rejects_injection_in_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.drop_procedure(target, "x]; DROP TABLE users--", "usp_ok")

    async def test_rejects_injection_in_procedure_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.drop_procedure(target, "dbo", "usp_ok] WHERE 1=1--")

    async def test_unrelated_error_propagates(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("connection reset")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(RuntimeError, match="connection reset"),
        ):
            await procedures.drop_procedure(target, "dbo", "usp_load")


# ===========================================================================
# transfer_procedure
# ===========================================================================


class TestTransferProcedure:
    async def test_emits_alter_schema_transfer(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET_MOVED], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await procedures.transfer_procedure(target, "dbo.usp_load", "archive")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert call_sql == "ALTER SCHEMA [archive] TRANSFER OBJECT::[dbo].[usp_load]"

    async def test_returns_procedure_from_target_schema(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET_MOVED], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await procedures.transfer_procedure(target, "dbo.usp_load", "archive")
        assert isinstance(result, StoredProcedure)
        assert result.schema_name == "archive"
        assert result.name == "usp_load"
        assert result.qualified_name == "archive.usp_load"

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET_MOVED], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await procedures.transfer_procedure(target, "dbo.usp_load", "archive")
        ddl_conn.commit.assert_called_once()

    async def test_no_endpoint_guard_raises(self) -> None:
        """transfer_procedure must NOT raise for SQL Analytics Endpoint targets.

        This asserts the absence of a DW-only guard: stored procedure transfer
        is supported on both Fabric Data Warehouses and SQL Analytics Endpoints.
        """
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_PROC_ROW_GET_MOVED], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            # Must not raise — no endpoint guard, no `kind` parameter at all.
            result = await procedures.transfer_procedure(target, "dbo.usp_load", "archive")
        assert isinstance(result, StoredProcedure)

    async def test_rejects_undotted_qualified_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="qualified"):
            await procedures.transfer_procedure(target, "nodot", "archive")

    async def test_rejects_invalid_schema_in_qualified_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.transfer_procedure(target, "bad--schema.usp_load", "archive")

    async def test_rejects_invalid_procedure_in_qualified_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.transfer_procedure(target, "dbo.bad--proc", "archive")

    async def test_rejects_invalid_target_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await procedures.transfer_procedure(target, "dbo.usp_load", "bad--schema")

    @pytest.mark.parametrize(
        "reserved", ["sys", "information_schema", "SYS", "Information_Schema", "guest", "db_owner"]
    )
    async def test_rejects_reserved_target_schema(self, reserved: str) -> None:
        """transfer_procedure propagates the system-schema rejection from the shared helper.

        The check itself (and its full enumeration of system schemas) is
        exercised exhaustively in TestAlterSchemaTransfer; this is a thin
        pass-through test confirming transfer_procedure does not bypass it.
        """
        target = _make_target()
        with pytest.raises(ValueError, match="reserved system schema"):
            await procedures.transfer_procedure(target, "dbo.usp_load", reserved)

    async def test_raises_not_found_when_fetch_returns_empty(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]),
            pytest.raises(NotFoundError, match="No procedure named"),
        ):
            await procedures.transfer_procedure(target, "dbo.usp_load", "archive")

    async def test_not_found_message_warns_about_non_procedure_objects(self) -> None:
        """The post-transfer NotFoundError must call out that a same-named
        non-procedure object (table/view/function) may have been moved
        instead, since OBJECT::[schema].[name] matches any schema-scoped
        object, not only procedures.
        """
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]),
            pytest.raises(NotFoundError, match="not only procedures"),
        ):
            await procedures.transfer_procedure(target, "dbo.usp_load", "archive")
