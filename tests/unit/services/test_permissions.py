"""Tests for fabric_dw.services.permissions — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from uuid import UUID

import httpx
import pytest
import respx

from fabric_dw.exceptions import NotFoundError, PermissionDeniedError
from fabric_dw.models import ItemAccess, PrincipalType
from fabric_dw.services import permissions
from tests.fixtures.api_payloads import (
    ITEM_ACCESS_DETAILS_PAGE1_PAYLOAD,
    ITEM_ACCESS_DETAILS_PAGE2_PAYLOAD,
    ITEM_ACCESS_DETAILS_PAYLOAD,
)
from tests.unit.services._helpers import _make_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE = "https://api.fabric.microsoft.com/v1"
_WORKSPACE_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_ITEM_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")

_ACCESS_URL = (
    f"{_BASE}/admin/workspaces/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    "/items/d4e5f6a7-b8c9-0123-def0-123456789abc/users"
)


# ---------------------------------------------------------------------------
# list_item_access — happy path (single page)
# ---------------------------------------------------------------------------


async def test_list_item_access_returns_all_principals() -> None:
    """list_item_access must return one ItemAccess per principal in the response."""
    payload = json.loads(ITEM_ACCESS_DETAILS_PAYLOAD)

    with respx.mock:
        respx.get(_ACCESS_URL).mock(return_value=httpx.Response(200, json=payload))

        client = await _make_client()
        async with client:
            result = await permissions.list_item_access(client, _WORKSPACE_ID, _ITEM_ID)

    assert len(result) == 3
    assert all(isinstance(item, ItemAccess) for item in result)


async def test_list_item_access_user_principal_fields() -> None:
    """User principal must have display_name, type=User, and user_principal_name populated."""
    payload = json.loads(ITEM_ACCESS_DETAILS_PAYLOAD)

    with respx.mock:
        respx.get(_ACCESS_URL).mock(return_value=httpx.Response(200, json=payload))

        client = await _make_client()
        async with client:
            result = await permissions.list_item_access(client, _WORKSPACE_ID, _ITEM_ID)

    user_items = [r for r in result if r.principal.type == PrincipalType.USER]
    assert len(user_items) == 1
    user = user_items[0].principal
    assert user.display_name == "Jacob Hancock"
    assert user.user_principal_name == "jacob@example.com"
    assert user.aad_app_id is None
    assert user.group_type is None


async def test_list_item_access_group_principal_fields() -> None:
    """Group principal must have group_type populated from groupDetails."""
    payload = json.loads(ITEM_ACCESS_DETAILS_PAYLOAD)

    with respx.mock:
        respx.get(_ACCESS_URL).mock(return_value=httpx.Response(200, json=payload))

        client = await _make_client()
        async with client:
            result = await permissions.list_item_access(client, _WORKSPACE_ID, _ITEM_ID)

    group_items = [r for r in result if r.principal.type == PrincipalType.GROUP]
    assert len(group_items) == 1
    group = group_items[0].principal
    assert group.display_name == "TestSecurityGroup"
    assert group.group_type == "SecurityGroup"
    assert group.user_principal_name is None


async def test_list_item_access_service_principal_fields() -> None:
    """ServicePrincipal principal must have aad_app_id populated from servicePrincipalDetails."""
    payload = json.loads(ITEM_ACCESS_DETAILS_PAYLOAD)

    with respx.mock:
        respx.get(_ACCESS_URL).mock(return_value=httpx.Response(200, json=payload))

        client = await _make_client()
        async with client:
            result = await permissions.list_item_access(client, _WORKSPACE_ID, _ITEM_ID)

    sp_items = [r for r in result if r.principal.type == PrincipalType.SERVICE_PRINCIPAL]
    assert len(sp_items) == 1
    sp = sp_items[0].principal
    assert sp.display_name == "MyServicePrincipal"
    assert sp.aad_app_id == UUID("b2c3d4e5-f6a7-8901-bcde-f01234567891")
    assert sp.user_principal_name is None


async def test_list_item_access_permissions_populated() -> None:
    """ItemAccess.item_access_details must have permissions and additional_permissions."""
    payload = json.loads(ITEM_ACCESS_DETAILS_PAYLOAD)

    with respx.mock:
        respx.get(_ACCESS_URL).mock(return_value=httpx.Response(200, json=payload))

        client = await _make_client()
        async with client:
            result = await permissions.list_item_access(client, _WORKSPACE_ID, _ITEM_ID)

    user_entry = next(r for r in result if r.principal.type == PrincipalType.USER)
    assert "Read" in user_entry.item_access_details.permissions
    assert "Write" in user_entry.item_access_details.permissions
    assert "ReadAll" in user_entry.item_access_details.additional_permissions
    assert user_entry.item_access_details.item_type == "Warehouse"


# ---------------------------------------------------------------------------
# list_item_access — empty response
# ---------------------------------------------------------------------------


async def test_list_item_access_empty_response_returns_empty_list() -> None:
    """list_item_access must return an empty list when accessDetails is empty."""
    with respx.mock:
        respx.get(_ACCESS_URL).mock(return_value=httpx.Response(200, json={"accessDetails": []}))

        client = await _make_client()
        async with client:
            result = await permissions.list_item_access(client, _WORKSPACE_ID, _ITEM_ID)

    assert result == []


# ---------------------------------------------------------------------------
# list_item_access — pagination
# ---------------------------------------------------------------------------


async def test_list_item_access_follows_continuation_uri() -> None:
    """list_item_access must follow continuationUri to fetch all pages."""
    page1 = json.loads(ITEM_ACCESS_DETAILS_PAGE1_PAYLOAD)
    page2 = json.loads(ITEM_ACCESS_DETAILS_PAGE2_PAYLOAD)

    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        url = str(request.url)
        if "continuationToken" in url:
            return httpx.Response(200, json=page2)
        return httpx.Response(200, json=page1)

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/admin/workspaces/.*/items/.*/users.*").mock(
            side_effect=side_effect
        )

        client = await _make_client()
        async with client:
            result = await permissions.list_item_access(client, _WORKSPACE_ID, _ITEM_ID)

    assert call_count == 2
    assert len(result) == 2  # 1 from page 1 + 1 from page 2


async def test_list_item_access_pagination_returns_all_items_as_item_access() -> None:
    """All items from all pages must be ItemAccess instances."""
    page1 = json.loads(ITEM_ACCESS_DETAILS_PAGE1_PAYLOAD)
    page2 = json.loads(ITEM_ACCESS_DETAILS_PAGE2_PAYLOAD)

    def side_effect(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "continuationToken" in url:
            return httpx.Response(200, json=page2)
        return httpx.Response(200, json=page1)

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*/admin/workspaces/.*/items/.*/users.*").mock(
            side_effect=side_effect
        )

        client = await _make_client()
        async with client:
            result = await permissions.list_item_access(client, _WORKSPACE_ID, _ITEM_ID)

    assert all(isinstance(item, ItemAccess) for item in result)


# ---------------------------------------------------------------------------
# list_item_access — error handling
# ---------------------------------------------------------------------------


async def test_list_item_access_403_raises_permission_denied_with_hint() -> None:
    """list_item_access must raise PermissionDeniedError with admin-role hint on 403."""
    with respx.mock:
        respx.get(_ACCESS_URL).mock(
            return_value=httpx.Response(403, json={"errorCode": "Forbidden"})
        )

        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError, match="Fabric Administrator"):
                await permissions.list_item_access(client, _WORKSPACE_ID, _ITEM_ID)


async def test_list_item_access_404_raises_not_found() -> None:
    """list_item_access must propagate NotFoundError on 404."""
    with respx.mock:
        respx.get(_ACCESS_URL).mock(
            return_value=httpx.Response(404, json={"errorCode": "ItemNotFound"})
        )

        client = await _make_client()
        async with client:
            with pytest.raises(NotFoundError):
                await permissions.list_item_access(client, _WORKSPACE_ID, _ITEM_ID)
