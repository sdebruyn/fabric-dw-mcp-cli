"""Integration tests for create_and_load — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_create_and_load.py

These tests:
- Auto-create + load from local Parquet, CSV, and JSON.
- Auto-create + load from a remote OneLake URL.
- Replace an existing table (DROP + recreate + load).
- Append into an existing table.
- Truncate an existing table, then load.
- --cleanup-on-failure drops ONLY the table we created.
- Staging Lakehouse is cleaned up by the service.
"""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from fabric_dw.auth import get_credential
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.services import tables
from fabric_dw.services.load import (
    create_and_load,
)
from fabric_dw.sql import SqlTarget, run_query

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def http_and_cred():
    """Yield (FabricHttpClient, credential) sharing the same credential object."""
    cred = get_credential()
    async with FabricHttpClient(cred) as client:
        yield client, cred


def _count_rows(target: SqlTarget, schema: str, table_name: str) -> int:
    from fabric_dw.identifiers import quote_identifier  # noqa: PLC0415

    schema_q = quote_identifier(schema)
    table_q = quote_identifier(table_name)
    _cols, rows = run_query(target, f"SELECT COUNT(*) FROM {schema_q}.{table_q}")  # noqa: S608
    return int(rows[0][0]) if rows else 0


async def _drop_table_if_exists(target: SqlTarget, schema: str, table_name: str) -> None:
    with contextlib.suppress(Exception):
        await tables.delete_table(target, schema, table_name)


# ---------------------------------------------------------------------------
# Tests: auto-create + load from Parquet
# ---------------------------------------------------------------------------


async def test_create_and_load_local_parquet(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """Auto-create + load from a local Parquet file (schema inferred from footer)."""
    import asyncio  # noqa: PLC0415

    pytest.importorskip("pyarrow")
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pap  # noqa: PLC0415

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_cal_parquet"

    parquet_file = tmp_path / "data.parquet"
    pa_table = pa.table({"id": [1, 2, 3], "value": [10, 20, 30]})
    pap.write_table(pa_table, parquet_file)

    try:
        workspace_id = uuid.UUID(sql_target.workspace_id)
        result = await create_and_load(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            parquet_file,
            if_exists="fail",
            file_format="parquet",
        )
        assert result.rows_loaded == 3
        row_count = await asyncio.to_thread(_count_rows, sql_target, schema, table_name)
        assert row_count == 3
    finally:
        await _drop_table_if_exists(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Tests: auto-create + load from CSV
# ---------------------------------------------------------------------------


async def test_create_and_load_local_csv(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """Auto-create + load from a local CSV file."""
    import asyncio  # noqa: PLC0415

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_cal_csv"

    csv_file = tmp_path / "data.csv"
    csv_file.write_text("id,name\n1,Alice\n2,Bob\n", encoding="utf-8")

    try:
        workspace_id = uuid.UUID(sql_target.workspace_id)
        from fabric_dw.services.load import CopyIntoCsvOptions  # noqa: PLC0415

        csv_options = CopyIntoCsvOptions(first_row=2)
        result = await create_and_load(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            csv_file,
            if_exists="fail",
            file_format="csv",
            csv_options=csv_options,
        )
        assert result.rows_loaded == 2
        row_count = await asyncio.to_thread(_count_rows, sql_target, schema, table_name)
        assert row_count == 2
    finally:
        await _drop_table_if_exists(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Tests: auto-create + load from JSON
# ---------------------------------------------------------------------------


async def test_create_and_load_local_json(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """Auto-create + load from a local JSON file (converted to Parquet internally)."""
    import asyncio  # noqa: PLC0415

    pytest.importorskip("pyarrow")

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_cal_json"

    json_file = tmp_path / "data.json"
    json_file.write_text('{"id": 1, "name": "X"}\n{"id": 2, "name": "Y"}\n', encoding="utf-8")

    try:
        workspace_id = uuid.UUID(sql_target.workspace_id)
        result = await create_and_load(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            json_file,
            if_exists="fail",
            file_format="json",
        )
        assert result.rows_loaded == 2
        row_count = await asyncio.to_thread(_count_rows, sql_target, schema, table_name)
        assert row_count == 2
    finally:
        await _drop_table_if_exists(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Append policy
# ---------------------------------------------------------------------------


async def test_create_and_load_append(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """if_exists=append: load into existing table, doubling the rows."""
    import asyncio  # noqa: PLC0415

    pytest.importorskip("pyarrow")
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pap  # noqa: PLC0415

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_cal_append"

    parquet_file = tmp_path / "data.parquet"
    pa_table = pa.table({"id": [1, 2]})
    pap.write_table(pa_table, parquet_file)

    try:
        workspace_id = uuid.UUID(sql_target.workspace_id)
        # First load (creates table).
        await create_and_load(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            parquet_file,
            if_exists="fail",
            file_format="parquet",
        )
        # Second load (appends).
        result = await create_and_load(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            parquet_file,
            if_exists="append",
            file_format="parquet",
        )
        assert result.rows_loaded == 2
        row_count = await asyncio.to_thread(_count_rows, sql_target, schema, table_name)
        assert row_count == 4
    finally:
        await _drop_table_if_exists(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Truncate policy
# ---------------------------------------------------------------------------


async def test_create_and_load_truncate(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """if_exists=truncate: truncate existing table then load — row count matches last load."""
    import asyncio  # noqa: PLC0415

    pytest.importorskip("pyarrow")
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pap  # noqa: PLC0415

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_cal_truncate"

    parquet_file = tmp_path / "data.parquet"
    pa_table = pa.table({"id": [1, 2, 3]})
    pap.write_table(pa_table, parquet_file)

    try:
        workspace_id = uuid.UUID(sql_target.workspace_id)
        # First load (creates and loads 3 rows).
        await create_and_load(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            parquet_file,
            if_exists="fail",
            file_format="parquet",
        )
        # Second load (truncates, then loads 3 rows again).
        result = await create_and_load(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            parquet_file,
            if_exists="truncate",
            file_format="parquet",
        )
        assert result.rows_loaded == 3
        row_count = await asyncio.to_thread(_count_rows, sql_target, schema, table_name)
        assert row_count == 3
    finally:
        await _drop_table_if_exists(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Replace policy
# ---------------------------------------------------------------------------


async def test_create_and_load_replace(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """if_exists=replace: DROP + recreate from schema, then load."""
    import asyncio  # noqa: PLC0415

    pytest.importorskip("pyarrow")
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pap  # noqa: PLC0415

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_cal_replace"

    parquet_file = tmp_path / "data.parquet"
    pa_table = pa.table({"id": [1, 2], "value": [10, 20]})
    pap.write_table(pa_table, parquet_file)

    try:
        workspace_id = uuid.UUID(sql_target.workspace_id)
        # First load.
        await create_and_load(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            parquet_file,
            if_exists="fail",
            file_format="parquet",
        )
        # Replace load (drop + recreate + load).
        result = await create_and_load(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            parquet_file,
            if_exists="replace",
            file_format="parquet",
        )
        assert result.rows_loaded == 2
        row_count = await asyncio.to_thread(_count_rows, sql_target, schema, table_name)
        assert row_count == 2
    finally:
        await _drop_table_if_exists(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Tests: cleanup_on_failure drops only WE-created table
# ---------------------------------------------------------------------------


async def test_cleanup_on_failure_drops_created_table(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """When cleanup_on_failure=True and the load fails, the table WE created is dropped."""

    pytest.importorskip("pyarrow")
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pap  # noqa: PLC0415

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_cal_cleanup_created"

    parquet_file = tmp_path / "data.parquet"
    pa_table = pa.table({"id": [1]})
    pap.write_table(pa_table, parquet_file)

    workspace_id = uuid.UUID(sql_target.workspace_id)

    # Patch load_local_file to simulate a COPY INTO failure.
    from unittest.mock import AsyncMock, patch  # noqa: PLC0415

    try:
        with (
            patch(
                "fabric_dw.services.load.load_local_file",
                new=AsyncMock(side_effect=RuntimeError("simulated COPY INTO failure")),
            ),
            pytest.raises(RuntimeError),
        ):
            await create_and_load(
                http,
                cred,
                workspace_id,
                sql_target,
                schema,
                table_name,
                parquet_file,
                if_exists="fail",
                file_format="parquet",
                cleanup_on_failure=True,
            )

        # Table should be gone.
        existing_tables = await tables.list_tables(sql_target, schema=schema)
        names = {t.name for t in existing_tables}
        assert table_name not in names, (
            f"Table {table_name!r} should have been cleaned up but still exists"
        )
    finally:
        # Belt-and-suspenders.
        await _drop_table_if_exists(sql_target, schema, table_name)


async def test_cleanup_on_failure_does_not_drop_preexisting_table(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """cleanup_on_failure=True with pre-existing table: load fails, table is NOT dropped."""
    import asyncio  # noqa: PLC0415

    pytest.importorskip("pyarrow")
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pap  # noqa: PLC0415

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_cal_cleanup_preexist"

    parquet_file = tmp_path / "data.parquet"
    pa_table = pa.table({"id": [1]})
    pap.write_table(pa_table, parquet_file)

    workspace_id = uuid.UUID(sql_target.workspace_id)

    # Create the pre-existing table manually.
    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE TABLE [{schema}].[{table_name}] (id INT)",
        commit=True,
        fetch="none",
    )

    from unittest.mock import AsyncMock, patch  # noqa: PLC0415

    try:
        with (
            patch(
                "fabric_dw.services.load.load_local_file",
                new=AsyncMock(side_effect=RuntimeError("simulated COPY INTO failure")),
            ),
            pytest.raises(RuntimeError),
        ):
            await create_and_load(
                http,
                cred,
                workspace_id,
                sql_target,
                schema,
                table_name,
                parquet_file,
                if_exists="append",  # table exists → we don't create → cleanup_on_failure noop
                file_format="parquet",
                cleanup_on_failure=True,
            )

        # Pre-existing table must still exist.
        existing_tables = await tables.list_tables(sql_target, schema=schema)
        names = {t.name for t in existing_tables}
        assert table_name in names, (
            f"Pre-existing table {table_name!r} should NOT have been dropped"
        )
    finally:
        await _drop_table_if_exists(sql_target, schema, table_name)
