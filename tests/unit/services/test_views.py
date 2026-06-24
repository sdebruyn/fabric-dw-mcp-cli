"""Tests for services.views — DMV-mock tests + identifier-validator tests (TDD)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, NotFoundError, PermissionDeniedError
from fabric_dw.models import View
from fabric_dw.services import views
from fabric_dw.services._helpers import normalize_object_definition as _normalize_definition
from fabric_dw.services.views import read_view, validate_identifier
from tests.unit.services._helpers import (
    _make_conn,
    _make_conn_for_ddl,
    _make_no_result_conn,
    _make_target,
)

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
_LATER = datetime(2024, 6, 2, 8, 30, 0, tzinfo=UTC)

_LIST_COLS = ["schema_name", "name", "created", "modified"]
_GET_COLS = ["schema_name", "name", "created", "modified", "definition"]

_VIEW_ROW_1 = ("dbo", "vw_sales", _NOW, _LATER)
_VIEW_ROW_2 = ("finance", "vw_monthly", _NOW, _NOW)
_VIEW_ROW_GET = ("dbo", "vw_sales", _NOW, _LATER, "SELECT id, amount FROM dbo.sales")


# ===========================================================================
# normalize_object_definition tests (imported as _normalize_definition)
# ===========================================================================


class TestNormalizeDefinition:
    """Tests for normalize_object_definition exercised via VIEW definitions."""

    def test_empty_schema_and_name_replaced(self) -> None:
        """The canonical Fabric bug: 'CREATE VIEW . AS ...' is fixed."""
        result = _normalize_definition("CREATE VIEW . AS SELECT 1", "dbo", "vw_sales")
        assert "CREATE VIEW [dbo].[vw_sales]" in result
        assert " AS SELECT 1" in result

    def test_empty_schema_only_replaced(self) -> None:
        """Schema is empty but name is present — schema is substituted."""
        result = _normalize_definition("CREATE VIEW .[vw_sales] AS SELECT 1", "dbo", "vw_sales")
        assert "[dbo].[vw_sales]" in result

    def test_empty_name_only_replaced(self) -> None:
        """Name is empty but schema is present — name is substituted."""
        result = _normalize_definition("CREATE VIEW [dbo]. AS SELECT 1", "dbo", "vw_sales")
        assert "[dbo].[vw_sales]" in result

    def test_already_correct_bracket_form_unchanged(self) -> None:
        """When both parts are present and bracket-quoted, they are left as-is."""
        defn = "CREATE VIEW [fdq_qa].[vw_dwh] AS SELECT id FROM t"
        result = _normalize_definition(defn, "fdq_qa", "vw_dwh")
        assert result == defn

    def test_already_correct_plain_form_unchanged(self) -> None:
        """When both parts are present without brackets, they are left as-is."""
        defn = "CREATE VIEW dbo.vw_sales AS SELECT 1"
        result = _normalize_definition(defn, "dbo", "vw_sales")
        assert result == defn

    def test_as_body_preserved_after_fix(self) -> None:
        """The SELECT body after AS is preserved verbatim."""
        body = "SELECT id, label FROM fdw_qa.t_ctas"
        result = _normalize_definition(f"CREATE VIEW . AS {body}", "fdw_qa", "vw_dwh")
        assert result.endswith(body)

    def test_case_insensitive_create_view(self) -> None:
        """Header matching is case-insensitive ('create view' works)."""
        result = _normalize_definition("create view . as SELECT 1", "dbo", "vw_sales")
        assert "[dbo].[vw_sales]" in result

    def test_create_or_alter_view_header(self) -> None:
        """CREATE OR ALTER VIEW header is also normalised."""
        result = _normalize_definition("CREATE OR ALTER VIEW . AS SELECT 1", "dbo", "vw_sales")
        assert "[dbo].[vw_sales]" in result

    def test_no_match_returns_unchanged(self) -> None:
        """A definition that has no recognisable CREATE VIEW header is returned as-is."""
        defn = "SELECT 1"  # no CREATE VIEW prefix
        assert _normalize_definition(defn, "dbo", "vw_sales") == defn

    def test_realistic_fabric_bug_case(self) -> None:
        """Reproduces the exact symptom from issue #715."""
        raw = "CREATE VIEW . AS SELECT id, label FROM fdw_qa.t_ctas"
        result = _normalize_definition(raw, "fdw_qa", "vw_dwh")
        assert result == "CREATE VIEW [fdw_qa].[vw_dwh] AS SELECT id, label FROM fdw_qa.t_ctas"


# ===========================================================================
# identifier validator tests
# ===========================================================================


class TestValidateIdentifier:
    def test_simple_valid_identifier(self) -> None:
        assert validate_identifier("my_view") == "my_view"

    def test_valid_with_letters_digits_underscores(self) -> None:
        assert validate_identifier("view_123_abc") == "view_123_abc"

    def test_valid_starts_with_underscore(self) -> None:
        assert validate_identifier("_private") == "_private"

    def test_valid_max_length_128(self) -> None:
        long_name = "a" * 128
        assert validate_identifier(long_name) == long_name

    def test_rejects_closing_bracket(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("view]name")

    def test_rejects_semicolon(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("view;name")

    def test_rejects_double_dash(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("view--comment")

    def test_rejects_leading_digit(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("1invalid")

    def test_rejects_space(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("my view")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("a" * 129)

    def test_rejects_sql_injection_drop(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("x; DROP TABLE users--")

    def test_rejects_bracket_injection(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("x] JOIN sys.tables--")

    def test_rejects_dot(self) -> None:
        """Dots are not allowed in an identifier segment."""
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("schema.view")

    def test_rejects_single_quote(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("view'name")

    def test_rejects_opening_bracket(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("view[name")

    def test_rejects_newline(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("view\nname")

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("view\x00name")

    def test_rejects_hyphen(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("view-name")

    def test_allows_single_underscore(self) -> None:
        """Single underscore matches ^[A-Za-z_][A-Za-z0-9_]{0,127}$ — allowed."""
        assert validate_identifier("_") == "_"

    def test_valid_mixed_case(self) -> None:
        assert validate_identifier("MyView") == "MyView"

    def test_valid_uppercase(self) -> None:
        assert validate_identifier("VW_SALES") == "VW_SALES"


# ===========================================================================
# list_views
# ===========================================================================


class TestListViews:
    async def test_returns_empty_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.list_views(target)
        assert result == []

    async def test_returns_view_instances(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.list_views(target)
        assert len(result) == 1
        assert isinstance(result[0], View)

    async def test_parses_fields_correctly(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.list_views(target)
        v = result[0]
        assert v.schema_name == "dbo"
        assert v.name == "vw_sales"
        assert v.qualified_name == "dbo.vw_sales"
        assert v.created == _NOW
        assert v.modified == _LATER
        assert v.definition is None

    async def test_returns_all_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_1, _VIEW_ROW_2], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.list_views(target)
        assert len(result) == 2

    async def test_sql_references_sys_views(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.list_views(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.views" in call_sql

    async def test_sql_references_sys_schemas(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.list_views(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.schemas" in call_sql

    async def test_filters_by_schema_when_provided(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.list_views(target, schema="dbo")
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        call_sql: str = call_args[0][0]
        # Schema is now bound as a ? parameter, not interpolated.
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
            await views.list_views(target, schema="bad]schema")

    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.list_views(target)
        conn.close.assert_called_once()

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sys.views")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await views.list_views(target)

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
            await views.list_views(target)

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
            await views.list_views(target)


# ===========================================================================
# read_view
# ===========================================================================


class TestReadView:
    async def test_returns_columns_and_rows(self) -> None:
        target = _make_target()
        cols = ["id", "name"]
        rows: list[tuple[object, ...]] = [(1, "Alice"), (2, "Bob")]
        conn = _make_conn(rows, cols)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result_cols, result_rows = await read_view(target, "dbo", "vw_sales")
        assert result_cols == cols
        assert list(result_rows) == rows

    async def test_sql_uses_select_top(self) -> None:
        target = _make_target()
        conn = _make_conn([(1,)], ["id"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await read_view(target, "dbo", "vw_sales", count=5)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "SELECT TOP" in call_sql
        # Assert the full TOP clause to avoid accidental matches on other numbers.
        assert "TOP (5)" in call_sql

    async def test_sql_uses_bracket_quoting(self) -> None:
        target = _make_target()
        conn = _make_conn([(1,)], ["id"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await read_view(target, "dbo", "vw_sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[vw_sales]" in call_sql

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await read_view(target, "bad;schema", "vw_sales")

    async def test_validates_view_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await read_view(target, "dbo", "vw--injection")

    async def test_raises_not_found_for_missing_view(self) -> None:
        """A missing view raises NotFoundError via SQL error 208 mapping in run_query.

        map_driver_error converts SQL error 208 ("invalid object name") to
        NotFoundError inside run_query, which re-raises it before returning.
        """
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()

        class _Err208Error(Exception):
            ddbc_error = "Error: 208 Invalid object name 'dbo.vw_nonexistent'"

            def __str__(self) -> str:
                return "Invalid object name 'dbo.vw_nonexistent'"

        cursor.execute.side_effect = _Err208Error()
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(NotFoundError),
        ):
            await read_view(target, "dbo", "vw_nonexistent")

    async def test_raises_not_found_when_no_columns_v10(self) -> None:
        """V10: read_view raises NotFoundError on empty column metadata (no cols guard).

        This mirrors read_table's behaviour: both functions now raise NotFoundError
        when the driver returns no column metadata (``description`` is None),
        providing a consistent not-found contract across both zuster-methods.
        """
        target = _make_target()
        cursor = MagicMock()
        cursor.description = None
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(NotFoundError),
        ):
            await read_view(target, "dbo", "vw_nonexistent")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object vw_sales")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await read_view(target, "dbo", "vw_sales")

    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn([(1,)], ["id"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await read_view(target, "dbo", "vw_sales")
        conn.close.assert_called_once()

    async def test_default_count_is_ten(self) -> None:
        target = _make_target()
        conn = _make_conn([(1,)], ["id"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await read_view(target, "dbo", "vw_sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        # Assert the full TOP clause to avoid accidental matches on other numbers.
        assert "TOP (10)" in call_sql


# ===========================================================================
# count_view_rows
# ===========================================================================


class TestCountViewRows:
    async def test_returns_row_count(self) -> None:
        target = _make_target()
        conn = _make_conn([(42,)], ["row_count"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.count_view_rows(target, "dbo", "vw_sales")
        assert result == 42

    async def test_returns_zero_for_empty_view(self) -> None:
        target = _make_target()
        conn = _make_conn([(0,)], ["row_count"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.count_view_rows(target, "dbo", "vw_sales")
        assert result == 0

    async def test_sql_uses_count_big(self) -> None:
        target = _make_target()
        conn = _make_conn([(10,)], ["row_count"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.count_view_rows(target, "dbo", "vw_sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "COUNT_BIG(*)" in call_sql

    async def test_sql_uses_bracket_quoting(self) -> None:
        target = _make_target()
        conn = _make_conn([(5,)], ["row_count"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.count_view_rows(target, "dbo", "vw_sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[vw_sales]" in call_sql

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.count_view_rows(target, "bad;schema", "vw_sales")

    async def test_validates_view_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.count_view_rows(target, "dbo", "view--injection")

    async def test_fabric_error_propagates(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object vw_sales")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await views.count_view_rows(target, "dbo", "vw_sales")

    async def test_raises_not_found_when_description_is_none(self) -> None:
        target = _make_target()
        conn = _make_no_result_conn()
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(NotFoundError),
        ):
            await views.count_view_rows(target, "dbo", "missing")


# ===========================================================================
# get_view
# ===========================================================================


class TestGetView:
    async def test_returns_view_with_definition(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.get_view(target, "dbo", "vw_sales")
        assert isinstance(result, View)
        assert result.definition == "SELECT id, amount FROM dbo.sales"

    async def test_parses_all_fields(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.get_view(target, "dbo", "vw_sales")
        assert result.schema_name == "dbo"
        assert result.name == "vw_sales"
        assert result.qualified_name == "dbo.vw_sales"
        assert result.created == _NOW
        assert result.modified == _LATER

    async def test_sql_includes_sys_sql_modules(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.get_view(target, "dbo", "vw_sales")
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
            await views.get_view(target, "dbo", "nonexistent")

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await views.get_view(target, "bad;schema", "vw_sales")

    async def test_validates_view_name_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await views.get_view(target, "dbo", "vw--injection")

    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.get_view(target, "dbo", "vw_sales")
        conn.close.assert_called_once()

    async def test_normalizes_empty_schema_name_in_definition(self) -> None:
        """get_view must fix a Fabric-returned 'CREATE VIEW . AS ...' definition (issue #715)."""
        broken_def = "CREATE VIEW . AS SELECT id, amount FROM dbo.sales"
        row = ("dbo", "vw_sales", _NOW, _LATER, broken_def)
        target = _make_target()
        conn = _make_conn([row], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.get_view(target, "dbo", "vw_sales")
        assert result.definition is not None
        assert "CREATE VIEW [dbo].[vw_sales]" in result.definition
        assert ". AS" not in result.definition

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
            await views.get_view(target, "dbo", "vw_sales")


# ===========================================================================
# create_view
# ===========================================================================


class TestCreateView:
    async def test_emits_create_view_ddl(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await views.create_view(target, "dbo", "vw_sales", "SELECT id FROM dbo.sales")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "CREATE VIEW" in call_sql.upper()
        assert "[dbo]" in call_sql
        assert "[vw_sales]" in call_sql

    async def test_includes_select_body(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await views.create_view(target, "dbo", "vw_sales", "SELECT id FROM dbo.sales")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "SELECT id FROM dbo.sales" in call_sql

    async def test_returns_view_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await views.create_view(target, "dbo", "vw_sales", "SELECT id FROM dbo.sales")

        assert isinstance(result, View)
        assert result.schema_name == "dbo"
        assert result.name == "vw_sales"

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.create_view(target, "bad]schema", "vw_sales", "SELECT 1")

    async def test_validates_view_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.create_view(target, "dbo", "vw;drop", "SELECT 1")

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await views.create_view(target, "dbo", "vw_sales", "SELECT id FROM dbo.sales")

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
            await views.create_view(target, "dbo", "vw_sales", "SELECT 1")

    async def test_rejects_identifier_injection_via_schema(self) -> None:
        """Bracket injection in schema name must be rejected before SQL is formed."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.create_view(target, "x]; DROP TABLE users--", "vw_ok", "SELECT 1")

    async def test_rejects_identifier_injection_via_view_name(self) -> None:
        """Bracket injection in view name must be rejected before SQL is formed."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.create_view(target, "dbo", "vw_ok] WITH SCHEMABINDING--", "SELECT 1")

    async def test_rejects_non_select_body_v23(self) -> None:
        """V23: create_view must validate that select_body starts with SELECT or WITH."""
        target = _make_target()
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            await views.create_view(target, "dbo", "vw_sales", "INSERT INTO foo SELECT 1")

    async def test_accepts_cte_body_v23(self) -> None:
        """V23: create_view must accept a CTE (WITH … SELECT) body."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await views.create_view(
                target,
                "dbo",
                "vw_sales",
                "WITH cte AS (SELECT id FROM dbo.src) SELECT * FROM cte",
            )
        assert isinstance(result, View)

    async def test_rejects_drop_body_in_create_view_v23(self) -> None:
        """V23: DROP TABLE as body must be rejected by create_view."""
        target = _make_target()
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            await views.create_view(target, "dbo", "vw_sales", "DROP TABLE dbo.foo")

    async def test_create_view_normalizes_empty_definition(self) -> None:
        """create_view must fix a Fabric-returned 'CREATE VIEW . AS ...' in the fetched view."""
        broken_def = "CREATE VIEW . AS SELECT id FROM dbo.sales"
        fetch_row = ("dbo", "vw_sales", _NOW, _LATER, broken_def)
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([fetch_row], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await views.create_view(target, "dbo", "vw_sales", "SELECT id FROM dbo.sales")

        assert result.definition is not None
        assert "CREATE VIEW [dbo].[vw_sales]" in result.definition
        assert ". AS" not in result.definition


# ===========================================================================
# update_view
# ===========================================================================


class TestUpdateView:
    async def test_emits_create_or_alter_view_ddl(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await views.update_view(target, "dbo", "vw_sales", "SELECT id, amount FROM dbo.sales")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "CREATE OR ALTER VIEW" in call_sql

    async def test_uses_brackets_for_schema_and_name(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await views.update_view(target, "dbo", "vw_sales", "SELECT 1")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[vw_sales]" in call_sql

    async def test_returns_view_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await views.update_view(target, "dbo", "vw_sales", "SELECT 1")

        assert isinstance(result, View)

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.update_view(target, "bad--schema", "vw_sales", "SELECT 1")

    async def test_validates_view_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.update_view(target, "dbo", "vw;injection", "SELECT 1")

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await views.update_view(target, "dbo", "vw_sales", "SELECT 1")

        ddl_conn.commit.assert_called_once()

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to alter view")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await views.update_view(target, "dbo", "vw_sales", "SELECT 1")

    async def test_rejects_non_select_body_v23(self) -> None:
        """V23: update_view must validate that select_body starts with SELECT or WITH."""
        target = _make_target()
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            await views.update_view(target, "dbo", "vw_sales", "DELETE FROM foo")

    async def test_accepts_cte_body_v23(self) -> None:
        """V23: update_view must accept a CTE (WITH … SELECT) body."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await views.update_view(
                target,
                "dbo",
                "vw_sales",
                "WITH cte AS (SELECT id FROM dbo.src) SELECT * FROM cte",
            )
        assert isinstance(result, View)


# ===========================================================================
# drop_view
# ===========================================================================


class TestDropView:
    async def test_emits_drop_view_ddl(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.drop_view(target, "dbo", "vw_sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP VIEW" in call_sql

    async def test_uses_brackets_for_schema_and_name(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.drop_view(target, "dbo", "vw_sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[vw_sales]" in call_sql

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.drop_view(target, "dbo", "vw_sales")
        conn.commit.assert_called_once()

    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.drop_view(target, "dbo", "vw_sales")
        conn.close.assert_called_once()

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.drop_view(target, "bad]schema", "vw_sales")

    async def test_validates_view_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.drop_view(target, "dbo", "vw--bad")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to drop view")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await views.drop_view(target, "dbo", "vw_sales")

    async def test_rejects_injection_in_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.drop_view(target, "x]; DROP TABLE users--", "vw_ok")

    async def test_rejects_injection_in_view_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.drop_view(target, "dbo", "vw_ok] WHERE 1=1--")

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
            await views.drop_view(target, "dbo", "vw_sales")


# ===========================================================================
# rename_view
# ===========================================================================


class TestRenameView:
    async def test_executes_sp_rename_sql(self) -> None:
        """sp_rename must be called with 'EXEC sp_rename' in the SQL."""
        target = _make_target()
        rename_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([("dbo", "vw_revenue", _NOW, _LATER, "SELECT 1")], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[rename_conn, fetch_conn]):
            await views.rename_view(target, "dbo.vw_sales", "vw_revenue")

        cursor = rename_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "SP_RENAME" in call_sql

    async def test_binds_old_qualified_name_and_new_name_as_params(self) -> None:
        """Both old qualified name and new bare name must be bound as ? parameters."""
        target = _make_target()
        rename_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([("dbo", "vw_revenue", _NOW, _LATER, "SELECT 1")], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[rename_conn, fetch_conn]):
            await views.rename_view(target, "dbo.vw_sales", "vw_revenue")

        cursor = rename_conn.cursor.return_value
        call_args = cursor.execute.call_args[0]
        params = list(call_args[1])
        assert params[0] == "dbo.vw_sales"
        assert params[1] == "vw_revenue"

    async def test_sp_rename_sql_uses_question_mark_placeholders(self) -> None:
        """The SQL template must use ? placeholders, not interpolated identifiers."""
        target = _make_target()
        rename_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([("dbo", "vw_revenue", _NOW, _LATER, "SELECT 1")], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[rename_conn, fetch_conn]):
            await views.rename_view(target, "dbo.vw_sales", "vw_revenue")

        cursor = rename_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        # Neither the old name nor the new name should appear directly in the SQL
        assert "vw_sales" not in call_sql
        assert "vw_revenue" not in call_sql
        assert "?" in call_sql

    async def test_sp_rename_includes_object_type(self) -> None:
        """The call must include 'OBJECT' as the third sp_rename argument."""
        target = _make_target()
        rename_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([("dbo", "vw_revenue", _NOW, _LATER, "SELECT 1")], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[rename_conn, fetch_conn]):
            await views.rename_view(target, "dbo.vw_sales", "vw_revenue")

        cursor = rename_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "'OBJECT'" in call_sql

    async def test_returns_view_with_new_name(self) -> None:
        """rename_view must return the renamed View object."""
        target = _make_target()
        rename_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([("dbo", "vw_revenue", _NOW, _LATER, "SELECT 1")], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[rename_conn, fetch_conn]):
            result = await views.rename_view(target, "dbo.vw_sales", "vw_revenue")

        assert isinstance(result, View)
        assert result.name == "vw_revenue"
        assert result.schema_name == "dbo"
        assert result.qualified_name == "dbo.vw_revenue"

    async def test_commits_after_sp_rename(self) -> None:
        """rename_view must commit after executing sp_rename."""
        target = _make_target()
        rename_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([("dbo", "vw_revenue", _NOW, _LATER, "SELECT 1")], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[rename_conn, fetch_conn]):
            await views.rename_view(target, "dbo.vw_sales", "vw_revenue")

        rename_conn.commit.assert_called_once()

    async def test_rejects_schema_qualified_new_name(self) -> None:
        """new_name must be a bare identifier; schema.view is rejected."""
        target = _make_target()
        with pytest.raises(ValueError, match="schema-qualified"):
            await views.rename_view(target, "dbo.vw_sales", "other.vw_revenue")

    async def test_rejects_invalid_new_name_identifier(self) -> None:
        """new_name must pass validate_identifier."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.rename_view(target, "dbo.vw_sales", "vw--injection")

    async def test_rejects_invalid_schema_in_qualified(self) -> None:
        """Schema part of qualified must pass validate_identifier."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.rename_view(target, "bad;schema.vw_sales", "vw_revenue")

    async def test_rejects_invalid_view_name_in_qualified(self) -> None:
        """Old view name part of qualified must pass validate_identifier."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.rename_view(target, "dbo.vw--injection", "vw_revenue")

    async def test_rejects_qualified_without_dot(self) -> None:
        """qualified without a dot raises ValueError."""
        target = _make_target()
        with pytest.raises(ValueError, match="qualified"):
            await views.rename_view(target, "nodot", "vw_revenue")

    async def test_raises_not_found_when_renamed_view_missing(self) -> None:
        """View not found after rename raises NotFoundError with rename-specific message."""
        from fabric_dw.exceptions import NotFoundError  # noqa: PLC0415

        target = _make_target()
        rename_conn = _make_conn_for_ddl()
        # Empty row set → get_view raises NotFoundError; rename_view wraps it.
        fetch_conn = _make_conn([], _GET_COLS)

        with (
            patch("fabric_dw.sql.open_connection", side_effect=[rename_conn, fetch_conn]),
            pytest.raises(NotFoundError, match="not found after rename"),
        ):
            await views.rename_view(target, "dbo.vw_sales", "vw_revenue")

    async def test_maps_permission_denied(self) -> None:
        """Driver permission errors must be mapped to PermissionDeniedError."""
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object vw_sales")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await views.rename_view(target, "dbo.vw_sales", "vw_revenue")

    async def test_does_not_reject_sql_endpoint_target(self) -> None:
        """rename_view must NOT raise for a SQL-endpoint target (no DW-only guard)."""
        from fabric_dw.models import WarehouseKind  # noqa: PLC0415

        # Construct a mock target that looks like a SQL Analytics Endpoint.
        target = MagicMock()
        target.kind = WarehouseKind.SQL_ENDPOINT

        rename_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([("dbo", "vw_revenue", _NOW, _LATER, "SELECT 1")], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[rename_conn, fetch_conn]):
            # Must not raise — no DW-only guard is applied.
            result = await views.rename_view(target, "dbo.vw_sales", "vw_revenue")

        assert result.name == "vw_revenue"
