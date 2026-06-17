"""Tests for fabric_dw.services.sql_endpoints — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx
import pytest
import respx

from fabric_dw.exceptions import FabricServerError, NotFoundError, PermissionDeniedError
from fabric_dw.models import TableSyncStatus, Warehouse, WarehouseKind, Workspace
from fabric_dw.services.sql_endpoints import list_all_workspaces
from tests.fixtures.api_payloads import (
    WAREHOUSE_SQL_ENDPOINTS_PAGE1_PAYLOAD,
    WAREHOUSE_SQL_ENDPOINTS_PAGE2_PAYLOAD,
    WAREHOUSE_SQL_ENDPOINTS_PAYLOAD,
)
from tests.unit.services._helpers import _make_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WORKSPACE_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_ENDPOINT_ID = UUID("e5f6a7b8-c9d0-1234-ef01-234567890abc")

_BASE = "https://api.fabric.microsoft.com/v1"
_SQL_ENDPOINTS_URL = f"{_BASE}/workspaces/{_WORKSPACE_ID}/sqlEndpoints"
_ENDPOINT_URL = f"{_SQL_ENDPOINTS_URL}/{_ENDPOINT_ID}"
_REFRESH_URL = f"{_ENDPOINT_URL}/refreshMetadata"
_OPERATION_URL = f"{_BASE}/operations/op-refresh-123"
_LAKEHOUSES_URL = f"{_BASE}/workspaces/{_WORKSPACE_ID}/lakehouses"


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

_REFRESH_TABLE_RESULTS: list[dict[str, Any]] = [
    {
        "tableName": "Table1",
        "startDateTime": "2025-08-08T10:31:22.270Z",
        "endDateTime": "2025-08-08T10:36:54.965Z",
        "status": "Success",
        "lastSuccessfulSyncDateTime": "2025-08-08T10:36:54.965Z",
    },
    {
        "tableName": "Table2",
        "startDateTime": "2025-08-08T10:31:22.270Z",
        "endDateTime": "2025-08-08T10:43:02.532Z",
        "status": "Failure",
        "error": {
            "errorCode": "AdalRetryException",
            "message": "Token error",
        },
        "lastSuccessfulSyncDateTime": "2025-08-07T10:44:27.263Z",
    },
]

# The LRO operation GET response wraps results in status/value envelope
_REFRESH_LRO_SUCCEEDED: dict[str, Any] = {
    "status": "Succeeded",
    "value": _REFRESH_TABLE_RESULTS,
}


# ---------------------------------------------------------------------------
# list_endpoints
# ---------------------------------------------------------------------------


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


async def test_get_endpoint_404_propagates_not_found() -> None:
    """get_endpoint must propagate NotFoundError on a 404 response."""
    from fabric_dw.services.sql_endpoints import get_endpoint  # noqa: PLC0415

    with respx.mock:
        respx.get(_ENDPOINT_URL).mock(
            return_value=httpx.Response(
                404, json={"error": {"code": "ItemNotFound", "message": "not found"}}
            )
        )

        client = await _make_client()
        async with client:
            with pytest.raises(NotFoundError):
                await get_endpoint(client, _WORKSPACE_ID, _ENDPOINT_ID)


# ---------------------------------------------------------------------------
# refresh_metadata
# ---------------------------------------------------------------------------


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
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(s, TableSyncStatus) for s in result)
    assert result[0].table_name == "Table1"
    assert result[0].status == "Success"
    assert result[1].status == "Failure"
    assert result[1].error is not None
    assert result[1].error.error_code == "AdalRetryException"


async def test_refresh_metadata_no_recreate_tables_sends_no_body() -> None:
    """refresh_metadata without recreate_tables must not send a JSON body."""
    from fabric_dw.services.sql_endpoints import refresh_metadata  # noqa: PLC0415

    captured_requests: list[httpx.Request] = []

    def post_side_effect(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(202, json={}, headers={"Location": _OPERATION_URL})

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.post(_REFRESH_URL).mock(side_effect=post_side_effect)
        mock_router.get(_OPERATION_URL).mock(
            return_value=httpx.Response(200, json=_REFRESH_LRO_SUCCEEDED)
        )

        client = await _make_client()
        async with client:
            await refresh_metadata(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert len(captured_requests) == 1
    # No body was sent — content should be empty
    assert captured_requests[0].content in (b"", b"null")


async def test_refresh_metadata_recreate_tables_sends_body() -> None:
    """refresh_metadata with recreate_tables=True must send {recreateTables: true} in the body."""
    from fabric_dw.services.sql_endpoints import refresh_metadata  # noqa: PLC0415

    captured_requests: list[httpx.Request] = []

    def post_side_effect(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(202, json={}, headers={"Location": _OPERATION_URL})

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.post(_REFRESH_URL).mock(side_effect=post_side_effect)
        mock_router.get(_OPERATION_URL).mock(
            return_value=httpx.Response(200, json=_REFRESH_LRO_SUCCEEDED)
        )

        client = await _make_client()
        async with client:
            result = await refresh_metadata(
                client, _WORKSPACE_ID, _ENDPOINT_ID, recreate_tables=True
            )

    assert len(captured_requests) == 1
    body = json.loads(captured_requests[0].content)
    assert body == {"recreateTables": True}
    assert isinstance(result, list)


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
    assert isinstance(result, list)
    assert len(result) == 2


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


async def test_list_all_workspaces_endpoints_aggregates_across_workspaces() -> None:
    """list_all_workspaces must collect endpoints from every visible workspace."""
    ws_a = _make_workspace(_EP_WS_A)
    ws_b = _make_workspace(_EP_WS_B)
    ws_c = _make_workspace(_EP_WS_C)
    ep_a = _make_ep(_EP_WS_A, _EP_A)
    ep_b = _make_ep(_EP_WS_B, _EP_B)
    ep_c = _make_ep(_EP_WS_C, _EP_C)

    mock_http = AsyncMock()

    with (
        patch(
            "fabric_dw.services.sql_endpoints._list_all_workspaces",
            new=AsyncMock(return_value=[ws_a, ws_b, ws_c]),
        ),
        patch(
            "fabric_dw.services.sql_endpoints.get_capacity_states",
            new=AsyncMock(return_value=None),  # proactive filter unavailable → no skip
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


async def test_list_all_workspaces_endpoints_skips_permission_denied(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """list_all_workspaces must skip workspaces where PermissionDeniedError is raised and warn."""
    ws_a = _make_workspace(_EP_WS_A)
    ws_b = _make_workspace(_EP_WS_B)
    ws_c = _make_workspace(_EP_WS_C)
    ep_a = _make_ep(_EP_WS_A, _EP_A)
    ep_c = _make_ep(_EP_WS_C, _EP_C)

    mock_http = AsyncMock()

    with (
        caplog.at_level(logging.WARNING, logger="fabric_dw.sql_endpoints"),
        patch(
            "fabric_dw.services.sql_endpoints._list_all_workspaces",
            new=AsyncMock(return_value=[ws_a, ws_b, ws_c]),
        ),
        patch(
            "fabric_dw.services.sql_endpoints.get_capacity_states",
            new=AsyncMock(return_value=None),  # proactive filter unavailable → no proactive skip
        ),
        patch(
            "fabric_dw.services.sql_endpoints.list_endpoints",
            new=AsyncMock(
                side_effect=[
                    [ep_a],
                    PermissionDeniedError("no access"),
                    [ep_c],
                ]
            ),
        ),
    ):
        result = await list_all_workspaces(mock_http)

    assert len(result) == 2
    ids = {e.id for e in result}
    assert ids == {_EP_A, _EP_C}
    assert any(f"WS-{_EP_WS_B}" in r.message for r in caplog.records)
    assert any("skipped 1 of 3" in r.message for r in caplog.records)


async def test_list_all_workspaces_endpoints_skips_not_found(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """list_all_workspaces must skip workspaces where NotFoundError is raised and warn."""
    ws_a = _make_workspace(_EP_WS_A)
    ws_b = _make_workspace(_EP_WS_B)
    ws_c = _make_workspace(_EP_WS_C)
    ep_a = _make_ep(_EP_WS_A, _EP_A)
    ep_c = _make_ep(_EP_WS_C, _EP_C)

    mock_http = AsyncMock()

    with (
        caplog.at_level(logging.WARNING, logger="fabric_dw.sql_endpoints"),
        patch(
            "fabric_dw.services.sql_endpoints._list_all_workspaces",
            new=AsyncMock(return_value=[ws_a, ws_b, ws_c]),
        ),
        patch(
            "fabric_dw.services.sql_endpoints.get_capacity_states",
            new=AsyncMock(return_value=None),  # proactive filter unavailable → no proactive skip
        ),
        patch(
            "fabric_dw.services.sql_endpoints.list_endpoints",
            new=AsyncMock(
                side_effect=[
                    [ep_a],
                    NotFoundError("workspace gone"),
                    [ep_c],
                ]
            ),
        ),
    ):
        result = await list_all_workspaces(mock_http)

    assert len(result) == 2
    ids = {e.id for e in result}
    assert ids == {_EP_A, _EP_C}
    assert any(f"WS-{_EP_WS_B}" in r.message for r in caplog.records)
    assert any("skipped 1 of 3" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# refresh_metadata — synchronous (no LRO header) path
# ---------------------------------------------------------------------------


async def test_refresh_metadata_sync_no_location_header_returns_statuses() -> None:
    """refresh_metadata with no Location/Operation-Location header must parse body inline.

    This covers the synchronous completion path: the API responds with 200 and
    table sync results directly in the body, no LRO header present.
    Should NOT raise KeyError.
    """
    from fabric_dw.services.sql_endpoints import refresh_metadata  # noqa: PLC0415

    with respx.mock:
        respx.post(_REFRESH_URL).mock(
            return_value=httpx.Response(
                200,
                json={"value": _REFRESH_TABLE_RESULTS},
                # Deliberately no Location or Operation-Location header
            )
        )

        client = await _make_client()
        async with client:
            result = await refresh_metadata(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(s, TableSyncStatus) for s in result)
    assert result[0].table_name == "Table1"
    assert result[0].status == "Success"
    assert result[1].table_name == "Table2"
    assert result[1].status == "Failure"


async def test_refresh_metadata_sync_empty_body_returns_empty_list() -> None:
    """refresh_metadata with 204 (no content) and no LRO header must return empty list."""
    from fabric_dw.services.sql_endpoints import refresh_metadata  # noqa: PLC0415

    with respx.mock:
        respx.post(_REFRESH_URL).mock(return_value=httpx.Response(204, content=b""))

        client = await _make_client()
        async with client:
            result = await refresh_metadata(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert result == []


async def test_refresh_metadata_operation_location_header_still_works() -> None:
    """refresh_metadata must also poll when the header is Operation-Location (not Location)."""
    from fabric_dw.services.sql_endpoints import refresh_metadata  # noqa: PLC0415

    with respx.mock:
        respx.post(_REFRESH_URL).mock(
            return_value=httpx.Response(
                202,
                json={},
                headers={"Operation-Location": _OPERATION_URL},
            )
        )
        respx.get(_OPERATION_URL).mock(
            return_value=httpx.Response(200, json=_REFRESH_LRO_SUCCEEDED)
        )

        client = await _make_client()
        async with client:
            result = await refresh_metadata(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0].table_name == "Table1"


# ---------------------------------------------------------------------------
# get_endpoint_connection_string — polling behaviour
# ---------------------------------------------------------------------------

_ENDPOINT_GET_EMPTY_CONN_STRING: dict[str, Any] = {
    "id": str(_ENDPOINT_ID),
    "displayName": "SalesLakehouse",
    "description": "SQL endpoint for sales lakehouse",
    "type": "SQLEndpoint",
    "workspaceId": str(_WORKSPACE_ID),
    "properties": {
        "sqlEndpointProperties": {
            "connectionString": "",
            "id": "f6a7b8c9-d0e1-2345-f012-34567890abcd",
            "provisioningStatus": "Success",
        }
    },
}


async def test_get_endpoint_connection_string_polls_until_populated() -> None:
    """get_endpoint_connection_string must poll until connection_string is non-empty.

    The endpoint resource returns empty connectionString on calls 1 and 2 —
    which triggers the lakehouse fallback (returning no match), so
    connection_string stays empty and the poller retries.  On call 3 the
    endpoint resource returns a populated connectionString directly.
    """
    from fabric_dw.services.sql_endpoints import get_endpoint_connection_string  # noqa: PLC0415

    ep_call_count = 0

    def ep_side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal ep_call_count
        ep_call_count += 1
        if ep_call_count < 3:
            # First two calls return empty connection_string → lakehouse fallback triggered
            return httpx.Response(200, json=_ENDPOINT_GET_EMPTY_CONN_STRING)
        # Third call returns populated connection_string — fast path, no lakehouse call
        return httpx.Response(200, json=_ENDPOINT_GET_PAYLOAD)

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(_ENDPOINT_URL).mock(side_effect=ep_side_effect)
        # Lakehouse scan returns no results — fallback finds no match for calls 1 & 2
        mock_router.get(_LAKEHOUSES_URL).mock(return_value=httpx.Response(200, json={"value": []}))

        client = await _make_client()
        async with client:
            with patch(
                "fabric_dw.services.sql_endpoints.asyncio.sleep",
                new=AsyncMock(),
            ) as mock_sleep:
                result = await get_endpoint_connection_string(
                    client,
                    _WORKSPACE_ID,
                    _ENDPOINT_ID,
                    poll_interval=5.0,
                    timeout=120.0,
                )

    assert result == "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com"
    assert ep_call_count == 3
    # sleep must have been called between polls (twice: after call 1 and call 2)
    assert mock_sleep.call_count == 2


async def test_get_endpoint_connection_string_immediate_return_no_sleep() -> None:
    """get_endpoint_connection_string must return immediately when already populated."""
    from fabric_dw.services.sql_endpoints import get_endpoint_connection_string  # noqa: PLC0415

    with respx.mock:
        respx.get(_ENDPOINT_URL).mock(return_value=httpx.Response(200, json=_ENDPOINT_GET_PAYLOAD))

        client = await _make_client()
        async with client:
            with patch(
                "fabric_dw.services.sql_endpoints.asyncio.sleep",
                new=AsyncMock(),
            ) as mock_sleep:
                result = await get_endpoint_connection_string(
                    client,
                    _WORKSPACE_ID,
                    _ENDPOINT_ID,
                )

    assert result == "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com"
    mock_sleep.assert_not_called()


async def test_get_endpoint_connection_string_timeout_raises_fabric_server_error() -> None:
    """get_endpoint_connection_string must raise FabricServerError after timeout.

    Passes timeout=0.0 so the deadline is already expired after the first GET
    that returns an empty connection_string — no need to mock time.  The
    lakehouse fallback is also mocked to return no matching lakehouse.
    """
    from fabric_dw.services.sql_endpoints import get_endpoint_connection_string  # noqa: PLC0415

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(_ENDPOINT_URL).mock(
            return_value=httpx.Response(200, json=_ENDPOINT_GET_EMPTY_CONN_STRING)
        )
        # Lakehouse scan returns no matching lakehouse → fallback yields no connection string
        mock_router.get(_LAKEHOUSES_URL).mock(return_value=httpx.Response(200, json={"value": []}))

        client = await _make_client()
        async with client:
            with pytest.raises(FabricServerError, match="connection_string"):
                await get_endpoint_connection_string(
                    client,
                    _WORKSPACE_ID,
                    _ENDPOINT_ID,
                    poll_interval=0.0,
                    timeout=0.0,
                )


# ---------------------------------------------------------------------------
# get_endpoint — lakehouse connection-string fallback
# ---------------------------------------------------------------------------

# The UUID that the paired lakehouse uses for its sqlEndpointProperties.id field.
# This matches _ENDPOINT_ID so the fallback can find the right lakehouse.
_LAKEHOUSE_CONN_STRING = "lh-derived.datawarehouse.fabric.microsoft.com"

# Lakehouse payload whose sqlEndpointProperties.id matches _ENDPOINT_ID.
_LAKEHOUSES_WITH_MATCH_PAYLOAD: dict[str, Any] = {
    "value": [
        {
            "id": "11111111-0000-0000-0000-000000000001",
            "displayName": "SalesLakehouse",
            "workspaceId": str(_WORKSPACE_ID),
            "properties": {
                "sqlEndpointProperties": {
                    "id": str(_ENDPOINT_ID),
                    "connectionString": _LAKEHOUSE_CONN_STRING,
                    "provisioningStatus": "Success",
                }
            },
        }
    ]
}

# Lakehouse payload whose sqlEndpointProperties.id does NOT match _ENDPOINT_ID.
_LAKEHOUSES_NO_MATCH_PAYLOAD: dict[str, Any] = {
    "value": [
        {
            "id": "22222222-0000-0000-0000-000000000002",
            "displayName": "OtherLakehouse",
            "workspaceId": str(_WORKSPACE_ID),
            "properties": {
                "sqlEndpointProperties": {
                    "id": "99999999-ffff-ffff-ffff-000000000099",
                    "connectionString": "other.datawarehouse.fabric.microsoft.com",
                    "provisioningStatus": "Success",
                }
            },
        }
    ]
}


async def test_get_endpoint_uses_lakehouse_fallback_when_conn_string_empty() -> None:
    """get_endpoint falls back to the parent Lakehouse when connectionString is empty.

    The /sqlEndpoints/{id} resource permanently returns an empty connectionString
    for lakehouse-derived endpoints.  get_endpoint must scan /lakehouses, find
    the lakehouse whose sqlEndpointProperties.id matches the endpoint ID, and
    return the connection string from there.
    """
    from fabric_dw.services.sql_endpoints import get_endpoint  # noqa: PLC0415

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(_ENDPOINT_URL).mock(
            return_value=httpx.Response(200, json=_ENDPOINT_GET_EMPTY_CONN_STRING)
        )
        mock_router.get(_LAKEHOUSES_URL).mock(
            return_value=httpx.Response(200, json=_LAKEHOUSES_WITH_MATCH_PAYLOAD)
        )

        client = await _make_client()
        async with client:
            result = await get_endpoint(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert isinstance(result, Warehouse)
    assert result.id == _ENDPOINT_ID
    assert result.kind == WarehouseKind.SQL_ENDPOINT
    assert result.connection_string == _LAKEHOUSE_CONN_STRING
    # The fallback must preserve every other field from the endpoint resource —
    # only connection_string is replaced (model_copy, not a hand-rolled dict).
    assert result.name == "SalesLakehouse"
    assert result.description == "SQL endpoint for sales lakehouse"
    assert result.workspace_id == _WORKSPACE_ID


async def test_get_endpoint_lakehouse_fallback_matches_uppercase_id() -> None:
    """get_endpoint must match the lakehouse even if the API returns an uppercase UUID.

    str(UUID) is always lowercase; Fabric could in principle return an
    uppercase/mixed-case sqlEndpointProperties.id.  The match must be
    case-insensitive so the connection string still resolves.
    """
    from fabric_dw.services.sql_endpoints import get_endpoint  # noqa: PLC0415

    uppercase_payload: dict[str, Any] = {
        "value": [
            {
                "id": "11111111-0000-0000-0000-000000000001",
                "displayName": "SalesLakehouse",
                "workspaceId": str(_WORKSPACE_ID),
                "properties": {
                    "sqlEndpointProperties": {
                        "id": str(_ENDPOINT_ID).upper(),
                        "connectionString": _LAKEHOUSE_CONN_STRING,
                        "provisioningStatus": "Success",
                    }
                },
            }
        ]
    }

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(_ENDPOINT_URL).mock(
            return_value=httpx.Response(200, json=_ENDPOINT_GET_EMPTY_CONN_STRING)
        )
        mock_router.get(_LAKEHOUSES_URL).mock(
            return_value=httpx.Response(200, json=uppercase_payload)
        )

        client = await _make_client()
        async with client:
            result = await get_endpoint(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert result.connection_string == _LAKEHOUSE_CONN_STRING


async def test_get_endpoint_no_lakehouse_call_when_conn_string_present() -> None:
    """get_endpoint must NOT call /lakehouses when endpoint already has a connectionString.

    The fast path must avoid the extra lakehouse scan so that warehouse-native
    endpoints (which always carry a connectionString) incur no overhead.
    """
    from fabric_dw.services.sql_endpoints import get_endpoint  # noqa: PLC0415

    lakehouses_called = False

    def lh_side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal lakehouses_called
        lakehouses_called = True
        return httpx.Response(200, json={"value": []})

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(_ENDPOINT_URL).mock(
            return_value=httpx.Response(200, json=_ENDPOINT_GET_PAYLOAD)
        )
        mock_router.get(_LAKEHOUSES_URL).mock(side_effect=lh_side_effect)

        client = await _make_client()
        async with client:
            result = await get_endpoint(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert result.connection_string == "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com"
    assert not lakehouses_called, (
        "lakehouse scan must not be triggered when connectionString is already present"
    )


async def test_get_endpoint_lakehouse_fallback_no_match_returns_empty_conn_string() -> None:
    """get_endpoint returns a Warehouse with empty connection_string when no lakehouse matches.

    Covers the case where the endpoint is not lakehouse-derived but the
    /sqlEndpoints/{id} resource still returned an empty connectionString — which
    should not happen in practice, but the code must handle it gracefully.
    """
    from fabric_dw.services.sql_endpoints import get_endpoint  # noqa: PLC0415

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(_ENDPOINT_URL).mock(
            return_value=httpx.Response(200, json=_ENDPOINT_GET_EMPTY_CONN_STRING)
        )
        mock_router.get(_LAKEHOUSES_URL).mock(
            return_value=httpx.Response(200, json=_LAKEHOUSES_NO_MATCH_PAYLOAD)
        )

        client = await _make_client()
        async with client:
            result = await get_endpoint(client, _WORKSPACE_ID, _ENDPOINT_ID)

    assert isinstance(result, Warehouse)
    assert result.id == _ENDPOINT_ID
    # No matching lakehouse → connection_string stays empty/None
    assert not result.connection_string


async def test_get_endpoint_connection_string_resolves_via_lakehouse_no_sleep() -> None:
    """get_endpoint_connection_string returns the lakehouse value on the first poll.

    This is the primary real-world scenario the PR fixes: the endpoint resource
    has an empty connectionString, but the lakehouse scan resolves it on the very
    first iteration, so the poller exits early without sleeping.
    """
    from fabric_dw.services.sql_endpoints import get_endpoint_connection_string  # noqa: PLC0415

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(_ENDPOINT_URL).mock(
            return_value=httpx.Response(200, json=_ENDPOINT_GET_EMPTY_CONN_STRING)
        )
        mock_router.get(_LAKEHOUSES_URL).mock(
            return_value=httpx.Response(200, json=_LAKEHOUSES_WITH_MATCH_PAYLOAD)
        )

        client = await _make_client()
        async with client:
            with patch(
                "fabric_dw.services.sql_endpoints.asyncio.sleep",
                new=AsyncMock(),
            ) as mock_sleep:
                result = await get_endpoint_connection_string(
                    client,
                    _WORKSPACE_ID,
                    _ENDPOINT_ID,
                )

    assert result == _LAKEHOUSE_CONN_STRING
    mock_sleep.assert_not_called()
