"""Tests for fabric_dw.services.workspaces — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock
from uuid import UUID

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken, TokenCredential

from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import Workspace
from tests.fixtures.api_payloads import (
    WORKSPACE_GET_PAYLOAD,
    WORKSPACE_LIST_PAGE2_PAYLOAD,
    WORKSPACE_LIST_PAYLOAD,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106

_WORKSPACE_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_CONTINUATION_URL = (
    "https://api.fabric.microsoft.com/v1/workspaces"
    "?continuationToken=eyJ0b2tlbiI6InRlc3QifQ%3D%3D"
)


def _make_credential(token: AccessToken = _FAKE_TOKEN) -> TokenCredential:
    cred = MagicMock(spec=TokenCredential)
    cred.get_token = MagicMock(return_value=token)
    return cred


async def _make_client(rps: int = 10) -> FabricHttpClient:
    return FabricHttpClient(credential=_make_credential(), rps=rps)


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_all_follows_continuation_uri() -> None:
    """list_all must follow continuationUri for ≥2 pages and return all workspaces."""
    from fabric_dw.services import workspaces

    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=json.loads(WORKSPACE_LIST_PAYLOAD))
        return httpx.Response(200, json=json.loads(WORKSPACE_LIST_PAGE2_PAYLOAD))

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(url__regex=r"https://api\.fabric\.microsoft\.com/v1/workspaces.*").mock(
            side_effect=side_effect
        )

        client = await _make_client()
        async with client:
            result = await workspaces.list_all(client)

    assert call_count == 2
    assert len(result) == 3  # 2 from page 1 + 1 from page 2
    assert all(isinstance(ws, Workspace) for ws in result)
    names = {ws.name for ws in result}
    assert "AnalyticsWorkspace" in names
    assert "DataScienceWorkspace" in names
    assert "MLWorkspace" in names


@pytest.mark.asyncio
async def test_list_all_returns_workspace_instances() -> None:
    """list_all items must be validated Workspace model instances."""
    from fabric_dw.services import workspaces

    payload = json.loads(WORKSPACE_LIST_PAYLOAD)
    # Remove continuation so only one page is returned
    payload.pop("continuationUri", None)

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/workspaces").mock(
            return_value=httpx.Response(200, json=payload)
        )

        client = await _make_client()
        async with client:
            result = await workspaces.list_all(client)

    assert len(result) == 2
    first = result[0]
    assert isinstance(first, Workspace)
    assert first.id == UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    assert first.name == "AnalyticsWorkspace"


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_populated_workspace() -> None:
    """get must return a single populated Workspace for the given workspace_id."""
    from fabric_dw.services import workspaces

    ws_id = _WORKSPACE_ID
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}"

    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(200, json=json.loads(WORKSPACE_GET_PAYLOAD)))

        client = await _make_client()
        async with client:
            result = await workspaces.get(client, ws_id)

    assert isinstance(result, Workspace)
    assert result.id == ws_id
    assert result.name == "AnalyticsWorkspace"
    assert result.description == "Primary analytics workspace for data engineering"
    assert result.capacity_id == UUID("cafebabe-dead-beef-cafe-babe12345678")


# ---------------------------------------------------------------------------
# get_collation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_collation_returns_none_when_absent() -> None:
    """get_collation must return None when the workspace payload has no collation field."""
    from fabric_dw.services import workspaces

    ws_id = _WORKSPACE_ID
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}"

    # WORKSPACE_GET_PAYLOAD has no defaultDataWarehouseCollation
    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(200, json=json.loads(WORKSPACE_GET_PAYLOAD)))

        client = await _make_client()
        async with client:
            result = await workspaces.get_collation(client, ws_id)

    assert result is None


@pytest.mark.asyncio
async def test_get_collation_returns_value_when_present() -> None:
    """get_collation must return the collation string when present in the workspace payload."""
    from fabric_dw.services import workspaces

    ws_id = _WORKSPACE_ID
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}"

    payload = json.loads(WORKSPACE_GET_PAYLOAD)
    payload["defaultDataWarehouseCollation"] = "Latin1_General_100_BIN2_UTF8"

    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(200, json=payload))

        client = await _make_client()
        async with client:
            result = await workspaces.get_collation(client, ws_id)

    assert result == "Latin1_General_100_BIN2_UTF8"


# ---------------------------------------------------------------------------
# set_collation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_collation_happy_path_returns_none() -> None:
    """set_collation must return None on a successful PATCH (200/202)."""
    from fabric_dw.services import workspaces

    ws_id = _WORKSPACE_ID
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}"

    with respx.mock:
        respx.patch(url).mock(return_value=httpx.Response(200, json={}))

        client = await _make_client()
        async with client:
            result = await workspaces.set_collation(
                client, ws_id, "Latin1_General_100_BIN2_UTF8"
            )

    assert result is None


@pytest.mark.asyncio
async def test_set_collation_202_happy_path() -> None:
    """set_collation must also return None on 202 (accepted)."""
    from fabric_dw.services import workspaces

    ws_id = _WORKSPACE_ID
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}"

    with respx.mock:
        respx.patch(url).mock(return_value=httpx.Response(202, json={}))

        client = await _make_client()
        async with client:
            result = await workspaces.set_collation(
                client, ws_id, "Latin1_General_100_CI_AS_KS_WS_SC_UTF8"
            )

    assert result is None


@pytest.mark.asyncio
async def test_set_collation_400_raises_fabric_error_with_portal_link() -> None:
    """set_collation must raise FabricError mentioning the portal on a 400 response."""
    from fabric_dw.services import workspaces

    ws_id = _WORKSPACE_ID
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}"

    with respx.mock:
        respx.patch(url).mock(
            return_value=httpx.Response(400, json={"error": {"code": "BadRequest"}})
        )

        client = await _make_client()
        async with client:
            with pytest.raises(FabricError, match="portal"):
                await workspaces.set_collation(
                    client, ws_id, "Latin1_General_100_BIN2_UTF8"
                )


@pytest.mark.asyncio
async def test_set_collation_invalid_value_raises_value_error() -> None:
    """set_collation must raise ValueError for unsupported collation strings."""
    from fabric_dw.services import workspaces

    ws_id = _WORKSPACE_ID

    client = await _make_client()
    async with client:
        with pytest.raises(ValueError, match="collation"):
            await workspaces.set_collation(client, ws_id, "SQL_Latin1_General_CP1_CI_AS")


# ---------------------------------------------------------------------------
# SUPPORTED_COLLATIONS constant
# ---------------------------------------------------------------------------


def test_supported_collations_constant() -> None:
    """SUPPORTED_COLLATIONS must be a frozenset with the two documented values."""
    from fabric_dw.services import workspaces

    assert isinstance(workspaces.SUPPORTED_COLLATIONS, frozenset)
    assert "Latin1_General_100_BIN2_UTF8" in workspaces.SUPPORTED_COLLATIONS
    assert "Latin1_General_100_CI_AS_KS_WS_SC_UTF8" in workspaces.SUPPORTED_COLLATIONS
    assert len(workspaces.SUPPORTED_COLLATIONS) == 2
