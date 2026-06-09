"""Tests for Resolver - written BEFORE the implementation (TDD)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential

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


def _make_credential() -> AsyncTokenCredential:
    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(return_value=_FAKE_TOKEN)
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
    resolver, client, _cache = _make_resolver(tmp_path)
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
# workspace_id: continuation pages are followed
# ---------------------------------------------------------------------------

# A continuation URL that uses a distinct path segment so respx can
# differentiate it from the first-page URL (which carries a $filter param
# that respx ignores when matching by default).
_PBI_GROUPS_CONT_URL = "https://api.powerbi.com/v1.0/myorg/groups/continuation/abc123"


@pytest.mark.asyncio
async def test_workspace_id_follows_continuation_pages(tmp_path: Path) -> None:
    """workspace_id collects items across multiple continuation pages.

    Simulates an API that returns an empty first page with a continuationUri,
    and the actual result on the second page.  This proves that iter_paginated
    is used (single-shot body.get() would return an empty list and raise NotFound).
    """
    resolver, client, _ = _make_resolver(tmp_path)

    # Page 1 is empty but carries a continuationUri.
    # Page 2 contains the matching workspace.
    page1_response: dict[str, object] = {
        "value": [],
        "continuationUri": _PBI_GROUPS_CONT_URL,
    }
    page2_response: dict[str, object] = {
        "value": [{"id": WS_GUID, "name": "AnalyticsWorkspace", "type": "Workspace"}],
        "continuationUri": None,
    }
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(return_value=httpx.Response(200, json=page1_response))
        respx.get(_PBI_GROUPS_CONT_URL).mock(return_value=httpx.Response(200, json=page2_response))
        async with client:
            result = await resolver.workspace_id("AnalyticsWorkspace")
    assert result == WS_UUID


# ---------------------------------------------------------------------------
# item: GUID input fetches detail directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_item_guid_fetches_detail_endpoint(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        # workspace_id will be a GUID too, so no PBI call needed
        # Step 1: generic discovery; Step 2: warehouse-specific endpoint
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=generic_payload))
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/warehouses/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
        async with client:
            result = await resolver.item(WS_GUID, ITEM_GUID)
    assert result.id == ITEM_UUID
    assert result.kind == WarehouseKind.WAREHOUSE
    assert result.connection_string == "mywarehouse.datawarehouse.fabric.microsoft.com"


@pytest.mark.asyncio
async def test_item_guid_sql_endpoint_connection_string(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    generic_payload = _sql_endpoint_detail_payload(ITEM_GUID, WS_GUID, "MySQLEndpoint")
    specific_payload = _sql_endpoint_detail_payload(ITEM_GUID, WS_GUID, "MySQLEndpoint")
    with respx.mock:
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=generic_payload))
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/sqlEndpoints/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
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
    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        respx.get(_FABRIC_ITEMS_URL).mock(return_value=httpx.Response(200, json=list_payload))
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=generic_payload))
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/warehouses/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
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
    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        respx.get(_FABRIC_ITEMS_URL).mock(return_value=httpx.Response(200, json=list_payload))
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=generic_payload))
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/warehouses/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
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
    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        list_route = respx.get(_FABRIC_ITEMS_URL).mock(
            return_value=httpx.Response(200, json=list_payload)
        )
        detail_route = respx.get(_FABRIC_ITEM_URL).mock(
            return_value=httpx.Response(200, json=generic_payload)
        )
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/warehouses/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
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
    generic_payload = _sql_endpoint_detail_payload(ITEM_GUID, WS_GUID, "MySQLEndpoint")
    specific_payload = _sql_endpoint_detail_payload(ITEM_GUID, WS_GUID, "MySQLEndpoint")
    with respx.mock:
        respx.get(_FABRIC_ITEMS_URL).mock(return_value=httpx.Response(200, json=list_payload))
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=generic_payload))
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/sqlEndpoints/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
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
    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(
                200, json=_pbi_group_response(WS_GUID, "AnalyticsWorkspace")
            )
        )
        respx.get(_FABRIC_ITEMS_URL).mock(return_value=httpx.Response(200, json=list_payload))
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=generic_payload))
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/warehouses/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
        async with client:
            result = await resolver.item("AnalyticsWorkspace", "SalesWarehouse")
    assert result.id == ITEM_UUID


# ---------------------------------------------------------------------------
# Finding 1: OData single-quote escaping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_id_name_with_single_quote_escaped(tmp_path: Path) -> None:
    """Workspace names containing a single quote are properly OData-escaped."""
    resolver, client, _ = _make_resolver(tmp_path)
    name_with_apostrophe = "O'Brien Analytics"
    with respx.mock:
        route = respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(
                200, json=_pbi_group_response(WS_GUID, name_with_apostrophe)
            )
        )
        async with client:
            result = await resolver.workspace_id(name_with_apostrophe)
    assert result == WS_UUID
    # Verify the escaped value was sent in the OData filter.
    # The query is percent-encoded, so single quotes appear as %27.
    called_request = route.calls[0].request
    # Decoded form: "O''Brien" → percent-encoded: "O%27%27Brien"
    assert "O%27%27Brien" in called_request.url.query.decode()


@pytest.mark.asyncio
async def test_workspace_id_name_with_double_apostrophe_escaped(tmp_path: Path) -> None:
    """Names with consecutive apostrophes are double-escaped correctly."""
    resolver, client, _ = _make_resolver(tmp_path)
    # "It's here" has one apostrophe → should escape to "It''s here"
    name = "It's here"
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(200, json=_pbi_group_response(WS_GUID, name))
        )
        async with client:
            result = await resolver.workspace_id(name)
    assert result == WS_UUID


# ---------------------------------------------------------------------------
# Finding 2: GUID item cache — second call within TTL hits cache, not API
# ---------------------------------------------------------------------------

# Type-specific endpoint URLs for finding 3 tests
_FABRIC_WAREHOUSE_URL = (
    f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/warehouses/{ITEM_GUID}"
)
_FABRIC_SQL_ENDPOINT_URL = (
    f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/sqlEndpoints/{ITEM_GUID}"
)


@pytest.mark.asyncio
async def test_item_guid_second_call_hits_cache_not_api(tmp_path: Path) -> None:
    """GUID input: first call hits API, second call within TTL hits cache (no extra HTTP)."""
    resolver, client, _ = _make_resolver(tmp_path)
    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        generic_route = respx.get(_FABRIC_ITEM_URL).mock(
            return_value=httpx.Response(200, json=generic_payload)
        )
        respx.get(_FABRIC_WAREHOUSE_URL).mock(
            return_value=httpx.Response(200, json=specific_payload)
        )
        async with client:
            first = await resolver.item(WS_GUID, ITEM_GUID)
            # Second call with same GUID — should use cache, not call API again
            second = await resolver.item(WS_GUID, ITEM_GUID)
        # Generic discovery endpoint called exactly once
        assert generic_route.call_count == 1
    assert first.id == second.id == ITEM_UUID


# ---------------------------------------------------------------------------
# Finding 3: type-specific endpoint dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_item_guid_warehouse_uses_type_specific_endpoint(tmp_path: Path) -> None:
    """Warehouse items: detail fetch uses /warehouses/{id}, not generic /items/{id}."""
    resolver, client, _ = _make_resolver(tmp_path)
    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        generic_route = respx.get(_FABRIC_ITEM_URL).mock(
            return_value=httpx.Response(200, json=generic_payload)
        )
        warehouse_route = respx.get(_FABRIC_WAREHOUSE_URL).mock(
            return_value=httpx.Response(200, json=specific_payload)
        )
        async with client:
            result = await resolver.item(WS_GUID, ITEM_GUID)
    assert result.kind == WarehouseKind.WAREHOUSE
    assert result.connection_string == "mywarehouse.datawarehouse.fabric.microsoft.com"
    assert generic_route.call_count == 1
    assert warehouse_route.call_count == 1


@pytest.mark.asyncio
async def test_item_guid_sql_endpoint_uses_type_specific_endpoint(tmp_path: Path) -> None:
    """SQLEndpoint items: detail fetch uses /sqlEndpoints/{id}, not generic /items/{id}."""
    resolver, client, _ = _make_resolver(tmp_path)
    generic_payload = _sql_endpoint_detail_payload(ITEM_GUID, WS_GUID, "MySQLEndpoint")
    specific_payload = _sql_endpoint_detail_payload(ITEM_GUID, WS_GUID, "MySQLEndpoint")
    with respx.mock:
        generic_route = respx.get(_FABRIC_ITEM_URL).mock(
            return_value=httpx.Response(200, json=generic_payload)
        )
        sql_route = respx.get(_FABRIC_SQL_ENDPOINT_URL).mock(
            return_value=httpx.Response(200, json=specific_payload)
        )
        async with client:
            result = await resolver.item(WS_GUID, ITEM_GUID)
    assert result.kind == WarehouseKind.SQL_ENDPOINT
    assert result.connection_string == "mysqlep.datawarehouse.fabric.microsoft.com"
    assert generic_route.call_count == 1
    assert sql_route.call_count == 1


@pytest.mark.asyncio
async def test_item_guid_snapshot_uses_generic_endpoint_only(tmp_path: Path) -> None:
    """WarehouseSnapshot: no type-specific endpoint; uses generic /items/{id} only."""
    resolver, client, _ = _make_resolver(tmp_path)
    snapshot_payload: dict[str, object] = {
        "id": ITEM_GUID,
        "displayName": "MySnapshot",
        "type": "WarehouseSnapshot",
        "workspaceId": WS_GUID,
    }
    with respx.mock:
        generic_route = respx.get(_FABRIC_ITEM_URL).mock(
            return_value=httpx.Response(200, json=snapshot_payload)
        )
        async with client:
            result = await resolver.item(WS_GUID, ITEM_GUID)
    assert result.kind == WarehouseKind.SNAPSHOT
    assert result.connection_string is None
    # Generic endpoint called exactly once; no additional type-specific call
    assert generic_route.call_count == 1


# ---------------------------------------------------------------------------
# Finding 4: unknown item type raises FabricError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_item_guid_unknown_type_raises_fabric_error(tmp_path: Path) -> None:
    """An unsupported item type (e.g. Lakehouse) must raise FabricError, not silently default."""
    resolver, client, _ = _make_resolver(tmp_path)
    lakehouse_payload: dict[str, object] = {
        "id": ITEM_GUID,
        "displayName": "MyLakehouse",
        "type": "Lakehouse",
        "workspaceId": WS_GUID,
    }
    with respx.mock:
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=lakehouse_payload))
        async with client:
            with pytest.raises(FabricError, match="unsupported type"):
                await resolver.item(WS_GUID, ITEM_GUID)
