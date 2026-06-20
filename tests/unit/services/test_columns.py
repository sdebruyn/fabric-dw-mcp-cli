"""Unit tests for services.columns.

Covers: format_data_type, get_object_columns, get_object_columns_or_raise,
get_columns_for_schemas.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from fabric_dw.exceptions import NotFoundError
from fabric_dw.services.columns import (
    format_data_type,
    get_columns_for_schemas,
    get_object_columns,
    get_object_columns_or_raise,
)
from tests.unit.services._helpers import _make_target

# ---------------------------------------------------------------------------
# Fixture data shared across tests
# ---------------------------------------------------------------------------

_SCHEMA = "dbo"
_TABLE = "Sales"
_VIEW = "vw_Sales"

_COLUMN_COLS = [
    "ordinal",
    "name",
    "type_name",
    "max_length",
    "precision",
    "scale",
    "nullable",
    "collation_name",
    "is_identity",
    "is_computed",
]

# Rows for a simple two-column table: id INT NOT NULL, name NVARCHAR(100) NULL
_ROWS_TWO_COLS: list[tuple[object, ...]] = [
    (1, "id", "int", 4, 10, 0, False, None, False, False),
    (2, "name", "nvarchar", 200, 0, 0, True, "Latin1_General_CI_AS", False, False),
]

# ---------------------------------------------------------------------------
# format_data_type — per type-family
# ---------------------------------------------------------------------------


class TestFormatDataType:
    """Covers every type family mentioned in the issue."""

    def test_int_no_suffix(self) -> None:
        assert format_data_type("int", 4, 10, 0) == "INT"

    def test_bigint_no_suffix(self) -> None:
        assert format_data_type("bigint", 8, 19, 0) == "BIGINT"

    def test_bit_no_suffix(self) -> None:
        assert format_data_type("bit", 1, 1, 0) == "BIT"

    def test_date_no_suffix(self) -> None:
        assert format_data_type("date", 3, 10, 0) == "DATE"

    def test_varchar_with_length(self) -> None:
        assert format_data_type("varchar", 50, 0, 0) == "VARCHAR(50)"

    def test_varchar_max(self) -> None:
        assert format_data_type("varchar", -1, 0, 0) == "VARCHAR(MAX)"

    def test_char_with_length(self) -> None:
        assert format_data_type("char", 10, 0, 0) == "CHAR(10)"

    def test_binary_with_length(self) -> None:
        assert format_data_type("binary", 16, 0, 0) == "BINARY(16)"

    def test_varbinary_max(self) -> None:
        assert format_data_type("varbinary", -1, 0, 0) == "VARBINARY(MAX)"

    def test_nvarchar_divides_max_length_by_two(self) -> None:
        # max_length=200 bytes → 100 chars
        assert format_data_type("nvarchar", 200, 0, 0) == "NVARCHAR(100)"

    def test_nvarchar_max(self) -> None:
        assert format_data_type("nvarchar", -1, 0, 0) == "NVARCHAR(MAX)"

    def test_nchar_divides_by_two(self) -> None:
        # max_length=20 bytes → 10 chars
        assert format_data_type("nchar", 20, 0, 0) == "NCHAR(10)"

    def test_decimal_precision_and_scale(self) -> None:
        assert format_data_type("decimal", 9, 18, 2) == "DECIMAL(18,2)"

    def test_numeric_precision_and_scale(self) -> None:
        assert format_data_type("numeric", 9, 10, 4) == "NUMERIC(10,4)"

    def test_datetime2_with_scale(self) -> None:
        assert format_data_type("datetime2", 8, 7, 7) == "DATETIME2(7)"

    def test_time_with_scale(self) -> None:
        assert format_data_type("time", 5, 0, 3) == "TIME(3)"

    def test_datetimeoffset_with_scale(self) -> None:
        assert format_data_type("datetimeoffset", 10, 7, 7) == "DATETIMEOFFSET(7)"

    def test_smallint_no_suffix(self) -> None:
        assert format_data_type("smallint", 2, 5, 0) == "SMALLINT"

    def test_float_no_suffix(self) -> None:
        assert format_data_type("float", 8, 53, 0) == "FLOAT"

    def test_real_no_suffix(self) -> None:
        assert format_data_type("real", 4, 24, 0) == "REAL"

    def test_uniqueidentifier_no_suffix(self) -> None:
        assert format_data_type("uniqueidentifier", 16, 0, 0) == "UNIQUEIDENTIFIER"

    def test_type_name_uppercased(self) -> None:
        # type_name from sys.types is always lowercase; result must be uppercase.
        assert format_data_type("int", 4, 10, 0) == "INT"
        assert format_data_type("varchar", 100, 0, 0) == "VARCHAR(100)"


# ---------------------------------------------------------------------------
# get_object_columns — happy path
# ---------------------------------------------------------------------------


class TestGetObjectColumns:
    async def test_returns_columns_ordered_by_ordinal(self) -> None:
        target = _make_target()

        with patch(
            "fabric_dw.services.columns.run_query",
            return_value=(_COLUMN_COLS, _ROWS_TWO_COLS),
        ):
            result = await get_object_columns(target, _SCHEMA, _TABLE)

        assert len(result) == 2
        assert result[0]["ordinal"] == 1
        assert result[0]["name"] == "id"
        assert result[0]["data_type"] == "INT"
        assert result[0]["nullable"] is False
        assert result[0]["collation_name"] is None
        assert result[0]["is_identity"] is False
        assert result[0]["is_computed"] is False

        assert result[1]["ordinal"] == 2
        assert result[1]["name"] == "name"
        assert result[1]["data_type"] == "NVARCHAR(100)"
        assert result[1]["nullable"] is True
        assert result[1]["collation_name"] == "Latin1_General_CI_AS"

    async def test_returns_empty_list_for_nonexistent_object(self) -> None:
        target = _make_target()

        with patch("fabric_dw.services.columns.run_query", return_value=(_COLUMN_COLS, [])):
            result = await get_object_columns(target, _SCHEMA, "NonExistent")

        assert result == []

    async def test_rejects_invalid_schema_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await get_object_columns(target, "bad schema!", _TABLE)

    async def test_rejects_invalid_object_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await get_object_columns(target, _SCHEMA, "bad; DROP TABLE foo--")

    async def test_formats_varchar_length_correctly(self) -> None:
        rows: list[tuple[object, ...]] = [
            (1, "descr", "varchar", 255, 0, 0, True, "Latin1_General_CI_AS", False, False),
        ]
        with patch("fabric_dw.services.columns.run_query", return_value=(_COLUMN_COLS, rows)):
            result = await get_object_columns(_make_target(), _SCHEMA, _TABLE)

        assert result[0]["data_type"] == "VARCHAR(255)"

    async def test_formats_decimal_precision_scale(self) -> None:
        rows: list[tuple[object, ...]] = [
            (1, "price", "decimal", 9, 18, 2, False, None, False, False),
        ]
        with patch("fabric_dw.services.columns.run_query", return_value=(_COLUMN_COLS, rows)):
            result = await get_object_columns(_make_target(), _SCHEMA, _TABLE)

        assert result[0]["data_type"] == "DECIMAL(18,2)"

    async def test_formats_datetime2_scale(self) -> None:
        rows: list[tuple[object, ...]] = [
            (1, "created_at", "datetime2", 8, 7, 7, False, None, False, False),
        ]
        with patch("fabric_dw.services.columns.run_query", return_value=(_COLUMN_COLS, rows)):
            result = await get_object_columns(_make_target(), _SCHEMA, _TABLE)

        assert result[0]["data_type"] == "DATETIME2(7)"

    async def test_works_for_views_same_as_tables(self) -> None:
        """get_object_columns is object-agnostic — same path for tables and views."""
        rows: list[tuple[object, ...]] = [
            (1, "col1", "bit", 1, 1, 0, False, None, False, False),
        ]
        with patch("fabric_dw.services.columns.run_query", return_value=(_COLUMN_COLS, rows)):
            result = await get_object_columns(_make_target(), _SCHEMA, _VIEW)

        assert result[0]["data_type"] == "BIT"
        assert result[0]["name"] == "col1"


# ---------------------------------------------------------------------------
# get_object_columns_or_raise — not-found path
# ---------------------------------------------------------------------------


class TestGetObjectColumnsOrRaise:
    async def test_raises_not_found_for_empty_result(self) -> None:
        target = _make_target()
        with (
            patch("fabric_dw.services.columns.run_query", return_value=(_COLUMN_COLS, [])),
            pytest.raises(NotFoundError, match=r"Table \[dbo\]\.\[ghost\] not found"),
        ):
            await get_object_columns_or_raise(target, "dbo", "ghost", kind_label="table")

    async def test_raises_not_found_with_view_label(self) -> None:
        target = _make_target()
        with (
            patch("fabric_dw.services.columns.run_query", return_value=(_COLUMN_COLS, [])),
            pytest.raises(NotFoundError, match=r"View \[dbo\]\.\[ghost\] not found"),
        ):
            await get_object_columns_or_raise(target, "dbo", "ghost", kind_label="view")

    async def test_returns_columns_when_found(self) -> None:
        rows: list[tuple[object, ...]] = [
            (1, "id", "int", 4, 10, 0, False, None, False, False),
        ]
        with patch("fabric_dw.services.columns.run_query", return_value=(_COLUMN_COLS, rows)):
            result = await get_object_columns_or_raise(
                _make_target(), _SCHEMA, _TABLE, kind_label="table"
            )

        assert len(result) == 1
        assert result[0]["name"] == "id"


# ---------------------------------------------------------------------------
# get_columns_for_schemas — bulk fetch
# ---------------------------------------------------------------------------

# Bulk query columns include schema_name and object_name as first two fields.
_BULK_COLS = [
    "schema_name",
    "object_name",
    "ordinal",
    "name",
    "type_name",
    "max_length",
    "precision",
    "scale",
    "nullable",
    "collation_name",
    "is_identity",
    "is_computed",
]

# Two tables across two schemas: dbo.Orders (id INT), finance.Budget (amount DECIMAL(18,2))
_BULK_ROWS: list[tuple[object, ...]] = [
    ("dbo", "Orders", 1, "id", "int", 4, 10, 0, False, None, False, False),
    ("finance", "Budget", 1, "amount", "decimal", 9, 18, 2, False, None, False, False),
    (
        "finance",
        "Budget",
        2,
        "dept",
        "nvarchar",
        100,  # max_length=100 bytes → 50 chars (nvarchar stores 2 bytes per char)
        0,
        0,
        True,
        "Latin1_General_CI_AS",
        False,
        False,
    ),
]


class TestGetColumnsForSchemas:
    async def test_returns_empty_dict_for_no_tables(self) -> None:
        with patch("fabric_dw.services.columns.run_query", return_value=(_BULK_COLS, [])):
            result = await get_columns_for_schemas(_make_target())

        assert result == {}

    async def test_keys_by_schema_and_table(self) -> None:
        with patch("fabric_dw.services.columns.run_query", return_value=(_BULK_COLS, _BULK_ROWS)):
            result = await get_columns_for_schemas(_make_target())

        assert ("dbo", "Orders") in result
        assert ("finance", "Budget") in result

    async def test_single_table_single_column(self) -> None:
        with patch("fabric_dw.services.columns.run_query", return_value=(_BULK_COLS, _BULK_ROWS)):
            result = await get_columns_for_schemas(_make_target())

        orders_cols = result[("dbo", "Orders")]
        assert len(orders_cols) == 1
        assert orders_cols[0]["name"] == "id"
        assert orders_cols[0]["data_type"] == "INT"
        assert orders_cols[0]["nullable"] is False

    async def test_multi_column_table_preserves_order(self) -> None:
        with patch("fabric_dw.services.columns.run_query", return_value=(_BULK_COLS, _BULK_ROWS)):
            result = await get_columns_for_schemas(_make_target())

        budget_cols = result[("finance", "Budget")]
        assert len(budget_cols) == 2
        assert budget_cols[0]["name"] == "amount"
        assert budget_cols[0]["data_type"] == "DECIMAL(18,2)"
        assert budget_cols[1]["name"] == "dept"
        assert budget_cols[1]["data_type"] == "NVARCHAR(50)"

    async def test_formats_type_correctly(self) -> None:
        """Ensure format_data_type is applied through the bulk path."""
        rows: list[tuple[object, ...]] = [
            ("dbo", "T", 1, "col", "nvarchar", 200, 0, 0, True, None, False, False),
        ]
        with patch("fabric_dw.services.columns.run_query", return_value=(_BULK_COLS, rows)):
            result = await get_columns_for_schemas(_make_target())

        assert result[("dbo", "T")][0]["data_type"] == "NVARCHAR(100)"

    async def test_issues_single_query(self) -> None:
        """Verify only one run_query call is made — no N+1."""
        with patch(
            "fabric_dw.services.columns.run_query", return_value=(_BULK_COLS, _BULK_ROWS)
        ) as mock_rq:
            await get_columns_for_schemas(_make_target())

        assert mock_rq.call_count == 1
