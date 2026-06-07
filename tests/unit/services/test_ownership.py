"""Tests for the ownership service (TDD - written before implementation)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import respx
from azure.core.credentials import AccessToken, TokenCredential

from fabric_dw.exceptions import NotFound, PermissionDenied
from fabric_dw.http_client import FabricHttpClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106

_WORKSPACE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_WAREHOUSE_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

_EXPECTED_URL = (
    f"https://api.powerbi.com/v1.0/myorg"
    f"/groups/{_WORKSPACE_ID}/datawarehouses/{_WAREHOUSE_ID}/takeover"
)


def _make_credential(token: AccessToken = _FAKE_TOKEN) -> TokenCredential:
    """Build a mock credential that returns *token*."""
    cred = MagicMock(spec=TokenCredential)
    cred.get_token = MagicMock(return_value=token)
    return cred


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_takeover_200_returns_none() -> None:
    """A 200 response should result in None being returned."""
    from fabric_dw.services.ownership import takeover

    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(200))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        result = await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)

    assert result is None


@respx.mock
async def test_takeover_202_returns_none() -> None:
    """A 202 response should result in None being returned."""
    from fabric_dw.services.ownership import takeover

    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(202))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        result = await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)

    assert result is None


@respx.mock
async def test_takeover_204_returns_none() -> None:
    """A 204 response should result in None being returned."""
    from fabric_dw.services.ownership import takeover

    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(204))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        result = await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)

    assert result is None


@respx.mock
async def test_takeover_403_raises_permission_denied() -> None:
    """A 403 response should raise PermissionDenied with a helpful message."""
    from fabric_dw.services.ownership import takeover

    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(403))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        with pytest.raises(PermissionDenied, match="Admin/Member/Contributor"):
            await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)


@respx.mock
async def test_takeover_404_raises_not_found() -> None:
    """A 404 response should raise NotFound."""
    from fabric_dw.services.ownership import takeover

    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(404))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        with pytest.raises(NotFound):
            await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)


@respx.mock
async def test_takeover_sends_empty_body() -> None:
    """POST body must be empty (None / no content-type: application/json)."""
    from fabric_dw.services.ownership import takeover

    received_requests: list[Any] = []

    def _capture(request: Any, route: Any) -> Any:  # noqa: ANN401
        received_requests.append(request)
        return respx.MockResponse(200)

    respx.post(_EXPECTED_URL).mock(side_effect=_capture)

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)

    assert len(received_requests) == 1
    req = received_requests[0]
    # httpx sends no body (empty bytes) when json=None
    assert req.content == b""
