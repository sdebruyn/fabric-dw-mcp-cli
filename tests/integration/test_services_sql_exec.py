"""Integration tests for services.sql_exec — requires a live Fabric warehouse.

Fixture note: uses ``shared_warehouse`` from conftest.  The SELECT 1 probe is
read-only and safe to run on the shared warm warehouse without any schema isolation.
"""

from __future__ import annotations

import pytest

from fabric_dw.services import sql_exec
from fabric_dw.sql import SqlTarget

from .conftest import SharedWarehouseTarget

pytestmark = pytest.mark.integration


async def test_select_1_returns_expected_result(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    """SELECT 1 AS hello must return columns=['hello'] and rows=[[1]]."""
    sql_target: SqlTarget = shared_warehouse.sql_target
    result = await sql_exec.execute(sql_target, "SELECT 1 AS hello")
    assert result.columns == ["hello"]
    assert result.rows == [[1]]
