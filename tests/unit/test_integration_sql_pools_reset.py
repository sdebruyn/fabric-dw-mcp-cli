"""Tests for the config-level pytest-pool reset helper in test_services_sql_pools.

``_reset_config_without_pytest_pools`` is a pure function (no live Fabric call):
it takes a fetched :class:`SqlPoolsConfiguration` and returns the configuration to
PATCH back so that every ``pytest-``-prefixed pool is removed while honouring the
API's default-pool and enabled-vs-empty rules.  These tests exercise every branch
with synthetic configurations — no admin credentials or network access required.
"""

from __future__ import annotations

from fabric_dw.models import SqlPoolsConfiguration
from tests.integration.test_services_sql_pools import _reset_config_without_pytest_pools


def _config(pools: list[dict[str, object]], *, enabled: bool) -> SqlPoolsConfiguration:
    return SqlPoolsConfiguration.model_validate(
        {"customSQLPoolsEnabled": enabled, "customSQLPools": pools}
    )


def test_clean_workspace_is_a_noop() -> None:
    """A config with no pytest pools returns None so the caller skips the PATCH."""
    config = _config(
        [{"name": "prod", "isDefault": True, "maxResourcePercentage": 50}],
        enabled=True,
    )
    assert _reset_config_without_pytest_pools(config) is None


def test_empty_workspace_is_a_noop() -> None:
    """An empty pool list (autonomous mode) has nothing to reset."""
    assert _reset_config_without_pytest_pools(_config([], enabled=False)) is None


def test_orphaned_default_pytest_pool_is_removed_by_disabling() -> None:
    """An orphaned isDefault=True pytest pool is the only pool → disable custom pools.

    This is the exact orphan state from the bug: a 100% ``isDefault=True``
    ``pytest-`` pool left behind that a per-pool delete cannot remove.  With no
    non-pytest custom pools remaining, the reset disables custom pools (the API
    rejects ``enabled=True`` with an empty list).
    """
    config = _config(
        [{"name": "pytest-roundtrip-pool", "isDefault": True, "maxResourcePercentage": 100}],
        enabled=True,
    )
    reset = _reset_config_without_pytest_pools(config)
    assert reset is not None
    assert reset.custom_sql_pools_enabled is False
    assert reset.custom_sql_pools == []
    # The result must be a valid PATCH body.
    reset.validate_for_patch()


def test_default_repointed_when_removed_default_was_pytest() -> None:
    """Removing the pytest default re-points the default onto a kept pool."""
    config = _config(
        [
            {"name": "pytest-roundtrip-pool", "isDefault": True, "maxResourcePercentage": 60},
            {"name": "prod-a", "isDefault": False, "maxResourcePercentage": 20},
            {"name": "prod-b", "isDefault": False, "maxResourcePercentage": 20},
        ],
        enabled=True,
    )
    reset = _reset_config_without_pytest_pools(config)
    assert reset is not None
    assert reset.custom_sql_pools_enabled is True
    kept_names = {p.name for p in reset.custom_sql_pools}
    assert kept_names == {"prod-a", "prod-b"}
    defaults = [p.name for p in reset.custom_sql_pools if p.is_default]
    assert defaults == ["prod-a"]  # first kept pool is re-pointed as default
    reset.validate_for_patch()


def test_existing_default_is_preserved_when_pytest_pool_was_not_default() -> None:
    """A non-default pytest pool is dropped; the existing default stays untouched."""
    config = _config(
        [
            {"name": "prod", "isDefault": True, "maxResourcePercentage": 50},
            {"name": "pytest-integration-pool", "isDefault": False, "maxResourcePercentage": 10},
        ],
        enabled=True,
    )
    reset = _reset_config_without_pytest_pools(config)
    assert reset is not None
    assert [p.name for p in reset.custom_sql_pools] == ["prod"]
    assert reset.custom_sql_pools[0].is_default is True
    reset.validate_for_patch()
