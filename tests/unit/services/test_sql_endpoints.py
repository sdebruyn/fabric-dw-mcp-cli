"""Tests for fabric_dw.services.sql_endpoints — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential

from fabric_dw.exceptions import FabricServerError, NotFound, PermissionDenied
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import Warehouse, WarehouseKind, Workspace
from tests.fixtures.api_payloads import (
    WAREHOUSE_SQL_ENDPOINTS_PAGE1_PAYLOAD,
    WAREHOUSE_SQL_ENDPOINTS_PAGE2_PAYLOAD,
    WAREHOUSE_SQL_ENDPOINTS_PAYLOAD,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106

_WORKSPACE_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_ENDPOINT_ID = UUID("e5f6a7b8-c9d0-1234-ef01-234567890abc")

_BASE = "https://api.fabric.microsoft.com/v1"
_SQL_ENDPOINTS_URL = f"{_BASE}/workspaces/{_WORKSPACE_ID}/sqlEndpoints"
_ENDPOINT_URL = f"{_SQL_ENDPOINTS_URL}/{_ENDPOINT_ID}"
_REFRESH_URL = f"{_ENDPOINT_URL}/refreshMetadata"
_OPERATION_URL = f"{_BASE}/operations/op-refresh-123"


def _make_credential(token: AccessToken = _FAKE_TOKEN) -> AsyncTokenCredential:
    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(return_value=token)
    return cred


async def _make_client(rps: int = 10) -> FabricHttpClient:
    return FabricHttpClient(credential=_make_credential(), rps=rps)


# Single SQL endpoint GET payload
_ENDPOINT_GET_PAYLOAD: dict[str, Any] = {
    "id": str(_ENDPOINT_ID),
    "displayName": "SalesLakehouse",
    "description": "SQL endpoint for sales lakehouse",
    "type": "SQLEndpoint",
    "workspaceId": str(_WORKSPACE_ID),
    "properties": {
        "sqlEndpointProperties": {
            "connectionString": "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
            "id": "f6a7b8c9-d0e1-2345-f012-34567890abcd",
            "provisioningStatus": "Success",
        }
    },
}

_REFRESH_LRO_SUCCEEDED: dict[str, Any] = {
    "status": "Succeeded",
    "createdTimeUtc": "2024-03-15T10:29:50Z",
    "lastUpdatedTimeUtc": "2024-03-15T10:30:00Z",
    "percentComplete": 100,
    "error": None,
}


# ---------------------------------------------------------------------------
# list_endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_endpoints_returns_sql_endpoint_items() -> None:
    """list_endpoints must return only SQL_ENDPOINT-kind Warehouse items."""
    from fabric_dw.services.sql_endpoints import list_endpoints  # noqa: PLC0415

    ep_payload = json.loads(WAREHOUSE_SQL_ENDPOINTS_PAYLOAD)

    with respx.mock:
        respx.get(_SQL_ENDPOINTS_URL).mock(return_value=httpx.Response(200, json=ep_payload))

        client = await _make_client()
        async with client:
            result = await list_endpoints(client, _WORKSPACE_ID)

    assert isinstance(result, list)
    assert len(result) == 1
    assert all(isinstance(ep, Warehouse) for ep in result)
    assert all(ep.kind == WarehouseKind.SQL_ENDPOINT for ep in result)


@pytest.mark.asyncio
async def test_list_endpoints_follows_pagination() -> None:
    """list_endpoints must follow continuationUri across pages."""
    from fabric_dw.services.sql_endpoints import list_endpoints  # noqa: PLC0415

    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=json.loads(WAREHOUSE_SQL_ENDPOINTS_PAGE1_PAYLOAD))
        return httpx.Response(200, json=json.loads(WAREHOUSE_SQL_ENDPOINTS_PAGE2_PAYLOAD))

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(url__regex=r".*/sqlEndpoints.*").mock(side_effect=side_effect)

        client = await _make_client()
        async with client:
            result = await list_endpoints(client, _WORKSPACE_ID)

    assert call_count == 2
    assert len(result) == 2
    assert all(ep.kind == WarehouseKind.SQL_ENDPOINT for ep in result)


@pytest.mark.asyncio
async def test_list_endpoints_empty_workspace_returns_empty_list() -> None:
    """list_endpoints must return an empty list when there are no SQL endpoints."""
    from fabric_dw.services.sql_endpoints import list_endpoints  # noqa: PLC0415

    with respx.mock:
        respx.get(_SQL_ENDPOINTS_URL).mock(return_value=httpx.Response(200, json={"value": []}))

        client = await _make_client()
        async with client:
            result = await list_endpoints(client, _WORKSPACE_ID)

    assert result == []


# ---------------------------------------------------------------------------
# get_endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_endpoint_returns_populated_warehouse() -> None:
    """get_endpoint must return a single populated Warehouse with SQL_ENDPOINT kind."""
    from fabric_dw.services.sql_endpoints import get_endpoint  # noqa: PLC0415

    with respx.mock:
        respx.get(_ENDPOINT_URL).mock(return_value=httpx.Response(200, json=_ENDPOINT_GET_PAYLOAD))

        client = await _make_client()
        async with client:
            result = await get_endpoint(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert isinstance(result, Warehouse)
    assert result.id == _ENDPOINT_ID
    assert result.name == "SalesLakehouse"
    assert result.kind == WarehouseKind.SQL_ENDPOINT
    assert result.connection_string == "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com"
    assert result.workspace_id == _WORKSPACE_ID


@pytest.mark.asyncio
async def test_get_endpoint_404_propagates_not_found() -> None:
    """get_endpoint must propagate NotFound on a 404 response."""
    from fabric_dw.services.sql_endpoints import get_endpoint  # noqa: PLC0415

    with respx.mock:
        respx.get(_ENDPOINT_URL).mock(
            return_value=httpx.Response(
                404, json={"error": {"code": "ItemNotFound", "message": "not found"}}
            )
        )

        client = await _make_client()
        async with client:
            with pytest.raises(NotFound):
                await get_endpoint(client, _WORKSPACE_ID, _ENDPOINT_ID)


# ---------------------------------------------------------------------------
# refresh_metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_metadata_posts_and_polls_lro() -> None:
    """refresh_metadata must POST to /refreshMetadata and poll the LRO to completion."""
    from fabric_dw.services.sql_endpoints import refresh_metadata  # noqa: PLC0415

    with respx.mock:
        post_route = respx.post(_REFRESH_URL).mock(
            return_value=httpx.Response(
                202,
                json={},
                headers={"Location": _OPERATION_URL},
            )
        )
        respx.get(_OPERATION_URL).mock(
            return_value=httpx.Response(200, json=_REFRESH_LRO_SUCCEEDED)
        )

        client = await _make_client()
        async with client:
            result = await refresh_metadata(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert post_route.called
    assert isinstance(result, dict)
    assert result.get("status") == "Succeeded"


@pytest.mark.asyncio
async def test_refresh_metadata_lro_poll_multiple_times() -> None:
    """refresh_metadata must poll the LRO until it succeeds (multi-poll path)."""
    from fabric_dw.services.sql_endpoints import refresh_metadata  # noqa: PLC0415

    poll_count = 0

    def lro_side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        poll_count += 1
        if poll_count < 3:
            return httpx.Response(
                200,
                json={"status": "Running", "percentComplete": poll_count * 30},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(200, json=_REFRESH_LRO_SUCCEEDED)

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.post(_REFRESH_URL).mock(
            return_value=httpx.Response(
                202,
                json={},
                headers={"Location": _OPERATION_URL},
            )
        )
        mock_router.get(_OPERATION_URL).mock(side_effect=lro_side_effect)

        client = await _make_client()
        async with client:
            result = await refresh_metadata(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert poll_count == 3
    assert result.get("status") == "Succeeded"


@pytest.mark.asyncio
async def test_refresh_metadata_lro_failed_raises_fabric_server_error() -> None:
    """refresh_metadata must raise FabricServerError when LRO status is 'Failed'."""
    from fabric_dw.services.sql_endpoints import refresh_metadata  # noqa: PLC0415

    with respx.mock:
        respx.post(_REFRESH_URL).mock(
            return_value=httpx.Response(
                202,
                json={},
                headers={"Location": _OPERATION_URL},
            )
        )
        respx.get(_OPERATION_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "Failed",
                    "error": {"code": "RefreshFailed", "message": "Refresh failed"},
                },
            )
        )

        client = await _make_client()
        async with client:
            with pytest.raises(FabricServerError):
                await refresh_metadata(client, _WORKSPACE_ID, _ENDPOINT_ID)


# ---------------------------------------------------------------------------
# list_all_workspaces for sql_endpoints
# ---------------------------------------------------------------------------


_EP_WS_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_EP_WS_B = UUID("bbbbbbbb-0000-0000-0000-000000000002")
_EP_WS_C = UUID("cccccccc-0000-0000-0000-000000000003")
_EP_A = UUID("aaaaaaaa-1111-0000-0000-000000000001")
_EP_B = UUID("bbbbbbbb-1111-0000-0000-000000000002")
_EP_C = UUID("cccccccc-1111-0000-0000-000000000003")


def _make_workspace(ws_id: UUID) -> Workspace:
    return Workspace.model_validate(
        {
            "id": str(ws_id),
            "displayName": f"WS-{ws_id}",
            "description": None,
            "capacityId": None,
        }
    )


def _make_ep(ws_id: UUID, ep_id: UUID) -> Warehouse:
    return Warehouse.model_validate(
        {
            "id": str(ep_id),
            "displayName": "EP",
            "workspaceId": str(ws_id),
            "kind": WarehouseKind.SQL_ENDPOINT,
            "connectionString": "ep.fabric.microsoft.com",
        }
    )


@pytest.mark.asyncio
async def test_list_all_workspaces_endpoints_aggregates_across_workspaces() -> None:
    """list_all_workspaces must collect endpoints from every visible workspace."""
    from fabric_dw.services.sql_endpoints import list_all_workspaces  # noqa: PLC0415

    ws_a = _make_workspace(_EP_WS_A)
    ws_b = _make_workspace(_EP_WS_B)
    ws_c = _make_workspace(_EP_WS_C)
    ep_a = _make_ep(_EP_WS_A, _EP_A)
    ep_b = _make_ep(_EP_WS_B, _EP_B)
    ep_c = _make_ep(_EP_WS_C, _EP_C)

    mock_http = AsyncMock()

    with (
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(return_value=[ws_a, ws_b, ws_c]),
        ),
        patch(
            "fabric_dw.services.sql_endpoints.list_endpoints",
            new=AsyncMock(
                side_effect=[
                    [ep_a],
                    [ep_b],
                    [ep_c],
                ]
            ),
        ),
    ):
        result = await list_all_workspaces(mock_http)

    assert len(result) == 3
    ids = {e.id for e in result}
    assert ids == {_EP_A, _EP_B, _EP_C}


@pytest.mark.asyncio
async def test_list_all_workspaces_endpoints_skips_permission_denied() -> None:
    """list_all_workspaces must skip workspaces where PermissionDenied is raised."""
    from fabric_dw.services.sql_endpoints import list_all_workspaces  # noqa: PLC0415

    ws_a = _make_workspace(_EP_WS_A)
    ws_b = _make_workspace(_EP_WS_B)
    ws_c = _make_workspace(_EP_WS_C)
    ep_a = _make_ep(_EP_WS_A, _EP_A)
    ep_c = _make_ep(_EP_WS_C, _EP_C)

    mock_http = AsyncMock()

    with (
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(return_value=[ws_a, ws_b, ws_c]),
        ),
        patch(
            "fabric_dw.services.sql_endpoints.list_endpoints",
            new=AsyncMock(
                side_effect=[
                    [ep_a],
                    PermissionDenied("no access"),
                    [ep_c],
                ]
            ),
        ),
    ):
        result = await list_all_workspaces(mock_http)

    assert len(result) == 2
    ids = {e.id for e in result}
    assert ids == {_EP_A, _EP_C}


@pytest.mark.asyncio
async def test_list_all_workspaces_endpoints_skips_not_found() -> None:
    """list_all_workspaces must skip workspaces where NotFound is raised."""
    from fabric_dw.services.sql_endpoints import list_all_workspaces  # noqa: PLC0415

    ws_a = _make_workspace(_EP_WS_A)
    ws_b = _make_workspace(_EP_WS_B)
    ws_c = _make_workspace(_EP_WS_C)
    ep_a = _make_ep(_EP_WS_A, _EP_A)
    ep_c = _make_ep(_EP_WS_C, _EP_C)

    mock_http = AsyncMock()

    with (
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(return_value=[ws_a, ws_b, ws_c]),
        ),
        patch(
            "fabric_dw.services.sql_endpoints.list_endpoints",
            new=AsyncMock(
                side_effect=[
                    [ep_a],
                    NotFound("workspace gone"),
                    [ep_c],
                ]
            ),
        ),
    ):
        result = await list_all_workspaces(mock_http)

    assert len(result) == 2
    ids = {e.id for e in result}
    assert ids == {_EP_A, _EP_C}
