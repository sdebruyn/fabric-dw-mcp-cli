import os
import uuid
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio

from fabric_dw.auth import get_credential
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import Warehouse, WarehouseSnapshot
from fabric_dw.services import snapshots, warehouses
from fabric_dw.sql import SqlTarget


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
