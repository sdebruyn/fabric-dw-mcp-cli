from uuid import UUID

import pytest

from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import Warehouse
from fabric_dw.services import audit

pytestmark = pytest.mark.integration


async def test_get_settings_on_fresh_warehouse(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    settings = await audit.get_settings(http, workspace_id, ephemeral_warehouse.id)
    assert settings.state in {"Enabled", "Disabled"}


async def test_enable_then_disable_roundtrip(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    enabled = await audit.enable(http, workspace_id, ephemeral_warehouse.id, retention_days=7)
    assert enabled.state == "Enabled"
    assert enabled.retention_days == 7

    disabled = await audit.disable(http, workspace_id, ephemeral_warehouse.id)
    assert disabled.state == "Disabled"


async def test_set_action_groups(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    # Re-enable so set_action_groups has something to act on
    await audit.enable(http, workspace_id, ephemeral_warehouse.id)
    updated = await audit.set_action_groups(
        http, workspace_id, ephemeral_warehouse.id, ["BATCH_COMPLETED_GROUP"]
    )
    assert "BATCH_COMPLETED_GROUP" in updated.action_groups


async def test_set_action_groups_rejects_lowercase(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    with pytest.raises(ValueError, match="bad_group"):
        await audit.set_action_groups(http, workspace_id, ephemeral_warehouse.id, ["bad_group"])


async def test_enable_then_set_retention(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    # Ensure audit is enabled first
    await audit.enable(http, workspace_id, ephemeral_warehouse.id, retention_days=7)
    # Update retention without changing state
    updated = await audit.set_retention(http, workspace_id, ephemeral_warehouse.id, days=14)
    assert updated.state == "Enabled"
    assert updated.retention_days == 14


async def test_set_retention_rejects_disabled_audit(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    await audit.disable(http, workspace_id, ephemeral_warehouse.id)
    with pytest.raises(ValueError, match="disabled"):
        await audit.set_retention(http, workspace_id, ephemeral_warehouse.id, days=30)


async def test_add_action_group_appears_in_settings(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    # Ensure auditing is enabled so add_action_group is permitted.
    await audit.enable(http, workspace_id, ephemeral_warehouse.id)
    # Start from a known baseline: one group only.
    await audit.set_action_groups(
        http,
        workspace_id,
        ephemeral_warehouse.id,
        ["BATCH_COMPLETED_GROUP"],
        ensure_enabled=True,
    )

    updated = await audit.add_action_group(
        http, workspace_id, ephemeral_warehouse.id, "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    )

    assert "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP" in updated.action_groups
    assert "BATCH_COMPLETED_GROUP" in updated.action_groups


async def test_add_action_group_is_idempotent(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    await audit.enable(http, workspace_id, ephemeral_warehouse.id)
    await audit.set_action_groups(
        http,
        workspace_id,
        ephemeral_warehouse.id,
        ["BATCH_COMPLETED_GROUP"],
        ensure_enabled=True,
    )

    first = await audit.add_action_group(
        http, workspace_id, ephemeral_warehouse.id, "BATCH_COMPLETED_GROUP"
    )
    second = await audit.add_action_group(
        http, workspace_id, ephemeral_warehouse.id, "BATCH_COMPLETED_GROUP"
    )

    assert first.action_groups == second.action_groups
    assert first.action_groups.count("BATCH_COMPLETED_GROUP") == 1


async def test_add_action_group_rejects_disabled_audit(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    await audit.disable(http, workspace_id, ephemeral_warehouse.id)
    with pytest.raises(ValueError, match="disabled"):
        await audit.add_action_group(
            http, workspace_id, ephemeral_warehouse.id, "BATCH_COMPLETED_GROUP"
        )


async def test_add_action_group_rejects_invalid_name(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    with pytest.raises(ValueError, match="bad_group"):
        await audit.add_action_group(http, workspace_id, ephemeral_warehouse.id, "bad_group")


async def test_remove_action_group_no_longer_in_settings(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    await audit.enable(http, workspace_id, ephemeral_warehouse.id)
    await audit.set_action_groups(
        http,
        workspace_id,
        ephemeral_warehouse.id,
        ["BATCH_COMPLETED_GROUP", "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"],
        ensure_enabled=True,
    )

    updated = await audit.remove_action_group(
        http, workspace_id, ephemeral_warehouse.id, "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    )

    assert "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP" not in updated.action_groups
    # Other groups must be preserved.
    assert "BATCH_COMPLETED_GROUP" in updated.action_groups


async def test_remove_action_group_is_idempotent(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    await audit.enable(http, workspace_id, ephemeral_warehouse.id)
    await audit.set_action_groups(
        http,
        workspace_id,
        ephemeral_warehouse.id,
        ["BATCH_COMPLETED_GROUP"],
        ensure_enabled=True,
    )

    first = await audit.remove_action_group(
        http, workspace_id, ephemeral_warehouse.id, "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    )
    second = await audit.remove_action_group(
        http, workspace_id, ephemeral_warehouse.id, "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    )

    assert first.action_groups == second.action_groups


async def test_add_then_remove_action_group_roundtrip(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    """End-to-end: add a group then remove it — net effect is no change."""
    await audit.enable(http, workspace_id, ephemeral_warehouse.id)
    baseline = await audit.set_action_groups(
        http,
        workspace_id,
        ephemeral_warehouse.id,
        ["BATCH_COMPLETED_GROUP"],
        ensure_enabled=True,
    )

    after_add = await audit.add_action_group(
        http, workspace_id, ephemeral_warehouse.id, "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    )
    assert "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP" in after_add.action_groups

    after_remove = await audit.remove_action_group(
        http, workspace_id, ephemeral_warehouse.id, "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    )
    # Fabric auto-manages a default authentication audit group that may appear in
    # GET-derived state (from add/remove_action_group) but not in the authoritative
    # state returned by set_action_groups.  Strict list equality between baseline
    # (set_action_groups) and after_remove (GET-derived) is therefore an invalid
    # assumption.  Instead assert the meaningful invariant: the removed group is
    # gone, and every group that was in baseline is still present.
    assert "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP" not in after_remove.action_groups
    for group in baseline.action_groups:
        assert group in after_remove.action_groups, (
            f"baseline group {group!r} missing from after_remove; got {after_remove.action_groups}"
        )


async def test_remove_action_group_rejects_disabled_audit(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    await audit.disable(http, workspace_id, ephemeral_warehouse.id)
    with pytest.raises(ValueError, match="disabled"):
        await audit.remove_action_group(
            http, workspace_id, ephemeral_warehouse.id, "BATCH_COMPLETED_GROUP"
        )


async def test_remove_action_group_rejects_invalid_name(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    with pytest.raises(ValueError, match="bad_group"):
        await audit.remove_action_group(http, workspace_id, ephemeral_warehouse.id, "bad_group")
