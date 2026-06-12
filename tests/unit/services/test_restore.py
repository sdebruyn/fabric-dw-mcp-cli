"""Tests for services.restore — written TDD-first before implementation."""

from __future__ import annotations

import json as _json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import anyio
import httpx
import pytest
import respx

from fabric_dw.exceptions import NotFoundError, PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import CreationModeType, RestorePoint
from fabric_dw.services import restore
from tests.unit.services._helpers import _make_credential

# ---------------------------------------------------------------------------
# Constants & Fixtures
# ---------------------------------------------------------------------------

_WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_WH_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")

# Restore point IDs are strings (timestamps), not UUIDs
_RP_ID = "1726617378000"
_RP_ID_2 = "1726617379000"


_BASE_URL = "https://api.fabric.microsoft.com/v1"
_RP_LIST_URL = f"{_BASE_URL}/workspaces/{_WS_ID}/warehouses/{_WH_ID}/restorePoints"
_RP_URL = f"{_RP_LIST_URL}/{_RP_ID}"
_RP_URL_2 = f"{_RP_LIST_URL}/{_RP_ID_2}"

_LRO_OP_ID = "0acd697c-1550-43cd-b998-91bfbfbd47c6"
_LRO_LOCATION = f"{_BASE_URL}/operations/{_LRO_OP_ID}"
_LRO_RESULT_URL = f"{_BASE_URL}/operations/{_LRO_OP_ID}/result"

# Minimal restore point payload as returned by the API
RP_PAYLOAD: dict[str, Any] = {
    "id": _RP_ID,
    "displayName": "Restore point 1",
    "description": "Restore point 1 description.",
    "creationMode": "UserDefined",
    "creationDetails": {
        "eventDateTime": "2024-10-18T22:17:09Z",
        "eventInitiator": {
            "id": "f3052d1c-61a9-46fb-8df9-0d78916ae041",
            "displayName": "Jacob Hancock",
            "type": "User",
            "userDetails": {"userPrincipalName": "jacob@contoso.com"},
        },
    },
}

RP_PAYLOAD_2: dict[str, Any] = {
    "id": _RP_ID_2,
    "displayName": "Restore point 2",
    "description": "",
    "creationMode": "SystemCreated",
    "creationDetails": {
        "eventDateTime": "2024-10-18T22:17:09Z",
        "eventInitiator": None,
    },
}

RP_LIST_PAYLOAD: dict[str, Any] = {
    "value": [RP_PAYLOAD, RP_PAYLOAD_2],
}

LRO_SUCCEEDED: dict[str, Any] = {
    "status": "Succeeded",
    "createdTimeUtc": "2024-10-18T22:17:09Z",
    "lastUpdatedTimeUtc": "2024-10-18T22:17:15Z",
    "percentComplete": 100,
    "error": None,
}


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress asyncio.sleep for all restore unit tests."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


def test_restore_point_from_api_parses_correctly() -> None:
    """RestorePoint.from_api should parse the flat API payload."""
    rp = RestorePoint.from_api(RP_PAYLOAD)
    assert rp.id == _RP_ID
    assert rp.name == "Restore point 1"
    assert rp.description == "Restore point 1 description."
    assert rp.creation_mode == CreationModeType.USER_DEFINED
    assert rp.event_date_time is not None


def test_restore_point_from_api_system_created() -> None:
    """RestorePoint.from_api should handle SystemCreated mode and null initiator."""
    rp = RestorePoint.from_api(RP_PAYLOAD_2)
    assert rp.id == _RP_ID_2
    assert rp.creation_mode == CreationModeType.SYSTEM_CREATED


def test_restore_point_from_api_missing_creation_details() -> None:
    """RestorePoint.from_api should handle missing creationDetails gracefully."""
    payload: dict[str, Any] = {
        "id": _RP_ID,
        "displayName": "Test",
        "description": None,
        "creationMode": "UserDefined",
    }
    rp = RestorePoint.from_api(payload)
    assert rp.event_date_time is None


# ---------------------------------------------------------------------------
# list_points
# ---------------------------------------------------------------------------


@respx.mock
async def test_list_points_returns_all_items() -> None:
    """list_points should return all restore points from the paginated list."""
    respx.get(url__regex=rf"{_RP_LIST_URL}(\?.*)?$").mock(
        return_value=httpx.Response(200, json=RP_LIST_PAYLOAD)
    )

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await restore.list_points(http, _WS_ID, _WH_ID)

    assert len(result) == 2
    assert all(isinstance(r, RestorePoint) for r in result)
    assert result[0].id == _RP_ID
    assert result[1].id == _RP_ID_2


@respx.mock
async def test_list_points_empty() -> None:
    """list_points should return an empty list when the warehouse has no restore points."""
    respx.get(url__regex=rf"{_RP_LIST_URL}(\?.*)?$").mock(
        return_value=httpx.Response(200, json={"value": []})
    )

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await restore.list_points(http, _WS_ID, _WH_ID)

    assert result == []


@respx.mock
async def test_list_points_follows_pagination() -> None:
    """list_points should follow continuationUri across pages."""
    page2_url = f"{_RP_LIST_URL}?continuationToken=page2"
    page1: dict[str, Any] = {"value": [RP_PAYLOAD], "continuationUri": page2_url}
    page2: dict[str, Any] = {"value": [RP_PAYLOAD_2]}

    call_count = 0

    def _side_effect(_req: Any) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=page1 if call_count == 1 else page2)

    respx.get(
        url__regex=rf"https://api\.fabric\.microsoft\.com/v1/workspaces/{_WS_ID}/warehouses/{_WH_ID}/restorePoints(\?.*)?$"
    ).mock(side_effect=_side_effect)

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await restore.list_points(http, _WS_ID, _WH_ID)

    assert len(result) == 2
    assert call_count == 2


@respx.mock
async def test_list_points_403_raises_permission_denied() -> None:
    """list_points should propagate PermissionDeniedError on 403."""
    respx.get(url__regex=rf"{_RP_LIST_URL}(\?.*)?$").mock(return_value=httpx.Response(403))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(PermissionDeniedError):
            await restore.list_points(http, _WS_ID, _WH_ID)


# ---------------------------------------------------------------------------
# get_point
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_point_returns_restore_point() -> None:
    """get_point should return the RestorePoint with the correct ID."""
    respx.get(_RP_URL).mock(return_value=httpx.Response(200, json=RP_PAYLOAD))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await restore.get_point(http, _WS_ID, _WH_ID, _RP_ID)

    assert isinstance(result, RestorePoint)
    assert result.id == _RP_ID
    assert result.name == "Restore point 1"


@respx.mock
async def test_get_point_404_raises_not_found() -> None:
    """get_point should raise NotFoundError on 404."""
    respx.get(_RP_URL).mock(return_value=httpx.Response(404))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(NotFoundError):
            await restore.get_point(http, _WS_ID, _WH_ID, _RP_ID)


@respx.mock
async def test_get_point_403_raises_permission_denied() -> None:
    """get_point should raise PermissionDeniedError on 403."""
    respx.get(_RP_URL).mock(return_value=httpx.Response(403))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(PermissionDeniedError):
            await restore.get_point(http, _WS_ID, _WH_ID, _RP_ID)


# ---------------------------------------------------------------------------
# create_point
# ---------------------------------------------------------------------------


@respx.mock
async def test_create_point_synchronous_201() -> None:
    """create_point should handle 201 synchronous response directly."""
    respx.post(_RP_LIST_URL).mock(return_value=httpx.Response(201, json=RP_PAYLOAD))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await restore.create_point(
            http, _WS_ID, _WH_ID, name="Restore point 1", description="desc"
        )

    assert isinstance(result, RestorePoint)
    assert result.id == _RP_ID


@respx.mock
async def test_create_point_sends_correct_body() -> None:
    """create_point should send displayName and description in the request body."""
    captured: list[Any] = []

    def _capture(request: Any) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json=RP_PAYLOAD)

    respx.post(_RP_LIST_URL).mock(side_effect=_capture)

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        await restore.create_point(http, _WS_ID, _WH_ID, name="My RP", description="My desc")

    assert len(captured) == 1
    body = _json.loads(captured[0].content)
    assert body["displayName"] == "My RP"
    assert body["description"] == "My desc"


@respx.mock
async def test_create_point_no_body_when_no_args() -> None:
    """create_point with no name/description should send empty or no body."""
    respx.post(_RP_LIST_URL).mock(return_value=httpx.Response(201, json=RP_PAYLOAD))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await restore.create_point(http, _WS_ID, _WH_ID)

    assert isinstance(result, RestorePoint)


@respx.mock
async def test_create_point_lro_202_polls_and_fetches_via_result_endpoint() -> None:
    """create_point should poll LRO on 202, then fetch via the /result endpoint.

    This reflects the realistic Fabric production path: the LRO status body
    does NOT contain a resource id (branch a is almost never hit in practice);
    the /result sub-endpoint is the primary fallback (branch b).
    """
    respx.post(_RP_LIST_URL).mock(
        return_value=httpx.Response(
            202,
            headers={
                "Location": _LRO_LOCATION,
                "x-ms-operation-id": _LRO_OP_ID,
                "Retry-After": "1",
            },
        )
    )
    respx.get(_RP_URL).mock(return_value=httpx.Response(200, json=RP_PAYLOAD))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with patch.object(http, "poll_operation", new_callable=AsyncMock) as mock_poll:
            # Realistic status body: no id/resourceId field
            mock_poll.return_value = LRO_SUCCEEDED
            with patch.object(http, "get_operation_result", new_callable=AsyncMock) as mock_result:
                mock_result.return_value = {"id": _RP_ID}
                result = await restore.create_point(http, _WS_ID, _WH_ID, name="My RP")

    assert isinstance(result, RestorePoint)
    assert result.id == _RP_ID
    mock_poll.assert_awaited_once_with(_LRO_LOCATION)
    mock_result.assert_awaited_once_with(_LRO_OP_ID)


@respx.mock
async def test_create_point_lro_202_falls_back_to_result_endpoint() -> None:
    """create_point should fall back to LRO result endpoint when status body has no id."""
    respx.post(_RP_LIST_URL).mock(
        return_value=httpx.Response(
            202,
            headers={"Location": _LRO_LOCATION},
        )
    )
    respx.get(_LRO_RESULT_URL).mock(return_value=httpx.Response(200, json={"id": _RP_ID}))
    respx.get(_RP_URL).mock(return_value=httpx.Response(200, json=RP_PAYLOAD))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with patch.object(http, "poll_operation", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = LRO_SUCCEEDED  # no id in status body

            with patch.object(http, "get_operation_result", new_callable=AsyncMock) as mock_result:
                mock_result.return_value = {"id": _RP_ID}
                result = await restore.create_point(http, _WS_ID, _WH_ID, name="My RP")

    assert isinstance(result, RestorePoint)
    assert result.id == _RP_ID


@respx.mock
async def test_create_point_lro_202_last_resort_list() -> None:
    """create_point falls back to list when LRO result has no id."""
    respx.post(_RP_LIST_URL).mock(
        return_value=httpx.Response(202, headers={"Location": _LRO_LOCATION})
    )
    respx.get(url__regex=rf"{_RP_LIST_URL}(\?.*)?$").mock(
        return_value=httpx.Response(200, json={"value": [RP_PAYLOAD]})
    )

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with patch.object(http, "poll_operation", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = LRO_SUCCEEDED  # no id

            with patch.object(http, "get_operation_result", new_callable=AsyncMock) as mock_result:
                mock_result.return_value = {}  # also no id
                result = await restore.create_point(http, _WS_ID, _WH_ID)

    assert isinstance(result, RestorePoint)


# ---------------------------------------------------------------------------
# update_point
# ---------------------------------------------------------------------------


@respx.mock
async def test_update_point_returns_updated_restore_point() -> None:
    """update_point should PATCH then GET and return the updated RestorePoint.

    Fabric PATCH returns a minimal body (often just the ID), so the service
    always follows up with a GET to return the full RestorePoint.
    """
    updated_payload = {**RP_PAYLOAD, "displayName": "Renamed RP"}
    # Minimal PATCH response (mirrors real Fabric API behaviour)
    respx.patch(_RP_URL).mock(return_value=httpx.Response(200, json={"id": _RP_ID}))
    respx.get(_RP_URL).mock(return_value=httpx.Response(200, json=updated_payload))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await restore.update_point(http, _WS_ID, _WH_ID, _RP_ID, name="Renamed RP")

    assert isinstance(result, RestorePoint)
    assert result.name == "Renamed RP"


@respx.mock
async def test_update_point_sends_correct_body() -> None:
    """update_point should include only the supplied fields in the PATCH body."""
    captured: list[Any] = []

    def _capture(request: Any) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": _RP_ID})

    respx.patch(_RP_URL).mock(side_effect=_capture)
    respx.get(_RP_URL).mock(return_value=httpx.Response(200, json=RP_PAYLOAD))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        await restore.update_point(
            http, _WS_ID, _WH_ID, _RP_ID, name="New Name", description="New desc"
        )

    body = _json.loads(captured[0].content)
    assert body["displayName"] == "New Name"
    assert body["description"] == "New desc"


@respx.mock
async def test_update_point_description_only() -> None:
    """update_point with only description should omit displayName from body."""
    captured: list[Any] = []

    def _capture(request: Any) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": _RP_ID})

    respx.patch(_RP_URL).mock(side_effect=_capture)
    respx.get(_RP_URL).mock(return_value=httpx.Response(200, json=RP_PAYLOAD))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        await restore.update_point(http, _WS_ID, _WH_ID, _RP_ID, description="New desc")

    body = _json.loads(captured[0].content)
    assert "displayName" not in body
    assert body["description"] == "New desc"


def test_update_point_no_args_raises_value_error() -> None:
    """update_point with neither name nor description should raise ValueError."""

    async def _run() -> None:
        async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
            with pytest.raises(ValueError, match="At least one"):
                await restore.update_point(http, _WS_ID, _WH_ID, _RP_ID)

    anyio.run(_run)


@respx.mock
async def test_update_point_404_raises_not_found() -> None:
    """update_point should raise NotFoundError on 404."""
    respx.patch(_RP_URL).mock(return_value=httpx.Response(404))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(NotFoundError):
            await restore.update_point(http, _WS_ID, _WH_ID, _RP_ID, name="X")


# ---------------------------------------------------------------------------
# delete_point
# ---------------------------------------------------------------------------


@respx.mock
async def test_delete_point_200_returns_none() -> None:
    """delete_point should issue DELETE and return None on 200."""
    respx.delete(_RP_URL).mock(return_value=httpx.Response(200))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await restore.delete_point(http, _WS_ID, _WH_ID, _RP_ID)

    assert result is None


@respx.mock
async def test_delete_point_404_raises_not_found() -> None:
    """delete_point should raise NotFoundError on 404."""
    respx.delete(_RP_URL).mock(return_value=httpx.Response(404))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(NotFoundError):
            await restore.delete_point(http, _WS_ID, _WH_ID, _RP_ID)


@respx.mock
async def test_delete_point_403_raises_permission_denied() -> None:
    """delete_point should raise PermissionDeniedError on 403."""
    respx.delete(_RP_URL).mock(return_value=httpx.Response(403))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(PermissionDeniedError):
            await restore.delete_point(http, _WS_ID, _WH_ID, _RP_ID)


# ---------------------------------------------------------------------------
# restore_in_place
# ---------------------------------------------------------------------------


@respx.mock
async def test_restore_in_place_synchronous_200() -> None:
    """restore_in_place should return None on synchronous 200."""
    respx.post(f"{_RP_URL}/restore").mock(return_value=httpx.Response(200))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await restore.restore_in_place(http, _WS_ID, _WH_ID, _RP_ID)

    assert result is None


@respx.mock
async def test_restore_in_place_lro_202_polls_to_completion() -> None:
    """restore_in_place should poll the LRO when 202 is returned."""
    respx.post(f"{_RP_URL}/restore").mock(
        return_value=httpx.Response(
            202,
            headers={"Location": _LRO_LOCATION, "Retry-After": "1"},
        )
    )

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with patch.object(http, "poll_operation", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = LRO_SUCCEEDED
            result = await restore.restore_in_place(http, _WS_ID, _WH_ID, _RP_ID)

    assert result is None
    mock_poll.assert_awaited_once_with(_LRO_LOCATION)


@respx.mock
async def test_restore_in_place_404_raises_not_found() -> None:
    """restore_in_place should raise NotFoundError on 404."""
    respx.post(f"{_RP_URL}/restore").mock(return_value=httpx.Response(404))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(NotFoundError):
            await restore.restore_in_place(http, _WS_ID, _WH_ID, _RP_ID)


@respx.mock
async def test_restore_in_place_403_raises_permission_denied() -> None:
    """restore_in_place should raise PermissionDeniedError on 403."""
    respx.post(f"{_RP_URL}/restore").mock(return_value=httpx.Response(403))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(PermissionDeniedError):
            await restore.restore_in_place(http, _WS_ID, _WH_ID, _RP_ID)
