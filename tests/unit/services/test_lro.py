"""Unit tests for services._lro — focused on extract_operation_id and resolve_lro_item_id."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from fabric_dw.exceptions import FabricServerError, NotFoundError
from fabric_dw.services._lro import (
    LRO_DETAIL_WAIT_S,
    LRO_MAX_DETAIL_RETRIES,
    extract_operation_id,
    resolve_lro_item_id,
)
from tests.unit.services._helpers import _make_client

_OP_UUID = "c1d2e3f4-a5b6-7890-cdef-123456789abc"
_OP_ID = _OP_UUID
_LOCATION = f"https://api.fabric.microsoft.com/v1/operations/{_OP_UUID}"


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
# extract_operation_id — hardened UUID-validating implementation
# ---------------------------------------------------------------------------


def test_extract_operation_id_plain_url() -> None:
    """extract_operation_id must return the UUID from a plain operation URL."""
    assert extract_operation_id(_LOCATION) == _OP_UUID


def test_extract_operation_id_trailing_slash() -> None:
    """extract_operation_id must handle a trailing slash by skipping empty segments."""
    url = f"{_LOCATION}/"
    assert extract_operation_id(url) == _OP_UUID


def test_extract_operation_id_query_string() -> None:
    """extract_operation_id must strip query strings before parsing."""
    url = f"{_LOCATION}?api-version=2023-11-01"
    assert extract_operation_id(url) == _OP_UUID


def test_extract_operation_id_non_uuid_raises() -> None:
    """extract_operation_id must raise FabricServerError when last segment is not a UUID."""
    url = "https://api.fabric.microsoft.com/v1/operations/not-a-uuid"
    with pytest.raises(FabricServerError, match="not a UUID"):
        extract_operation_id(url)


def test_extract_operation_id_empty_path_raises() -> None:
    """extract_operation_id must raise FabricServerError when URL has no path segments."""
    url = "https://api.fabric.microsoft.com"
    with pytest.raises(FabricServerError):
        extract_operation_id(url)


def test_extract_operation_id_result_suffix_raises() -> None:
    """extract_operation_id must fail UUID validation when URL ends in /result.

    A URL like .../operations/{uuid}/result has 'result' as the last path segment,
    which is NOT a UUID.  This documents and guards the parsing behavior so callers
    know to pass the bare operation URL (without /result) to this helper.
    """
    url = f"https://api.fabric.microsoft.com/v1/operations/{_OP_UUID}/result"
    with pytest.raises(FabricServerError, match="not a UUID"):
        extract_operation_id(url)


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


async def test_resolve_lro_item_id_path_a_id_not_in_default_keys() -> None:
    """'id' is NOT in the default result_id_keys for Path A.

    A bare 'id' field in the LRO status body is ambiguous — it may be the
    operation ID, not the resource ID.  The default Path A key set intentionally
    excludes 'id'; callers that need 'id' from the status body must pass it
    explicitly via result_id_keys.  The authoritative 'id' for a created resource
    comes from the /result endpoint (Path B).
    """
    client = await _make_client()
    async with client:
        with patch.object(client, "get_operation_result", new_callable=AsyncMock) as mock_result:
            mock_result.return_value = {}  # Path B also returns nothing
            result = await resolve_lro_item_id(
                client,
                operation_result={"status": "Succeeded", "id": "op-id-456"},
                location=_LOCATION,
                # default result_id_keys — does NOT include "id"
            )
    # "id" in status body is NOT used when not in result_id_keys → falls through to Path B
    # Path B also returns nothing → result is None
    assert result is None


async def test_resolve_lro_item_id_path_a_id_explicit_key() -> None:
    """'id' in the status body IS used when explicitly included in result_id_keys."""
    client = await _make_client()
    async with client:
        result = await resolve_lro_item_id(
            client,
            operation_result={"status": "Succeeded", "id": "explicit-item-456"},
            location=_LOCATION,
            result_id_keys=("resourceId", "createdItemId", "itemId", "id"),
        )
    assert result == "explicit-item-456"


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


# ---------------------------------------------------------------------------
# resolve_lro_item_id — 404 race retry (Path B)
# ---------------------------------------------------------------------------


async def test_resolve_lro_item_id_404_then_success_retries() -> None:
    """resolve_lro_item_id retries on 404 from /result and succeeds on second attempt."""
    client = await _make_client()
    call_count = 0

    async def _side_effect(_op_id: str) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise NotFoundError("result not yet available")
        return {"id": "retried-item-id"}

    async with client:
        with (
            patch.object(client, "get_operation_result", side_effect=_side_effect),
            patch("fabric_dw.services._lro.asyncio.sleep") as mock_sleep,
        ):
            result = await resolve_lro_item_id(
                client,
                operation_result={},
                location=_LOCATION,
            )

    assert result == "retried-item-id"
    assert call_count == 2
    mock_sleep.assert_called_once()


async def test_resolve_lro_item_id_persistent_404_raises_fabric_server_error() -> None:
    """resolve_lro_item_id raises FabricServerError when /result keeps returning 404."""
    client = await _make_client()

    async def _always_404(_op_id: str) -> dict[str, object]:
        raise NotFoundError("not found")

    async with client:
        with (
            patch.object(client, "get_operation_result", side_effect=_always_404),
            patch("fabric_dw.services._lro.asyncio.sleep"),
        ):
            with pytest.raises(FabricServerError, match=r"404|not yet available"):
                await resolve_lro_item_id(
                    client,
                    operation_result={},
                    location=_LOCATION,
                )


async def test_resolve_lro_item_id_404_race_does_not_suppress_other_exceptions() -> None:
    """resolve_lro_item_id must NOT swallow non-404 exceptions from /result."""
    client = await _make_client()

    async def _server_error(_op_id: str) -> dict[str, object]:
        raise FabricServerError("internal server error", status=500)

    async with client:
        with patch.object(client, "get_operation_result", side_effect=_server_error):
            with pytest.raises(FabricServerError, match="internal server error"):
                await resolve_lro_item_id(
                    client,
                    operation_result={},
                    location=_LOCATION,
                )
