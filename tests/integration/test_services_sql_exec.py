"""Integration tests for services.sql_exec — requires a live Fabric warehouse."""

from __future__ import annotations

import pytest

from fabric_dw.services import sql_exec
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration


async def test_select_1_returns_expected_result(ephemeral_sql_target: SqlTarget) -> None:
    """SELECT 1 AS hello must return columns=['hello'] and rows=[[1]]."""
    result = await sql_exec.execute(ephemeral_sql_target, "SELECT 1 AS hello")
    assert result.columns == ["hello"]
    assert result.rows == [[1]]
