"""Tests for Resolver - written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken, TokenCredential

from fabric_dw.cache import LookupCache
from fabric_dw.exceptions import FabricError, NotFound
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import WarehouseKind
from fabric_dw.resolver import Resolver

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WS_UUID = UUID(WS_GUID)
WS_GUID_2 = "b2c3d4e5-f6a7-8901-bcde-f01234567891"

ITEM_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
ITEM_UUID = UUID(ITEM_GUID)

# Power BI OData group endpoint
_PBI_GROUPS_URL = "https://api.powerbi.com/v1.0/myorg/groups"

# Fabric items listing endpoint
_FABRIC_ITEMS_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/items"
_FABRIC_ITEM_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/items/{ITEM_GUID}"


def _make_credential() -> TokenCredential:
    cred = MagicMock(spec=TokenCredential)
    cred.get_token = MagicMock(return_value=_FAKE_TOKEN)
    return cred


def _make_resolver(tmp_path: Path) -> tuple[Resolver, FabricHttpClient, LookupCache]:
    cache = LookupCache(path=tmp_path / "lookup.json")
    client = FabricHttpClient(credential=_make_credential(), rps=100)
    resolver = Resolver(http=client, cache=cache)
    return resolver, client, cache


def _pbi_group_response(ws_id: str, name: str) -> dict[str, object]:
    return {"value": [{"id": ws_id, "name": name, "type": "Workspace"}]}


def _pbi_empty_response() -> dict[str, object]:
    return {"value": []}


def _pbi_multi_response() -> dict[str, object]:
    return {
        "value": [
            {"id": WS_GUID, "name": "Ambiguous", "type": "Workspace"},
            {"id": WS_GUID_2, "name": "Ambiguous", "type": "Workspace"},
        ]
    }


def _warehouse_detail_payload(item_id: str, ws_id: str, name: str) -> dict[str, object]:
    return {
        "id": item_id,
        "displayName": name,
        "type": "Warehouse",
        "workspaceId": ws_id,
        "properties": {
            "connectionString": "mywarehouse.datawarehouse.fabric.microsoft.com",
        },
    }


def _sql_endpoint_detail_payload(item_id: str, ws_id: str, name: str) -> dict[str, object]:
    return {
        "id": item_id,
        "displayName": name,
        "type": "SQLEndpoint",
        "workspaceId": ws_id,
        "properties": {
            "sqlEndpointProperties": {
                "connectionString": "mysqlep.datawarehouse.fabric.microsoft.com",
                "id": "deadbeef-dead-beef-dead-beef00000001",
                "provisioningStatus": "Success",
            }
        },
    }


def _items_list_payload(*items: dict[str, object]) -> dict[str, object]:
    return {"value": list(items), "continuationUri": None}


def _warehouse_list_item(item_id: str, name: str) -> dict[str, object]:
    return {
        "id": item_id,
        "displayName": name,
        "type": "Warehouse",
        "workspaceId": WS_GUID,
    }


def _sql_endpoint_list_item(item_id: str, name: str) -> dict[str, object]:
    return {
        "id": item_id,
        "displayName": name,
        "type": "SQLEndpoint",
        "workspaceId": WS_GUID,
    }


# ---------------------------------------------------------------------------
# workspace_id: GUID input bypasses API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_id_guid_no_api_call(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        async with client:
            result = await resolver.workspace_id(WS_GUID)
        # No routes should have been called
        assert result == WS_UUID
        assert len(respx.calls) == 0


# ---------------------------------------------------------------------------
# workspace_id: name input hits Power BI OData
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_id_name_hits_api(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(
                200, json=_pbi_group_response(WS_GUID, "AnalyticsWorkspace")
            )
        )
        async with client:
            result = await resolver.workspace_id("AnalyticsWorkspace")
    assert result == WS_UUID


@pytest.mark.asyncio
async def test_workspace_id_name_cached_after_first_call(tmp_path: Path) -> None:
    resolver, client, cache = _make_resolver(tmp_path)
    with respx.mock:
        route = respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(
                200, json=_pbi_group_response(WS_GUID, "AnalyticsWorkspace")
            )
        )
        async with client:
            first = await resolver.workspace_id("AnalyticsWorkspace")
            # Second call should hit cache (not API)
            second = await resolver.workspace_id("AnalyticsWorkspace")
        # Only one HTTP call should have been made
        assert route.call_count == 1
    assert first == second == WS_UUID


@pytest.mark.asyncio
async def test_workspace_id_name_not_found(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(200, json=_pbi_empty_response())
        )
        async with client:
            with pytest.raises(NotFound, match="workspace"):
                await resolver.workspace_id("NonExistentWorkspace")


@pytest.mark.asyncio
async def test_workspace_id_name_multiple_matches_raises_fabric_error(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(200, json=_pbi_multi_response())
        )
        async with client:
            with pytest.raises(FabricError, match=WS_GUID):
                await resolver.workspace_id("Ambiguous")


@pytest.mark.asyncio
async def test_workspace_id_multiple_matches_error_mentions_all_ids(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(200, json=_pbi_multi_response())
        )
        async with client:
            with pytest.raises(FabricError) as exc_info:
                await resolver.workspace_id("Ambiguous")
    err_msg = str(exc_info.value)
    assert WS_GUID in err_msg
    assert WS_GUID_2 in err_msg


# ---------------------------------------------------------------------------
# item: GUID input fetches detail directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_item_guid_fetches_detail_endpoint(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        # workspace_id will be a GUID too, so no PBI call needed
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=payload))
        async with client:
            result = await resolver.item(WS_GUID, ITEM_GUID)
    assert result.id == ITEM_UUID
    assert result.kind == WarehouseKind.WAREHOUSE
    assert result.connection_string == "mywarehouse.datawarehouse.fabric.microsoft.com"


@pytest.mark.asyncio
async def test_item_guid_sql_endpoint_connection_string(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    payload = _sql_endpoint_detail_payload(ITEM_GUID, WS_GUID, "MySQLEndpoint")
    with respx.mock:
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=payload))
        async with client:
            result = await resolver.item(WS_GUID, ITEM_GUID)
    assert result.kind == WarehouseKind.SQL_ENDPOINT
    assert result.connection_string == "mysqlep.datawarehouse.fabric.microsoft.com"


# ---------------------------------------------------------------------------
# item: name input pages through /items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_item_name_pages_items_and_fetches_detail(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    list_payload = _items_list_payload(
        _warehouse_list_item(ITEM_GUID, "SalesWarehouse"),
    )
    detail_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        respx.get(_FABRIC_ITEMS_URL).mock(return_value=httpx.Response(200, json=list_payload))
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=detail_payload))
        async with client:
            result = await resolver.item(WS_GUID, "SalesWarehouse")
    assert result.id == ITEM_UUID
    assert result.kind == WarehouseKind.WAREHOUSE
    assert result.connection_string == "mywarehouse.datawarehouse.fabric.microsoft.com"


@pytest.mark.asyncio
async def test_item_name_case_insensitive_match(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    list_payload = _items_list_payload(
        _warehouse_list_item(ITEM_GUID, "SalesWarehouse"),
    )
    detail_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        respx.get(_FABRIC_ITEMS_URL).mock(return_value=httpx.Response(200, json=list_payload))
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=detail_payload))
        async with client:
            result = await resolver.item(WS_GUID, "saleswarehouse")
    assert result.id == ITEM_UUID


@pytest.mark.asyncio
async def test_item_name_not_found(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    # Return a list with no matching items
    list_payload = _items_list_payload(
        _warehouse_list_item(ITEM_GUID, "OtherWarehouse"),
    )
    with respx.mock:
        respx.get(_FABRIC_ITEMS_URL).mock(return_value=httpx.Response(200, json=list_payload))
        async with client:
            with pytest.raises(NotFound):
                await resolver.item(WS_GUID, "NonExistentWarehouse")


@pytest.mark.asyncio
async def test_item_name_cached_after_first_call(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    list_payload = _items_list_payload(
        _warehouse_list_item(ITEM_GUID, "SalesWarehouse"),
    )
    detail_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        list_route = respx.get(_FABRIC_ITEMS_URL).mock(
            return_value=httpx.Response(200, json=list_payload)
        )
        detail_route = respx.get(_FABRIC_ITEM_URL).mock(
            return_value=httpx.Response(200, json=detail_payload)
        )
        async with client:
            first = await resolver.item(WS_GUID, "SalesWarehouse")
            # Second call should hit cache
            second = await resolver.item(WS_GUID, "SalesWarehouse")
        assert list_route.call_count == 1
        assert detail_route.call_count == 1
    assert first.id == second.id


@pytest.mark.asyncio
async def test_item_name_sql_endpoint(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    list_payload = _items_list_payload(
        _sql_endpoint_list_item(ITEM_GUID, "MySQLEndpoint"),
    )
    detail_payload = _sql_endpoint_detail_payload(ITEM_GUID, WS_GUID, "MySQLEndpoint")
    with respx.mock:
        respx.get(_FABRIC_ITEMS_URL).mock(return_value=httpx.Response(200, json=list_payload))
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=detail_payload))
        async with client:
            result = await resolver.item(WS_GUID, "MySQLEndpoint")
    assert result.kind == WarehouseKind.SQL_ENDPOINT
    assert result.connection_string == "mysqlep.datawarehouse.fabric.microsoft.com"


@pytest.mark.asyncio
async def test_item_name_workspace_resolved_first(tmp_path: Path) -> None:
    """When workspace is given as a name, it gets resolved via PBI first."""
    resolver, client, _ = _make_resolver(tmp_path)
    list_payload = _items_list_payload(
        _warehouse_list_item(ITEM_GUID, "SalesWarehouse"),
    )
    detail_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(
                200, json=_pbi_group_response(WS_GUID, "AnalyticsWorkspace")
            )
        )
        respx.get(_FABRIC_ITEMS_URL).mock(return_value=httpx.Response(200, json=list_payload))
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=detail_payload))
        async with client:
            result = await resolver.item("AnalyticsWorkspace", "SalesWarehouse")
    assert result.id == ITEM_UUID
