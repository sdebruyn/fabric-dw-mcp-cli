"""Tests for services.functions — DMV-mock tests + identifier-validator tests (TDD)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, NotFoundError, PermissionDeniedError
from fabric_dw.models import Function, FunctionDetails, FunctionKind, FunctionParameter
from fabric_dw.services import functions
from fabric_dw.services.functions import validate_identifier, validate_kind
from tests.unit.services._helpers import _make_conn, _make_conn_for_ddl, _make_target

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
_LATER = datetime(2024, 6, 2, 8, 30, 0, tzinfo=UTC)

_LIST_COLS = ["schema_name", "name", "type", "type_desc", "created", "modified", "is_inlineable"]
_GET_COLS = [
    "schema_name",
    "name",
    "type",
    "type_desc",
    "created",
    "modified",
    "definition",
    "is_inlineable",
]
_PARAM_COLS = ["parameter_id", "name", "data_type", "max_length", "is_output"]

# FN = scalar, IF = inline TVF, TF = mstvf
_FN_ROW_1 = ("dbo", "fn_clean", "FN", "SQL_SCALAR_FUNCTION", _NOW, _LATER, 1)
_FN_ROW_2 = ("finance", "fn_calc_tax", "FN", "SQL_SCALAR_FUNCTION", _NOW, _NOW, 0)
_IF_ROW = ("dbo", "fn_get_orders", "IF", "SQL_INLINE_TABLE_VALUED_FUNCTION", _NOW, _LATER, 1)
_TF_ROW = ("dbo", "fn_complex", "TF", "SQL_TABLE_VALUED_FUNCTION", _NOW, _NOW, None)

_GET_ROW_FN = (
    "dbo",
    "fn_clean",
    "FN",
    "SQL_SCALAR_FUNCTION",
    _NOW,
    _LATER,
    "(@input NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN LTRIM(RTRIM(@input)) END",
    1,
)
_GET_ROW_IF = (
    "dbo",
    "fn_get_orders",
    "IF",
    "SQL_INLINE_TABLE_VALUED_FUNCTION",
    _NOW,
    _LATER,
    "(@cust_id INT) RETURNS TABLE AS RETURN (SELECT * FROM dbo.orders WHERE customer_id = @cust_id)",  # noqa: E501
    1,
)

_PARAM_RETURN = (0, "", "nvarchar", 200, False)
_PARAM_INPUT = (1, "@input", "nvarchar", 200, False)


# ===========================================================================
# identifier validator re-export
# ===========================================================================


class TestValidateIdentifier:
    def test_simple_valid_identifier(self) -> None:
        assert validate_identifier("fn_clean") == "fn_clean"

    def test_rejects_semicolon(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("fn;injection")

    def test_rejects_bracket(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("fn]name")

    def test_rejects_dash_dash(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("fn--injection")

    def test_rejects_injection_in_schema(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("x]; DROP TABLE users--")

    def test_rejects_injection_in_name(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("fn_ok] WITH EXECUTE AS OWNER--")


# ===========================================================================
# _row_to_function — kind parsing
# ===========================================================================


class TestRowToFunction:
    def test_fn_mapped_to_scalar(self) -> None:
        result = functions._row_to_function(_LIST_COLS, _FN_ROW_1)
        assert result.kind == FunctionKind.SCALAR

    def test_if_mapped_to_inline_tvf(self) -> None:
        result = functions._row_to_function(_LIST_COLS, _IF_ROW)
        assert result.kind == FunctionKind.INLINE_TVF

    def test_tf_mapped_to_mstvf(self) -> None:
        result = functions._row_to_function(_LIST_COLS, _TF_ROW)
        assert result.kind == FunctionKind.MSTVF

    def test_is_inlineable_true(self) -> None:
        result = functions._row_to_function(_LIST_COLS, _FN_ROW_1)
        assert result.is_inlineable is True

    def test_is_inlineable_false(self) -> None:
        result = functions._row_to_function(_LIST_COLS, _FN_ROW_2)
        assert result.is_inlineable is False

    def test_is_inlineable_none_for_tf(self) -> None:
        result = functions._row_to_function(_LIST_COLS, _TF_ROW)
        assert result.is_inlineable is None

    def test_qualified_name_built(self) -> None:
        result = functions._row_to_function(_LIST_COLS, _FN_ROW_1)
        assert result.qualified_name == "dbo.fn_clean"

    def test_dates_set(self) -> None:
        result = functions._row_to_function(_LIST_COLS, _FN_ROW_1)
        assert result.created == _NOW
        assert result.modified == _LATER


# ===========================================================================
# list_functions
# ===========================================================================


class TestListFunctions:
    async def test_returns_empty_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await functions.list_functions(target)
        assert result == []

    async def test_returns_function_instances(self) -> None:
        target = _make_target()
        conn = _make_conn([_FN_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await functions.list_functions(target)
        assert len(result) == 1
        assert isinstance(result[0], Function)

    async def test_parses_fields_correctly(self) -> None:
        target = _make_target()
        conn = _make_conn([_FN_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await functions.list_functions(target)
        f = result[0]
        assert f.schema_name == "dbo"
        assert f.name == "fn_clean"
        assert f.qualified_name == "dbo.fn_clean"
        assert f.kind == FunctionKind.SCALAR
        assert f.is_inlineable is True
        assert f.created == _NOW
        assert f.modified == _LATER

    async def test_returns_all_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([_FN_ROW_1, _FN_ROW_2, _IF_ROW], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await functions.list_functions(target)
        assert len(result) == 3

    async def test_sql_references_sys_objects(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.list_functions(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.objects" in call_sql

    async def test_sql_references_sys_sql_modules(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.list_functions(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.sql_modules" in call_sql

    async def test_filters_by_schema_when_provided(self) -> None:
        target = _make_target()
        conn = _make_conn([_FN_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.list_functions(target, schema="dbo")
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
            await functions.list_functions(target, schema="bad]schema")

    async def test_kind_scalar_filters_fn(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.list_functions(target, kind="scalar")
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        call_sql: str = call_args[0][0]
        assert "o.type = ?" in call_sql
        params = list(call_args[0][1]) if len(call_args[0]) > 1 else []
        assert "FN" in params

    async def test_kind_inline_tvf_filters_if(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.list_functions(target, kind="inline-tvf")
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        call_sql: str = call_args[0][0]
        assert "o.type = ?" in call_sql
        params = list(call_args[0][1]) if len(call_args[0]) > 1 else []
        assert "IF" in params

    async def test_kind_all_includes_fn_if_tf(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.list_functions(target, kind="all")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "IN ('FN', 'IF', 'TF')" in call_sql

    async def test_no_endpoint_guard_raises(self) -> None:
        """list_functions must NOT raise for SQL Analytics Endpoint targets."""
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        endpoint_target = SqlTarget(
            workspace_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            database="MyLakehouse",
            connection_string="ep.datawarehouse.fabric.microsoft.com",
        )
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await functions.list_functions(endpoint_target)
        assert isinstance(result, list)

    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn([_FN_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.list_functions(target)
        conn.close.assert_called_once()

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sys.objects")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await functions.list_functions(target)

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
            await functions.list_functions(target)


# ===========================================================================
# get_function
# ===========================================================================


class TestGetFunction:
    async def test_returns_function_details_with_definition(self) -> None:
        target = _make_target()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[conn_fn, conn_params]):
            result = await functions.get_function(target, "dbo", "fn_clean")
        assert isinstance(result, FunctionDetails)
        assert result.definition is not None
        assert "LTRIM" in result.definition

    async def test_parses_all_fields(self) -> None:
        target = _make_target()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[conn_fn, conn_params]):
            result = await functions.get_function(target, "dbo", "fn_clean")
        assert result.schema_name == "dbo"
        assert result.name == "fn_clean"
        assert result.qualified_name == "dbo.fn_clean"
        assert result.kind == FunctionKind.SCALAR
        assert result.is_inlineable is True
        assert result.created == _NOW
        assert result.modified == _LATER

    async def test_parses_parameters(self) -> None:
        target = _make_target()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[conn_fn, conn_params]):
            result = await functions.get_function(target, "dbo", "fn_clean")
        assert len(result.parameters) == 2
        assert isinstance(result.parameters[0], FunctionParameter)
        assert result.parameters[0].parameter_id == 0  # return value
        assert result.parameters[1].parameter_id == 1
        assert result.parameters[1].name == "@input"

    async def test_raises_not_found_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(NotFoundError),
        ):
            await functions.get_function(target, "dbo", "nonexistent")

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await functions.get_function(target, "bad;schema", "fn_clean")

    async def test_validates_function_name_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await functions.get_function(target, "dbo", "fn--injection")

    async def test_sql_references_sys_sql_modules(self) -> None:
        target = _make_target()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([], _PARAM_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[conn_fn, conn_params]):
            await functions.get_function(target, "dbo", "fn_clean")
        cursor = conn_fn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.sql_modules" in call_sql

    async def test_if_function_kind(self) -> None:
        target = _make_target()
        conn_fn = _make_conn([_GET_ROW_IF], _GET_COLS)
        conn_params = _make_conn([], _PARAM_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[conn_fn, conn_params]):
            result = await functions.get_function(target, "dbo", "fn_get_orders")
        assert result.kind == FunctionKind.INLINE_TVF

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
            await functions.get_function(target, "dbo", "fn_clean")


# ===========================================================================
# create_function
# ===========================================================================


class TestCreateFunction:
    async def test_emits_create_function_ddl(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            await functions.create_function(
                target,
                "dbo",
                "fn_clean",
                "(@x NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN @x END",
            )

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "CREATE FUNCTION" in call_sql.upper()
        assert "[dbo]" in call_sql
        assert "[fn_clean]" in call_sql

    async def test_includes_body(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)
        body = "(@x NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN @x END"

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            await functions.create_function(target, "dbo", "fn_clean", body)

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert body in call_sql

    async def test_returns_function_details(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            result = await functions.create_function(
                target,
                "dbo",
                "fn_clean",
                "(@x NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN @x END",
            )

        assert isinstance(result, FunctionDetails)
        assert result.schema_name == "dbo"
        assert result.name == "fn_clean"

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.create_function(target, "bad]schema", "fn_clean", "...")

    async def test_validates_function_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.create_function(target, "dbo", "fn;drop", "...")

    async def test_rejects_injection_via_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.create_function(target, "x]; DROP TABLE users--", "fn_ok", "...")

    async def test_rejects_injection_via_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.create_function(target, "dbo", "fn_ok] WITH EXECUTE AS OWNER--", "...")

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            await functions.create_function(target, "dbo", "fn_clean", "...")

        ddl_conn.commit.assert_called_once()

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on database")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await functions.create_function(target, "dbo", "fn_clean", "...")


# ===========================================================================
# update_function
# ===========================================================================


class TestUpdateFunction:
    async def test_emits_create_or_alter_function_ddl(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            await functions.update_function(target, "dbo", "fn_clean", "...")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "CREATE OR ALTER FUNCTION" in call_sql

    async def test_uses_brackets_for_schema_and_name(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            await functions.update_function(target, "dbo", "fn_clean", "...")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[fn_clean]" in call_sql

    async def test_returns_function_details(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            result = await functions.update_function(target, "dbo", "fn_clean", "...")

        assert isinstance(result, FunctionDetails)

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.update_function(target, "bad--schema", "fn_clean", "...")

    async def test_validates_function_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.update_function(target, "dbo", "fn;injection", "...")

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            await functions.update_function(target, "dbo", "fn_clean", "...")

        ddl_conn.commit.assert_called_once()


# ===========================================================================
# drop_function
# ===========================================================================


class TestDropFunction:
    async def test_emits_drop_function_ddl(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.drop_function(target, "dbo", "fn_clean")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP FUNCTION" in call_sql

    async def test_uses_brackets_for_schema_and_name(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.drop_function(target, "dbo", "fn_clean")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[fn_clean]" in call_sql

    async def test_if_exists_adds_if_exists_clause(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.drop_function(target, "dbo", "fn_clean", if_exists=True)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP FUNCTION IF EXISTS" in call_sql

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.drop_function(target, "dbo", "fn_clean")
        conn.commit.assert_called_once()

    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await functions.drop_function(target, "dbo", "fn_clean")
        conn.close.assert_called_once()

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.drop_function(target, "bad]schema", "fn_clean")

    async def test_validates_function_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.drop_function(target, "dbo", "fn--bad")

    async def test_rejects_injection_in_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.drop_function(target, "x]; DROP TABLE users--", "fn_ok")

    async def test_rejects_injection_in_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.drop_function(target, "dbo", "fn_ok] WHERE 1=1--")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to drop function")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await functions.drop_function(target, "dbo", "fn_clean")

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
            await functions.drop_function(target, "dbo", "fn_clean")


# ===========================================================================
# rename_function
# ===========================================================================


class TestRenameFunction:
    async def test_emits_sp_rename_ddl(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "SP_RENAME" in call_sql

    async def test_passes_old_and_new_name_as_params(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        cursor = ddl_conn.cursor.return_value
        call_args = cursor.execute.call_args
        params = list(call_args[0][1]) if len(call_args[0]) > 1 else []
        assert "dbo.fn_clean" in params
        assert "fn_sanitize" in params

    async def test_returns_function_details_with_new_name(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        # get_function is called with new name — mock it returning fn_clean details
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            result = await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        assert isinstance(result, FunctionDetails)

    async def test_rejects_schema_qualified_new_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="must not be schema-qualified"):
            await functions.rename_function(target, "dbo.fn_clean", "other.fn_sanitize")

    async def test_validates_schema_in_qualified_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.rename_function(target, "bad;schema.fn_clean", "fn_sanitize")

    async def test_validates_old_function_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.rename_function(target, "dbo.fn--bad", "fn_sanitize")

    async def test_validates_new_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.rename_function(target, "dbo.fn_clean", "fn_bad]name")

    async def test_rejects_injection_in_new_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await functions.rename_function(target, "dbo.fn_clean", "fn_ok] WHERE 1=1--")

    async def test_raises_not_found_when_post_rename_get_fails(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        empty_conn = _make_conn([], _GET_COLS)

        with (
            patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, empty_conn]),
            pytest.raises(NotFoundError, match="not found after rename"),
        ):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([_GET_ROW_FN], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        ddl_conn.commit.assert_called_once()


# ===========================================================================
# validate_kind
# ===========================================================================


class TestValidateKind:
    @pytest.mark.parametrize("kind", ["scalar", "inline-tvf", "all"])
    def test_valid_kinds_returned_unchanged(self, kind: str) -> None:
        assert validate_kind(kind) == kind

    @pytest.mark.parametrize(
        "bad_kind",
        ["", "SCALAR", "Scalar", "fn", "tvf", "all-types", "multistatement-tvf", "unknown"],
    )
    def test_invalid_kind_raises_value_error(self, bad_kind: str) -> None:
        with pytest.raises(ValueError, match="Invalid kind"):
            validate_kind(bad_kind)

    def test_error_message_lists_valid_choices(self) -> None:
        with pytest.raises(ValueError, match="scalar"):
            validate_kind("nope")

    @pytest.mark.parametrize("kind", ["scalar", "inline-tvf", "all"])
    async def test_list_functions_accepts_valid_kind(self, kind: str) -> None:
        """list_functions does not raise for any of the three valid kinds."""
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await functions.list_functions(target, kind=validate_kind(kind))
        assert isinstance(result, list)
