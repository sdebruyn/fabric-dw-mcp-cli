"""Tests for the ownership service."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
import respx

from fabric_dw.exceptions import NotFoundError, PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.services.ownership import _ALREADY_OWNER_ERROR_CODE, _TAKEOVER_HINT, takeover
from tests.unit.services._helpers import _make_credential

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_WORKSPACE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_WAREHOUSE_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

_EXPECTED_URL = (
    f"https://api.powerbi.com/v1.0/myorg"
    f"/groups/{_WORKSPACE_ID}/datawarehouses/{_WAREHOUSE_ID}/takeover"
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_takeover_200_returns_none() -> None:
    """A 200 response should result in None being returned."""
    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(200))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)


@respx.mock
async def test_takeover_202_returns_none() -> None:
    """A 202 response should result in None being returned."""
    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(202))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)


@respx.mock
async def test_takeover_204_returns_none() -> None:
    """A 204 response should result in None being returned."""
    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(204))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)


@respx.mock
async def test_takeover_403_raises_permission_denied() -> None:
    """A 403 response should raise PermissionDeniedError with a helpful message."""
    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(403))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        with pytest.raises(PermissionDeniedError, match="Admin/Member/Contributor"):
            await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)


@respx.mock
async def test_takeover_404_raises_not_found() -> None:
    """A 404 response should raise NotFoundError."""
    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(404))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        with pytest.raises(NotFoundError):
            await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)


@respx.mock
async def test_takeover_sends_empty_body() -> None:
    """POST body must be empty (None / no content-type: application/json)."""
    received_requests: list[Any] = []

    def _capture(request: Any) -> respx.MockResponse:
        received_requests.append(request)
        return respx.MockResponse(200)

    respx.post(_EXPECTED_URL).mock(side_effect=_capture)

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)

    assert len(received_requests) == 1
    req = received_requests[0]
    # httpx sends no body (empty bytes) when json=None
    assert req.content == b""


@respx.mock
async def test_takeover_403_already_owner_raises_clear_message() -> None:
    """HTTP 403 with ArtifactTakeOverNotAllowedByOwner -> clear 'already owner' message.

    Uses the real error envelope returned by the legacy Power BI ``/takeover``
    endpoint (see issue #955): the code is nested under ``error.code`` (and
    mirrored under ``error["pbi.error"]["code"]``), NOT at the top level. A
    body with only a top-level ``errorCode`` key would not reproduce the bug.

    The misleading role hint must NOT appear in the error; the caller is not
    missing a role — they already own the warehouse.
    """
    body = {
        "error": {
            "code": _ALREADY_OWNER_ERROR_CODE,
            "pbi.error": {
                "code": _ALREADY_OWNER_ERROR_CODE,
                "parameters": {"ErrorMessage": "Owner is not allowed to takeover"},
                "details": [],
                "exceptionCulprit": 1,
            },
        }
    }
    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(403, json=body))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        with pytest.raises(PermissionDeniedError) as exc_info:
            await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)

    error_text = str(exc_info.value)
    assert "already the owner" in error_text
    # The generic role hint must NOT appear for this specific error code.
    assert _TAKEOVER_HINT not in error_text
    # The raw REST URL and raw JSON body must not leak into the message.
    assert _EXPECTED_URL not in error_text
    assert "pbi.error" not in error_text


@respx.mock
async def test_takeover_403_already_owner_top_level_error_code_shape() -> None:
    """A top-level ``errorCode`` (Fabric-REST style) is also recognised.

    Defensive coverage: some Fabric endpoints return the code at the top
    level instead of nested under ``error``. Both shapes must be detected.
    """
    body = {"errorCode": _ALREADY_OWNER_ERROR_CODE, "message": "Caller is already the owner."}
    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(403, json=body))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        with pytest.raises(PermissionDeniedError) as exc_info:
            await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)

    error_text = str(exc_info.value)
    assert "already the owner" in error_text
    assert _TAKEOVER_HINT not in error_text


@respx.mock
async def test_takeover_403_generic_still_shows_role_hint() -> None:
    """A generic HTTP 403 (not ArtifactTakeOverNotAllowedByOwner) still shows the role hint."""
    body = {"error": {"code": "Forbidden", "message": "Caller lacks the required role."}}
    respx.post(_EXPECTED_URL).mock(return_value=respx.MockResponse(403, json=body))

    async with FabricHttpClient(credential=_make_credential(), rps=10) as http:
        with pytest.raises(PermissionDeniedError) as exc_info:
            await takeover(http, _WORKSPACE_ID, _WAREHOUSE_ID)

    error_text = str(exc_info.value)
    assert _TAKEOVER_HINT in error_text
    assert "already the owner" not in error_text
