"""Integration tests for services.settings — requires real Fabric credentials.

Fixture note: uses ``read_target`` from conftest for the read leg
(``get_settings`` is dual-target) and ``shared_warehouse`` for the DWH-only
write leg (``set_result_set_caching`` / ``set_time_travel_retention`` /
``set_data_lake_log_publishing``).

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

from .conftest import SharedSqlEndpointTarget, SharedWarehouseTarget

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
    # data_lake_log_publishing is always a bool; NULL from the driver (SQL
    # Analytics Endpoints) is mapped to False by _row_to_settings.
    assert isinstance(result.data_lake_log_publishing, bool)


@pytest.mark.sql_endpoint
async def test_get_settings_sql_endpoint_dllp_is_false(
    shared_sql_endpoint: SharedSqlEndpointTarget,
) -> None:
    """get_settings on a SQL Analytics Endpoint must return data_lake_log_publishing=False.

    SQL Analytics Endpoints return NULL for ``data_lake_log_publishing_desc``
    in ``sys.databases`` (warehouse-only column).  _row_to_settings maps NULL
    to False; this test verifies that mapping holds end-to-end with a live
    endpoint connection.
    """
    result = await settings.get_settings(shared_sql_endpoint.sql_target)
    assert isinstance(result, WarehouseSettings)
    assert result.data_lake_log_publishing is False


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
