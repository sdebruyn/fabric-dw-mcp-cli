"""Integration tests for services.statistics — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_statistics.py

Fixture notes:
- ``read_target`` (parametrized): runs list_statistics against both the shared warm
  warehouse and the shared SQL analytics endpoint.
- ``warehouse_schema``: used for the full create/show/update/drop lifecycle because
  ``tables.create_table`` and ``statistics.create_statistics`` are DWH-only operations.

The ``read_target`` fixture is parametrized over two targets:
  - ``[warehouse]``     — Data Warehouse (always runs)
  - ``[sql_endpoint]``  — SQL Analytics Endpoint (``pytest.mark.sql_endpoint``, CI only)

Note on ``show_statistics``: DBCC SHOW_STATISTICS requires a statistic to exist on a
table. Since CREATE STATISTICS is DWH-only (rejected on SQL Analytics Endpoints), the
show_statistics test is covered only through the warehouse roundtrip below.  On the SQL
analytics endpoint, show_statistics could theoretically surface auto-created stats from
the Lakehouse Delta tables, but the endpoint may not have any statistics to show on a
freshly provisioned endpoint, so no separate show_statistics endpoint test is added here.
"""

from __future__ import annotations

import contextlib

import pytest

from fabric_dw.exceptions import ItemKindError, NotFoundError
from fabric_dw.models import Statistic, StatisticDetails, WarehouseKind
from fabric_dw.services import statistics, tables
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Dual-target read test — runs against both warehouse and SQL analytics endpoint
# ---------------------------------------------------------------------------


async def test_list_statistics_returns_a_list(
    read_target: SqlTarget,
) -> None:
    """list_statistics works on both Data Warehouses and SQL Analytics Endpoints."""
    result = await statistics.list_statistics(read_target)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# SQL Endpoint guard rejection (client-side, no network SQL required)
# ---------------------------------------------------------------------------


async def test_create_statistics_endpoint_guard_rejected(
    read_target: SqlTarget,
) -> None:
    """create_statistics raises ItemKindError on SQL Analytics Endpoints (client-side guard)."""
    with pytest.raises(ItemKindError, match="read-only"):
        await statistics.create_statistics(
            read_target,
            "dbo.nonexistent_table",
            "id",
            name="should_never_be_created",
            kind=WarehouseKind.SQL_ENDPOINT,
        )


async def test_update_statistics_endpoint_guard_rejected(
    read_target: SqlTarget,
) -> None:
    """update_statistics raises ItemKindError on SQL Analytics Endpoints (client-side guard)."""
    with pytest.raises(ItemKindError, match="read-only"):
        await statistics.update_statistics(
            read_target,
            "dbo.nonexistent_table",
            "should_not_update",
            kind=WarehouseKind.SQL_ENDPOINT,
        )


async def test_drop_statistics_endpoint_guard_rejected(
    read_target: SqlTarget,
) -> None:
    """drop_statistics raises ItemKindError on SQL Analytics Endpoints (client-side guard)."""
    with pytest.raises(ItemKindError, match="read-only"):
        await statistics.drop_statistics(
            read_target,
            "dbo.nonexistent_table",
            "should_not_drop",
            kind=WarehouseKind.SQL_ENDPOINT,
        )


# ---------------------------------------------------------------------------
# Full create → list → show → update → drop round-trip on shared warehouse
# (DWH-only: CREATE TABLE and CREATE STATISTICS are not supported on endpoints)
# ---------------------------------------------------------------------------


async def test_create_list_show_update_drop_roundtrip(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """Full lifecycle: create a table, build a statistic, inspect it, then drop all."""
    sql_target, schema = warehouse_schema
    table_name = "pytest_stat_roundtrip"
    stat_name = "pytest_stat_on_id"
    select_body = "SELECT 1 AS id, 'hello' AS name"

    try:
        # Create the table inside the per-test schema.
        await tables.create_table(sql_target, schema, table_name, select_body)

        qualified_table = f"{schema}.{table_name}"

        # Create the statistic (fullscan)
        created = await statistics.create_statistics(
            sql_target,
            qualified_table,
            "id",
            name=stat_name,
            fullscan=True,
        )
        assert isinstance(created, Statistic)
        assert created.name == stat_name
        assert created.qualified_table == qualified_table
        assert created.column == "id"
        assert created.user_created is True
        assert created.auto_created is False

        # list_statistics — stat must be visible
        all_stats = await statistics.list_statistics(sql_target)
        stat_names = {s.name for s in all_stats}
        assert stat_name in stat_names

        # list_statistics filtered by schema and table
        filtered = await statistics.list_statistics(
            sql_target, schema=schema, table=table_name, user_only=True
        )
        assert any(s.name == stat_name for s in filtered)

        # show_statistics — stat header must be populated
        details = await statistics.show_statistics(sql_target, qualified_table, stat_name)
        assert isinstance(details, StatisticDetails)
        assert details.stat_header is not None
        assert details.stat_header.name == stat_name

        # show_statistics with histogram_only
        hist_details = await statistics.show_statistics(
            sql_target, qualified_table, stat_name, histogram_only=True
        )
        assert isinstance(hist_details, StatisticDetails)
        assert hist_details.stat_header is None  # skipped

        # update_statistics
        await statistics.update_statistics(sql_target, qualified_table, stat_name, fullscan=True)

        # After update, show still works
        updated_details = await statistics.show_statistics(sql_target, qualified_table, stat_name)
        assert updated_details.stat_header is not None

        # drop_statistics
        await statistics.drop_statistics(sql_target, qualified_table, stat_name)

        # After drop, show should raise NotFoundError (statistics.py raises it at line ~408)
        with pytest.raises(NotFoundError):
            await statistics.show_statistics(sql_target, qualified_table, stat_name)

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)
