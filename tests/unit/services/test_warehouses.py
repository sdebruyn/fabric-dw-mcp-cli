"""Tests for fabric_dw.services.warehouses — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import time
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
from fabric_dw.services import warehouses
from tests.fixtures.api_payloads import (
    LAKEHOUSE_GET_PAYLOAD,
    WAREHOUSE_CREATE_202_PAYLOAD,
    WAREHOUSE_GET_PAYLOAD,
    WAREHOUSE_LIST_PAGE2_PAYLOAD,
    WAREHOUSE_LIST_PAYLOAD,
    WAREHOUSE_OPERATION_SUCCEEDED_NO_LOCATION_PAYLOAD,
    WAREHOUSE_OPERATION_SUCCEEDED_PAYLOAD,
    WAREHOUSE_SQL_ENDPOINTS_PAGE1_PAYLOAD,
    WAREHOUSE_SQL_ENDPOINTS_PAGE2_PAYLOAD,
    WAREHOUSE_SQL_ENDPOINTS_PAYLOAD,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106

_WORKSPACE_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_WAREHOUSE_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
_SQL_ENDPOINT_ID = UUID("e5f6a7b8-c9d0-1234-ef01-234567890abc")

_BASE = "https://api.fabric.microsoft.com/v1"
_WAREHOUSES_URL = f"{_BASE}/workspaces/{_WORKSPACE_ID}/warehouses"
_SQL_ENDPOINTS_URL = f"{_BASE}/workspaces/{_WORKSPACE_ID}/sqlEndpoints"
_WAREHOUSE_URL = f"{_WAREHOUSES_URL}/{_WAREHOUSE_ID}"
_ITEMS_URL = f"{_BASE}/workspaces/{_WORKSPACE_ID}/items"
_OPERATION_URL = f"{_BASE}/operations/op-abc123"


def _make_credential(token: AccessToken = _FAKE_TOKEN) -> AsyncTokenCredential:
    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(return_value=token)
    return cred


async def _make_client(rps: int = 10) -> FabricHttpClient:
    return FabricHttpClient(credential=_make_credential(), rps=rps)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_merges_warehouses_and_sql_endpoints() -> None:
    """list_warehouses must combine items from /warehouses and /sqlEndpoints with correct kind."""
    wh_payload = json.loads(WAREHOUSE_LIST_PAYLOAD)
    wh_payload.pop("continuationUri", None)  # single-page response
    ep_payload = json.loads(WAREHOUSE_SQL_ENDPOINTS_PAYLOAD)

    with respx.mock:
        respx.get(_WAREHOUSES_URL).mock(return_value=httpx.Response(200, json=wh_payload))
        respx.get(_SQL_ENDPOINTS_URL).mock(return_value=httpx.Response(200, json=ep_payload))

        client = await _make_client()
        async with client:
            result = await warehouses.list_warehouses(client, _WORKSPACE_ID)

    assert len(result) == 3  # 2 warehouses + 1 sql endpoint
    kinds = {item.kind for item in result}
    assert WarehouseKind.WAREHOUSE in kinds
    assert WarehouseKind.SQL_ENDPOINT in kinds

    wh_items = [i for i in result if i.kind == WarehouseKind.WAREHOUSE]
    ep_items = [i for i in result if i.kind == WarehouseKind.SQL_ENDPOINT]
    assert len(wh_items) == 2
    assert len(ep_items) == 1


@pytest.mark.asyncio
async def test_list_follows_continuation_uri_for_warehouses() -> None:
    """list_warehouses must follow continuationUri for the warehouses endpoint."""
    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        url = str(request.url)
        if "sqlEndpoints" in url:
            return httpx.Response(200, json={"value": []})
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=json.loads(WAREHOUSE_LIST_PAYLOAD))
        return httpx.Response(200, json=json.loads(WAREHOUSE_LIST_PAGE2_PAYLOAD))

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(url__regex=r".*/workspaces/.*/warehouses.*").mock(side_effect=side_effect)
        mock_router.get(url__regex=r".*/workspaces/.*/sqlEndpoints.*").mock(
            return_value=httpx.Response(200, json={"value": []})
        )

        client = await _make_client()
        async with client:
            result = await warehouses.list_warehouses(client, _WORKSPACE_ID)

    assert call_count == 2
    assert len(result) == 3  # 2 from page 1 + 1 from page 2 (all warehouses, no endpoints)


@pytest.mark.asyncio
async def test_list_follows_continuation_uri_for_sql_endpoints() -> None:
    """list_warehouses must follow continuationUri for the sqlEndpoints endpoint."""
    ep_call_count = 0

    def ep_side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal ep_call_count
        ep_call_count += 1
        if ep_call_count == 1:
            return httpx.Response(200, json=json.loads(WAREHOUSE_SQL_ENDPOINTS_PAGE1_PAYLOAD))
        return httpx.Response(200, json=json.loads(WAREHOUSE_SQL_ENDPOINTS_PAGE2_PAYLOAD))

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(url__regex=r".*/workspaces/.*/warehouses.*").mock(
            return_value=httpx.Response(200, json={"value": []})
        )
        mock_router.get(url__regex=r".*/workspaces/.*/sqlEndpoints.*").mock(
            side_effect=ep_side_effect
        )

        client = await _make_client()
        async with client:
            result = await warehouses.list_warehouses(client, _WORKSPACE_ID)

    assert ep_call_count == 2
    ep_items = [i for i in result if i.kind == WarehouseKind.SQL_ENDPOINT]
    assert len(ep_items) == 2  # 1 from page 1 + 1 from page 2


@pytest.mark.asyncio
async def test_list_all_items_are_warehouse_instances() -> None:
    """list_warehouses must return only Warehouse instances."""
    wh_payload = json.loads(WAREHOUSE_LIST_PAYLOAD)
    # Remove continuation for simplicity
    wh_payload.pop("continuationUri", None)

    with respx.mock:
        respx.get(_WAREHOUSES_URL).mock(return_value=httpx.Response(200, json=wh_payload))
        respx.get(_SQL_ENDPOINTS_URL).mock(return_value=httpx.Response(200, json={"value": []}))

        client = await _make_client()
        async with client:
            result = await warehouses.list_warehouses(client, _WORKSPACE_ID)

    assert all(isinstance(item, Warehouse) for item in result)


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_populated_warehouse() -> None:
    """get_warehouse must return a single populated Warehouse with WAREHOUSE kind."""
    wh_payload = json.loads(WAREHOUSE_GET_PAYLOAD)

    with respx.mock:
        respx.get(_WAREHOUSE_URL).mock(return_value=httpx.Response(200, json=wh_payload))

        client = await _make_client()
        async with client:
            result = await warehouses.get_warehouse(client, _WORKSPACE_ID, _WAREHOUSE_ID)

    assert isinstance(result, Warehouse)
    assert result.id == _WAREHOUSE_ID
    assert result.name == "SalesWarehouse"
    assert result.kind == WarehouseKind.WAREHOUSE
    assert result.connection_string == "saleswarehouse.datawarehouse.fabric.microsoft.com"
    assert result.collation == "Latin1_General_100_BIN2_UTF8"


@pytest.mark.asyncio
async def test_get_not_found_propagates() -> None:
    """get_warehouse must propagate NotFound on a 404 response."""
    with respx.mock:
        respx.get(_WAREHOUSE_URL).mock(
            return_value=httpx.Response(404, json={"error": {"code": "ItemNotFound"}})
        )

        client = await _make_client()
        async with client:
            with pytest.raises(NotFound):
                await warehouses.get_warehouse(client, _WORKSPACE_ID, _WAREHOUSE_ID)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_with_collation_polls_lro_and_returns_warehouse() -> None:
    """create must POST with collation, poll the LRO, then GET and return the warehouse."""
    create_resp = json.loads(WAREHOUSE_CREATE_202_PAYLOAD)
    op_succeeded = json.loads(WAREHOUSE_OPERATION_SUCCEEDED_PAYLOAD)
    wh_payload = json.loads(WAREHOUSE_GET_PAYLOAD)

    # The new warehouse ID returned by the LRO
    new_wh_id = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
    new_wh_url = f"{_WAREHOUSES_URL}/{new_wh_id}"

    with respx.mock:
        # POST to create
        post_route = respx.post(_ITEMS_URL).mock(
            return_value=httpx.Response(
                202,
                json=create_resp,
                headers={"Location": _OPERATION_URL},
            )
        )
        # Poll LRO operation
        respx.get(_OPERATION_URL).mock(return_value=httpx.Response(200, json=op_succeeded))
        # Final GET
        respx.get(new_wh_url).mock(return_value=httpx.Response(200, json=wh_payload))

        client = await _make_client()
        async with client:
            result = await warehouses.create(
                client,
                _WORKSPACE_ID,
                "SalesWarehouse",
                collation="Latin1_General_100_BIN2_UTF8",
            )

    assert isinstance(result, Warehouse)
    assert result.id == new_wh_id
    assert result.kind == WarehouseKind.WAREHOUSE

    # Verify the POST body included creationPayload with collation
    sent_body = json.loads(post_route.calls[0].request.content)
    assert sent_body["type"] == "Warehouse"
    assert sent_body["displayName"] == "SalesWarehouse"
    assert sent_body["creationPayload"]["defaultCollation"] == "Latin1_General_100_BIN2_UTF8"


@pytest.mark.asyncio
async def test_create_without_collation_omits_creation_payload() -> None:
    """create without collation must omit creationPayload from the POST body."""
    op_succeeded = json.loads(WAREHOUSE_OPERATION_SUCCEEDED_PAYLOAD)
    wh_payload = json.loads(WAREHOUSE_GET_PAYLOAD)
    new_wh_id = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
    new_wh_url = f"{_WAREHOUSES_URL}/{new_wh_id}"

    with respx.mock:
        post_route = respx.post(_ITEMS_URL).mock(
            return_value=httpx.Response(
                202,
                json={},
                headers={"Location": _OPERATION_URL},
            )
        )
        respx.get(_OPERATION_URL).mock(return_value=httpx.Response(200, json=op_succeeded))
        respx.get(new_wh_url).mock(return_value=httpx.Response(200, json=wh_payload))

        client = await _make_client()
        async with client:
            result = await warehouses.create(client, _WORKSPACE_ID, "SalesWarehouse")

    assert isinstance(result, Warehouse)
    sent_body = json.loads(post_route.calls[0].request.content)
    assert "creationPayload" not in sent_body


@pytest.mark.asyncio
async def test_create_with_description() -> None:
    """create with description must include it in the POST body."""
    op_succeeded = json.loads(WAREHOUSE_OPERATION_SUCCEEDED_PAYLOAD)
    wh_payload = json.loads(WAREHOUSE_GET_PAYLOAD)
    new_wh_id = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
    new_wh_url = f"{_WAREHOUSES_URL}/{new_wh_id}"

    with respx.mock:
        post_route = respx.post(_ITEMS_URL).mock(
            return_value=httpx.Response(
                202,
                json={},
                headers={"Location": _OPERATION_URL},
            )
        )
        respx.get(_OPERATION_URL).mock(return_value=httpx.Response(200, json=op_succeeded))
        respx.get(new_wh_url).mock(return_value=httpx.Response(200, json=wh_payload))

        client = await _make_client()
        async with client:
            result = await warehouses.create(
                client,
                _WORKSPACE_ID,
                "SalesWarehouse",
                description="My warehouse",
            )

    assert isinstance(result, Warehouse)
    sent_body = json.loads(post_route.calls[0].request.content)
    assert sent_body["description"] == "My warehouse"


@pytest.mark.asyncio
async def test_create_invalid_collation_raises_value_error() -> None:
    """create must raise ValueError for unsupported collation before any HTTP call."""
    with respx.mock:  # any HTTP call here is a bug
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="collation"):
                await warehouses.create(
                    client,
                    _WORKSPACE_ID,
                    "SalesWarehouse",
                    collation="SQL_Latin1_General_CP1_CI_AS",
                )


@pytest.mark.asyncio
async def test_create_empty_name_raises_value_error() -> None:
    """create must raise ValueError for an empty name before any HTTP call."""
    with respx.mock:  # any HTTP call here is a bug
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="name"):
                await warehouses.create(client, _WORKSPACE_ID, "")


@pytest.mark.asyncio
async def test_create_whitespace_only_name_raises_value_error() -> None:
    """create must raise ValueError for a whitespace-only name before any HTTP call."""
    with respx.mock:  # any HTTP call here is a bug
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="name"):
                await warehouses.create(client, _WORKSPACE_ID, "   ")


@pytest.mark.asyncio
async def test_create_missing_resource_location_raises_fabric_server_error() -> None:
    """create must raise FabricServerError when LRO completes with null resourceLocation."""
    op_no_location = json.loads(WAREHOUSE_OPERATION_SUCCEEDED_NO_LOCATION_PAYLOAD)

    with respx.mock:
        respx.post(_ITEMS_URL).mock(
            return_value=httpx.Response(
                202,
                json={},
                headers={"Location": _OPERATION_URL},
            )
        )
        respx.get(_OPERATION_URL).mock(return_value=httpx.Response(200, json=op_no_location))

        client = await _make_client()
        async with client:
            with pytest.raises(FabricServerError, match="resourceLocation"):
                await warehouses.create(client, _WORKSPACE_ID, "SalesWarehouse")


@pytest.mark.asyncio
async def test_create_no_location_header_but_body_present_returns_warehouse() -> None:
    """create must do a follow-up GET when 201 body is present (no Location header).

    The 201 body never includes properties.connectionString, so a GET is required
    to return a fully-populated Warehouse (regression: used to return connection_string=None).
    """
    # Fabric sometimes responds 201 with the new warehouse directly in the body
    body = json.loads(WAREHOUSE_CREATE_202_PAYLOAD)  # has id + displayName, no connectionString
    wh_payload = json.loads(WAREHOUSE_GET_PAYLOAD)  # GET returns connectionString

    new_wh_id = _WAREHOUSE_ID
    new_wh_url = f"{_WAREHOUSES_URL}/{new_wh_id}"

    with respx.mock:
        respx.post(_ITEMS_URL).mock(
            # 201, no Location header, body contains id + displayName
            return_value=httpx.Response(201, json=body)
        )
        # Follow-up GET must be made to populate connectionString
        respx.get(new_wh_url).mock(return_value=httpx.Response(200, json=wh_payload))

        client = await _make_client()
        async with client:
            result = await warehouses.create(client, _WORKSPACE_ID, "SalesWarehouse")

    assert isinstance(result, Warehouse)
    assert result.id == _WAREHOUSE_ID
    assert result.name == "SalesWarehouse"
    assert result.kind == WarehouseKind.WAREHOUSE
    # connection_string must be populated by the follow-up GET
    assert result.connection_string == "saleswarehouse.datawarehouse.fabric.microsoft.com"


@pytest.mark.asyncio
async def test_create_no_location_header_and_empty_body_raises_fabric_server_error() -> None:
    """create must raise FabricServerError when no Location header and body is empty."""
    with respx.mock:
        respx.post(_ITEMS_URL).mock(
            return_value=httpx.Response(202, json={})  # no Location header, empty body
        )

        client = await _make_client()
        async with client:
            with pytest.raises(FabricServerError, match="no Location header and no usable body"):
                await warehouses.create(client, _WORKSPACE_ID, "SalesWarehouse")


@pytest.mark.asyncio
async def test_create_existing_path_with_location_header_still_works() -> None:
    """create must still work correctly (LRO poll + final GET) when a Location header is present."""
    op_succeeded = json.loads(WAREHOUSE_OPERATION_SUCCEEDED_PAYLOAD)
    wh_payload = json.loads(WAREHOUSE_GET_PAYLOAD)
    new_wh_id = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
    new_wh_url = f"{_WAREHOUSES_URL}/{new_wh_id}"

    with respx.mock:
        respx.post(_ITEMS_URL).mock(
            return_value=httpx.Response(
                202,
                json={},
                headers={"Location": _OPERATION_URL},
            )
        )
        respx.get(_OPERATION_URL).mock(return_value=httpx.Response(200, json=op_succeeded))
        respx.get(new_wh_url).mock(return_value=httpx.Response(200, json=wh_payload))

        client = await _make_client()
        async with client:
            result = await warehouses.create(client, _WORKSPACE_ID, "SalesWarehouse")

    assert isinstance(result, Warehouse)
    assert result.id == new_wh_id
    assert result.kind == WarehouseKind.WAREHOUSE


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_returns_updated_warehouse() -> None:
    """rename must PATCH and return the updated Warehouse from the response body."""
    wh_payload = json.loads(WAREHOUSE_GET_PAYLOAD)
    updated = {**wh_payload, "displayName": "RenamedWarehouse"}

    with respx.mock:
        patch_route = respx.patch(_WAREHOUSE_URL).mock(
            return_value=httpx.Response(200, json=updated)
        )

        client = await _make_client()
        async with client:
            result = await warehouses.rename(
                client, _WORKSPACE_ID, _WAREHOUSE_ID, "RenamedWarehouse"
            )

    assert isinstance(result, Warehouse)
    assert result.name == "RenamedWarehouse"
    assert result.kind == WarehouseKind.WAREHOUSE

    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body["displayName"] == "RenamedWarehouse"
    assert "description" not in sent_body


@pytest.mark.asyncio
async def test_rename_with_description_includes_it_in_body() -> None:
    """rename with description must include it in the PATCH body."""
    wh_payload = json.loads(WAREHOUSE_GET_PAYLOAD)
    updated = {**wh_payload, "displayName": "RenamedWarehouse", "description": "New desc"}

    with respx.mock:
        patch_route = respx.patch(_WAREHOUSE_URL).mock(
            return_value=httpx.Response(200, json=updated)
        )

        client = await _make_client()
        async with client:
            result = await warehouses.rename(
                client,
                _WORKSPACE_ID,
                _WAREHOUSE_ID,
                "RenamedWarehouse",
                description="New desc",
            )

    assert isinstance(result, Warehouse)
    assert result.description == "New desc"

    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body["description"] == "New desc"


@pytest.mark.asyncio
async def test_rename_empty_name_raises_value_error() -> None:
    """rename must raise ValueError for an empty new_name before any HTTP call."""
    with respx.mock:  # any HTTP call here is a bug
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="name"):
                await warehouses.rename(client, _WORKSPACE_ID, _WAREHOUSE_ID, "")


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_204_returns_none() -> None:
    """delete must return None on 204 No Content."""
    with respx.mock:
        respx.delete(_WAREHOUSE_URL).mock(return_value=httpx.Response(204))

        client = await _make_client()
        async with client:
            # delete is typed -> None; just verify no exception is raised
            await warehouses.delete(client, _WORKSPACE_ID, _WAREHOUSE_ID)


@pytest.mark.asyncio
async def test_delete_404_propagates_not_found() -> None:
    """delete must propagate NotFound on a 404 response."""
    with respx.mock:
        respx.delete(_WAREHOUSE_URL).mock(
            return_value=httpx.Response(404, json={"error": {"code": "ItemNotFound"}})
        )

        client = await _make_client()
        async with client:
            with pytest.raises(NotFound):
                await warehouses.delete(client, _WORKSPACE_ID, _WAREHOUSE_ID)


# ---------------------------------------------------------------------------
# Fixture sanity: LAKEHOUSE_GET_PAYLOAD has sqlEndpointProperties
# ---------------------------------------------------------------------------


def test_lakehouse_fixture_has_sql_endpoint() -> None:
    """LAKEHOUSE_GET_PAYLOAD must contain sqlEndpointProperties for SQL_ENDPOINT kind tests."""
    payload = json.loads(LAKEHOUSE_GET_PAYLOAD)
    props = payload.get("properties", {})
    assert "sqlEndpointProperties" in props
    conn = props["sqlEndpointProperties"]["connectionString"]
    assert conn == "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com"


# ---------------------------------------------------------------------------
# list_all_workspaces
# ---------------------------------------------------------------------------


def _make_workspace(ws_id: UUID) -> Workspace:
    return Workspace.model_validate(
        {
            "id": str(ws_id),
            "displayName": f"WS-{ws_id}",
            "description": None,
            "capacityId": None,
        }
    )


def _make_wh(ws_id: UUID, wh_id: UUID) -> Warehouse:
    return Warehouse.model_validate(
        {
            "id": str(wh_id),
            "displayName": "WH",
            "workspaceId": str(ws_id),
            "kind": WarehouseKind.WAREHOUSE,
            "connectionString": "wh.fabric.microsoft.com",
        }
    )


_WS_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_WS_B = UUID("bbbbbbbb-0000-0000-0000-000000000002")
_WS_C = UUID("cccccccc-0000-0000-0000-000000000003")
_WH_A = UUID("aaaaaaaa-1111-0000-0000-000000000001")
_WH_B = UUID("bbbbbbbb-1111-0000-0000-000000000002")
_WH_C = UUID("cccccccc-1111-0000-0000-000000000003")


@pytest.mark.asyncio
async def test_list_all_workspaces_aggregates_across_workspaces() -> None:
    """list_all_workspaces must collect warehouses from every visible workspace."""
    ws_a = _make_workspace(_WS_A)
    ws_b = _make_workspace(_WS_B)
    ws_c = _make_workspace(_WS_C)
    wh_a = _make_wh(_WS_A, _WH_A)
    wh_b = _make_wh(_WS_B, _WH_B)
    wh_c = _make_wh(_WS_C, _WH_C)

    mock_http = AsyncMock()

    with (
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(return_value=[ws_a, ws_b, ws_c]),
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(
                side_effect=[
                    [wh_a],
                    [wh_b],
                    [wh_c],
                ]
            ),
        ),
    ):
        result = await warehouses.list_all_workspaces(mock_http)

    assert len(result) == 3
    ids = {w.id for w in result}
    assert ids == {_WH_A, _WH_B, _WH_C}


@pytest.mark.asyncio
async def test_list_all_workspaces_skips_permission_denied() -> None:
    """list_all_workspaces must skip workspaces where PermissionDenied is raised."""
    ws_a = _make_workspace(_WS_A)
    ws_b = _make_workspace(_WS_B)
    ws_c = _make_workspace(_WS_C)
    wh_a = _make_wh(_WS_A, _WH_A)
    wh_c = _make_wh(_WS_C, _WH_C)

    mock_http = AsyncMock()

    with (
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(return_value=[ws_a, ws_b, ws_c]),
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(
                side_effect=[
                    [wh_a],
                    PermissionDenied("no access"),
                    [wh_c],
                ]
            ),
        ),
    ):
        result = await warehouses.list_all_workspaces(mock_http)

    assert len(result) == 2
    ids = {w.id for w in result}
    assert ids == {_WH_A, _WH_C}


@pytest.mark.asyncio
async def test_list_all_workspaces_skips_not_found() -> None:
    """list_all_workspaces must skip workspaces where NotFound is raised."""
    ws_a = _make_workspace(_WS_A)
    ws_b = _make_workspace(_WS_B)
    ws_c = _make_workspace(_WS_C)
    wh_a = _make_wh(_WS_A, _WH_A)
    wh_c = _make_wh(_WS_C, _WH_C)

    mock_http = AsyncMock()

    with (
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(return_value=[ws_a, ws_b, ws_c]),
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(
                side_effect=[
                    [wh_a],
                    NotFound("workspace gone"),
                    [wh_c],
                ]
            ),
        ),
    ):
        result = await warehouses.list_all_workspaces(mock_http)

    assert len(result) == 2
    ids = {w.id for w in result}
    assert ids == {_WH_A, _WH_C}
