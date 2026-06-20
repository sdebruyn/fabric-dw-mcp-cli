"""Integration tests for services.query_insights — hits real Fabric APIs.

Fixture note: uses ``read_target`` from conftest.  Query-insights views
accumulate history over the lifetime of the shared warehouse/endpoint, but all
assertions here only check shape (isinstance list) and upper-bound limits
(len <= 1 for limit=1 tests).  These assertions are deliberately robust to an
accumulating query history and thus safe to run against either target.

If a future test needs to assert specific row counts or particular query text,
it should use ``warehouse_schema`` (or a dedicated warehouse) to get a clean
slate.

The ``read_target`` fixture is parametrized over two targets:
  - ``[warehouse]``     — Data Warehouse (always runs)
  - ``[sql_endpoint]``  — SQL Analytics Endpoint (``pytest.mark.sql_endpoint``, CI only)

On fresh warehouses or SQL analytics endpoints, the queryinsights views may not
yet be populated or may not be accessible (the service principal may lack
permissions to the queryinsights schema).  Each test catches those variants and
issues ``pytest.skip`` — this is not a test failure.
"""

from __future__ import annotations

import pytest

from fabric_dw.services import query_insights
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration

_SKIP_REASON = (
    "queryinsights views not available on this target "
    "(schema may not be initialised on a fresh/ephemeral warehouse or endpoint, "
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
    """Return True when the error indicates queryinsights is inaccessible on this target."""
    msg = str(exc).lower()
    return any(all(frag in msg for frag in fragments) for fragments in _SKIP_FRAGMENTS)


async def test_list_request_history_returns_a_list(
    read_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_request_history(read_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_session_history_returns_a_list(
    read_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_session_history(read_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_frequent_queries_returns_a_list(
    read_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_frequent_queries(read_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_long_running_queries_returns_a_list(
    read_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_long_running_queries(read_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_sql_pool_insights_returns_a_list(
    read_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_sql_pool_insights(read_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_request_history_respects_limit(
    read_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_request_history(read_target, limit=1)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert len(result) <= 1


async def test_list_frequent_queries_respects_limit(
    read_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_frequent_queries(read_target, limit=1)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert len(result) <= 1
