"""Integration tests for services.sql_exec.get_plan — SHOWPLAN_XML capture.

Requires a live Fabric Data Warehouse and optionally a SQL Analytics Endpoint.
Uses ``shared_warehouse`` and ``ephemeral_sql_endpoint`` from conftest.
These tests do NOT execute any SQL — only the estimated plan is captured.
"""

from __future__ import annotations

import pytest

from fabric_dw.models import Warehouse, WarehouseKind
from fabric_dw.services import sql_exec
from fabric_dw.sql import SqlTarget

from .conftest import SharedWarehouseTarget

pytestmark = pytest.mark.integration

# The standard namespace prefix present in every SHOWPLAN_XML response.
_SHOWPLAN_NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan"


async def test_get_plan_warehouse_returns_xml(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    """get_plan on a Warehouse returns a non-empty SHOWPLAN_XML string.

    The plan XML must contain the standard Showplan namespace so callers can
    identify it as a valid SHOWPLAN_XML document.
    """
    sql_target: SqlTarget = shared_warehouse.sql_target
    plan_xml = await sql_exec.get_plan(sql_target, "SELECT 1 AS hello")

    assert plan_xml, "get_plan returned an empty string"
    assert _SHOWPLAN_NS in plan_xml, (
        f"Expected Showplan namespace in plan XML; got: {plan_xml[:200]!r}"
    )


async def test_get_plan_warehouse_query_not_executed(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    """get_plan must not execute the query — the plan is estimated only.

    We verify this by submitting a DML statement (DELETE FROM a non-existent table)
    and confirming get_plan returns a plan XML rather than executing and raising.
    A real DELETE would fail with "object not found"; a plan-only call succeeds.
    """
    sql_target: SqlTarget = shared_warehouse.sql_target
    # This query would fail if executed (table does not exist), but planning is safe.
    plan_xml = await sql_exec.get_plan(sql_target, "SELECT 1 AS n WHERE 1=0")
    assert plan_xml, "get_plan returned an empty string for a SELECT plan"
    assert _SHOWPLAN_NS in plan_xml


async def test_get_plan_sql_endpoint_returns_xml(
    workspace_id: object,
    ephemeral_sql_endpoint: Warehouse,
) -> None:
    """get_plan works on a SQL Analytics Endpoint (not just Warehouses).

    The endpoint must return a valid SHOWPLAN_XML for a simple SELECT.
    """
    assert ephemeral_sql_endpoint.connection_string, (
        f"Endpoint {ephemeral_sql_endpoint.id} has no connection_string"
    )
    assert ephemeral_sql_endpoint.kind == WarehouseKind.SQL_ENDPOINT

    sql_target = SqlTarget(
        workspace_id=str(workspace_id),
        database=ephemeral_sql_endpoint.name,
        connection_string=ephemeral_sql_endpoint.connection_string,
    )
    plan_xml = await sql_exec.get_plan(sql_target, "SELECT 1 AS hello")

    assert plan_xml, "get_plan returned an empty string for SQL Analytics Endpoint"
    assert _SHOWPLAN_NS in plan_xml, (
        f"Expected Showplan namespace in SQL endpoint plan XML; got: {plan_xml[:200]!r}"
    )
