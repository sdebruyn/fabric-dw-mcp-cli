"""Integration tests for services.statistics — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_statistics.py

Fixture note: mutating tests use ``warehouse_schema`` from conftest, which creates
a uniquely-named schema inside the session-shared warm warehouse and cascade-drops
it on teardown.  Statistics are schema-scoped so each test is fully isolated.
The SQL Analytics Endpoint read-only test uses ``ephemeral_sql_endpoint`` because
it requires an actual SQL Analytics Endpoint (Lakehouse-backed item).
"""

from __future__ import annotations

import contextlib
from uuid import UUID

import pytest

from fabric_dw.exceptions import FabricError, ItemKindError, NotFoundError
from fabric_dw.models import Statistic, StatisticDetails, Warehouse, WarehouseKind
from fabric_dw.services import statistics, tables
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration


def _endpoint_to_target(endpoint: Warehouse, workspace_id: UUID) -> SqlTarget:
    """Build a SqlTarget from an ephemeral SQL Analytics Endpoint Warehouse."""
    assert endpoint.connection_string, "SQL endpoint has no connection string"
    return SqlTarget(
        workspace_id=str(workspace_id),
        database=endpoint.name,
        connection_string=endpoint.connection_string,
    )


# ---------------------------------------------------------------------------
# list / read-only operations on a SQL Analytics Endpoint
# ---------------------------------------------------------------------------


async def test_list_statistics_on_sql_endpoint(
    ephemeral_sql_endpoint: Warehouse,
    workspace_id: UUID,
) -> None:
    """list_statistics works on SQL Analytics Endpoints (read-only is allowed)."""
    target = _endpoint_to_target(ephemeral_sql_endpoint, workspace_id)
    result = await statistics.list_statistics(target)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# SQL Endpoint guard rejection (client-side, no network SQL required)
# ---------------------------------------------------------------------------


async def test_create_statistics_endpoint_guard_rejected(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """create_statistics raises ItemKindError on SQL Analytics Endpoints (client-side guard)."""
    sql_target, _schema = warehouse_schema
    with pytest.raises(ItemKindError, match="read-only"):
        await statistics.create_statistics(
            sql_target,
            "dbo.nonexistent_table",
            "id",
            name="should_never_be_created",
            kind=WarehouseKind.SQL_ENDPOINT,
        )


async def test_update_statistics_endpoint_guard_rejected(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """update_statistics raises ItemKindError on SQL Analytics Endpoints (client-side guard)."""
    sql_target, _schema = warehouse_schema
    with pytest.raises(ItemKindError, match="read-only"):
        await statistics.update_statistics(
            sql_target,
            "dbo.nonexistent_table",
            "should_not_update",
            kind=WarehouseKind.SQL_ENDPOINT,
        )


async def test_drop_statistics_endpoint_guard_rejected(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """drop_statistics raises ItemKindError on SQL Analytics Endpoints (client-side guard)."""
    sql_target, _schema = warehouse_schema
    with pytest.raises(ItemKindError, match="read-only"):
        await statistics.drop_statistics(
            sql_target,
            "dbo.nonexistent_table",
            "should_not_drop",
            kind=WarehouseKind.SQL_ENDPOINT,
        )


# ---------------------------------------------------------------------------
# Full create → list → show → update → drop round-trip on shared warehouse
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

        # After drop, show should raise NotFoundError or similar
        with pytest.raises((NotFoundError, FabricError, Exception)):
            await statistics.show_statistics(sql_target, qualified_table, stat_name)

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)
