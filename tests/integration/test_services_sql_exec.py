"""Integration tests for services.sql_exec — requires a live Fabric warehouse.

Fixture note: the SELECT 1 probe is read-only and runs against both the shared
warm warehouse and the shared SQL analytics endpoint via ``read_target``.
"""

from __future__ import annotations

import pytest

from fabric_dw.services import sql_exec
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration


async def test_select_1_returns_expected_result(
    read_target: SqlTarget,
) -> None:
    """SELECT 1 AS hello must return columns=['hello'] and rows=[[1]]."""
    result = await sql_exec.execute(read_target, "SELECT 1 AS hello")
    assert result.columns == ["hello"]
    assert result.rows == [[1]]
