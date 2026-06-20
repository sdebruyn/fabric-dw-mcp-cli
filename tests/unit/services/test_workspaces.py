"""Tests for fabric_dw.services.workspaces — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from uuid import UUID

import httpx
import pytest
import respx

from fabric_dw.exceptions import FabricError
from fabric_dw.models import Workspace
from fabric_dw.services import workspaces
from tests.fixtures.api_payloads import (
    WORKSPACE_GET_PAYLOAD,
    WORKSPACE_LIST_PAGE2_PAYLOAD,
    WORKSPACE_LIST_PAYLOAD,
)
from tests.unit.services._helpers import _make_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WORKSPACE_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_CONTINUATION_URL = (
    "https://api.fabric.microsoft.com/v1/workspaces?continuationToken=eyJ0b2tlbiI6InRlc3QifQ%3D%3D"
)


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


async def test_list_all_follows_continuation_uri() -> None:
    """list_all must follow continuationUri for ≥2 pages and return all workspaces."""
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
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


async def test_list_all_returns_workspace_instances() -> None:
    """list_all items must be validated Workspace model instances."""
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


async def test_get_returns_populated_workspace() -> None:
    """get must return a single populated Workspace for the given workspace_id."""
    ws_id = _WORKSPACE_ID
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}"
    ws_payload = json.loads(WORKSPACE_GET_PAYLOAD)

    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(200, json=ws_payload))

        client = await _make_client()
        async with client:
            result = await workspaces.get(client, ws_id)

    assert isinstance(result, Workspace)
    assert result.id == ws_id
    assert result.name == "AnalyticsWorkspace"
    assert result.description == "Primary analytics workspace for data engineering"
    assert result.capacity_id == UUID("cafebabe-dead-beef-cafe-babe12345678")


# ---------------------------------------------------------------------------
# set_collation
# ---------------------------------------------------------------------------


async def test_set_collation_happy_path_returns_none() -> None:
    """set_collation must return None on a successful PATCH (200/202)."""
    ws_id = _WORKSPACE_ID
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}"

    with respx.mock:
        respx.patch(url).mock(return_value=httpx.Response(200, json={}))

        client = await _make_client()
        async with client:
            await workspaces.set_collation(client, ws_id, "Latin1_General_100_BIN2_UTF8")

    # set_collation returns None implicitly on success; no assertion needed beyond no exception


async def test_set_collation_202_happy_path() -> None:
    """set_collation must also return None on 202 (accepted)."""
    ws_id = _WORKSPACE_ID
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}"

    with respx.mock:
        respx.patch(url).mock(return_value=httpx.Response(202, json={}))

        client = await _make_client()
        async with client:
            await workspaces.set_collation(client, ws_id, "Latin1_General_100_CI_AS_KS_WS_SC_UTF8")

    # set_collation returns None implicitly on success; no assertion needed beyond no exception


async def test_set_collation_400_raises_fabric_error_with_portal_link() -> None:
    """set_collation must raise FabricError mentioning the portal on a 400 response."""
    ws_id = _WORKSPACE_ID
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}"

    with respx.mock:
        respx.patch(url).mock(
            return_value=httpx.Response(400, json={"error": {"code": "BadRequest"}})
        )

        client = await _make_client()
        async with client:
            with pytest.raises(FabricError, match="portal"):
                await workspaces.set_collation(client, ws_id, "Latin1_General_100_BIN2_UTF8")


async def test_set_collation_invalid_value_raises_value_error() -> None:
    """set_collation must raise ValueError for unsupported collation strings."""
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
    assert isinstance(workspaces.SUPPORTED_COLLATIONS, frozenset)
    assert "Latin1_General_100_BIN2_UTF8" in workspaces.SUPPORTED_COLLATIONS
    assert "Latin1_General_100_CI_AS_KS_WS_SC_UTF8" in workspaces.SUPPORTED_COLLATIONS
    assert len(workspaces.SUPPORTED_COLLATIONS) == 2


# ---------------------------------------------------------------------------
# assign_to_capacity
# ---------------------------------------------------------------------------

_CAPACITY_ID = UUID("deadbeef-dead-beef-dead-beef00000001")
_ASSIGN_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{_WORKSPACE_ID}/assignToCapacity"


async def test_assign_to_capacity_202_returns_none() -> None:
    """assign_to_capacity must return None on a 202 Accepted response."""
    with respx.mock:
        respx.post(_ASSIGN_URL).mock(return_value=httpx.Response(202))

        client = await _make_client()
        async with client:
            result = await workspaces.assign_to_capacity(client, _WORKSPACE_ID, _CAPACITY_ID)

    assert result is None


async def test_assign_to_capacity_sends_correct_body() -> None:
    """assign_to_capacity must POST capacityId as a string UUID in the request body."""
    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202)

    with respx.mock:
        respx.post(_ASSIGN_URL).mock(side_effect=capture)

        client = await _make_client()
        async with client:
            await workspaces.assign_to_capacity(client, _WORKSPACE_ID, _CAPACITY_ID)

    assert len(captured) == 1
    body = json.loads(captured[0].content)
    assert body == {"capacityId": str(_CAPACITY_ID)}


async def test_assign_to_capacity_4xx_raises_fabric_error() -> None:
    """assign_to_capacity must raise FabricError on a 4xx response."""
    with respx.mock:
        respx.post(_ASSIGN_URL).mock(
            return_value=httpx.Response(
                404, json={"error": {"code": "WorkspaceNotFound", "message": "not found"}}
            )
        )

        client = await _make_client()
        async with client:
            with pytest.raises(FabricError):
                await workspaces.assign_to_capacity(client, _WORKSPACE_ID, _CAPACITY_ID)
