"""Integration tests for services.sql_pools.

Requires workspace admin rights on FABRIC_TEST_WORKSPACE_ID.
Run only in environments where admin credentials are available.
"""

import contextlib
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
import pytest_asyncio

from fabric_dw.exceptions import AlreadyExistsError, NotFoundError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import SqlPool, SqlPoolsConfiguration
from fabric_dw.services import sql_pools

pytestmark = pytest.mark.integration

# Prefix used to identify pools created by test runs so stale pools can be
# cleaned up before a new run tries to create pools with the same names or
# would push the sum of maxResourcePercentage over 100.
# The hyphen suffix ensures this cannot match any non-test pool whose name
# merely starts with "pytest" but is not test-owned.
_PYTEST_POOL_PREFIX = "pytest-"


async def _remove_stale_pytest_pools(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> None:
    """Remove any leftover pytest-created custom SQL pools from the workspace.

    Pools whose name starts with ``_PYTEST_POOL_PREFIX`` are considered
    test-owned.  They may be left behind when a previous run is interrupted
    before teardown completes (e.g. a transient connection drop mid-finally).
    Calling this helper before creating new pools prevents the sum of
    ``maxResourcePercentage`` across all pools from exceeding 100.

    The operation is best-effort: individual delete failures are suppressed so
    that a partial cleanup does not mask the original test failure.  If no
    stale pools exist, this is a no-op (one GET, zero PATCHes).
    """
    current = await sql_pools.get_configuration(http, workspace_id)
    stale = [p for p in current.custom_sql_pools if p.name.startswith(_PYTEST_POOL_PREFIX)]
    for pool in stale:
        with contextlib.suppress(Exception):
            await sql_pools.delete_pool(http, workspace_id, pool.name)


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _clean_stale_pools(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> AsyncGenerator[None, None]:
    """Autouse fixture: sweep stale pytest-prefixed pools before every test.

    Runs before every sql-pools test in this module so that any test pool
    left behind by an interrupted prior run is removed before the workspace
    configuration is read or modified.  This protects every test regardless
    of run order.
    """
    await _remove_stale_pytest_pools(http, workspace_id)
    yield


async def test_get_configuration_returns_model(http: FabricHttpClient, workspace_id: UUID) -> None:
    config = await sql_pools.get_configuration(http, workspace_id)
    assert isinstance(config, SqlPoolsConfiguration)
    assert isinstance(config.custom_sql_pools_enabled, bool)
    assert isinstance(config.custom_sql_pools, list)


async def test_enable_disable_roundtrip(http: FabricHttpClient, workspace_id: UUID) -> None:
    original = await sql_pools.get_configuration(http, workspace_id)

    pool_name = "pytest-roundtrip-pool"

    # The API refuses to set customSQLPoolsEnabled=True when customSQLPools is
    # empty.  Seed at least one pool so the enable call can succeed.
    seed_config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {
                    "name": pool_name,
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
        # Best-effort targeted delete before the full config restore so the
        # pool does not accumulate in the workspace if the restore call raises.
        with contextlib.suppress(Exception):
            await sql_pools.delete_pool(http, workspace_id, pool_name)
        await sql_pools.update_configuration(http, workspace_id, original)


async def test_create_update_delete_roundtrip(http: FabricHttpClient, workspace_id: UUID) -> None:
    """Integration test: create → update → delete roundtrip for a single pool.

    Stale ``pytest-``-prefixed pools left by interrupted prior runs are
    removed by the ``_clean_stale_pools`` autouse fixture that runs before
    this test, ensuring the workspace starts clean and the sum of
    ``maxResourcePercentage`` does not exceed 100.
    """
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

        # Duplicate name must raise AlreadyExistsError
        with pytest.raises(AlreadyExistsError):
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

        # Missing name must raise NotFoundError
        with pytest.raises(NotFoundError):
            await sql_pools.delete_pool(http, workspace_id, pool_name)

    finally:
        # Best-effort targeted delete before the full config restore so the
        # pool does not accumulate in the workspace if the restore call raises.
        with contextlib.suppress(Exception):
            await sql_pools.delete_pool(http, workspace_id, pool_name)
        await sql_pools.update_configuration(http, workspace_id, original)
