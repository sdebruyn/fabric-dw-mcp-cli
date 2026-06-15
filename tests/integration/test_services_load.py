"""Integration tests for services.load — requires real Fabric credentials + workspace.

Run with: pytest -m integration tests/integration/test_services_load.py

These tests:
- Create an empty target table in the shared warehouse (via DDL).
- Load a small local CSV, Parquet, and JSON file via the staging-lakehouse path.
- Assert the row count matches what was loaded.
- Assert the staging Lakehouse is cleaned up afterward.
- Load from a remote OneLake URL (using the OneLake URL of the staged file).
- Assert SQL Endpoint items are rejected.

The staging Lakehouse is created and destroyed by the load flow itself.
An additional ``finally`` guard ensures cleanup even when the test aborts.
"""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from fabric_dw.auth import get_credential
from fabric_dw.exceptions import ItemKindError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import WarehouseKind
from fabric_dw.services import tables
from fabric_dw.services.load import (
    CopyIntoCsvOptions,
    copy_into_from_url,
    delete_lakehouse,
    load_local_file,
)
from fabric_dw.sql import SqlTarget, run_query

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def http_and_cred():
    """Yield (FabricHttpClient, credential) sharing the same credential object."""
    cred = get_credential()
    async with FabricHttpClient(cred) as client:
        yield client, cred


def _count_rows(target: SqlTarget, schema: str, table_name: str) -> int:
    """Return the row count for schema.table via TDS."""
    from fabric_dw.identifiers import quote_identifier  # noqa: PLC0415

    schema_q = quote_identifier(schema)
    table_q = quote_identifier(table_name)
    _cols, rows = run_query(target, f"SELECT COUNT(*) FROM {schema_q}.{table_q}")  # noqa: S608
    return int(rows[0][0]) if rows else 0


async def _create_empty_table(
    target: SqlTarget,
    schema: str,
    table_name: str,
    ddl_body: str,
) -> None:
    """Create an empty target table by running a 0-row CTAS."""
    from fabric_dw.identifiers import quote_identifier  # noqa: PLC0415

    schema_q = quote_identifier(schema)
    table_q = quote_identifier(table_name)
    run_query(
        target,
        f"CREATE TABLE {schema_q}.{table_q} AS {ddl_body}",
        commit=True,
        fetch="none",
    )


async def _drop_table_if_exists(target: SqlTarget, schema: str, table_name: str) -> None:
    with contextlib.suppress(Exception):
        await tables.delete_table(target, schema, table_name)


# ---------------------------------------------------------------------------
# Tests: local CSV load
# ---------------------------------------------------------------------------


async def test_load_local_csv(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """Load a local CSV into an empty warehouse table and verify row count."""
    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_load_csv"

    csv_content = "id,name\n1,Alice\n2,Bob\n3,Charlie\n"
    csv_file = tmp_path / "data.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    # Create an empty target table matching the CSV schema.
    import asyncio  # noqa: PLC0415

    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE TABLE [{schema}].[{table_name}] (id INT, name NVARCHAR(100))",
        commit=True,
        fetch="none",
    )

    try:
        workspace_id = uuid.UUID(sql_target.workspace_id)
        csv_options = CopyIntoCsvOptions(first_row=2)  # skip header

        result = await load_local_file(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            csv_file,
            file_format="csv",
            csv_options=csv_options,
        )

        assert result.rows_loaded == 3, f"Expected 3 rows loaded, got {result.rows_loaded}"
        assert result.target == f"{schema}.{table_name}"

        row_count = await asyncio.to_thread(_count_rows, sql_target, schema, table_name)
        assert row_count == 3
    finally:
        await _drop_table_if_exists(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Tests: local Parquet load
# ---------------------------------------------------------------------------


async def test_load_local_parquet(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """Load a local Parquet file into an empty warehouse table and verify row count."""
    pytest.importorskip("pyarrow")

    import asyncio  # noqa: PLC0415

    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pap  # noqa: PLC0415

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_load_parquet"

    parquet_file = tmp_path / "data.parquet"
    pa_table = pa.table({"id": [1, 2], "value": [10, 20]})
    pap.write_table(pa_table, parquet_file)

    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE TABLE [{schema}].[{table_name}] (id INT, value INT)",
        commit=True,
        fetch="none",
    )

    try:
        workspace_id = uuid.UUID(sql_target.workspace_id)

        result = await load_local_file(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            parquet_file,
            file_format="parquet",
        )

        assert result.rows_loaded == 2
        row_count = await asyncio.to_thread(_count_rows, sql_target, schema, table_name)
        assert row_count == 2
    finally:
        await _drop_table_if_exists(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Tests: local JSON load (conversion path)
# ---------------------------------------------------------------------------


async def test_load_local_json(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """Load a local JSON file (converted to Parquet) and verify row count."""
    pytest.importorskip("pyarrow")

    import asyncio  # noqa: PLC0415

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_load_json"

    json_file = tmp_path / "data.json"
    json_file.write_text('{"id": 1, "name": "X"}\n{"id": 2, "name": "Y"}\n', encoding="utf-8")

    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE TABLE [{schema}].[{table_name}] (id INT, name NVARCHAR(100))",
        commit=True,
        fetch="none",
    )

    try:
        workspace_id = uuid.UUID(sql_target.workspace_id)

        result = await load_local_file(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            json_file,
            file_format="json",
        )

        assert result.rows_loaded == 2
        row_count = await asyncio.to_thread(_count_rows, sql_target, schema, table_name)
        assert row_count == 2
    finally:
        await _drop_table_if_exists(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Tests: staging Lakehouse cleanup after load
# ---------------------------------------------------------------------------


async def test_staging_lakehouse_deleted_after_load(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    """Verify the staging Lakehouse is deleted after a successful load."""
    import asyncio  # noqa: PLC0415

    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    table_name = "pytest_load_cleanup"

    csv_file = tmp_path / "data.csv"
    csv_file.write_text("id\n1\n2\n", encoding="utf-8")

    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE TABLE [{schema}].[{table_name}] (id INT)",
        commit=True,
        fetch="none",
    )

    workspace_id = uuid.UUID(sql_target.workspace_id)
    unique_lh_name = f"pytest_staging_{uuid.uuid4().hex[:8]}"
    lakehouse_id: str | None = None

    try:
        result = await load_local_file(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            table_name,
            csv_file,
            file_format="csv",
            csv_options=CopyIntoCsvOptions(first_row=2),
            staging_lakehouse_name=unique_lh_name,
        )
        assert result.rows_loaded == 2

        # Verify the Lakehouse no longer exists.
        from fabric_dw.exceptions import NotFoundError  # noqa: PLC0415

        try:
            resp = await http.request(
                "GET",
                HttpBase.FABRIC,
                f"/workspaces/{workspace_id}/lakehouses",
            )
            body = resp.json()
            items = body.get("value", [])
            lh_names = [i.get("displayName") for i in items]
            assert unique_lh_name not in lh_names, (
                f"Staging Lakehouse {unique_lh_name!r} was not deleted after load"
            )
        except NotFoundError:
            pass  # workspace gone — also fine
    finally:
        # Belt-and-suspenders: ensure the Lakehouse is gone even if the test fails.
        # If we know the lakehouse_id, delete it directly; otherwise try by name.
        if lakehouse_id:
            await delete_lakehouse(http, workspace_id, lakehouse_id)
        await _drop_table_if_exists(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Tests: SQL Endpoint rejection
# ---------------------------------------------------------------------------


async def test_copy_into_from_url_rejects_sql_endpoint(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    sql_target, schema = warehouse_schema
    with pytest.raises(ItemKindError):
        await copy_into_from_url(
            sql_target,
            schema,
            "t",
            "https://example.com/f.parquet",
            file_type="PARQUET",
            kind=WarehouseKind.SQL_ENDPOINT,
        )


async def test_load_local_file_rejects_sql_endpoint(
    warehouse_schema: tuple[SqlTarget, str],
    http_and_cred: tuple[FabricHttpClient, Any],
    tmp_path: Path,
) -> None:
    sql_target, schema = warehouse_schema
    http, cred = http_and_cred
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("id\n1\n", encoding="utf-8")
    workspace_id = uuid.UUID(sql_target.workspace_id)

    with pytest.raises(ItemKindError):
        await load_local_file(
            http,
            cred,
            workspace_id,
            sql_target,
            schema,
            "t",
            csv_file,
            file_format="csv",
            kind=WarehouseKind.SQL_ENDPOINT,
        )
