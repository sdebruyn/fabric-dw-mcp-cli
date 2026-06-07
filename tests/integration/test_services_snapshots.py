import uuid
from uuid import UUID

import pytest

from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import Warehouse
from fabric_dw.services import snapshots

pytestmark = pytest.mark.integration


async def test_create_list_rename_delete_roundtrip(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    name = f"pytest-snap-{uuid.uuid4().hex[:8]}"
    snap = await snapshots.create(http, workspace_id, ephemeral_warehouse.id, name)
    try:
        listed = await snapshots.list_snapshots(http, workspace_id, ephemeral_warehouse.id)
        assert snap.id in {s.id for s in listed}

        new_name = f"{name}-renamed"
        renamed = await snapshots.rename(http, workspace_id, snap.id, new_name=new_name)
        assert renamed.name == new_name
    finally:
        await snapshots.delete(http, workspace_id, snap.id)
