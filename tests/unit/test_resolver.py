"""Tests for Resolver - written BEFORE the implementation (TDD)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential

from fabric_dw.cache import LookupCache
from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import WarehouseKind
from fabric_dw.resolver import (
    _ITEM_TYPE_INFO,
    _ITEM_TYPES,
    _NEGATIVE_TTL,
    ItemTypeInfo,
    Resolver,
    _odata_escape,
)

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


async def test_workspace_id_name_not_found(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(200, json=_pbi_empty_response())
        )
        async with client:
            with pytest.raises(NotFoundError, match="workspace"):
                await resolver.workspace_id("NonExistentWorkspace")


async def test_workspace_id_name_multiple_matches_raises_fabric_error(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(200, json=_pbi_multi_response())
        )
        async with client:
            with pytest.raises(FabricError, match=WS_GUID):
                await resolver.workspace_id("Ambiguous")


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


async def test_item_name_not_found(tmp_path: Path) -> None:
    resolver, client, _ = _make_resolver(tmp_path)
    # Return a list with no matching items
    list_payload = _items_list_payload(
        _warehouse_list_item(ITEM_GUID, "OtherWarehouse"),
    )
    with respx.mock:
        respx.get(_FABRIC_ITEMS_URL).mock(return_value=httpx.Response(200, json=list_payload))
        async with client:
            with pytest.raises(NotFoundError):
                await resolver.item(WS_GUID, "NonExistentWarehouse")


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


# ---------------------------------------------------------------------------
# Negative cache: repeated NotFoundError avoids API call
# ---------------------------------------------------------------------------


async def test_workspace_negative_cache_avoids_second_api_call(tmp_path: Path) -> None:
    """Second lookup of a missing workspace must not hit the API (negative cache)."""
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        route = respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(200, json=_pbi_empty_response())
        )
        async with client:
            with pytest.raises(NotFoundError):
                await resolver.workspace_id("GhostWorkspace")
            # Second call: negative cache should fire before the HTTP route
            with pytest.raises(NotFoundError):
                await resolver.workspace_id("GhostWorkspace")
    # The real API should only have been called once
    assert route.call_count == 1, "negative cache must suppress the second API call"


async def test_workspace_negative_cache_clears_on_success(tmp_path: Path) -> None:
    """After a successful lookup the negative cache entry must be cleared.

    This test verifies that when workspace_id() finds the workspace via the API
    (positive result), it calls _negative_clear() and removes any stale negative
    entry for that key — WITHOUT relying on manual cache manipulation.
    """
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        # First call: not found → recorded in negative cache
        route = respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(200, json=_pbi_empty_response())
        )
        async with client:
            with pytest.raises(NotFoundError):
                await resolver.workspace_id("GhostWorkspace")

        # Verify the entry was recorded
        assert ("workspace", "ghostworkspace") in resolver._negative

        # Now the workspace "appears" — but the negative TTL (5s) is still active,
        # so we back-date the negative entry timestamp to simulate TTL expiry.
        resolver._negative[("workspace", "ghostworkspace")] = time.monotonic() - 10.0

        route.side_effect = None  # type: ignore[attr-defined]
        route.mock(
            return_value=httpx.Response(200, json=_pbi_group_response(WS_GUID, "GhostWorkspace"))
        )
        async with client:
            result = await resolver.workspace_id("GhostWorkspace")

    # Successful lookup must have cleared the negative entry
    assert ("workspace", "ghostworkspace") not in resolver._negative
    assert result == WS_UUID


async def test_item_negative_cache_avoids_second_api_call(tmp_path: Path) -> None:
    """Second lookup of a missing item must not page through items again."""
    resolver, client, _ = _make_resolver(tmp_path)
    # Items list returns nothing matching the name
    list_payload = _items_list_payload(_warehouse_list_item(ITEM_GUID, "OtherWarehouse"))
    with respx.mock:
        route = respx.get(_FABRIC_ITEMS_URL).mock(
            return_value=httpx.Response(200, json=list_payload)
        )
        async with client:
            with pytest.raises(NotFoundError):
                await resolver.item(WS_GUID, "GhostItem")
            # Second call: negative cache should fire before hitting the list endpoint
            with pytest.raises(NotFoundError):
                await resolver.item(WS_GUID, "GhostItem")
    assert route.call_count == 1, "negative cache must suppress the second items-list call"


# ---------------------------------------------------------------------------
# _odata_escape: length guard
# ---------------------------------------------------------------------------


def test_odata_escape_rejects_oversized_value() -> None:
    """_odata_escape must raise FabricError for values exceeding _ODATA_MAX_LEN."""
    ok_value = "A" * 256  # exactly at the limit
    assert _odata_escape(ok_value) == ok_value  # must not raise

    too_long = "A" * 257
    with pytest.raises(FabricError, match="exceeds 256 characters"):
        _odata_escape(too_long)


async def test_workspace_id_oversized_name_raises_fabric_error(tmp_path: Path) -> None:
    """workspace_id must raise FabricError (not ValueError) for names exceeding _ODATA_MAX_LEN."""
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        async with client:
            with pytest.raises(FabricError, match="exceeds 256 characters"):
                await resolver.workspace_id("A" * 257)


# ---------------------------------------------------------------------------
# Type filter passed to iter_paginated
# ---------------------------------------------------------------------------


async def test_item_name_passes_type_param_to_items_api(tmp_path: Path) -> None:
    """item() with item_type kwarg must send type= query param to the items API."""
    resolver, client, _ = _make_resolver(tmp_path)
    list_payload = _items_list_payload(_warehouse_list_item(ITEM_GUID, "SalesWarehouse"))
    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        list_route = respx.get(_FABRIC_ITEMS_URL).mock(
            return_value=httpx.Response(200, json=list_payload)
        )
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=generic_payload))
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/warehouses/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
        async with client:
            result = await resolver.item(WS_GUID, "SalesWarehouse", item_type="Warehouse")
    assert result.id == ITEM_UUID
    # Verify the request carried the type query parameter
    assert list_route.call_count == 1
    called_url = list_route.calls[0].request.url
    assert "type=Warehouse" in str(called_url) or "type" in called_url.params


async def test_item_name_no_type_param_when_item_type_not_provided(tmp_path: Path) -> None:
    """item() without item_type must not send a type= param to the items API."""
    resolver, client, _ = _make_resolver(tmp_path)
    list_payload = _items_list_payload(_warehouse_list_item(ITEM_GUID, "SalesWarehouse"))
    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        list_route = respx.get(_FABRIC_ITEMS_URL).mock(
            return_value=httpx.Response(200, json=list_payload)
        )
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=generic_payload))
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/warehouses/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
        async with client:
            await resolver.item(WS_GUID, "SalesWarehouse")
    called_url = list_route.calls[0].request.url
    assert "type" not in str(called_url.params)


# ---------------------------------------------------------------------------
# put_items: batch write (single lock cycle) via _fetch_item_detail
# ---------------------------------------------------------------------------


async def test_fetch_item_detail_uses_put_items(tmp_path: Path) -> None:
    """_fetch_item_detail must call put_items (not two separate put_item calls)."""
    resolver, client, cache = _make_resolver(tmp_path)
    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")

    original_put_items = cache.put_items

    put_items_spy = MagicMock(wraps=original_put_items)
    put_item_spy = MagicMock(wraps=lambda *_a: None)

    with (
        respx.mock,
        patch.object(cache, "put_items", put_items_spy),
        patch.object(cache, "put_item", put_item_spy),
    ):
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=generic_payload))
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/warehouses/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
        async with client:
            await resolver.item(WS_GUID, ITEM_GUID)

    assert put_items_spy.call_count == 1, "must use put_items (single write cycle)"
    assert put_item_spy.call_count == 0, "must NOT fall back to individual put_item calls"


# ---------------------------------------------------------------------------
# Negative cache: TTL constant, expired-entry pruning, clear-on-put
# ---------------------------------------------------------------------------


def test_negative_ttl_is_five_seconds() -> None:
    """_NEGATIVE_TTL must be 5 seconds (not 60) to avoid masking newly created items."""
    assert _NEGATIVE_TTL == 5.0, (
        f"_NEGATIVE_TTL should be 5.0 s but is {_NEGATIVE_TTL}; "
        "a longer window masks newly created resources in the MCP singleton"
    )


async def test_negative_cache_expired_entry_pruned_on_check(tmp_path: Path) -> None:
    """_negative_check must prune expired entries to keep the dict bounded."""
    resolver, client, _ = _make_resolver(tmp_path)
    # Manually insert an expired negative entry
    resolver._negative[("workspace", "ghost")] = time.monotonic() - (_NEGATIVE_TTL + 1)
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(200, json=_pbi_group_response(WS_GUID, "ghost"))
        )
        async with client:
            # Should NOT raise NotFoundError (entry is expired), and must prune it
            result = await resolver.workspace_id("ghost")
    assert result == WS_UUID
    # Expired entry must have been pruned
    assert ("workspace", "ghost") not in resolver._negative


async def test_clear_negative_cache_empties_all_entries(tmp_path: Path) -> None:
    """clear_negative_cache() must discard all in-memory negative entries."""
    resolver, client, _ = _make_resolver(tmp_path)
    with respx.mock:
        respx.get(_PBI_GROUPS_URL).mock(
            return_value=httpx.Response(200, json=_pbi_empty_response())
        )
        async with client:
            with pytest.raises(NotFoundError):
                await resolver.workspace_id("Ghost1")
            with pytest.raises(NotFoundError):
                await resolver.workspace_id("Ghost2")
    assert len(resolver._negative) == 2
    resolver.clear_negative_cache()
    assert len(resolver._negative) == 0


async def test_item_fetch_clears_workspace_negative_entries(tmp_path: Path) -> None:
    """After _fetch_item_detail succeeds, negative entries for that workspace are cleared."""
    resolver, client, _ = _make_resolver(tmp_path)
    # Pre-seed negative cache entries for the workspace scope
    ws_key = str(WS_UUID)
    resolver._negative[(ws_key, "saleswarehouse")] = time.monotonic()
    resolver._negative[(ws_key, "otherwarehouse")] = time.monotonic()
    # An unrelated workspace entry must not be touched
    resolver._negative[("otherws", "something")] = time.monotonic()

    generic_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    specific_payload = _warehouse_detail_payload(ITEM_GUID, WS_GUID, "SalesWarehouse")
    with respx.mock:
        respx.get(_FABRIC_ITEM_URL).mock(return_value=httpx.Response(200, json=generic_payload))
        respx.get(
            f"https://api.fabric.microsoft.com/v1/workspaces/{WS_GUID}/warehouses/{ITEM_GUID}"
        ).mock(return_value=httpx.Response(200, json=specific_payload))
        async with client:
            await resolver.item(WS_GUID, ITEM_GUID)

    # Workspace-scoped negative entries must have been cleared
    assert (ws_key, "saleswarehouse") not in resolver._negative
    assert (ws_key, "otherwarehouse") not in resolver._negative
    # Unrelated workspace entries must be untouched
    assert ("otherws", "something") in resolver._negative


# ---------------------------------------------------------------------------
# _ITEM_TYPE_INFO consolidation
# ---------------------------------------------------------------------------


def test_item_type_info_covers_all_known_types() -> None:
    """_ITEM_TYPE_INFO must cover Warehouse, SQLEndpoint, and WarehouseSnapshot."""
    assert "Warehouse" in _ITEM_TYPE_INFO
    assert "SQLEndpoint" in _ITEM_TYPE_INFO
    assert "WarehouseSnapshot" in _ITEM_TYPE_INFO
    # _ITEM_TYPES must be derived from _ITEM_TYPE_INFO keys
    assert frozenset(_ITEM_TYPE_INFO) == _ITEM_TYPES


def test_item_type_info_namedtuple_fields() -> None:
    """ItemTypeInfo must expose kind and endpoint attributes."""
    wh = _ITEM_TYPE_INFO["Warehouse"]
    assert isinstance(wh, ItemTypeInfo)
    assert wh.kind == WarehouseKind.WAREHOUSE
    assert wh.endpoint == "warehouses"

    snap = _ITEM_TYPE_INFO["WarehouseSnapshot"]
    assert snap.endpoint is None  # no type-specific endpoint


# ---------------------------------------------------------------------------
# D22 -- reject invalid item_type with a clear FabricError
# ---------------------------------------------------------------------------


class TestD22InvalidItemType:
    """D22: resolver.item() must raise FabricError immediately for unknown item_type."""

    @pytest.mark.asyncio
    async def test_invalid_item_type_raises_fabric_error_name_path(self, tmp_path: Path) -> None:
        """Passing an unknown item_type by name must raise FabricError before any API call."""
        from fabric_dw.cache import LookupCache  # noqa: PLC0415
        from fabric_dw.exceptions import FabricError  # noqa: PLC0415

        cache = LookupCache(path=tmp_path / "lookup.json")
        # Use a minimal mock for the HTTP client -- it must NOT be called.
        mock_http = MagicMock()
        mock_http.iter_paginated = AsyncMock()
        resolver = Resolver(http=mock_http, cache=cache)

        ws_guid = "00000000-0000-0000-0000-000000000001"

        with pytest.raises(FabricError, match="Unknown item_type"):
            await resolver.item(ws_guid, "MyWarehouse", item_type="NotARealType")

        # The HTTP layer must NOT have been called -- error raised before pagination.
        mock_http.iter_paginated.assert_not_called()
