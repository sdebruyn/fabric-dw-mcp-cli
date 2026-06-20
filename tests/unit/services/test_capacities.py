"""Tests for fabric_dw.services.capacities — written TDD-first."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest
import respx

from fabric_dw.exceptions import PermissionDeniedError
from fabric_dw.models import Capacity
from fabric_dw.services import capacities
from tests.unit.services._helpers import _make_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE = "https://api.fabric.microsoft.com/v1"
_CAPACITIES_URL = f"{_BASE}/capacities"

_CAP1_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_CAP2_ID = UUID("bbbbbbbb-0000-0000-0000-000000000002")

_CAPACITY_LIST_PAYLOAD = {
    "value": [
        {
            "id": str(_CAP1_ID),
            "displayName": "F64Capacity",
            "sku": "F64",
            "region": "West Europe",
            "state": "Active",
        },
        {
            "id": str(_CAP2_ID),
            "displayName": "F8Capacity",
            "sku": "F8",
            "region": "East US",
            "state": "Inactive",
        },
    ]
}

_CAPACITY_LIST_PAGE1 = {
    "value": [
        {
            "id": str(_CAP1_ID),
            "displayName": "F64Capacity",
            "sku": "F64",
            "region": "West Europe",
            "state": "Active",
        }
    ],
    "continuationUri": f"{_CAPACITIES_URL}?continuationToken=page2",
}

_CAPACITY_LIST_PAGE2 = {
    "value": [
        {
            "id": str(_CAP2_ID),
            "displayName": "F8Capacity",
            "sku": "F8",
            "region": "East US",
            "state": "Inactive",
        }
    ]
}


# ---------------------------------------------------------------------------
# list_all — happy path
# ---------------------------------------------------------------------------


async def test_list_all_returns_capacity_instances() -> None:
    """list_all must return validated Capacity model instances."""
    with respx.mock:
        respx.get(_CAPACITIES_URL).mock(
            return_value=httpx.Response(200, json=_CAPACITY_LIST_PAYLOAD)
        )

        client = await _make_client()
        async with client:
            result = await capacities.list_all(client)

    assert len(result) == 2
    assert all(isinstance(c, Capacity) for c in result)
    assert result[0].id == _CAP1_ID
    assert result[0].name == "F64Capacity"
    assert result[0].sku == "F64"
    assert result[0].region == "West Europe"
    assert result[0].state == "Active"


async def test_list_all_follows_continuation_uri() -> None:
    """list_all must follow continuationUri and return all capacities across pages."""
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=_CAPACITY_LIST_PAGE1)
        return httpx.Response(200, json=_CAPACITY_LIST_PAGE2)

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(url__regex=rf"{_CAPACITIES_URL}.*").mock(side_effect=side_effect)

        client = await _make_client()
        async with client:
            result = await capacities.list_all(client)

    assert call_count == 2
    assert len(result) == 2
    names = {c.name for c in result}
    assert "F64Capacity" in names
    assert "F8Capacity" in names


async def test_list_all_returns_empty_list_when_no_capacities() -> None:
    """list_all must return an empty list when the API returns no items."""
    with respx.mock:
        respx.get(_CAPACITIES_URL).mock(return_value=httpx.Response(200, json={"value": []}))

        client = await _make_client()
        async with client:
            result = await capacities.list_all(client)

    assert result == []


# ---------------------------------------------------------------------------
# list_all — 403 raises PermissionDeniedError
# ---------------------------------------------------------------------------


async def test_list_all_403_raises_permission_denied() -> None:
    """list_all must raise PermissionDeniedError on a 403 response."""
    with respx.mock:
        respx.get(_CAPACITIES_URL).mock(
            return_value=httpx.Response(
                403, json={"error": {"code": "Forbidden", "message": "Insufficient permissions"}}
            )
        )

        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await capacities.list_all(client)
