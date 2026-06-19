"""Unit tests for services.tables — DMV-mock tests + CTAS SELECT-lead check."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_dw.exceptions import AuthError, ItemKindError, NotFoundError, PermissionDeniedError
from fabric_dw.models import ColumnSpec, Table, WarehouseKind
from fabric_dw.services import tables
from fabric_dw.services.tables import validate_identifier
from tests.unit.services._helpers import _make_conn, _make_conn_for_ddl, _make_target

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
        tables.reject_non_select("SELECT id FROM dbo.foo")

    def test_select_with_leading_whitespace(self) -> None:
        tables.reject_non_select("   SELECT id FROM dbo.foo")

    def test_select_with_leading_block_comment(self) -> None:
        tables.reject_non_select("/* comment */ SELECT id FROM dbo.foo")

    def test_select_with_leading_line_comment(self) -> None:
        tables.reject_non_select("-- comment\nSELECT id FROM dbo.foo")

    def test_select_case_insensitive(self) -> None:
        tables.reject_non_select("select id from dbo.foo")

    def test_with_cte_select_passes(self) -> None:
        tables.reject_non_select("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte")

    def test_with_cte_multiline_passes(self) -> None:
        tables.reject_non_select(
            "WITH cte AS (\n    SELECT id, name FROM dbo.source\n)\nSELECT * FROM cte"
        )

    def test_with_case_insensitive(self) -> None:
        tables.reject_non_select("with cte as (select 1) select * from cte")

    def test_with_leading_whitespace_passes(self) -> None:
        tables.reject_non_select("   WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_with_leading_comment_passes(self) -> None:
        tables.reject_non_select("-- build cte\nWITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_insert_rejected(self) -> None:
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables.reject_non_select("INSERT INTO dbo.bar SELECT 1")

    def test_drop_rejected(self) -> None:
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables.reject_non_select("DROP TABLE dbo.bar")

    def test_create_rejected(self) -> None:
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables.reject_non_select("CREATE TABLE dbo.t AS SELECT 1")

    def test_empty_body_rejected(self) -> None:
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables.reject_non_select("")

    def test_comment_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables.reject_non_select("/* only a comment */")

    def test_multiple_block_comments_then_select(self) -> None:
        tables.reject_non_select("/* a */ /* b */ SELECT 1")

    def test_mixed_comments_then_select(self) -> None:
        tables.reject_non_select("-- line\n/* block */ SELECT 1")

    # -----------------------------------------------------------------------
    # ReDoS regression tests (CodeQL py/redos — must complete in < 2 s)
    # -----------------------------------------------------------------------

    def test_redos_alternating_block_comment_delimiters_rejected_fast(self) -> None:
        """Adversarial input '/*' + '*//*' * N must be REJECTED and not hang.

        The previous nested-quantifier regex ``(?:\\s*(?:/\\*.*?\\*/|...))*``
        backtracked exponentially on this pattern.  The procedural rewrite is
        linear: after consuming the first block comment ``/* */``, the remaining
        ``/*`` is an unclosed block comment that leaves no SELECT/WITH keyword,
        so it is rejected immediately.
        """
        malicious = "/*" + "*//*" * 50_000
        start = time.monotonic()
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables.reject_non_select(malicious)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"ReDoS: took {elapsed:.3f}s (expected < 2s)"

    def test_redos_many_unclosed_block_comments_rejected_fast(self) -> None:
        """Many opening ``/*`` without closing ``*/`` must be REJECTED fast."""
        malicious = "/* " * 50_000
        start = time.monotonic()
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            tables.reject_non_select(malicious)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"ReDoS: took {elapsed:.3f}s (expected < 2s)"

    def test_redos_many_whitespace_and_comments_select_accepted_fast(self) -> None:
        """Many leading whitespace + closed block comments before SELECT must PASS fast."""
        preamble = "/* ok */ " * 5_000
        body = preamble + "SELECT 1"
        start = time.monotonic()
        tables.reject_non_select(body)  # must not raise
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"ReDoS: took {elapsed:.3f}s (expected < 2s)"


# ===========================================================================
# list_tables
# ===========================================================================


class TestListTables:
    async def test_returns_empty_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await tables.list_tables(target)
        assert result == []

    async def test_returns_table_instances(self) -> None:
        target = _make_target()
        conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await tables.list_tables(target)
        assert len(result) == 1
        assert isinstance(result[0], Table)

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

    async def test_returns_all_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([_TABLE_ROW_1, _TABLE_ROW_2], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await tables.list_tables(target)
        assert len(result) == 2

    async def test_sql_references_sys_tables(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.list_tables(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.tables" in call_sql

    async def test_sql_references_sys_schemas(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.list_tables(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.schemas" in call_sql

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

    async def test_schema_filter_validates_identifier(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(ValueError, match="Invalid SQL identifier"),
        ):
            await tables.list_tables(target, schema="bad]schema")

    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.list_tables(target)
        conn.close.assert_called_once()

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sys.tables")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await tables.list_tables(target)

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
    async def test_returns_columns_and_rows(self) -> None:
        target = _make_target()
        cols = ["id", "name"]
        rows: list[tuple[object, ...]] = [(1, "Alice"), (2, "Bob")]
        conn = _make_conn(rows, cols)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result_cols, result_rows = await tables.read_table(target, "dbo", "sales")
        assert result_cols == cols
        assert list(result_rows) == rows

    async def test_sql_uses_select_top(self) -> None:
        target = _make_target()
        conn = _make_conn([(1,)], ["id"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.read_table(target, "dbo", "sales", count=5)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "SELECT TOP" in call_sql
        # Assert the full TOP clause to avoid accidental matches on other numbers.
        assert "TOP (5)" in call_sql

    async def test_sql_uses_bracket_quoting(self) -> None:
        target = _make_target()
        conn = _make_conn([(1,)], ["id"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.read_table(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[sales]" in call_sql

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.read_table(target, "bad;schema", "sales")

    async def test_validates_table_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.read_table(target, "dbo", "table--injection")

    async def test_raises_not_found_when_no_columns(self) -> None:
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
            await tables.read_table(target, "dbo", "nonexistent")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sales")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await tables.read_table(target, "dbo", "sales")

    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn([(1,)], ["id"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.read_table(target, "dbo", "sales")
        conn.close.assert_called_once()


# ===========================================================================
# count_table_rows
# ===========================================================================


class TestCountTableRows:
    async def test_returns_row_count(self) -> None:
        target = _make_target()
        conn = _make_conn([(42,)], ["row_count"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await tables.count_table_rows(target, "dbo", "sales")
        assert result == 42

    async def test_returns_zero_for_empty_table(self) -> None:
        target = _make_target()
        conn = _make_conn([(0,)], ["row_count"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await tables.count_table_rows(target, "dbo", "sales")
        assert result == 0

    async def test_sql_uses_count_big(self) -> None:
        target = _make_target()
        conn = _make_conn([(10,)], ["row_count"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.count_table_rows(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "COUNT_BIG(*)" in call_sql

    async def test_sql_uses_bracket_quoting(self) -> None:
        target = _make_target()
        conn = _make_conn([(5,)], ["row_count"])
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.count_table_rows(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[sales]" in call_sql

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.count_table_rows(target, "bad;schema", "sales")

    async def test_validates_table_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.count_table_rows(target, "dbo", "table--injection")

    async def test_fabric_error_propagates(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sales")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await tables.count_table_rows(target, "dbo", "sales")


# ===========================================================================
# create_table
# ===========================================================================


class TestCreateTable:
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

    async def test_includes_select_body(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.create_table(target, "dbo", "sales", "SELECT id FROM src.raw")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "SELECT id FROM src.raw" in call_sql

    async def test_returns_table_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.create_table(target, "dbo", "sales", "SELECT 1 AS id")
        assert isinstance(result, Table)

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.create_table(target, "dbo", "sales", "SELECT 1")
        ddl_conn.commit.assert_called_once()

    async def test_rejects_non_select_body(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
            await tables.create_table(target, "dbo", "sales", "INSERT INTO foo SELECT 1")

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

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.create_table(target, "bad]schema", "sales", "SELECT 1")

    async def test_validates_table_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.create_table(target, "dbo", "sales;drop", "SELECT 1")

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
            await tables.create_table(target, "dbo", "sales", "SELECT 1")

    async def test_rejects_injection_in_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.create_table(target, "x]; DROP TABLE users--", "sales", "SELECT 1")


# ===========================================================================
# delete_table
# ===========================================================================


class TestDeleteTable:
    async def test_emits_drop_table(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.delete_table(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP TABLE" in call_sql

    async def test_uses_brackets(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.delete_table(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[sales]" in call_sql

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.delete_table(target, "dbo", "sales")
        conn.commit.assert_called_once()

    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.delete_table(target, "dbo", "sales")
        conn.close.assert_called_once()

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.delete_table(target, "bad]schema", "sales")

    async def test_validates_table_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.delete_table(target, "dbo", "sales--bad")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to drop table")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await tables.delete_table(target, "dbo", "sales")

    async def test_rejects_injection_in_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.delete_table(target, "x]; DROP TABLE users--", "ok")

    async def test_rejects_injection_in_table_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.delete_table(target, "dbo", "ok] WHERE 1=1--")

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
    async def test_emits_truncate_table(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.clear_table(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "TRUNCATE TABLE" in call_sql

    async def test_uses_brackets(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.clear_table(target, "dbo", "sales")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo]" in call_sql
        assert "[sales]" in call_sql

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.clear_table(target, "dbo", "sales")
        conn.commit.assert_called_once()

    async def test_closes_connection(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await tables.clear_table(target, "dbo", "sales")
        conn.close.assert_called_once()

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.clear_table(target, "bad;schema", "sales")

    async def test_validates_table_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.clear_table(target, "dbo", "sales]injection")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to truncate table")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await tables.clear_table(target, "dbo", "sales")

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

    async def test_delete_table_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        with pytest.raises(ItemKindError, match="read-only"):
            await tables.delete_table(
                target,
                "dbo",
                "sales",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    async def test_clear_table_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        with pytest.raises(ItemKindError, match="read-only"):
            await tables.clear_table(
                target,
                "dbo",
                "sales",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

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

    async def test_rename_table_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        with pytest.raises(ItemKindError, match="read-only"):
            await tables.rename_table(
                target,
                "dbo.sales",
                "sales_v2",
                kind=WarehouseKind.SQL_ENDPOINT,
            )


# ===========================================================================
# clone_table
# ===========================================================================


class TestCloneTable:
    async def test_emits_create_table_as_clone_of(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        mock_oc = patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn])
        with mock_oc as mock_open:
            await tables.clone_table(target, "dbo.source_tbl", "dbo.sales")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "CREATE TABLE" in call_sql
        assert "CLONE OF" in call_sql
        assert mock_open.call_args_list[0].kwargs.get("autocommit") is True

    async def test_bracket_quotes_all_identifiers(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        mock_oc = patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn])
        with mock_oc as mock_open:
            await tables.clone_table(target, "dbo.source_tbl", "dbo.sales")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo].[sales]" in call_sql
        assert "[dbo].[source_tbl]" in call_sql
        assert mock_open.call_args_list[0].kwargs.get("autocommit") is True

    async def test_cross_schema_clone_quotes_correctly(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_row: tuple[object, ...] = ("staging", "copy_tbl", _NOW, _NOW)
        fetch_conn = _make_conn([fetch_row], _LIST_COLS)
        mock_oc = patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn])
        with mock_oc as mock_open:
            await tables.clone_table(target, "dbo.source_tbl", "staging.copy_tbl")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[staging].[copy_tbl]" in call_sql
        assert "[dbo].[source_tbl]" in call_sql
        assert mock_open.call_args_list[0].kwargs.get("autocommit") is True

    async def test_no_at_clause_without_timestamp(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        mock_oc = patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn])
        with mock_oc as mock_open:
            await tables.clone_table(target, "dbo.source_tbl", "dbo.sales")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert " AT " not in call_sql
        assert mock_open.call_args_list[0].kwargs.get("autocommit") is True

    async def test_at_clause_appended_when_provided(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        at_dt = datetime(2024, 5, 20, 14, 0, 0, tzinfo=UTC)
        mock_oc = patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn])
        with mock_oc as mock_open:
            await tables.clone_table(target, "dbo.source_tbl", "dbo.sales", at=at_dt)
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "AT '2024-05-20T14:00:00.000'" in call_sql
        assert mock_open.call_args_list[0].kwargs.get("autocommit") is True

    async def test_at_clause_formats_milliseconds(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        # 123456 microseconds → 123 milliseconds in the literal
        at_dt = datetime(2024, 5, 20, 14, 0, 0, 123456, tzinfo=UTC)
        mock_oc = patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn])
        with mock_oc as mock_open:
            await tables.clone_table(target, "dbo.source_tbl", "dbo.sales", at=at_dt)
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "AT '2024-05-20T14:00:00.123'" in call_sql
        assert mock_open.call_args_list[0].kwargs.get("autocommit") is True

    async def test_at_clause_rounds_sub_millisecond_up(self) -> None:
        """V17: sub-millisecond precision is rounded, not truncated.

        750 µs is >= 500 µs so it rounds to 1 ms.  The old truncation
        (// 1000) would have produced .000, silently shifting the
        point-in-time 0.75 ms earlier.
        """
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        # 750 µs → rounds to 1 ms (not truncated to 0 ms)
        at_dt = datetime(2024, 5, 20, 14, 0, 0, 750, tzinfo=UTC)
        mock_oc = patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn])
        with mock_oc as mock_open:
            await tables.clone_table(target, "dbo.source_tbl", "dbo.sales", at=at_dt)
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "AT '2024-05-20T14:00:00.001'" in call_sql
        assert mock_open.call_args_list[0].kwargs.get("autocommit") is True

    async def test_at_clause_rounds_carry_into_seconds(self) -> None:
        """V17: when rounding rolls microseconds to 1000 ms the carry propagates.

        999_750 µs rounds to 1000 ms = 1 s, so the second in the literal
        must increment and the millisecond part reset to .000.
        """
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        # 999_750 µs → rounds to 1000 ms → 1 s carry → 14:00:01.000
        at_dt = datetime(2024, 5, 20, 14, 0, 0, 999_750, tzinfo=UTC)
        mock_oc = patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn])
        with mock_oc as mock_open:
            await tables.clone_table(target, "dbo.source_tbl", "dbo.sales", at=at_dt)
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "AT '2024-05-20T14:00:01.000'" in call_sql
        assert mock_open.call_args_list[0].kwargs.get("autocommit") is True

    async def test_returns_table_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        mock_oc = patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn])
        with mock_oc as mock_open:
            result = await tables.clone_table(target, "dbo.source_tbl", "dbo.sales")
        assert isinstance(result, Table)
        assert mock_open.call_args_list[0].kwargs.get("autocommit") is True

    async def test_uses_autocommit_not_commit_without_at(self) -> None:
        """Clone DDL must use autocommit=True (no implicit transaction) — non-AT path."""
        target = _make_target()
        # First call: DDL (returns empty); second call: _fetch_table (returns row).
        fetch_return = (_LIST_COLS, [_TABLE_ROW_1])
        with patch(
            "fabric_dw.services.tables.run_query",
            side_effect=[([], []), fetch_return],
        ) as mock_run_query:
            await tables.clone_table(target, "dbo.source_tbl", "dbo.sales")
        # The FIRST call is the DDL; it must use autocommit=True, not commit=True.
        ddl_call = mock_run_query.call_args_list[0]
        assert ddl_call.kwargs.get("autocommit") is True, (
            "clone DDL must pass autocommit=True to run_query"
        )
        assert not ddl_call.kwargs.get("commit"), (
            "clone DDL must not pass commit=True (autocommit handles it)"
        )

    async def test_uses_autocommit_not_commit_with_at(self) -> None:
        """Clone DDL must use autocommit=True (no implicit transaction) — AT path."""
        target = _make_target()
        at_dt = datetime(2024, 5, 20, 14, 0, 0, tzinfo=UTC)
        fetch_return = (_LIST_COLS, [_TABLE_ROW_1])
        with patch(
            "fabric_dw.services.tables.run_query",
            side_effect=[([], []), fetch_return],
        ) as mock_run_query:
            await tables.clone_table(target, "dbo.source_tbl", "dbo.sales", at=at_dt)
        ddl_call = mock_run_query.call_args_list[0]
        assert ddl_call.kwargs.get("autocommit") is True, (
            "clone DDL with AT must pass autocommit=True to run_query"
        )
        assert not ddl_call.kwargs.get("commit"), (
            "clone DDL with AT must not pass commit=True (autocommit handles it)"
        )

    async def test_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        with pytest.raises(ItemKindError, match="read-only"):
            await tables.clone_table(
                target,
                "dbo.source_tbl",
                "dbo.sales",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    async def test_endpoint_guard_fires_before_io(self) -> None:
        """Guard fires without touching the network even if identifiers are invalid."""
        target = _make_target()
        with pytest.raises(ItemKindError):
            await tables.clone_table(
                target,
                "bad]schema.src",
                "dbo.sales",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    async def test_validates_source_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.clone_table(target, "bad]schema.src", "dbo.sales")

    async def test_validates_source_table_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.clone_table(target, "dbo.src--bad", "dbo.sales")

    async def test_validates_new_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.clone_table(target, "dbo.src", "bad]schema.sales")

    async def test_validates_new_table_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.clone_table(target, "dbo.src", "dbo.sales;drop")

    async def test_rejects_missing_dot_in_source(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match=r"substring not found|qualified"):
            await tables.clone_table(target, "nodot", "dbo.sales")

    async def test_rejects_missing_dot_in_new_table(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match=r"substring not found|qualified"):
            await tables.clone_table(target, "dbo.src", "nodot")

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
            await tables.clone_table(target, "dbo.source_tbl", "dbo.sales")


# ===========================================================================
# rename_table
# ===========================================================================


class TestRenameTable:
    async def test_emits_sp_rename(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.rename_table(target, "dbo.sales", "sales_v2")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "SP_RENAME" in call_sql

    async def test_passes_old_qualified_and_new_name_as_params(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.rename_table(target, "dbo.sales", "sales_v2")
        cursor = ddl_conn.cursor.return_value
        call_args = cursor.execute.call_args
        # params is the second positional arg to cursor.execute
        params = call_args[0][1] if len(call_args[0]) > 1 else (call_args[1] or {}).get("params")
        assert params is not None
        params_list = list(params)
        assert "dbo.sales" in params_list
        assert "sales_v2" in params_list

    async def test_object_type_is_embedded_in_sql(self) -> None:
        """'OBJECT' must appear literally in the SQL (not as a param)."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.rename_table(target, "dbo.sales", "sales_v2")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "'OBJECT'" in call_sql or "OBJECT" in call_sql.upper()

    async def test_returns_renamed_table_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        renamed_row: tuple[object, ...] = ("dbo", "sales_v2", _NOW, _LATER)
        fetch_conn = _make_conn([renamed_row], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.rename_table(target, "dbo.sales", "sales_v2")
        assert isinstance(result, Table)
        assert result.name == "sales_v2"
        assert result.schema_name == "dbo"
        assert result.qualified_name == "dbo.sales_v2"

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.rename_table(target, "dbo.sales", "sales_v2")
        ddl_conn.commit.assert_called_once()

    async def test_rejects_schema_qualified_new_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="schema-qualified"):
            await tables.rename_table(target, "dbo.sales", "other.sales_v2")

    async def test_rejects_invalid_new_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.rename_table(target, "dbo.sales", "bad--name")

    async def test_rejects_empty_new_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.rename_table(target, "dbo.sales", "")

    async def test_rejects_undotted_qualified_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="qualified"):
            await tables.rename_table(target, "nodot", "sales_v2")

    async def test_rejects_invalid_schema_in_qualified_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.rename_table(target, "bad--schema.sales", "sales_v2")

    async def test_rejects_invalid_table_in_qualified_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.rename_table(target, "dbo.bad--table", "sales_v2")

    async def test_raises_not_found_when_fetch_returns_empty(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([], _LIST_COLS)
        with (
            patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]),
            pytest.raises(NotFoundError, match="not found after rename"),
        ):
            await tables.rename_table(target, "dbo.sales", "sales_v2")

    async def test_warehouse_kind_allowed(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.rename_table(
                target, "dbo.sales", "sales_v2", kind=WarehouseKind.WAREHOUSE
            )
        assert isinstance(result, Table)


# ===========================================================================
# create_empty_table
# ===========================================================================


class TestCreateEmptyTable:
    _COLS = _LIST_COLS  # schema_name, name, created, modified

    async def test_basic_ddl_executed_and_table_returned(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        cols = [
            ColumnSpec(name="id", sql_type="INT", nullable=False),
            ColumnSpec(name="name", sql_type="VARCHAR(255)", nullable=True),
        ]
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.create_empty_table(target, "dbo", "sales", cols)
        assert isinstance(result, Table)
        assert result.name == "sales"

    async def test_ddl_contains_create_table(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        cols = [ColumnSpec(name="id", sql_type="INT", nullable=False)]
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.create_empty_table(target, "dbo", "sales", cols)
        ddl_cursor = ddl_conn.cursor.return_value
        sql: str = ddl_cursor.execute.call_args[0][0]
        assert "CREATE TABLE" in sql
        assert "[dbo]" in sql
        assert "[sales]" in sql

    async def test_ddl_uses_not_null(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        cols = [ColumnSpec(name="id", sql_type="INT", nullable=False)]
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.create_empty_table(target, "dbo", "sales", cols)
        ddl_cursor = ddl_conn.cursor.return_value
        sql: str = ddl_cursor.execute.call_args[0][0]
        assert "NOT NULL" in sql

    async def test_ddl_uses_null(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        cols = [ColumnSpec(name="desc", sql_type="VARCHAR(100)", nullable=True)]
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.create_empty_table(target, "dbo", "sales", cols)
        ddl_cursor = ddl_conn.cursor.return_value
        sql: str = ddl_cursor.execute.call_args[0][0]
        assert " NULL" in sql

    async def test_rejects_empty_columns(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="empty"):
            await tables.create_empty_table(target, "dbo", "sales", [])

    async def test_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        cols = [ColumnSpec(name="id", sql_type="INT", nullable=True)]
        with pytest.raises(ItemKindError):
            await tables.create_empty_table(
                target, "dbo", "sales", cols, kind=WarehouseKind.SQL_ENDPOINT
            )

    async def test_rejects_invalid_schema(self) -> None:
        target = _make_target()
        cols = [ColumnSpec(name="id", sql_type="INT", nullable=True)]
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.create_empty_table(target, "bad;schema", "sales", cols)

    async def test_rejects_invalid_table_name(self) -> None:
        target = _make_target()
        cols = [ColumnSpec(name="id", sql_type="INT", nullable=True)]
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.create_empty_table(target, "dbo", "bad--table", cols)

    async def test_rejects_invalid_column_name(self) -> None:
        target = _make_target()
        cols = [ColumnSpec(name="bad]col", sql_type="INT", nullable=True)]
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.create_empty_table(target, "dbo", "sales", cols)

    async def test_rejects_unsupported_sql_type(self) -> None:
        target = _make_target()
        cols = [ColumnSpec(name="col", sql_type="TEXT", nullable=True)]
        with pytest.raises(ValueError, match="Unsupported"):
            await tables.create_empty_table(target, "dbo", "sales", cols)

    async def test_rejects_injection_in_type(self) -> None:
        target = _make_target()
        cols = [ColumnSpec(name="col", sql_type="INT; DROP TABLE foo--", nullable=True)]
        with pytest.raises(ValueError, match="Unsupported"):
            await tables.create_empty_table(target, "dbo", "sales", cols)

    async def test_rejects_injection_in_col_name(self) -> None:
        target = _make_target()
        cols = [ColumnSpec(name="col]; DROP TABLE foo--", sql_type="INT", nullable=True)]
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await tables.create_empty_table(target, "dbo", "sales", cols)

    async def test_warehouse_kind_allowed(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        cols = [ColumnSpec(name="id", sql_type="INT", nullable=True)]
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.create_empty_table(
                target, "dbo", "sales", cols, kind=WarehouseKind.WAREHOUSE
            )
        assert isinstance(result, Table)


# ===========================================================================
# create_table_from_parquet
# ===========================================================================


class TestCreateTableFromParquet:
    async def test_creates_table_from_parquet_schema(self, tmp_path: Path) -> None:
        parquet_file = tmp_path / "data.parquet"
        schema = pa.schema(
            [
                pa.field("id", pa.int32(), nullable=False),
                pa.field("name", pa.string(), nullable=True),
            ]
        )
        pq.write_table(
            pa.table(
                {"id": pa.array([], type=pa.int32()), "name": pa.array([], type=pa.string())},
                schema=schema,
            ),
            str(parquet_file),
        )

        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.create_table_from_parquet(target, "dbo", "sales", parquet_file)
        assert isinstance(result, Table)

    async def test_parquet_ddl_uses_bracket_quoted_names(self, tmp_path: Path) -> None:
        parquet_file = tmp_path / "data.parquet"
        schema = pa.schema([pa.field("id", pa.int32())])
        pq.write_table(
            pa.table({"id": pa.array([], type=pa.int32())}, schema=schema),
            str(parquet_file),
        )

        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.create_table_from_parquet(target, "dbo", "sales", parquet_file)
        ddl_cursor = ddl_conn.cursor.return_value
        sql: str = ddl_cursor.execute.call_args[0][0]
        assert "[dbo]" in sql
        assert "[sales]" in sql
        assert "[id]" in sql

    async def test_parquet_rejects_sql_endpoint(self, tmp_path: Path) -> None:
        parquet_file = tmp_path / "data.parquet"
        pq.write_table(pa.table({"id": pa.array([], type=pa.int32())}), str(parquet_file))
        target = _make_target()
        with pytest.raises(ItemKindError):
            await tables.create_table_from_parquet(
                target, "dbo", "sales", parquet_file, kind=WarehouseKind.SQL_ENDPOINT
            )

    async def test_parquet_raises_file_not_found(self, tmp_path: Path) -> None:
        target = _make_target()
        with pytest.raises(FileNotFoundError):
            await tables.create_table_from_parquet(
                target, "dbo", "sales", tmp_path / "nonexistent.parquet"
            )


# ===========================================================================
# create_table_from_csv
# ===========================================================================


class TestCreateTableFromCsv:
    async def test_creates_table_from_csv_header(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id,name\n1,Alice\n2,Bob\n")

        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.create_table_from_csv(target, "dbo", "sales", csv_file)
        assert isinstance(result, Table)

    async def test_all_varchar_uses_varchar(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("count,label\n1,foo\n")

        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await tables.create_table_from_csv(target, "dbo", "sales", csv_file, all_varchar=True)
        ddl_cursor = ddl_conn.cursor.return_value
        sql: str = ddl_cursor.execute.call_args[0][0]
        assert "VARCHAR" in sql

    async def test_csv_rejects_sql_endpoint(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n")
        target = _make_target()
        with pytest.raises(ItemKindError):
            await tables.create_table_from_csv(
                target, "dbo", "sales", csv_file, kind=WarehouseKind.SQL_ENDPOINT
            )

    async def test_csv_raises_file_not_found(self, tmp_path: Path) -> None:
        target = _make_target()
        with pytest.raises(FileNotFoundError):
            await tables.create_table_from_csv(target, "dbo", "sales", tmp_path / "nonexistent.csv")

    async def test_csv_empty_file_raises(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("")
        target = _make_target()
        with pytest.raises(ValueError, match="empty"):
            await tables.create_table_from_csv(target, "dbo", "sales", csv_file, all_varchar=True)

    async def test_csv_bounded_streaming_reads_only_prefix(self, tmp_path: Path) -> None:
        """Only a bounded prefix of the CSV is read — large tail is never loaded.

        We write a 10-row CSV and pass sample_rows=3.  The resulting table must
        use the inferred schema (not all-varchar) and the DDL must reference the
        correct column names, confirming inference ran on the sample only.
        """
        csv_file = tmp_path / "big.csv"
        rows = ["id,value"] + [f"{i},{i * 10}" for i in range(10)]
        csv_file.write_text("\n".join(rows) + "\n")

        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.create_table_from_csv(
                target, "dbo", "sales", csv_file, sample_rows=3
            )
        assert isinstance(result, Table)
        ddl_cursor = ddl_conn.cursor.return_value
        sql: str = ddl_cursor.execute.call_args[0][0]
        # Columns must appear in the DDL — confirms schema was inferred.
        assert "[id]" in sql
        assert "[value]" in sql

    async def test_csv_header_only_produces_varchar_columns(self, tmp_path: Path) -> None:
        """A header-only CSV (no data rows) results in NULL-typed columns mapped to VARCHAR."""
        csv_file = tmp_path / "header_only.csv"
        csv_file.write_text("col_a,col_b\n")

        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_TABLE_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await tables.create_table_from_csv(target, "dbo", "sales", csv_file)
        assert isinstance(result, Table)
        ddl_cursor = ddl_conn.cursor.return_value
        sql: str = ddl_cursor.execute.call_args[0][0]
        assert "[col_a]" in sql
        assert "[col_b]" in sql
