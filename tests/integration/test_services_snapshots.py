import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import Warehouse, WarehouseSnapshot
from fabric_dw.services import snapshots
from fabric_dw.sql import SqlTarget

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


async def test_roll_timestamp_updates_snapshot(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_snapshot: WarehouseSnapshot,
    ephemeral_sql_target: SqlTarget,
) -> None:
    """roll_timestamp advances the snapshot's timestamp to the requested datetime."""
    # Pick a roll target that satisfies both constraints:
    #   1. AFTER source DB creation: the source warehouse was already SQL-ready
    #      before this line runs, thanks to the readiness-polling fixtures — so it
    #      is provably tens of seconds old, well above the 30 s buffer.
    #   2. SAFELY IN THE PAST: using exactly "now" risks clock-skew rejection
    #      (Fabric's server clock may be a few seconds ahead of the test client).
    #      Subtracting 30 s ensures the target is comfortably in the past from
    #      the server's perspective while still landing after DB creation.
    new_dt = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(seconds=30)

    await snapshots.roll_timestamp(
        ephemeral_sql_target,
        ephemeral_snapshot.name,
        new_dt,
    )

    # Re-fetch the snapshot via the typed API to confirm the change is visible.
    listed = await snapshots.list_snapshots(
        http, workspace_id, ephemeral_snapshot.parent_warehouse_id
    )
    updated = next((s for s in listed if s.id == ephemeral_snapshot.id), None)
    assert updated is not None, "snapshot not found after roll_timestamp"
    assert updated.snapshot_dt is not None, "snapshot_dt should be set after roll_timestamp"
    # The API stores the timestamp without sub-second precision; compare at second granularity.
    assert updated.snapshot_dt.replace(tzinfo=UTC, microsecond=0) == new_dt
