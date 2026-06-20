"""Smoke test for the ``shared_sql_endpoint`` fixture and lakehouse seed data.

Verifies that the session-scoped ``shared_sql_endpoint`` fixture:
- provisions a SQL analytics endpoint backed by a schema-enabled Lakehouse,
- seeds ``sample.colors`` and ``sample.numbers`` by writing minimal Delta Lake
  layouts (Parquet data file + ``_delta_log/`` commit) directly to
  ``Tables/sample/<table>/`` via the OneLake ADLS Gen2 DFS API,
- refreshes metadata on the endpoint,
- and yields a :class:`~fabric_dw.sql.SqlTarget` on which the seeded tables are
  immediately queryable via TDS.

This is the end-to-end smoke for fixture infrastructure (PR 1 of 5).  It does
not test any service function — it only asserts that the fixture wiring works
and that the seeded rows are visible from the endpoint.
"""

from __future__ import annotations

import asyncio

import pytest

from fabric_dw.sql import SqlTarget, run_query

from .conftest import SharedSqlEndpointTarget

pytestmark = [pytest.mark.integration, pytest.mark.sql_endpoint]


def _select_colors(target: SqlTarget) -> list[tuple[object, ...]]:
    """Run ``SELECT * FROM [sample].[colors] ORDER BY id`` and return all rows."""
    _cols, rows = run_query(
        target,
        "SELECT * FROM [sample].[colors] ORDER BY id",
        fetch="all",
    )
    return rows or []


def _select_numbers(target: SqlTarget) -> list[tuple[object, ...]]:
    """Run ``SELECT * FROM [sample].[numbers] ORDER BY id`` and return all rows."""
    _cols, rows = run_query(
        target,
        "SELECT * FROM [sample].[numbers] ORDER BY id",
        fetch="all",
    )
    return rows or []


async def test_shared_sql_endpoint_seed_colors_visible(
    shared_sql_endpoint: SharedSqlEndpointTarget,
) -> None:
    """sample.colors must contain exactly 3 seeded rows on the SQL analytics endpoint."""
    sql_target = shared_sql_endpoint.sql_target

    rows = await asyncio.to_thread(_select_colors, sql_target)

    assert len(rows) == 3, f"expected 3 rows in sample.colors, got {len(rows)}: {rows!r}"
    # Verify row content — ids 1/2/3 with colour names.
    ids = {row[0] for row in rows}
    assert ids == {1, 2, 3}, f"unexpected id set in sample.colors: {ids!r}"
    names = {row[1] for row in rows}
    assert names == {"red", "green", "blue"}, f"unexpected name set in sample.colors: {names!r}"


async def test_shared_sql_endpoint_seed_numbers_visible(
    shared_sql_endpoint: SharedSqlEndpointTarget,
) -> None:
    """sample.numbers must contain exactly 3 seeded rows on the SQL analytics endpoint."""
    sql_target = shared_sql_endpoint.sql_target

    rows = await asyncio.to_thread(_select_numbers, sql_target)

    assert len(rows) == 3, f"expected 3 rows in sample.numbers, got {len(rows)}: {rows!r}"
    ids = {row[0] for row in rows}
    assert ids == {1, 2, 3}, f"unexpected id set in sample.numbers: {ids!r}"
    values = {row[1] for row in rows}
    assert values == {10, 20, 30}, f"unexpected value set in sample.numbers: {values!r}"
