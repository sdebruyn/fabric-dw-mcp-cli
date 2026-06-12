import asyncio
import contextlib
import os
import time
import uuid
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio

from fabric_dw.auth import get_credential
from fabric_dw.exceptions import NotFoundError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import Warehouse, WarehouseKind, WarehouseSnapshot
from fabric_dw.services import snapshots, warehouses
from fabric_dw.sql import SqlTarget

# Maximum time to wait for a SQL analytics endpoint to provision on a new lakehouse.
_SQL_ENDPOINT_PROVISION_TIMEOUT_S = 300
# Polling interval between provisioning status checks.
_SQL_ENDPOINT_POLL_INTERVAL_S = 5


@pytest_asyncio.fixture
async def workspace_id() -> UUID:
    raw = os.environ.get("FABRIC_TEST_WORKSPACE_ID")
    if not raw:
        pytest.skip(
            "set FABRIC_TEST_WORKSPACE_ID to run integration tests",
            allow_module_level=True,
        )
    return UUID(raw)


@pytest_asyncio.fixture
async def http() -> AsyncIterator[FabricHttpClient]:
    cred = get_credential()
    async with FabricHttpClient(cred) as client:
        yield client


@pytest_asyncio.fixture
async def ephemeral_warehouse(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> AsyncIterator[Warehouse]:
    name = f"pytest-{uuid.uuid4().hex[:8]}-wh"
    wh = await warehouses.create(http, workspace_id, name)
    try:
        yield wh
    finally:
        await warehouses.delete(http, workspace_id, wh.id)


@pytest_asyncio.fixture
async def ephemeral_snapshot(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_warehouse: Warehouse,
) -> AsyncIterator[WarehouseSnapshot]:
    name = f"pytest-{uuid.uuid4().hex[:8]}-snap"
    snap = await snapshots.create(http, workspace_id, ephemeral_warehouse.id, name)
    try:
        yield snap
    finally:
        await snapshots.delete(http, workspace_id, snap.id)


@pytest_asyncio.fixture
async def ephemeral_sql_target(
    workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> AsyncIterator[SqlTarget]:
    assert ephemeral_warehouse.connection_string
    yield SqlTarget(
        workspace_id=str(workspace_id),
        database=ephemeral_warehouse.name,
        connection_string=ephemeral_warehouse.connection_string,
    )


@pytest_asyncio.fixture
async def ephemeral_lakehouse(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> AsyncIterator[dict[str, object]]:
    """Create a schema-enabled Lakehouse and delete it after the test.

    Yields the raw API response dict from the POST (or the GET after LRO), which
    contains at minimum ``id``, ``displayName``, and ``workspaceId``.

    The Lakehouse is created with ``creationPayload.enableSchemas=true`` so that
    the auto-provisioned SQL analytics endpoint is fully functional.  Fabric
    automatically provisions a paired SQL analytics endpoint alongside every
    Lakehouse — no extra API call is needed to create it.

    The fixture does NOT wait for the SQL endpoint to provision; use
    ``ephemeral_sql_endpoint`` if you need a ready endpoint.
    """
    name = f"pytest-{uuid.uuid4().hex[:8]}-lh"
    body: dict[str, object] = {
        "displayName": name,
        "description": "ephemeral integration-test lakehouse",
        "creationPayload": {"enableSchemas": True},
    }

    resp = await http.request(
        "POST",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/lakehouses",
        json=body,
    )

    location = resp.headers.get("Location")
    if location is not None:
        # 202 Accepted — poll the LRO then fetch the created item
        lro_result = await http.poll_operation(location)
        resource_location = lro_result.get("resourceLocation")
        if not isinstance(resource_location, str) or not resource_location:
            pytest.skip(
                f"create lakehouse LRO completed but no resourceLocation returned: {lro_result}"
            )
        lakehouse_id = resource_location.rsplit("/", 1)[-1]
        get_resp = await http.request(
            "GET",
            HttpBase.FABRIC,
            f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}",
        )
        lakehouse = get_resp.json()
    else:
        # 201 Created — body contains the new item directly
        lakehouse = resp.json()

    try:
        yield lakehouse
    finally:
        lh_id = lakehouse.get("id")
        if lh_id:
            with contextlib.suppress(NotFoundError):
                await http.request(
                    "DELETE",
                    HttpBase.FABRIC,
                    f"/workspaces/{workspace_id}/lakehouses/{lh_id}",
                )


@pytest_asyncio.fixture
async def ephemeral_sql_endpoint(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_lakehouse: dict[str, object],
) -> AsyncIterator[Warehouse]:
    """Derive the SQL Analytics Endpoint from ``ephemeral_lakehouse`` and wait for it to provision.

    Polls ``GET /workspaces/{ws}/lakehouses/{id}`` until
    ``properties.sqlEndpointProperties.provisioningStatus`` reaches ``Success``.
    Skips the test (rather than failing) if provisioning does not complete
    within ``_SQL_ENDPOINT_PROVISION_TIMEOUT_S`` seconds or if it fails.

    Yields a :class:`~fabric_dw.models.Warehouse` instance typed as
    ``WarehouseKind.SQL_ENDPOINT`` with the endpoint ID and connection string
    populated.
    """
    lh_id = ephemeral_lakehouse.get("id")
    if not lh_id:
        pytest.skip("ephemeral_lakehouse fixture returned no id")

    deadline = time.monotonic() + _SQL_ENDPOINT_PROVISION_TIMEOUT_S

    while True:
        if time.monotonic() >= deadline:
            pytest.skip(
                f"SQL analytics endpoint for lakehouse {lh_id} did not provision within "
                f"{_SQL_ENDPOINT_PROVISION_TIMEOUT_S}s — skipping endpoint-specific tests"
            )

        get_resp = await http.request(
            "GET",
            HttpBase.FABRIC,
            f"/workspaces/{workspace_id}/lakehouses/{lh_id}",
        )
        lh_body = get_resp.json()
        props = lh_body.get("properties") or {}
        sql_ep_props = props.get("sqlEndpointProperties") or {}

        status = sql_ep_props.get("provisioningStatus", "")
        if status == "Success":
            ep_id_raw = sql_ep_props.get("id", "")
            ep_conn = sql_ep_props.get("connectionString", "")
            if not ep_id_raw:
                pytest.skip(f"SQL endpoint provisioned but id is missing for lakehouse {lh_id}")
            # Construct a Warehouse-shaped dict so we can use model_validate
            ep_uuid = UUID(str(ep_id_raw))
            wh = Warehouse.model_validate(
                {
                    "id": str(ep_uuid),
                    "displayName": lh_body.get("displayName", ""),
                    "workspaceId": str(workspace_id),
                    "kind": WarehouseKind.SQL_ENDPOINT,
                    "connectionString": ep_conn,
                }
            )
            yield wh
            return

        if status == "Failed":
            pytest.skip(
                f"SQL analytics endpoint provisioning failed for lakehouse {lh_id} — skipping"
            )

        await asyncio.sleep(_SQL_ENDPOINT_POLL_INTERVAL_S)
