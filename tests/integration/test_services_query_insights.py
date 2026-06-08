"""Integration tests for services.query_insights — hits real Fabric APIs."""

from __future__ import annotations

import pytest

from fabric_dw.services import query_insights
from fabric_dw.sql import SqlTarget

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
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_request_history(ephemeral_sql_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_session_history_returns_a_list(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_session_history(ephemeral_sql_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_frequent_queries_returns_a_list(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_frequent_queries(ephemeral_sql_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_long_running_queries_returns_a_list(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_long_running_queries(ephemeral_sql_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_sql_pool_insights_returns_a_list(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_sql_pool_insights(ephemeral_sql_target)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_request_history_respects_limit(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_request_history(ephemeral_sql_target, limit=1)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert len(result) <= 1


async def test_list_frequent_queries_respects_limit(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_frequent_queries(ephemeral_sql_target, limit=1)
    except Exception as exc:
        if _is_queryinsights_unavailable(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert len(result) <= 1
