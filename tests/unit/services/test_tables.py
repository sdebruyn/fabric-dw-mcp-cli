"""Unit tests for services.tables — DMV-mock tests + CTAS SELECT-lead check."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, ItemKindError, NotFound, PermissionDenied
from fabric_dw.models import Table, WarehouseKind
from fabric_dw.services import tables
from fabric_dw.services.tables import validate_identifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_target() -> MagicMock:
    return MagicMock()


def _make_conn(rows: list[tuple[object, ...]], columns: list[str]) -> MagicMock:
    cursor = MagicMock()
    cursor.description = [(c, None) for c in columns]
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def _make_conn_for_ddl() -> MagicMock:
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
_TABLE_ROW_1: tuple[object, ...] = ("dbo", "sales", _NOW, _LATER)
_TABLE_ROW_2: tuple[object, ...] = ("finance", "invoices", _NOW, _NOW)


# ===========================================================================
# validate_identifier — re-exported from views
# ===========================================================================


class TestValidateIdentifierReexport:
    def test_valid_identifier_passes(self) -> None:
        assert validate_identifier("my_table") == "my_table"

    def test_rejects_injection(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            validate_identifier("t]; DROP TABLE users--")


# ===========================================================================
# CTAS SELECT-lead validator
# ===========================================================================


class TestRejectNonSelect:
    def test_plain_select_passes(self) -> None:
        tables._reject_non_select("SELECT id FROM dbo.foo")

    def test_select_with_leading_whitespace(self) -> None:
        tables._reject_non_select("   SELECT id FROM dbo.foo")

    def test_select_with_leading_block_comment(self) -> None:
        tables._reject_non_select("/* comment */ SELECT id FROM dbo.foo")

    def test_select_with_leading_line_comment(self) -> None:
        tables._reject_non_select("-- comment\nSELECT id FROM dbo.foo")

    def test_select_case_insensitive(self) -> None:
        tables._reject_non_select("select id from dbo.foo")

    def test_with_cte_select_passes(self) -> None:
        tables._reject_non_select("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte")

    def test_with_cte_multiline_passes(self) -> None:
        tables._reject_non_select(
            "WITH cte AS (\n    SELECT id, name FROM dbo.source\n)\nSELECT * FROM cte"
        )

    def test_with_case_insensitive(self) -> None:
        tables._reject_non_select("with cte as (select 1) select * from cte")

    def test_with_leading_whitespace_passes(self) -> None:
        tables._reject_non_select("   WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_with_leading_comment_passes(self) -> None:
        tables._reject_non_select("-- build cte\nWITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_insert_rejected(self) -> None:
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables._reject_non_select("INSERT INTO dbo.bar SELECT 1")

    def test_drop_rejected(self) -> None:
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables._reject_non_select("DROP TABLE dbo.bar")

    def test_create_rejected(self) -> None:
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables._reject_non_select("CREATE TABLE dbo.t AS SELECT 1")

    def test_empty_body_rejected(self) -> None:
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables._reject_non_select("")

    def test_comment_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables._reject_non_select("/* only a comment */")

    def test_multiple_block_comments_then_select(self) -> None:
        tables._reject_non_select("/* a */ /* b */ SELECT 1")

    def test_mixed_comments_then_select(self) -> None:
        tables._reject_non_select("-- line\n/* block */ SELECT 1")


# ===========================================================================
# list_tables
# ===========================================================================


class TestListTables:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await tables.list_tables(target)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_table_instances(self) -> None:
        target = _make_target()
        conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await tables.list_tables(target)
        assert len(result) == 1
        assert isinstance(result[0], Table)

    @pytest.mark.asyncio
    async def test_parses_fields_correctly(self) -> None:
        target = _make_target()
        conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await tables.list_tables(target)
        t = result[0]
        assert t.schema_name == "dbo"
        assert t.name == "sales"
        assert t.qualified_name == "dbo.sales"
        assert t.created == _NOW
        assert t.modified == _LATER

    @pytest.mark.asyncio
    async def test_returns_all_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([_TABLE_ROW_1, _TABLE_ROW_2], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await tables.list_tables(target)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_sql_references_sys_tables(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.list_tables(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.tables" in call_sql

    @pytest.mark.asyncio
    async def test_sql_references_sys_schemas(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.list_tables(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.schemas" in call_sql

    @pytest.mark.asyncio
    async def test_filters_by_schema_when_provided(self) -> None:
        target = _make_target()
        conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.list_tables(target, schema="dbo")
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        call_sql: str = call_args[0][0]
        # Schema is now bound as a ? parameter, not interpolated.
        assert "s.name = ?" in call_sql
        params = call_args[0][1] if len(call_args[0]) > 1 else (call_args[1] or {}).get("params")
        assert params is not None
        assert "dbo" in list(params)

    @pytest.mark.asyncio
    async def test_schema_filter_validates_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await tables.list_tables(target, schema="bad]schema")

    @pytest.mark.asyncio
    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.list_tables(target)
        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sys.tables")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDenied),
        ):
            await tables.list_tables(target)

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
            await tables.list_tables(target)

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
            await tables.list_tables(target)


# ===========================================================================
# read_table
# ===========================================================================


class TestReadTable:
    @pytest.mark.asyncio
    async def test_returns_columns_and_rows(self) -> None:
        target = _make_target()
        cols = ["id", "name"]
        rows: list[tuple[object, ...]] = [(1, "Alice"), (2, "Bob")]
        conn = _make_conn(rows, cols)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result_cols, result_rows = await tables.read_table(target, "dbo", "sales")
        assert result_cols == cols
        assert list(result_rows) == rows

    @pytest.mark.asyncio
    async def test_sql_uses_select_top(self) -> None:
        target = _make_target()
        conn = _make_conn([(1,)], ["id"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.read_table(target, "dbo", "sales", count=5)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "SELECT TOP" in call_sql
        assert "5" in call_sql

    @pytest.mark.asyncio
    async def test_sql_uses_bracket_quoting(self) -> None:
        target = _make_target()
        conn = _make_conn([(1,)], ["id"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.read_table(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[sales]" in call_sql

    @pytest.mark.asyncio
    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.read_table(target, "bad;schema", "sales")

    @pytest.mark.asyncio
    async def test_validates_table_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.read_table(target, "dbo", "table--injection")

    @pytest.mark.asyncio
    async def test_raises_not_found_when_no_columns(self) -> None:
        target = _make_target()
        cursor = MagicMock()
        cursor.description = None
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(NotFound),
        ):
            await tables.read_table(target, "dbo", "nonexistent")

    @pytest.mark.asyncio
    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sales")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDenied),
        ):
            await tables.read_table(target, "dbo", "sales")

    @pytest.mark.asyncio
    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn([(1,)], ["id"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.read_table(target, "dbo", "sales")
        conn.close.assert_called_once()


# ===========================================================================
# create_table
# ===========================================================================


class TestCreateTable:
    @pytest.mark.asyncio
    async def test_emits_create_table_as_select(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.create_table(target, "dbo", "sales", "SELECT id FROM src.raw")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "CREATE TABLE" in call_sql
        assert "[DBO]" in call_sql
        assert "[SALES]" in call_sql

    @pytest.mark.asyncio
    async def test_includes_select_body(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.create_table(target, "dbo", "sales", "SELECT id FROM src.raw")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "SELECT id FROM src.raw" in call_sql

    @pytest.mark.asyncio
    async def test_returns_table_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.create_table(target, "dbo", "sales", "SELECT 1 AS id")
        assert isinstance(result, Table)

    @pytest.mark.asyncio
    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.create_table(target, "dbo", "sales", "SELECT 1")
        ddl_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_non_select_body(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            await tables.create_table(target, "dbo", "sales", "INSERT INTO foo SELECT 1")

    @pytest.mark.asyncio
    async def test_accepts_cte_body(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.create_table(
                target,
                "dbo",
                "sales",
                "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte",
            )
        assert isinstance(result, Table)

    @pytest.mark.asyncio
    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.create_table(target, "bad]schema", "sales", "SELECT 1")

    @pytest.mark.asyncio
    async def test_validates_table_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.create_table(target, "dbo", "sales;drop", "SELECT 1")

    @pytest.mark.asyncio
    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on database")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDenied),
        ):
            await tables.create_table(target, "dbo", "sales", "SELECT 1")

    @pytest.mark.asyncio
    async def test_rejects_injection_in_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.create_table(target, "x]; DROP TABLE users--", "sales", "SELECT 1")


# ===========================================================================
# delete_table
# ===========================================================================


class TestDeleteTable:
    @pytest.mark.asyncio
    async def test_emits_drop_table(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.delete_table(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP TABLE" in call_sql

    @pytest.mark.asyncio
    async def test_uses_brackets(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.delete_table(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[sales]" in call_sql

    @pytest.mark.asyncio
    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.delete_table(target, "dbo", "sales")
        conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.delete_table(target, "dbo", "sales")
        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.delete_table(target, "bad]schema", "sales")

    @pytest.mark.asyncio
    async def test_validates_table_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.delete_table(target, "dbo", "sales--bad")

    @pytest.mark.asyncio
    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to drop table")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDenied),
        ):
            await tables.delete_table(target, "dbo", "sales")

    @pytest.mark.asyncio
    async def test_rejects_injection_in_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.delete_table(target, "x]; DROP TABLE users--", "ok")

    @pytest.mark.asyncio
    async def test_rejects_injection_in_table_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.delete_table(target, "dbo", "ok] WHERE 1=1--")

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
            await tables.delete_table(target, "dbo", "sales")


# ===========================================================================
# clear_table
# ===========================================================================


class TestClearTable:
    @pytest.mark.asyncio
    async def test_emits_truncate_table(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.clear_table(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "TRUNCATE TABLE" in call_sql

    @pytest.mark.asyncio
    async def test_uses_brackets(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.clear_table(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[sales]" in call_sql

    @pytest.mark.asyncio
    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.clear_table(target, "dbo", "sales")
        conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.clear_table(target, "dbo", "sales")
        conn.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.clear_table(target, "bad;schema", "sales")

    @pytest.mark.asyncio
    async def test_validates_table_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.clear_table(target, "dbo", "sales]injection")

    @pytest.mark.asyncio
    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to truncate table")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDenied),
        ):
            await tables.clear_table(target, "dbo", "sales")

    @pytest.mark.asyncio
    async def test_unrelated_error_propagates(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("timeout")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(RuntimeError, match="timeout"),
        ):
            await tables.clear_table(target, "dbo", "sales")


# ===========================================================================
# SQL Endpoint guard — service layer
# ===========================================================================


class TestSqlEndpointGuard:
    """Verify that create/delete/clear reject SQL Endpoint items before any I/O."""

    @pytest.mark.asyncio
    async def test_create_table_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        with pytest.raises(ItemKindError, match="read-only"):
            await tables.create_table(
                target,
                "dbo",
                "sales",
                "SELECT 1",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    @pytest.mark.asyncio
    async def test_delete_table_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        with pytest.raises(ItemKindError, match="read-only"):
            await tables.delete_table(
                target,
                "dbo",
                "sales",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    @pytest.mark.asyncio
    async def test_clear_table_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        with pytest.raises(ItemKindError, match="read-only"):
            await tables.clear_table(
                target,
                "dbo",
                "sales",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    @pytest.mark.asyncio
    async def test_create_table_warehouse_allowed(self) -> None:
        """WarehouseKind.WAREHOUSE must not be blocked by the guard."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.create_table(
                target,
                "dbo",
                "sales",
                "SELECT 1 AS id",
                kind=WarehouseKind.WAREHOUSE,
            )
        assert isinstance(result, Table)

    @pytest.mark.asyncio
    async def test_guard_fires_before_identifier_validation(self) -> None:
        """ItemKindError must be raised even when schema/table identifiers are invalid."""
        target = _make_target()
        with pytest.raises(ItemKindError):
            await tables.create_table(
                target,
                "bad]schema",
                "sales",
                "SELECT 1",
                kind=WarehouseKind.SQL_ENDPOINT,
            )
