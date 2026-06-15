"""Integration tests for services.query_insights — hits real Fabric APIs.

Fixture note: uses ``shared_warehouse`` from conftest.  Query-insights views
accumulate history over the lifetime of the shared warehouse, but all assertions
here only check shape (isinstance list) and upper-bound limits (len <= 1 for
limit=1 tests).  These assertions are deliberately robust to an accumulating
query history and thus safe to run against the shared warm warehouse.

If a future test needs to assert specific row counts or particular query text,
it should use ``warehouse_schema`` (or a dedicated warehouse) to get a clean
slate.
"""

from __future__ import annotations

import pytest

from fabric_dw.services import query_insights
from fabric_dw.sql import SqlTarget

from .conftest import SharedWarehouseTarget

pytestmark = pytest.mark.integration

_SKIP_REASON = (
    "queryinsights views not available on this warehouse "
    "(schema may not be initialised on a fresh/ephemeral warehouse, "
    "or the service principal lacks the required permissions)"
)

_SKIP_FRAGMENTS = (
    # View doesn't exist yet (no query history on a fresh warehouse)
    ("invalid object name", "queryinsights"),
    # Driver-level auth/permission failure accessing the queryinsights schema
    ("authentication was successful, but the database was not found",),
    ("insufficient permissions to connect",),
    ("invalid authorization specification",),
)


def _is_queryinsights_unavailable(exc: BaseException) -> bool:
    """Return True when the error indicates queryinsights is inaccessible on this warehouse."""
    msg = str(exc).lower()
    return any(all(frag in msg for frag in fragments) for fragments in _SKIP_FRAGMENTS)


async def test_list_request_history_returns_a_list(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    sql_target: SqlTarget = shared_warehouse.sql_target
    try:
        result = await query_insights.list_request_history(sql_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_session_history_returns_a_list(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    sql_target: SqlTarget = shared_warehouse.sql_target
    try:
        result = await query_insights.list_session_history(sql_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_frequent_queries_returns_a_list(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    sql_target: SqlTarget = shared_warehouse.sql_target
    try:
        result = await query_insights.list_frequent_queries(sql_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_long_running_queries_returns_a_list(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    sql_target: SqlTarget = shared_warehouse.sql_target
    try:
        result = await query_insights.list_long_running_queries(sql_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_sql_pool_insights_returns_a_list(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    sql_target: SqlTarget = shared_warehouse.sql_target
    try:
        result = await query_insights.list_sql_pool_insights(sql_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_request_history_respects_limit(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    sql_target: SqlTarget = shared_warehouse.sql_target
    try:
        result = await query_insights.list_request_history(sql_target, limit=1)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert len(result) <= 1


async def test_list_frequent_queries_respects_limit(
    shared_warehouse: SharedWarehouseTarget,
) -> None:
    sql_target: SqlTarget = shared_warehouse.sql_target
    try:
        result = await query_insights.list_frequent_queries(sql_target, limit=1)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert len(result) <= 1
