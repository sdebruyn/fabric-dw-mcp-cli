"""Integration tests for services.restore — runs against a real Fabric environment.

These tests require:
    FABRIC_TEST_WORKSPACE_ID  — UUID of the target workspace.

The ``ephemeral_warehouse`` fixture creates a fresh warehouse and deletes it
after the test, so the workspace is left clean.

restore_in_place is intentionally NOT tested here because it mutates the
warehouse for ~10 minutes and would break any concurrent test that touches the
same warehouse. It is covered in unit tests with full LRO mocking.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import RestorePoint, Warehouse
from fabric_dw.services import restore

pytestmark = pytest.mark.integration


async def test_create_list_rename_delete_roundtrip(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_warehouse: Warehouse,
) -> None:
    """Full CRUD round-trip: create → list → rename → delete."""
    name = f"pytest-rp-{uuid.uuid4().hex[:8]}"
    rp: RestorePoint | None = None

    try:
        rp = await restore.create_point(
            http,
            workspace_id,
            ephemeral_warehouse.id,
            name=name,
            description="integration test restore point",
        )
        assert isinstance(rp, RestorePoint)
        assert rp.name == name

        # list — the new point should appear
        listed = await restore.list_points(http, workspace_id, ephemeral_warehouse.id)
        assert any(r.id == rp.id for r in listed)

        # get — individual fetch
        fetched = await restore.get_point(http, workspace_id, ephemeral_warehouse.id, rp.id)
        assert fetched.id == rp.id
        assert fetched.name == name

        # rename
        new_name = f"{name}-renamed"
        updated = await restore.update_point(
            http,
            workspace_id,
            ephemeral_warehouse.id,
            rp.id,
            name=new_name,
        )
        assert updated.name == new_name

    finally:
        if rp is not None:
            await restore.delete_point(http, workspace_id, ephemeral_warehouse.id, rp.id)
