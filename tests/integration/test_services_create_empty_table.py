"""Integration tests for create_empty_table, create_table_from_parquet, create_table_from_csv.

Run with: pytest -m integration tests/integration/test_services_create_empty_table.py

Fixture note: uses ``warehouse_schema`` from conftest, which creates a uniquely-named
schema inside the session-shared warm warehouse and cascade-drops it on teardown.
The endpoint guard test uses ``ephemeral_sql_endpoint`` (a separate, function-scoped
fixture that creates and tears down an ephemeral lakehouse + SQL endpoint).
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_dw.exceptions import ItemKindError
from fabric_dw.models import ColumnSpec, Table, WarehouseKind
from fabric_dw.services import tables
from fabric_dw.sql import SqlTarget, run_query

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_columns(target: SqlTarget, schema: str, table_name: str) -> list[dict[str, object]]:
    """Fetch column metadata from sys.columns for the given table."""
    sql = """\
SELECT c.name AS column_name, t.name AS type_name, c.is_nullable
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
JOIN sys.tables tbl ON tbl.object_id = c.object_id
JOIN sys.schemas s ON s.schema_id = tbl.schema_id
WHERE s.name = ? AND tbl.name = ?
ORDER BY c.column_id;
"""
    cols, rows = run_query(target, sql, params=[schema, table_name])
    return [dict(zip(cols, r, strict=True)) for r in rows]


# ---------------------------------------------------------------------------
# create_empty_table — explicit schema
# ---------------------------------------------------------------------------


async def test_create_empty_table_explicit_schema(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """Creating a table from an explicit ColumnSpec list produces the correct columns."""
    sql_target, schema = warehouse_schema
    table_name = "pytest_empty_explicit"

    columns = [
        ColumnSpec(name="id", sql_type="INT", nullable=False),
        ColumnSpec(name="label", sql_type="VARCHAR(200)", nullable=True),
        ColumnSpec(name="score", sql_type="FLOAT", nullable=True),
    ]

    try:
        result = await tables.create_empty_table(sql_target, schema, table_name, columns)
        assert isinstance(result, Table)
        assert result.schema_name == schema
        assert result.name == table_name

        # Verify schema via sys.columns
        db_cols = _get_columns(sql_target, schema, table_name)
        assert len(db_cols) == 3
        col_names = [c["column_name"] for c in db_cols]
        assert "id" in col_names
        assert "label" in col_names
        assert "score" in col_names

        # Verify nullability: id should be NOT NULL
        id_col = next(c for c in db_cols if c["column_name"] == "id")
        assert not id_col["is_nullable"], "id column should be NOT NULL"

        # Verify the table is empty (DDL-only, no data)
        _, rows = run_query(sql_target, f"SELECT COUNT(*) FROM [{schema}].[{table_name}]")  # noqa: S608  # noqa: S608
        assert rows[0][0] == 0

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# create_empty_table — endpoint guard
# ---------------------------------------------------------------------------


async def test_create_empty_table_rejects_sql_endpoint(
    ephemeral_sql_endpoint: object,
) -> None:
    """create_empty_table raises ItemKindError for SQL Analytics Endpoints."""
    from fabric_dw.models import Warehouse  # noqa: PLC0415

    endpoint = ephemeral_sql_endpoint  # type: ignore[assignment]
    assert isinstance(endpoint, Warehouse)
    assert endpoint.connection_string

    # Use the already-provisioned endpoint fixture.
    target = SqlTarget(
        workspace_id=str(endpoint.workspace_id),
        database=endpoint.name,
        connection_string=endpoint.connection_string,  # type: ignore[arg-type]
    )
    cols = [ColumnSpec(name="id", sql_type="INT", nullable=True)]
    with pytest.raises(ItemKindError):
        await tables.create_empty_table(
            target, "dbo", "should_not_be_created", cols, kind=WarehouseKind.SQL_ENDPOINT
        )


# ---------------------------------------------------------------------------
# create_table_from_parquet
# ---------------------------------------------------------------------------


async def test_create_table_from_parquet(
    warehouse_schema: tuple[SqlTarget, str],
    tmp_path: Path,
) -> None:
    """create_table_from_parquet reads schema from a Parquet file and creates an empty table."""
    sql_target, schema = warehouse_schema
    table_name = "pytest_empty_parquet"

    # Write a tiny Parquet file with a typed schema — no data rows.
    arrow_schema = pa.schema(
        [
            pa.field("record_id", pa.int64(), nullable=False),
            pa.field("description", pa.string(), nullable=True),
            pa.field("amount", pa.float64(), nullable=True),
            pa.field("is_active", pa.bool_(), nullable=True),
        ]
    )
    parquet_file = tmp_path / "sample.parquet"
    pq.write_table(
        pa.table(
            {
                "record_id": pa.array([], type=pa.int64()),
                "description": pa.array([], type=pa.string()),
                "amount": pa.array([], type=pa.float64()),
                "is_active": pa.array([], type=pa.bool_()),
            },
            schema=arrow_schema,
        ),
        str(parquet_file),
    )

    try:
        result = await tables.create_table_from_parquet(
            sql_target, schema, table_name, parquet_file
        )
        assert isinstance(result, Table)
        assert result.schema_name == schema
        assert result.name == table_name

        # Verify columns in the DB match the Parquet schema.
        db_cols = _get_columns(sql_target, schema, table_name)
        col_names = [c["column_name"] for c in db_cols]
        assert "record_id" in col_names
        assert "description" in col_names
        assert "amount" in col_names
        assert "is_active" in col_names

        # Verify empty (no data loaded).
        _, rows = run_query(sql_target, f"SELECT COUNT(*) FROM [{schema}].[{table_name}]")  # noqa: S608
        assert rows[0][0] == 0

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# create_table_from_csv
# ---------------------------------------------------------------------------


async def test_create_table_from_csv(
    warehouse_schema: tuple[SqlTarget, str],
    tmp_path: Path,
) -> None:
    """create_table_from_csv reads header + sample from CSV and creates an empty table."""
    sql_target, schema = warehouse_schema
    table_name = "pytest_empty_csv"

    csv_file = tmp_path / "sample.csv"
    csv_file.write_text(
        "product_id,product_name,unit_price,in_stock\n1,Widget,9.99,true\n2,Gadget,19.99,false\n"
    )

    try:
        result = await tables.create_table_from_csv(sql_target, schema, table_name, csv_file)
        assert isinstance(result, Table)
        assert result.schema_name == schema
        assert result.name == table_name

        # Verify columns exist.
        db_cols = _get_columns(sql_target, schema, table_name)
        col_names = [c["column_name"] for c in db_cols]
        assert "product_id" in col_names
        assert "product_name" in col_names

        # Verify empty (no data loaded).
        _, rows = run_query(sql_target, f"SELECT COUNT(*) FROM [{schema}].[{table_name}]")  # noqa: S608
        assert rows[0][0] == 0

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)


async def test_create_table_from_csv_all_varchar(
    warehouse_schema: tuple[SqlTarget, str],
    tmp_path: Path,
) -> None:
    """create_table_from_csv with --all-varchar maps every column to VARCHAR."""
    sql_target, schema = warehouse_schema
    table_name = "pytest_empty_csv_varchar"

    csv_file = tmp_path / "sample_vc.csv"
    csv_file.write_text("qty,name\n1,foo\n2,bar\n")

    try:
        result = await tables.create_table_from_csv(
            sql_target, schema, table_name, csv_file, all_varchar=True, varchar_length=255
        )
        assert isinstance(result, Table)

        db_cols = _get_columns(sql_target, schema, table_name)
        for col in db_cols:
            assert str(col["type_name"]).lower() == "varchar", (
                f"Expected VARCHAR for {col['column_name']!r}, got {col['type_name']!r}"
            )

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)
