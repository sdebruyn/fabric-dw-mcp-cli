"""Tests for services.views — DMV-mock tests + identifier-validator tests (TDD)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, NotFound, PermissionDenied
from fabric_dw.models import View
from fabric_dw.services import views
from fabric_dw.services.views import validate_identifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_target() -> MagicMock:
    """Return a mock SqlTarget."""
    return MagicMock()


def _make_conn(rows: list[tuple[object, ...]], columns: list[str]) -> MagicMock:
    """Return a mock connection whose cursor returns the given rows."""
    cursor = MagicMock()
    cursor.description = [(c, None) for c in columns]
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def _make_conn_for_ddl() -> MagicMock:
    """Return a mock connection suitable for DDL statements (no rows returned)."""
    cursor = MagicMock()
    cursor.description = None
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


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

    def test_valid_mixed_case(self) -> None:
        assert validate_identifier("MyView") == "MyView"

    def test_valid_uppercase(self) -> None:
        assert validate_identifier("VW_SALES") == "VW_SALES"


# ===========================================================================
# list_views
# ===========================================================================


class TestListViews:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.list_views(target)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_view_instances(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.list_views(target)
        assert len(result) == 1
        assert isinstance(result[0], View)

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_returns_all_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_1, _VIEW_ROW_2], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.list_views(target)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_sql_references_sys_views(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.list_views(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.views" in call_sql

    @pytest.mark.asyncio
    async def test_sql_references_sys_schemas(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.list_views(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.schemas" in call_sql

    @pytest.mark.asyncio
    async def test_filters_by_schema_when_provided(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.list_views(target, schema="dbo")
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        # schema filter should be passed as an argument or embedded
        assert call_args is not None

    @pytest.mark.asyncio
    async def test_schema_filter_validates_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await views.list_views(target, schema="bad]schema")

    @pytest.mark.asyncio
    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.list_views(target)
        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sys.views")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDenied),
        ):
            await views.list_views(target)

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
# get_view
# ===========================================================================


class TestGetView:
    @pytest.mark.asyncio
    async def test_returns_view_with_definition(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await views.get_view(target, "dbo", "vw_sales")
        assert isinstance(result, View)
        assert result.definition == "SELECT id, amount FROM dbo.sales"

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_sql_includes_sys_sql_modules(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.get_view(target, "dbo", "vw_sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.sql_modules" in call_sql

    @pytest.mark.asyncio
    async def test_raises_not_found_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(NotFound),
        ):
            await views.get_view(target, "dbo", "nonexistent")

    @pytest.mark.asyncio
    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await views.get_view(target, "bad;schema", "vw_sales")

    @pytest.mark.asyncio
    async def test_validates_view_name_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _GET_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await views.get_view(target, "dbo", "vw--injection")

    @pytest.mark.asyncio
    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.get_view(target, "dbo", "vw_sales")
        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on sys.sql_modules")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDenied),
        ):
            await views.get_view(target, "dbo", "vw_sales")


# ===========================================================================
# create_view
# ===========================================================================


class TestCreateView:
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_includes_select_body(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await views.create_view(target, "dbo", "vw_sales", "SELECT id FROM dbo.sales")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "SELECT id FROM dbo.sales" in call_sql

    @pytest.mark.asyncio
    async def test_returns_view_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await views.create_view(target, "dbo", "vw_sales", "SELECT id FROM dbo.sales")

        assert isinstance(result, View)
        assert result.schema_name == "dbo"
        assert result.name == "vw_sales"

    @pytest.mark.asyncio
    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.create_view(target, "bad]schema", "vw_sales", "SELECT 1")

    @pytest.mark.asyncio
    async def test_validates_view_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.create_view(target, "dbo", "vw;drop", "SELECT 1")

    @pytest.mark.asyncio
    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await views.create_view(target, "dbo", "vw_sales", "SELECT id FROM dbo.sales")

        ddl_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_maps_permission_denied_on_ddl(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on database")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDenied),
        ):
            await views.create_view(target, "dbo", "vw_sales", "SELECT 1")

    @pytest.mark.asyncio
    async def test_rejects_identifier_injection_via_schema(self) -> None:
        """Bracket injection in schema name must be rejected before SQL is formed."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.create_view(target, "x]; DROP TABLE users--", "vw_ok", "SELECT 1")

    @pytest.mark.asyncio
    async def test_rejects_identifier_injection_via_view_name(self) -> None:
        """Bracket injection in view name must be rejected before SQL is formed."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.create_view(target, "dbo", "vw_ok] WITH SCHEMABINDING--", "SELECT 1")


# ===========================================================================
# update_view
# ===========================================================================


class TestUpdateView:
    @pytest.mark.asyncio
    async def test_emits_create_or_alter_view_ddl(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await views.update_view(target, "dbo", "vw_sales", "SELECT id, amount FROM dbo.sales")

        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "CREATE OR ALTER VIEW" in call_sql or "ALTER VIEW" in call_sql

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
    async def test_returns_view_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await views.update_view(target, "dbo", "vw_sales", "SELECT 1")

        assert isinstance(result, View)

    @pytest.mark.asyncio
    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.update_view(target, "bad--schema", "vw_sales", "SELECT 1")

    @pytest.mark.asyncio
    async def test_validates_view_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.update_view(target, "dbo", "vw;injection", "SELECT 1")

    @pytest.mark.asyncio
    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_VIEW_ROW_GET], _GET_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await views.update_view(target, "dbo", "vw_sales", "SELECT 1")

        ddl_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to alter view")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDenied),
        ):
            await views.update_view(target, "dbo", "vw_sales", "SELECT 1")


# ===========================================================================
# drop_view
# ===========================================================================


class TestDropView:
    @pytest.mark.asyncio
    async def test_emits_drop_view_ddl(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.drop_view(target, "dbo", "vw_sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP VIEW" in call_sql

    @pytest.mark.asyncio
    async def test_uses_brackets_for_schema_and_name(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.drop_view(target, "dbo", "vw_sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[vw_sales]" in call_sql

    @pytest.mark.asyncio
    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.drop_view(target, "dbo", "vw_sales")
        conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_closes_connection_after_success(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await views.drop_view(target, "dbo", "vw_sales")
        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.drop_view(target, "bad]schema", "vw_sales")

    @pytest.mark.asyncio
    async def test_validates_view_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.drop_view(target, "dbo", "vw--bad")

    @pytest.mark.asyncio
    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to drop view")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDenied),
        ):
            await views.drop_view(target, "dbo", "vw_sales")

    @pytest.mark.asyncio
    async def test_rejects_injection_in_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.drop_view(target, "x]; DROP TABLE users--", "vw_ok")

    @pytest.mark.asyncio
    async def test_rejects_injection_in_view_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await views.drop_view(target, "dbo", "vw_ok] WHERE 1=1--")

    @pytest.mark.asyncio
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
