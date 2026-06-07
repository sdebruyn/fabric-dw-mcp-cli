import os
import uuid
from collections.abc import AsyncIterator
from uuid import UUID

import pytest_asyncio

from fabric_dw.auth import get_credential
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import Warehouse
from fabric_dw.services import warehouses
from fabric_dw.sql_client import FabricSqlClient


@pytest_asyncio.fixture
async def workspace_id() -> UUID:
    raw = os.environ.get("FABRIC_TEST_WORKSPACE_ID")
    if not raw:
        msg = "set FABRIC_TEST_WORKSPACE_ID for integration tests"
        raise RuntimeError(msg)
    return UUID(raw)


@pytest_asyncio.fixture
async def http() -> AsyncIterator[FabricHttpClient]:
    cred = get_credential()
    async with FabricHttpClient(cred) as client:
        yield client


@pytest_asyncio.fixture
async def sql() -> AsyncIterator[FabricSqlClient]:
    async with FabricSqlClient() as client:
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
