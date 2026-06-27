"""Tests for services.functions — DMV-mock tests + identifier-validator tests (TDD)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, FabricServerError, NotFoundError, PermissionDeniedError
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

    async def test_normalizes_empty_schema_name_in_definition(self) -> None:
        """get_function must fix a Fabric-returned 'CREATE FUNCTION . ...' (issue #715)."""
        broken_def = "CREATE FUNCTION . (@x INT) RETURNS INT AS BEGIN RETURN @x END"
        row = (
            "dbo",
            "fn_clean",
            "FN",
            "SQL_SCALAR_FUNCTION",
            _NOW,
            _LATER,
            broken_def,
            1,
        )
        target = _make_target()
        conn_fn = _make_conn([row], _GET_COLS)
        conn_params = _make_conn([], _PARAM_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[conn_fn, conn_params]):
            result = await functions.get_function(target, "dbo", "fn_clean")
        assert result.definition is not None
        assert "CREATE FUNCTION [dbo].[fn_clean]" in result.definition
        assert ". (" not in result.definition

    async def test_get_function_regression_746_bare_dot_form(self) -> None:
        """Regression #746: bare-dot 'CREATE FUNCTION . (...) ...' is fixed by get_function."""
        raw_def = "CREATE FUNCTION . (@x INT) RETURNS INT AS BEGIN RETURN @x END"
        row = (
            "fdw_qa",
            "fn_compute",
            "FN",
            "SQL_SCALAR_FUNCTION",
            _NOW,
            _LATER,
            raw_def,
            1,
        )
        target = _make_target()
        conn_fn = _make_conn([row], _GET_COLS)
        conn_params = _make_conn([], _PARAM_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[conn_fn, conn_params]):
            result = await functions.get_function(target, "fdw_qa", "fn_compute")
        assert result.definition is not None
        assert "CREATE FUNCTION [fdw_qa].[fn_compute]" in result.definition
        assert ". (" not in result.definition

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

    async def test_create_function_regression_746_bare_dot_form(self) -> None:
        """Regression #746: create_function returns a function whose definition is normalised.

        Fabric stores 'CREATE FUNCTION . (...) ...' in sys.sql_modules after DDL;
        the bare-dot header must be rewritten before the caller sees it.
        """
        body = "(@x INT) RETURNS INT AS BEGIN RETURN @x END"
        raw_def = f"CREATE FUNCTION . {body}"
        fetch_row = (
            "fdw_qa",
            "fn_compute",
            "FN",
            "SQL_SCALAR_FUNCTION",
            _NOW,
            _LATER,
            raw_def,
            1,
        )
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        conn_fn = _make_conn([fetch_row], _GET_COLS)
        conn_params = _make_conn([], _PARAM_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, conn_fn, conn_params]):
            result = await functions.create_function(target, "fdw_qa", "fn_compute", body)

        assert result.definition is not None
        assert "CREATE FUNCTION [fdw_qa].[fn_compute]" in result.definition
        assert ". (" not in result.definition


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

    async def test_invalid_column_raises_fabric_server_error(self) -> None:
        """#747: a driver SQL error (invalid column) on update_function raises FabricServerError.

        The mssql_python driver raises a ProgrammingError with a ``ddbc_error``
        attribute when the server rejects the DDL.  run_query must wrap it as
        FabricServerError so the CLI catches it cleanly.
        """
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()

        class _InvalidColumnError(Exception):
            ddbc_error = "[Microsoft][SQL Server]Invalid column name 'amount'."

            def __str__(self) -> str:
                return (
                    "Driver Error: Column not found; DDBC Error: "
                    "[Microsoft][SQL Server]Invalid column name 'amount'."
                )

        cursor.execute.side_effect = _InvalidColumnError()
        conn.cursor.return_value = cursor

        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(FabricServerError) as exc_info,
        ):
            await functions.update_function(target, "dbo", "fn_bad", "RETURN amount FROM t")

        assert "Invalid column name 'amount'" in str(exc_info.value)
        assert "Driver Error:" not in str(exc_info.value)


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

    async def test_returns_true_when_dropped(self) -> None:
        """drop_function without --if-exists always returns True."""
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await functions.drop_function(target, "dbo", "fn_clean")
        assert result is True

    async def test_if_exists_existing_function_emits_drop_ddl_and_returns_true(self) -> None:
        """When if_exists=True and the function exists, DROP is issued and True is returned."""
        target = _make_target()
        # Single connection: DROP succeeds (function exists)
        conn_drop = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn_drop):
            result = await functions.drop_function(target, "dbo", "fn_clean", if_exists=True)
        assert result is True
        drop_cursor = conn_drop.cursor.return_value
        drop_sql: str = drop_cursor.execute.call_args[0][0].upper()
        assert "DROP FUNCTION" in drop_sql
        # No pre-SELECT; no IF EXISTS in the DDL — single round-trip only
        assert "IF EXISTS" not in drop_sql

    async def test_if_exists_missing_function_returns_false(self) -> None:
        """When if_exists=True and the function does not exist, NotFoundError is caught
        and False is returned (no-op)."""
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        # Simulate SQL Server error 3701 mapped to NotFoundError by run_query
        cursor.execute.side_effect = Exception("cannot drop the function 'fn_nope'")
        conn.cursor.return_value = cursor
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await functions.drop_function(target, "dbo", "fn_nope", if_exists=True)
        assert result is False

    async def test_without_if_exists_missing_function_raises_not_found(self) -> None:
        """Without if_exists=True, NotFoundError propagates to the caller."""
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("cannot drop the function 'fn_nope'")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(NotFoundError),
        ):
            await functions.drop_function(target, "dbo", "fn_nope")

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

# Definition as stored in sys.sql_modules — starts with the full CREATE preamble.
_RENAME_DEF_BRACKET = (
    "CREATE FUNCTION [dbo].[fn_clean]"
    "(@input NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN LTRIM(RTRIM(@input)) END"
)
_RENAME_DEF_UNQUOTED = (
    "CREATE FUNCTION dbo.fn_clean"
    "(@input NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN LTRIM(RTRIM(@input)) END"
)

_GET_ROW_FN_WITH_DEF = (
    "dbo",
    "fn_clean",
    "FN",
    "SQL_SCALAR_FUNCTION",
    _NOW,
    _LATER,
    _RENAME_DEF_BRACKET,
    1,
)
_GET_ROW_FN_UNQUOTED = (
    "dbo",
    "fn_clean",
    "FN",
    "SQL_SCALAR_FUNCTION",
    _NOW,
    _LATER,
    _RENAME_DEF_UNQUOTED,
    1,
)


class TestRenameFunction:
    """rename_function uses DROP + CREATE (not sp_rename) because Fabric DW rejects
    sp_rename for user-defined functions.  The Microsoft T-SQL reference explicitly
    recommends dropping and re-creating functions instead."""

    # Connections consumed by rename_function in the happy path (6 total):
    # 1) get_function (old) → metadata fetch
    # 2) get_function (old) → params fetch
    # 3) create_function DDL
    # 4) get_function (new) → metadata fetch (inside create_function)
    # 5) get_function (new) → params fetch (inside create_function)
    # 6) drop_function DDL
    #
    # Note: create_function() already calls get_function() internally and returns
    # FunctionDetails — rename_function reuses that result without an extra round-trip.

    def _make_rename_conns(
        self,
        *,
        old_row: tuple[object, ...] | None = None,
    ) -> list[MagicMock]:
        """Return the standard 6-connection list for a successful rename."""
        if old_row is None:
            old_row = _GET_ROW_FN_WITH_DEF
        return [
            _make_conn([old_row], _GET_COLS),  # 1: get old fn metadata
            _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS),  # 2: get old fn params
            _make_conn_for_ddl(),  # 3: create DDL
            _make_conn([_GET_ROW_FN], _GET_COLS),  # 4: get new fn (in create_function)
            _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS),  # 5: get new fn params
            _make_conn_for_ddl(),  # 6: drop DDL
        ]

    # ------------------------------------------------------------------
    # Happy-path: sequence of calls
    # ------------------------------------------------------------------

    async def test_issues_create_function_ddl(self) -> None:
        """The rename must emit a CREATE FUNCTION DDL for the new name."""
        target = _make_target()
        conns = self._make_rename_conns()
        conn_create_ddl = conns[2]

        with patch("fabric_dw.sql.open_connection", side_effect=conns):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        create_cursor = conn_create_ddl.cursor.return_value
        create_sql: str = create_cursor.execute.call_args[0][0].upper()
        assert "CREATE FUNCTION" in create_sql
        assert "[FN_SANITIZE]" in create_sql or "FN_SANITIZE" in create_sql

    async def test_issues_drop_function_ddl(self) -> None:
        """The rename must emit a DROP FUNCTION DDL for the old name."""
        target = _make_target()
        conns = self._make_rename_conns()
        conn_drop_ddl = conns[5]

        with patch("fabric_dw.sql.open_connection", side_effect=conns):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        drop_cursor = conn_drop_ddl.cursor.return_value
        drop_sql: str = drop_cursor.execute.call_args[0][0].upper()
        assert "DROP FUNCTION" in drop_sql
        assert "[FN_CLEAN]" in drop_sql or "FN_CLEAN" in drop_sql

    async def test_returns_function_details_with_new_name(self) -> None:
        """rename_function must return FunctionDetails (result from create_function)."""
        target = _make_target()
        conns = self._make_rename_conns()

        with patch("fabric_dw.sql.open_connection", side_effect=conns):
            result = await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        assert isinstance(result, FunctionDetails)

    async def test_does_not_emit_sp_rename(self) -> None:
        """Fabric DW rejects sp_rename for UDFs — must NOT appear in any DDL."""
        target = _make_target()
        conns = self._make_rename_conns()

        with patch("fabric_dw.sql.open_connection", side_effect=conns):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        for conn in conns:
            cursor = conn.cursor.return_value
            if cursor.execute.called:
                sql: str = cursor.execute.call_args[0][0].upper()
                assert "SP_RENAME" not in sql, f"sp_rename must not be used; got: {sql!r}"

    async def test_strips_create_preamble_bracket_quoted(self) -> None:
        """Body extraction must work when sys.sql_modules returns bracket-quoted names."""
        target = _make_target()
        conns = self._make_rename_conns()
        conn_create_ddl = conns[2]

        with patch("fabric_dw.sql.open_connection", side_effect=conns):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        create_cursor = conn_create_ddl.cursor.return_value
        create_sql: str = create_cursor.execute.call_args[0][0]
        # Body content must be passed through correctly
        assert "LTRIM" in create_sql.upper() or "ltrim" in create_sql.lower()
        # The old name must not appear as the function name in the CREATE statement.
        after_fn_kw = create_sql.split("CREATE FUNCTION", 1)[-1]
        before_params = after_fn_kw.split("(", maxsplit=1)[0]
        assert "fn_clean" not in before_params.lower()

    async def test_strips_create_preamble_unquoted(self) -> None:
        """Body extraction must also handle unquoted names in sys.sql_modules."""
        target = _make_target()
        conns = self._make_rename_conns(old_row=_GET_ROW_FN_UNQUOTED)
        conn_create_ddl = conns[2]

        with patch("fabric_dw.sql.open_connection", side_effect=conns):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        create_cursor = conn_create_ddl.cursor.return_value
        create_sql: str = create_cursor.execute.call_args[0][0]
        assert "LTRIM" in create_sql.upper() or "ltrim" in create_sql.lower()

    async def test_strips_create_preamble_with_leading_comment(self) -> None:
        """Body extraction must skip leading comment blocks containing 'FUNCTION'.

        sys.sql_modules may store definitions that begin with a comment whose
        text contains the word FUNCTION.  The old find('FUNCTION') approach
        latched on that first occurrence; re.search(r'\\bCREATE\\s+FUNCTION\\b')
        skips it and finds the actual DDL keyword.
        """
        # Definition whose very first token is a comment containing "FUNCTION"
        definition_with_comment = (
            "-- This FUNCTION cleans strings\n"
            "CREATE FUNCTION [dbo].[fn_clean]"
            "(@input NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN LTRIM(RTRIM(@input)) END"
        )
        row_with_comment = (
            "dbo",
            "fn_clean",
            "FN",
            "SQL_SCALAR_FUNCTION",
            _NOW,
            _LATER,
            definition_with_comment,
            1,
        )
        target = _make_target()
        conns = self._make_rename_conns(old_row=row_with_comment)
        conn_create_ddl = conns[2]

        with patch("fabric_dw.sql.open_connection", side_effect=conns):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        create_cursor = conn_create_ddl.cursor.return_value
        create_sql: str = create_cursor.execute.call_args[0][0]
        # The body (parameter list etc.) must appear in the new CREATE statement
        assert "LTRIM" in create_sql.upper() or "ltrim" in create_sql.lower()
        # The generated DDL must reference the new name, not the old
        after_fn_kw = create_sql.split("CREATE FUNCTION", 1)[-1]
        before_params = after_fn_kw.split("(", maxsplit=1)[0]
        assert "fn_clean" not in before_params.lower()
        assert "fn_sanitize" in before_params.lower()

    async def test_leading_line_comment_containing_create_function(self) -> None:
        """A leading line comment whose text contains 'CREATE FUNCTION' must not
        corrupt the reconstructed DDL.

        Before the fix, re.search found the 'CREATE FUNCTION' inside the comment
        first, causing the body to start mid-comment and the generated DDL to be
        invalid.  After the fix, find_statement_start skips the comment and the
        real header is located correctly.
        """
        definition_with_comment = (
            "-- CREATE FUNCTION helper_do_not_use\n"
            "CREATE FUNCTION [dbo].[fn_clean]"
            "(@input NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN LTRIM(RTRIM(@input)) END"
        )
        row_with_comment = (
            "dbo",
            "fn_clean",
            "FN",
            "SQL_SCALAR_FUNCTION",
            _NOW,
            _LATER,
            definition_with_comment,
            1,
        )
        target = _make_target()
        conns = self._make_rename_conns(old_row=row_with_comment)
        conn_create_ddl = conns[2]

        with patch("fabric_dw.sql.open_connection", side_effect=conns):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        create_cursor = conn_create_ddl.cursor.return_value
        create_sql: str = create_cursor.execute.call_args[0][0]
        # The body must be preserved intact.
        assert "LTRIM" in create_sql.upper() or "ltrim" in create_sql.lower()
        # The generated DDL must name the new function, not the old one or the
        # comment's dummy name.
        after_fn_kw = create_sql.split("CREATE FUNCTION", 1)[-1]
        before_params = after_fn_kw.split("(", maxsplit=1)[0]
        assert "fn_clean" not in before_params.lower()
        assert "helper_do_not_use" not in before_params.lower()
        assert "fn_sanitize" in before_params.lower()

    async def test_leading_block_comment_containing_create_function(self) -> None:
        """A leading block comment whose text contains 'CREATE FUNCTION' must not
        corrupt the reconstructed DDL.

        Mirrors test_leading_line_comment_containing_create_function but uses a
        block comment (/* ... */) instead of a line comment (-- ...).
        """
        definition_with_block_comment = (
            "/* CREATE FUNCTION helper_do_not_use -- legacy stub */\n"
            "CREATE FUNCTION [dbo].[fn_clean]"
            "(@input NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN LTRIM(RTRIM(@input)) END"
        )
        row_with_block_comment = (
            "dbo",
            "fn_clean",
            "FN",
            "SQL_SCALAR_FUNCTION",
            _NOW,
            _LATER,
            definition_with_block_comment,
            1,
        )
        target = _make_target()
        conns = self._make_rename_conns(old_row=row_with_block_comment)
        conn_create_ddl = conns[2]

        with patch("fabric_dw.sql.open_connection", side_effect=conns):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        create_cursor = conn_create_ddl.cursor.return_value
        create_sql: str = create_cursor.execute.call_args[0][0]
        assert "LTRIM" in create_sql.upper() or "ltrim" in create_sql.lower()
        after_fn_kw = create_sql.split("CREATE FUNCTION", 1)[-1]
        before_params = after_fn_kw.split("(", maxsplit=1)[0]
        assert "fn_clean" not in before_params.lower()
        assert "helper_do_not_use" not in before_params.lower()
        assert "fn_sanitize" in before_params.lower()

    async def test_create_fails_leaves_old_function_intact(self) -> None:
        """If create_function raises (e.g. new name already exists), the old function
        is never dropped.  The exception propagates and drop_function is not called.
        """
        target = _make_target()
        # get_function (old) succeeds — returns the existing function with a definition
        conn_get_old_fn = _make_conn([_GET_ROW_FN_WITH_DEF], _GET_COLS)
        conn_get_old_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)
        # create_function DDL fails (new name already exists)
        conn_create_fail = MagicMock()
        cursor_fail = MagicMock()
        cursor_fail.execute.side_effect = Exception(
            "There is already an object named 'fn_sanitize' in the database."
        )
        conn_create_fail.cursor.return_value = cursor_fail

        drop_conn = _make_conn_for_ddl()  # must NOT be consumed

        with (
            patch(
                "fabric_dw.sql.open_connection",
                side_effect=[conn_get_old_fn, conn_get_old_params, conn_create_fail],
            ),
            pytest.raises(Exception, match="already an object named"),
        ):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

        # drop_function DDL connection must never have been opened
        drop_conn.cursor.return_value.execute.assert_not_called()

    # ------------------------------------------------------------------
    # Validation / error-path
    # ------------------------------------------------------------------

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

    async def test_raises_not_found_when_source_function_missing(self) -> None:
        """get_function for the old name raises NotFoundError when it does not exist."""
        target = _make_target()
        empty_conn = _make_conn([], _GET_COLS)

        with (
            patch("fabric_dw.sql.open_connection", return_value=empty_conn),
            pytest.raises(NotFoundError),
        ):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")

    async def test_raises_not_found_when_definition_is_none(self) -> None:
        """If definition is NULL in sys.sql_modules, rename must raise NotFoundError."""
        target = _make_target()
        row_no_def = ("dbo", "fn_clean", "FN", "SQL_SCALAR_FUNCTION", _NOW, _LATER, None, 1)
        conn_fn = _make_conn([row_no_def], _GET_COLS)
        conn_params = _make_conn([_PARAM_RETURN, _PARAM_INPUT], _PARAM_COLS)

        with (
            patch("fabric_dw.sql.open_connection", side_effect=[conn_fn, conn_params]),
            pytest.raises(NotFoundError),
        ):
            await functions.rename_function(target, "dbo.fn_clean", "fn_sanitize")


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
