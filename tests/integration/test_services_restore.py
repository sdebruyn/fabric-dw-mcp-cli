"""Integration tests for services.restore — runs against a real Fabric environment.

These tests require:
    FABRIC_TEST_WORKSPACE_ID  — UUID of the target workspace.

The ``ephemeral_warehouse`` fixture creates a fresh warehouse and deletes it
after the test, so the workspace is left clean.

restore_in_place opt-in
-----------------------
``test_restore_in_place_reverts_warehouse_state`` is skip-guarded by the
``FABRIC_RESTORE_IN_PLACE_TESTS`` environment variable.  It is NOT run in
standard CI because ``restore_in_place`` takes ~10 minutes for the LRO to
complete — this makes it impractical as an automated gate (it would stall the
pipeline for 10+ minutes per run).

The operation is safe to run on the ephemeral warehouse (teardown deletes the
warehouse regardless of state), but the unavoidable wall-clock cost is the
blocking constraint.  To run it manually::

    FABRIC_RESTORE_IN_PLACE_TESTS=1 uv run pytest \\
        tests/integration/test_services_restore.py::test_restore_in_place_reverts_warehouse_state \\
        -m integration -s -v
"""

from __future__ import annotations

import contextlib
import os
import uuid
from uuid import UUID

import pytest
import pytest_asyncio

from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import RestorePoint, Warehouse
from fabric_dw.services import restore, sql_exec
from fabric_dw.sql import SqlTarget

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


# ---------------------------------------------------------------------------
# restore_in_place — opt-in (guarded by FABRIC_RESTORE_IN_PLACE_TESTS)
# ---------------------------------------------------------------------------

# This test is intentionally skip-guarded.  restore_in_place is safe to run on
# an ephemeral warehouse (teardown deletes the warehouse regardless), but the
# LRO takes ~10 minutes to complete, making it impractical for standard CI.
# Set FABRIC_RESTORE_IN_PLACE_TESTS=1 to opt in when running manually.
_RESTORE_IN_PLACE_ENABLED = bool(os.environ.get("FABRIC_RESTORE_IN_PLACE_TESTS", ""))


@pytest_asyncio.fixture
async def restore_sql_target(workspace_id: UUID, ephemeral_warehouse: Warehouse) -> SqlTarget:
    """Return a SqlTarget for *ephemeral_warehouse*; skip if no connection string."""
    if not ephemeral_warehouse.connection_string:
        pytest.skip("ephemeral_warehouse has no connection_string — cannot run SQL assertions")
    return SqlTarget(
        workspace_id=str(workspace_id),
        database=ephemeral_warehouse.name,
        connection_string=ephemeral_warehouse.connection_string,
    )


@pytest.mark.skipif(
    not _RESTORE_IN_PLACE_ENABLED,
    reason=(
        "restore_in_place takes ~10 minutes (LRO); skipped in standard CI. "
        "Set FABRIC_RESTORE_IN_PLACE_TESTS=1 to opt in."
    ),
)
async def test_restore_in_place_reverts_warehouse_state(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_warehouse: Warehouse,
    restore_sql_target: SqlTarget,
) -> None:
    """restore_in_place rolls back schema changes made after the restore point.

    Workflow
    --------
    1. Create a restore point (baseline — empty warehouse).
    2. Create a sentinel table via SQL so there is a detectable state delta.
    3. Call ``restore_in_place`` to roll back to the baseline restore point;
       poll the LRO to completion (up to 15 minutes, matching documented SLA).
    4. Assert the sentinel table no longer exists — confirming the rollback.
    5. Cleanup: delete the restore point (warehouse teardown handles the rest).
    """
    sentinel_table = f"pytest_rip_{uuid.uuid4().hex[:8]}"
    rp: RestorePoint | None = None

    try:
        # Step 1 — create restore point before any schema changes.
        rp = await restore.create_point(
            http,
            workspace_id,
            ephemeral_warehouse.id,
            name=f"pytest-rip-{uuid.uuid4().hex[:8]}",
            description="restore_in_place integration test baseline",
        )
        assert isinstance(rp, RestorePoint)

        # Step 2 — introduce a detectable schema change after the restore point.
        await sql_exec.execute(
            restore_sql_target,
            f"CREATE TABLE {sentinel_table} (id INT)",
        )

        # Confirm the table exists before restore.
        # sentinel_table comes from uuid.uuid4().hex — not user input.
        tbl = sentinel_table
        # fmt: off
        table_count_sql = f"SELECT COUNT(*) AS n FROM sys.objects WHERE name='{tbl}' AND type='U'"  # noqa: S608
        # fmt: on
        pre_restore = await sql_exec.execute(restore_sql_target, table_count_sql)
        assert pre_restore.rows[0][0] == 1, "sentinel table should exist before restore"

        # Step 3 — restore in-place to the baseline restore point.
        # restore_in_place internally calls http.poll_operation with default timeout_s=600,
        # which covers the documented ~10-minute LRO SLA.
        await restore.restore_in_place(
            http,
            workspace_id,
            ephemeral_warehouse.id,
            rp.id,
        )

        # Step 4 — assert rollback: the sentinel table must no longer exist.
        post_restore = await sql_exec.execute(restore_sql_target, table_count_sql)
        assert post_restore.rows[0][0] == 0, (
            f"sentinel table '{sentinel_table}' still exists after restore_in_place — "
            "rollback did not revert the schema change"
        )

    finally:
        # Step 5 — clean up the restore point (warehouse itself deleted by ephemeral fixture).
        if rp is not None:
            with contextlib.suppress(Exception):
                await restore.delete_point(http, workspace_id, ephemeral_warehouse.id, rp.id)
