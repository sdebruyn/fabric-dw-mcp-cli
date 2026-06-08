"""Integration tests for services.query_insights — hits real Fabric APIs."""

from __future__ import annotations

import pytest

from fabric_dw.services import query_insights
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration

_SKIP_REASON = (
    "queryinsights views not available on this warehouse "
    "(requires query history; schema may not be initialised on a fresh warehouse)"
)


def _is_missing_view(exc: BaseException) -> bool:
    """Return True when the error indicates the queryinsights view does not exist."""
    return "invalid object name" in str(exc).lower() and "queryinsights" in str(exc).lower()


async def test_list_request_history_returns_a_list(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_request_history(ephemeral_sql_target)
    except Exception as exc:
        if _is_missing_view(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_session_history_returns_a_list(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_session_history(ephemeral_sql_target)
    except Exception as exc:
        if _is_missing_view(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_frequent_queries_returns_a_list(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_frequent_queries(ephemeral_sql_target)
    except Exception as exc:
        if _is_missing_view(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_long_running_queries_returns_a_list(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_long_running_queries(ephemeral_sql_target)
    except Exception as exc:
        if _is_missing_view(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_sql_pool_insights_returns_a_list(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_sql_pool_insights(ephemeral_sql_target)
    except Exception as exc:
        if _is_missing_view(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert isinstance(result, list)


async def test_list_request_history_respects_limit(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_request_history(ephemeral_sql_target, limit=1)
    except Exception as exc:
        if _is_missing_view(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert len(result) <= 1


async def test_list_frequent_queries_respects_limit(
    ephemeral_sql_target: SqlTarget,
) -> None:
    try:
        result = await query_insights.list_frequent_queries(ephemeral_sql_target, limit=1)
    except Exception as exc:
        if _is_missing_view(exc):
            pytest.skip(_SKIP_REASON)
        raise
    assert len(result) <= 1
