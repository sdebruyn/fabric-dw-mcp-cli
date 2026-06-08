"""Integration tests for services.sql_pools.

Requires workspace admin rights on FABRIC_TEST_WORKSPACE_ID.
Run only in environments where admin credentials are available.
"""

from uuid import UUID

import pytest

from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import SqlPoolsConfiguration
from fabric_dw.services import sql_pools

pytestmark = pytest.mark.integration


async def test_get_configuration_returns_model(http: FabricHttpClient, workspace_id: UUID) -> None:
    config = await sql_pools.get_configuration(http, workspace_id)
    assert isinstance(config, SqlPoolsConfiguration)
    assert isinstance(config.custom_sql_pools_enabled, bool)
    assert isinstance(config.custom_sql_pools, list)


async def test_enable_disable_roundtrip(http: FabricHttpClient, workspace_id: UUID) -> None:
    original = await sql_pools.get_configuration(http, workspace_id)

    try:
        disabled = await sql_pools.disable(http, workspace_id)
        assert disabled.custom_sql_pools_enabled is False

        enabled = await sql_pools.enable(http, workspace_id)
        assert enabled.custom_sql_pools_enabled is True
    finally:
        await sql_pools.update_configuration(http, workspace_id, original)


async def test_update_configuration_roundtrip(http: FabricHttpClient, workspace_id: UUID) -> None:
    original = await sql_pools.get_configuration(http, workspace_id)

    test_config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {
                    "name": "pytest-pool",
                    "isDefault": True,
                    "maxResourcePercentage": 100,
                    "optimizeForReads": False,
                }
            ],
        }
    )

    try:
        result = await sql_pools.update_configuration(http, workspace_id, test_config)
        assert isinstance(result, SqlPoolsConfiguration)
        pool_names = [p.name for p in result.custom_sql_pools]
        assert "pytest-pool" in pool_names
    finally:
        await sql_pools.update_configuration(http, workspace_id, original)
