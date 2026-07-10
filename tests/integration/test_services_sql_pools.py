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

from .conftest import SharedWarehouseTarget

# ``maxResourcePercentage`` is a single global budget per workspace (sum ≤ 100),
# and every test here mutates the one shared ``FABRIC_TEST_WORKSPACE_ID`` config.
# ``xdist_group`` pins the whole module onto a single xdist worker so these
# config-mutating tests never run concurrently and cannot push the sum over 100
# (e.g. a 100% default pool from one test coexisting with another's 10% create).
# Requires ``--dist loadgroup`` for xdist to honour the group; the integration
# workflow runs with that dist mode.  The ``_clean_stale_pools`` autouse sweep
# below remains the safety net for pools left behind by an interrupted prior run.
pytestmark = [pytest.mark.integration, pytest.mark.xdist_group("sql_pools")]

# Prefix used to identify pools created by test runs so stale pools can be
# cleaned up before a new run tries to create pools with the same names or
# would push the sum of maxResourcePercentage over 100.
# The hyphen suffix ensures this cannot match any non-test pool whose name
# merely starts with "pytest" but is not test-owned.
_PYTEST_POOL_PREFIX = "pytest-"


def _reset_config_without_pytest_pools(
    current: SqlPoolsConfiguration,
) -> SqlPoolsConfiguration | None:
    """Build a config that drops every ``pytest-``-prefixed pool, or ``None`` if clean.

    Returns ``None`` when the workspace contains no pytest-owned pools (so the
    caller can skip the PATCH entirely — a clean workspace is a no-op).  When
    pytest pools are present, returns a :class:`SqlPoolsConfiguration` with every
    pytest-prefixed pool removed, adjusted so it satisfies the API's rules:

    * **Default-pool rule** — the API cannot delete a pool marked ``isDefault``
      via a per-pool delete, but a config-level PATCH that simply omits it can.
      If the removed default was a pytest pool and non-pytest custom pools remain,
      the first remaining pool is re-pointed as the default so exactly one default
      survives.
    * **Enabled-vs-empty rule** — the API refuses ``customSQLPoolsEnabled=True``
      with an empty pool list.  When removing pytest pools leaves no non-pytest
      custom pools, custom pools are disabled so the resulting config is valid.
    * **Budget rule** — removing pools only lowers the ``maxResourcePercentage``
      sum, so the ≤100 budget is preserved by construction.

    The non-pytest pools are returned untouched (including their ``isDefault`` and
    ``maxResourcePercentage`` values) unless a default re-point is required.
    """
    kept = [p for p in current.custom_sql_pools if not p.name.startswith(_PYTEST_POOL_PREFIX)]
    if len(kept) == len(current.custom_sql_pools):
        # No pytest-owned pools — nothing to reset.
        return None

    if not kept:
        # No non-pytest custom pools remain.  The API rejects enabled+empty, so
        # disable custom pools (autonomous workload management takes over).
        return SqlPoolsConfiguration.model_validate(
            {"customSQLPoolsEnabled": False, "customSQLPools": []}
        )

    # Re-point the default if none of the kept pools is currently the default
    # (e.g. the removed default was a pytest pool).  Exactly one default must
    # remain; mark the first kept pool and clear the flag on the rest.
    if not any(p.is_default for p in kept):
        kept = [p.model_copy(update={"is_default": i == 0}) for i, p in enumerate(kept)]

    return SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": current.custom_sql_pools_enabled,
            "customSQLPools": [p.model_dump(by_alias=True, mode="json") for p in kept],
        }
    )


async def _remove_stale_pytest_pools(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> None:
    """Reset the workspace config so no leftover pytest-created SQL pool remains.

    Pools whose name starts with ``_PYTEST_POOL_PREFIX`` are considered
    test-owned.  They may be left behind when a previous run is interrupted
    before teardown completes (e.g. a transient connection drop mid-finally).
    Calling this helper before creating new pools prevents the sum of
    ``maxResourcePercentage`` across all pools from exceeding 100.

    Unlike a per-pool ``delete_pool`` sweep, this resets at the **config level**:
    it builds a new :class:`SqlPoolsConfiguration` that excludes every pytest pool
    and PATCHes it in a single ``update_configuration`` call.  A per-pool delete
    cannot remove a pool marked ``isDefault=True`` (the API forbids it), so an
    orphaned 100% default pytest pool would survive every sweep and push the next
    create over the 100 budget; the config-level PATCH omits it outright and
    re-points / disables defaults as needed (see
    :func:`_reset_config_without_pytest_pools`).

    The operation is best-effort: the PATCH is suppressed on failure so that a
    transient cleanup error does not mask the original test failure.  If no
    pytest pools exist, this is a no-op (one GET, zero PATCHes).
    """
    current = await sql_pools.get_configuration(http, workspace_id)
    reset = _reset_config_without_pytest_pools(current)
    if reset is None:
        return
    with contextlib.suppress(Exception):
        await sql_pools.update_configuration(http, workspace_id, reset)


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _clean_stale_pools(
    http: FabricHttpClient,
    workspace_id: UUID,
    shared_warehouse: SharedWarehouseTarget,  # noqa: ARG001
) -> AsyncGenerator[None, None]:
    """Autouse fixture: sweep stale pytest-prefixed pools before every test.

    Runs before every sql-pools test in this module so that any test pool
    left behind by an interrupted prior run is removed before the workspace
    configuration is read or modified.  This protects every test regardless
    of run order.

    The ``shared_warehouse`` parameter is requested for its side effect only:
    the sql-pools configuration endpoint (GET .../sqlPoolsConfiguration) returns
    HTTP 500 when the workspace contains no warehouse or SQL analytics endpoint.
    Declaring this session-scoped fixture as a dependency guarantees that at
    least one warehouse exists in the workspace before ``get_configuration`` is
    called here or in any test in this module.
    See: https://learn.microsoft.com/fabric/data-warehouse/custom-sql-pools#limitations?WT.mc_id=MVP_310840
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
        # The seed pool is ``isDefault=True`` at 100%, which a per-pool
        # ``delete_pool`` cannot remove (the API forbids deleting the default).
        # Reset at the config level first so an orphaned 100% default pool can
        # never survive an interrupted teardown and push a later run over the
        # 100 budget; only then restore the original configuration.  Both steps
        # are best-effort so a transient failure does not mask a test failure.
        with contextlib.suppress(Exception):
            await _remove_stale_pytest_pools(http, workspace_id)
        with contextlib.suppress(Exception):
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
        # Reset at the config level first so no pytest pool can accumulate in the
        # workspace if the restore call is interrupted, then restore the original
        # configuration.  Both steps are best-effort so a transient cleanup
        # failure does not mask a test failure.
        with contextlib.suppress(Exception):
            await _remove_stale_pytest_pools(http, workspace_id)
        with contextlib.suppress(Exception):
            await sql_pools.update_configuration(http, workspace_id, original)
