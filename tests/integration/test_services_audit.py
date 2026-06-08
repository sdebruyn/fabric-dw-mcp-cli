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
