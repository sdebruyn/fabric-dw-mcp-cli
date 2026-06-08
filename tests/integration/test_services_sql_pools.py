"""Integration tests for services.sql_pools.

Requires workspace admin rights on FABRIC_TEST_WORKSPACE_ID.
Run only in environments where admin credentials are available.
"""

import asyncio
import time
from uuid import UUID

import pytest

from fabric_dw.exceptions import AlreadyExists, NotFound
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import SqlPool, SqlPoolsConfiguration
from fabric_dw.services import sql_pools

pytestmark = pytest.mark.integration


async def test_get_configuration_returns_model(http: FabricHttpClient, workspace_id: UUID) -> None:
    config = await sql_pools.get_configuration(http, workspace_id)
    assert isinstance(config, SqlPoolsConfiguration)
    assert isinstance(config.custom_sql_pools_enabled, bool)
    assert isinstance(config.custom_sql_pools, list)


async def test_enable_disable_roundtrip(http: FabricHttpClient, workspace_id: UUID) -> None:
    original = await sql_pools.get_configuration(http, workspace_id)

    # The API refuses to set customSQLPoolsEnabled=True when customSQLPools is
    # empty.  Seed at least one pool so the enable call can succeed.
    seed_config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {
                    "name": "pytest-roundtrip-pool",
                    "isDefault": True,
                    "maxResourcePercentage": 100,
                    "optimizeForReads": False,
                }
            ],
        }
    )

    try:
        await sql_pools.update_configuration(http, workspace_id, seed_config)

        disabled = await sql_pools.disable(http, workspace_id)
        assert disabled.custom_sql_pools_enabled is False

        enabled = await sql_pools.enable(http, workspace_id)
        assert enabled.custom_sql_pools_enabled is True
    finally:
        await sql_pools.update_configuration(http, workspace_id, original)


async def test_create_update_delete_roundtrip(http: FabricHttpClient, workspace_id: UUID) -> None:
    """Integration test: create → update → delete roundtrip for a single pool."""
    original = await sql_pools.get_configuration(http, workspace_id)

    pool_name = "pytest-integration-pool"

    try:
        # Create
        new_pool = SqlPool.model_validate(
            {
                "name": pool_name,
                "isDefault": False,
                "maxResourcePercentage": 10,
                "optimizeForReads": True,
                "classifier": {
                    "type": "Application Name",
                    "value": ["pytest-app"],
                },
            }
        )
        after_create = await sql_pools.create_pool(http, workspace_id, new_pool)
        created = next((p for p in after_create.custom_sql_pools if p.name == pool_name), None)
        assert created is not None
        assert created.max_resource_percentage == 10
        assert created.optimize_for_reads is True

        # Duplicate name must raise AlreadyExists
        with pytest.raises(AlreadyExists):
            await sql_pools.create_pool(http, workspace_id, new_pool)

        # Update
        after_update = await sql_pools.update_pool(
            http, workspace_id, pool_name, max_resource_percentage=20, optimize_for_reads=False
        )
        updated = next((p for p in after_update.custom_sql_pools if p.name == pool_name), None)
        assert updated is not None
        assert updated.max_resource_percentage == 20
        assert updated.optimize_for_reads is False
        assert updated.classifier is not None
        assert updated.classifier.value == ["pytest-app"]

        # Delete
        after_delete = await sql_pools.delete_pool(http, workspace_id, pool_name)
        assert not any(p.name == pool_name for p in after_delete.custom_sql_pools)

        # Missing name must raise NotFound
        with pytest.raises(NotFound):
            await sql_pools.delete_pool(http, workspace_id, pool_name)

    finally:
        await sql_pools.update_configuration(http, workspace_id, original)


async def test_reset_pools(http: FabricHttpClient, workspace_id: UUID) -> None:
    """reset_pools clears all pools while preserving the enabled flag."""
    original = await sql_pools.get_configuration(http, workspace_id)

    seed_config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {
                    "name": "pytest-reset-pool",
                    "isDefault": True,
                    "maxResourcePercentage": 100,
                    "optimizeForReads": False,
                }
            ],
        }
    )

    try:
        await sql_pools.update_configuration(http, workspace_id, seed_config)

        await sql_pools.reset_pools(http, workspace_id)

        # Beta API has eventual-consistency between PATCH and GET. See issue #205.
        # Poll up to 30 s for the reset to be reflected by the GET endpoint.
        # Fetch once before the loop so cfg is always bound even if the
        # deadline has already elapsed on the first monotonic() check.
        cfg = await sql_pools.get_configuration(http, workspace_id)
        deadline = time.monotonic() + 30.0
        while cfg.custom_sql_pools and time.monotonic() < deadline:
            await asyncio.sleep(2.0)
            cfg = await sql_pools.get_configuration(http, workspace_id)

        if cfg.custom_sql_pools:
            pytest.fail(
                "reset_pools did not clear the configuration within 30s (eventual consistency)"
            )

        assert cfg.custom_sql_pools == []
        assert cfg.custom_sql_pools_enabled is True
    finally:
        await sql_pools.update_configuration(http, workspace_id, original)
