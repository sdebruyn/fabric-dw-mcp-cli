"""Unit tests for services._lro — focused on extract_operation_id and resolve_lro_item_id."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fabric_dw.services._lro import (
    LRO_DETAIL_WAIT_S,
    LRO_MAX_DETAIL_RETRIES,
    extract_operation_id,
    resolve_lro_item_id,
)
from tests.unit.services._helpers import _make_client

_OP_ID = "abc-def-123"
_LOCATION = f"https://api.fabric.microsoft.com/v1/operations/{_OP_ID}"


# ---------------------------------------------------------------------------
# named constants
# ---------------------------------------------------------------------------


def test_lro_max_detail_retries_is_positive_int() -> None:
    """LRO_MAX_DETAIL_RETRIES should be a positive integer."""
    assert isinstance(LRO_MAX_DETAIL_RETRIES, int)
    assert LRO_MAX_DETAIL_RETRIES > 0


def test_lro_detail_wait_s_is_positive_float() -> None:
    """LRO_DETAIL_WAIT_S should be a positive float."""
    assert isinstance(LRO_DETAIL_WAIT_S, float)
    assert LRO_DETAIL_WAIT_S > 0


# ---------------------------------------------------------------------------
# extract_operation_id
# ---------------------------------------------------------------------------


def test_extract_operation_id_parses_last_segment() -> None:
    """extract_operation_id should return the last path segment of the URL."""
    assert extract_operation_id(_LOCATION) == _OP_ID


def test_extract_operation_id_simple_path() -> None:
    """extract_operation_id works for simple path segments."""
    assert extract_operation_id("https://example.com/ops/my-op-id") == "my-op-id"


# ---------------------------------------------------------------------------
# resolve_lro_item_id — Path A (status body)
# ---------------------------------------------------------------------------


async def test_resolve_lro_item_id_path_a_resourceid() -> None:
    """resolve_lro_item_id returns the id from resourceId in the status body (Path A)."""
    client = await _make_client()
    async with client:
        result = await resolve_lro_item_id(
            client,
            operation_result={"status": "Succeeded", "resourceId": "res-123"},
            location=_LOCATION,
        )
    assert result == "res-123"


async def test_resolve_lro_item_id_path_a_id() -> None:
    """resolve_lro_item_id returns the id from 'id' in the status body (Path A fallback)."""
    client = await _make_client()
    async with client:
        result = await resolve_lro_item_id(
            client,
            operation_result={"status": "Succeeded", "id": "item-456"},
            location=_LOCATION,
        )
    assert result == "item-456"


async def test_resolve_lro_item_id_path_a_custom_keys() -> None:
    """resolve_lro_item_id checks custom result_id_keys in order."""
    client = await _make_client()
    async with client:
        result = await resolve_lro_item_id(
            client,
            operation_result={"createdItemId": "snap-789"},
            location=_LOCATION,
            result_id_keys=("resourceId", "createdItemId", "itemId", "id"),
        )
    assert result == "snap-789"


# ---------------------------------------------------------------------------
# resolve_lro_item_id — Path B (/result endpoint)
# ---------------------------------------------------------------------------


async def test_resolve_lro_item_id_path_b_result_endpoint() -> None:
    """resolve_lro_item_id falls back to /result endpoint when status body has no id."""
    client = await _make_client()
    async with client:
        with patch.object(client, "get_operation_result", new_callable=AsyncMock) as mock_result:
            mock_result.return_value = {"id": "result-id-from-endpoint"}
            result = await resolve_lro_item_id(
                client,
                operation_result={"status": "Succeeded"},  # no id in status body
                location=_LOCATION,
            )

    assert result == "result-id-from-endpoint"
    mock_result.assert_awaited_once_with(_OP_ID)


async def test_resolve_lro_item_id_returns_none_when_both_paths_fail() -> None:
    """resolve_lro_item_id returns None when neither Path A nor Path B yields an id."""
    client = await _make_client()
    async with client:
        with patch.object(client, "get_operation_result", new_callable=AsyncMock) as mock_result:
            mock_result.return_value = {}  # no id in /result either
            result = await resolve_lro_item_id(
                client,
                operation_result={"status": "Succeeded"},
                location=_LOCATION,
            )

    assert result is None


async def test_resolve_lro_item_id_path_a_takes_priority_over_path_b() -> None:
    """Path A (status body) should be checked before Path B (/result endpoint)."""
    client = await _make_client()
    async with client:
        with patch.object(client, "get_operation_result", new_callable=AsyncMock) as mock_result:
            mock_result.return_value = {"id": "path-b-id"}
            result = await resolve_lro_item_id(
                client,
                operation_result={"resourceId": "path-a-id"},
                location=_LOCATION,
            )

    assert result == "path-a-id"
    # Path B should NOT have been called
    mock_result.assert_not_awaited()
