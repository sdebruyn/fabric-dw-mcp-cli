"""Integration tests for services.sql_endpoints — runs against a real Fabric environment.

These tests require:
    FABRIC_TEST_WORKSPACE_ID  — UUID of the target workspace.

## Test organisation

### Read / list / discovery tests  (no Lakehouse required)
These tests call ``list_endpoints``, ``list_all_workspaces``, and ``get_endpoint``
against whatever SQL analytics endpoints already exist in the workspace (or confirm
graceful behaviour when the workspace is empty).  They do NOT depend on the
``ephemeral_lakehouse`` / ``ephemeral_sql_endpoint`` fixtures.

### Endpoint-specific tests  (require ephemeral_sql_endpoint)
These tests exercise operations that need a real, provisioned SQL analytics
endpoint: ``get_endpoint`` (by ID) and ``refresh_metadata``.  They are gated on
the ``ephemeral_sql_endpoint`` fixture which creates a schema-enabled Lakehouse,
waits for its SQL endpoint to provision, and tears the Lakehouse down afterwards.

Note: ``refresh_metadata`` with ``recreate_tables=True`` is intentionally NOT
tested here because it is destructive and could break other items in the
workspace.  It is covered in unit tests with full LRO mocking.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from fabric_dw.exceptions import NotFoundError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import TableSyncStatus, Warehouse, WarehouseKind
from fabric_dw.services import sql_endpoints

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Read / list / discovery tests — no ephemeral Lakehouse required
# ---------------------------------------------------------------------------


async def test_list_endpoints_returns_list(http: FabricHttpClient, workspace_id: UUID) -> None:
    """list_endpoints always returns a list (may be empty)."""
    items = await sql_endpoints.list_endpoints(http, workspace_id)
    assert isinstance(items, list)


async def test_list_endpoints_all_sql_endpoint_kind(
    http: FabricHttpClient, workspace_id: UUID
) -> None:
    """Every item returned by list_endpoints must have kind == SQL_ENDPOINT."""
    items = await sql_endpoints.list_endpoints(http, workspace_id)
    for item in items:
        assert isinstance(item, Warehouse)
        assert item.kind == WarehouseKind.SQL_ENDPOINT, (
            f"expected SQL_ENDPOINT, got {item.kind!r} for item {item.id}"
        )


async def test_list_endpoints_items_have_id_and_name(
    http: FabricHttpClient, workspace_id: UUID
) -> None:
    """Each listed endpoint has a non-empty id and displayName."""
    items = await sql_endpoints.list_endpoints(http, workspace_id)
    for item in items:
        assert item.id, f"endpoint missing id: {item!r}"
        assert item.name, f"endpoint missing name: {item!r}"


async def test_list_all_workspaces_returns_list(http: FabricHttpClient) -> None:
    """list_all_workspaces scans all workspaces and returns a flat list."""
    items = await sql_endpoints.list_all_workspaces(http)
    assert isinstance(items, list)
    for item in items:
        assert isinstance(item, Warehouse)
        assert item.kind == WarehouseKind.SQL_ENDPOINT


async def test_get_endpoint_nonexistent_raises_not_found(
    http: FabricHttpClient, workspace_id: UUID
) -> None:
    """get_endpoint raises NotFoundError for a UUID that doesn't exist."""
    bogus = uuid.uuid4()
    with pytest.raises(NotFoundError):
        await sql_endpoints.get_endpoint(http, workspace_id, bogus)


# ---------------------------------------------------------------------------
# Endpoint-specific tests — require a provisioned SQL analytics endpoint
# ---------------------------------------------------------------------------


async def test_get_endpoint_by_id(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_sql_endpoint: Warehouse,
) -> None:
    """get_endpoint fetches the same endpoint we provisioned via the Lakehouse."""
    fetched = await sql_endpoints.get_endpoint(http, workspace_id, ephemeral_sql_endpoint.id)
    assert fetched.id == ephemeral_sql_endpoint.id
    assert fetched.kind == WarehouseKind.SQL_ENDPOINT


async def test_get_endpoint_connection_string_populated(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_sql_endpoint: Warehouse,
) -> None:
    """A fully provisioned endpoint must expose a non-empty connection string."""
    fetched = await sql_endpoints.get_endpoint(http, workspace_id, ephemeral_sql_endpoint.id)
    assert fetched.connection_string, (
        f"expected non-empty connection_string for endpoint {fetched.id}"
    )


async def test_endpoint_appears_in_list(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_sql_endpoint: Warehouse,
) -> None:
    """The provisioned endpoint must appear in list_endpoints for the workspace."""
    items = await sql_endpoints.list_endpoints(http, workspace_id)
    ids = {item.id for item in items}
    assert ephemeral_sql_endpoint.id in ids, (
        f"endpoint {ephemeral_sql_endpoint.id} not found in list_endpoints result: {ids}"
    )


async def test_refresh_metadata_returns_table_sync_statuses(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_sql_endpoint: Warehouse,
) -> None:
    """refresh_metadata (non-destructive) completes and returns a list of TableSyncStatus.

    A brand-new schema-enabled Lakehouse has no Delta tables yet, so the list
    may be empty — that is acceptable.  We assert shape, not count.
    """
    results = await sql_endpoints.refresh_metadata(
        http,
        workspace_id,
        ephemeral_sql_endpoint.id,
        recreate_tables=False,
    )
    assert isinstance(results, list)
    for entry in results:
        assert isinstance(entry, TableSyncStatus)
        assert entry.table_name
        assert entry.status


# ---------------------------------------------------------------------------
# Lakehouse discovery-gap cross-check (#1064)
# ---------------------------------------------------------------------------
#
# ephemeral_sql_endpoint is backed by a SCHEMA-ENABLED Lakehouse (see its
# fixture docstring above ephemeral_lakehouse), so it exercises the
# SCHEMA_ENABLED_UNSUPPORTED refusal path deterministically and proves that
# properties.defaultSchema really is present on a live schema-enabled
# Lakehouse -- the assumption the whole refusal is built on.
#
# It cannot exercise the OK (successful comparison, gap found or not) path:
# that needs a non-schema-enabled Lakehouse with a Delta table written
# directly to OneLake but not yet synced to the endpoint catalog, a state
# issue #1064 itself flagged as "awkward to produce on demand". That path is
# unverified against a live tenant -- see the PR description.


async def test_resolve_backing_lakehouse_finds_schema_enabled_lakehouse(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_lakehouse: dict[str, object],
    ephemeral_sql_endpoint: Warehouse,
) -> None:
    """resolve_backing_lakehouse resolves the endpoint back to its Lakehouse.

    Also confirms the core assumption find_undiscovered_lakehouse_tables relies
    on: a live schema-enabled Lakehouse's GET/list response really does carry
    properties.defaultSchema (here "dbo"), which is the documented signal used
    to refuse the table comparison rather than guess at a bare name's schema.
    """
    from fabric_dw._fabric_api import resolve_backing_lakehouse  # noqa: PLC0415

    match = await resolve_backing_lakehouse(http, workspace_id, ephemeral_sql_endpoint.id)
    assert match is not None, (
        f"expected to resolve endpoint {ephemeral_sql_endpoint.id} back to its "
        f"Lakehouse {ephemeral_lakehouse.get('id')!r}"
    )
    assert str(match.id) == str(ephemeral_lakehouse.get("id"))
    assert match.default_schema == "dbo", (
        f"expected a schema-enabled lakehouse to report defaultSchema='dbo', got "
        f"{match.default_schema!r}"
    )


async def test_find_undiscovered_tables_schema_enabled_lakehouse_is_unsupported(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_sql_endpoint: Warehouse,
) -> None:
    """A real schema-enabled Lakehouse-backed endpoint refuses the comparison."""
    result = await sql_endpoints.find_undiscovered_lakehouse_tables(
        http, workspace_id, ephemeral_sql_endpoint.id, frozenset()
    )
    assert result.status == sql_endpoints.LakehouseDiscoveryStatus.SCHEMA_ENABLED_UNSUPPORTED
    assert result.missing_table_names == ()


async def test_find_undiscovered_tables_non_lakehouse_endpoint_id(
    http: FabricHttpClient, workspace_id: UUID
) -> None:
    """A UUID that pairs with no lakehouse at all resolves as NOT_LAKEHOUSE_BACKED.

    No lakehouse in the workspace's /lakehouses listing pairs with a random
    UUID, so the scan simply finds no match -- the same code path an endpoint
    backed by a mirrored database (or anything else that isn't a Lakehouse)
    would take.
    """
    bogus = uuid.uuid4()
    result = await sql_endpoints.find_undiscovered_lakehouse_tables(
        http, workspace_id, bogus, frozenset()
    )
    assert result.status == sql_endpoints.LakehouseDiscoveryStatus.NOT_LAKEHOUSE_BACKED
    assert result.missing_table_names == ()
