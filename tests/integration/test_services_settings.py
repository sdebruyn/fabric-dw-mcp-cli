"""Integration tests for services.settings — requires real Fabric credentials.

Fixture note: uses ``read_target`` from conftest for the read leg
(``get_settings`` is dual-target) and ``shared_warehouse`` for the DWH-only
write leg (``set_result_set_caching`` / ``set_time_travel_retention``).

The ``read_target`` fixture is parametrized over two targets:
  - ``[warehouse]``     — Data Warehouse (always runs)
  - ``[sql_endpoint]``  — SQL Analytics Endpoint (``pytest.mark.sql_endpoint``, CI only)

Write tests use ``shared_warehouse`` directly (not ``warehouse_schema``) because
``ALTER DATABASE CURRENT`` targets the whole database, not a schema, and the
shared warehouse is the correct scope.
"""

from __future__ import annotations

import pytest

from fabric_dw.models import WarehouseSettings
from fabric_dw.services import settings
from fabric_dw.sql import SqlTarget

from .conftest import SharedWarehouseTarget

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Dual-target read tests — get_settings via read_target
# ---------------------------------------------------------------------------


async def test_get_settings_returns_warehouse_settings(
    read_target: SqlTarget,
) -> None:
    """get_settings must succeed on both a warehouse and a SQL Analytics Endpoint.

    The ``sys.databases`` query is dual-target; both item types return a row.
    The returned :class:`~fabric_dw.models.WarehouseSettings` must be a valid
    model instance.
    """
    result = await settings.get_settings(read_target)
    assert isinstance(result, WarehouseSettings)
    # database name is always populated from sys.databases.name
    assert isinstance(result.database, str)
    assert result.database != ""
    # result_set_caching is a boolean flag (True or False)
    assert isinstance(result.result_set_caching, bool)
    # time_travel_retention_days may be None on SQL Analytics Endpoints
    # (the column can be NULL for endpoints); validate the type when present
    if result.time_travel_retention_days is not None:
        assert isinstance(result.time_travel_retention_days, int)
        assert result.time_travel_retention_days >= 0


# ---------------------------------------------------------------------------
# DWH-only write tests — set_result_set_caching / set_time_travel_retention
# ---------------------------------------------------------------------------


async def test_set_result_set_caching_roundtrip(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    """set_result_set_caching enable then disable returns the correct state each time."""
    target = shared_warehouse.sql_target

    after_enable = await settings.set_result_set_caching(target, enabled=True)
    assert isinstance(after_enable, WarehouseSettings)
    assert after_enable.result_set_caching is True

    after_disable = await settings.set_result_set_caching(target, enabled=False)
    assert isinstance(after_disable, WarehouseSettings)
    assert after_disable.result_set_caching is False
