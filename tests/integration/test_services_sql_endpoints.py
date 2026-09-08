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

import asyncio
import time
import uuid
from uuid import UUID

import pytest

from fabric_dw.exceptions import NotFoundError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import TableSyncStatus, Warehouse, WarehouseKind
from fabric_dw.services import sql_endpoints

from .conftest import SharedSqlEndpointTarget, _write_delta_table_to_onelake

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
# Lakehouse discovery-gap cross-check (#1064; schema-enabled support #1060)
# ---------------------------------------------------------------------------
#
# ephemeral_sql_endpoint is backed by a SCHEMA-ENABLED Lakehouse (see its
# fixture docstring above ephemeral_lakehouse), so it exercises exactly the
# path find_undiscovered_lakehouse_tables uses for a schema-enabled Lakehouse
# (the OneLake table API), and proves that properties.defaultSchema really is
# present on a live schema-enabled Lakehouse -- the signal that path branches
# on.
#
# test_find_undiscovered_tables_finds_a_real_gap (below) is the first live
# proof that the OK (successful comparison, gap found) path actually works
# end to end: it writes a Delta table directly to OneLake without refreshing
# the endpoint's metadata sync, then asserts the cross-check finds exactly
# that table under its real schema. Up to #1060, this feature had only ever
# been proven to refuse to run, never to work.


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
    result = await sql_endpoints.find_undiscovered_lakehouse_tables(http, workspace_id, bogus, {})
    assert result.status == sql_endpoints.LakehouseDiscoveryStatus.NOT_LAKEHOUSE_BACKED
    assert result.missing_tables == ()


# Bounded polling for the OneLake table API to pick up a table written
# directly to OneLake outside its own metadata sync. This latency is
# unconfirmed against a live tenant -- see the skip branch below.
_GAP_TABLE_VISIBLE_TIMEOUT_S = 120  # 2 min
_GAP_TABLE_POLL_INTERVAL_S = 5


@pytest.mark.sql_endpoint
async def test_find_undiscovered_tables_finds_a_real_gap(
    http: FabricHttpClient,
    shared_sql_endpoint: SharedSqlEndpointTarget,
) -> None:
    """Live proof that the OK (gap found) comparison path actually works end to end (#1060).

    Every prior integration test for this feature could only prove a REFUSAL
    (SCHEMA_ENABLED_UNSUPPORTED, since removed, or NOT_LAKEHOUSE_BACKED) --
    never that the comparison itself finds a real gap. This test writes one
    Delta table directly to OneLake, under a schema unique to this run, via
    :func:`~tests.integration.conftest._write_delta_table_to_onelake`,
    deliberately WITHOUT calling ``refresh_metadata`` afterwards. That means
    the table exists in the Lakehouse's own OneLake table inventory but can
    never appear in the endpoint's ``sys.tables`` catalog during this test --
    a real, on-demand discovery gap, not a simulated one.

    The gap schema is unique per test run (a fresh UUID suffix) and distinct
    from the shared ``sample`` schema other ``sql_endpoint``-marked tests read
    from concurrently in the same session, so this never touches, and does
    not need to avoid, the shared seed data -- see ``shared_sql_endpoint``'s
    "MUST NOT mutate the seed schema" rule in conftest.py.
    """
    from fabric_dw.services import tables as tables_svc  # noqa: PLC0415
    from fabric_dw.services.sql_endpoints import (  # noqa: PLC0415
        LakehouseDiscoveryStatus,
        find_undiscovered_lakehouse_tables,
    )

    gap_schema = f"pytest_gap_{uuid.uuid4().hex[:8]}"
    gap_table = "gaptable"

    await _write_delta_table_to_onelake(
        shared_sql_endpoint.workspace_id,
        shared_sql_endpoint.lakehouse_id,
        gap_schema,
        gap_table,
    )

    # Build the real known-names-by-schema map from the endpoint's current
    # catalog (sample.colors / sample.numbers, already synced by the shared
    # fixture) so the assertion below can be an exact match rather than a
    # "somewhere in here" membership check.
    catalog_rows = await tables_svc.list_table_sync_status(
        shared_sql_endpoint.sql_target, kind=WarehouseKind.SQL_ENDPOINT
    )
    known_by_schema: dict[str, set[str]] = {}
    for row in catalog_rows:
        known_by_schema.setdefault(row.schema_name, set()).add(row.name)
    known_names_by_schema = {s: frozenset(n) for s, n in known_by_schema.items()}

    # The OneLake table API's discovery latency for a brand-new schema/table
    # written directly (rather than via a Fabric-driven ingestion path) is not
    # documented anywhere; poll with a bounded timeout instead of assuming the
    # write is visible immediately, mirroring this file's other live-latency
    # waits (e.g. _wait_for_seeded_tables_visible).
    deadline = time.monotonic() + _GAP_TABLE_VISIBLE_TIMEOUT_S
    while True:
        result = await find_undiscovered_lakehouse_tables(
            http,
            shared_sql_endpoint.workspace_id,
            shared_sql_endpoint.endpoint.id,
            known_names_by_schema,
        )
        if result.status != LakehouseDiscoveryStatus.OK:
            pytest.fail(
                f"expected LakehouseDiscoveryStatus.OK for a schema-enabled Lakehouse-backed "
                f"endpoint, got {result.status!r}"
            )
        if (gap_schema, gap_table) in result.missing_tables:
            break
        if time.monotonic() >= deadline:
            pytest.skip(
                f"table {gap_schema}.{gap_table} was written directly to OneLake but never "
                f"appeared via the OneLake table API within {_GAP_TABLE_VISIBLE_TIMEOUT_S}s -- "
                "discovery latency for this preview API is unconfirmed against a live tenant; "
                f"seen so far: {result.missing_tables!r}"
            )
        await asyncio.sleep(_GAP_TABLE_POLL_INTERVAL_S)

    # Exact match: the ONLY gap against the real catalog is the table this
    # test just wrote, under its real schema -- not "a gap was found
    # somewhere", but precisely this one.
    assert result.missing_tables == ((gap_schema, gap_table),)
    assert result.case_mismatched_tables == ()
