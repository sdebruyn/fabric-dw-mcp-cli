"""Tests for services.audit — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential

from fabric_dw.exceptions import PermissionDenied
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import AuditSettings
from fabric_dw.services import audit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_WH_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")

_AUDIT_PATH = f"/workspaces/{_WS_ID}/warehouses/{_WH_ID}/settings/sqlAudit"
_AUDIT_URL = f"https://api.fabric.microsoft.com/v1{_AUDIT_PATH}"

AUDIT_SETTINGS_PAYLOAD: dict[str, Any] = {
    "state": "Enabled",
    "retentionDays": 30,
    "auditActionsAndGroups": [
        "BATCH_COMPLETED_GROUP",
        "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
    ],
}

AUDIT_SETTINGS_DISABLED_PAYLOAD: dict[str, Any] = {
    "state": "Disabled",
    "retentionDays": 0,
    "auditActionsAndGroups": [],
}

_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106


def _make_credential() -> AsyncTokenCredential:
    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(return_value=_FAKE_TOKEN)
    return cred


async def _make_client() -> FabricHttpClient:
    return FabricHttpClient(credential=_make_credential(), rps=100)


# ---------------------------------------------------------------------------
# get_settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_settings_returns_audit_settings() -> None:
    """get_settings should GET /settings/sqlAudit and return AuditSettings."""
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        client = await _make_client()
        async with client:
            result = await audit.get_settings(client, _WS_ID, _WH_ID)

    assert isinstance(result, AuditSettings)
    assert result.state == "Enabled"
    assert result.retention_days == 30
    assert result.action_groups == [
        "BATCH_COMPLETED_GROUP",
        "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
    ]


@pytest.mark.asyncio
async def test_get_settings_403_raises_permission_denied() -> None:
    """get_settings should propagate PermissionDenied on 403."""
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await audit.get_settings(client, _WS_ID, _WH_ID)


# ---------------------------------------------------------------------------
# enable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_patches_with_enabled_state_and_retention() -> None:
    """enable should PATCH with state=Enabled and retentionDays, then GET and return fresh state."""
    get_response = AUDIT_SETTINGS_PAYLOAD.copy()
    get_response["retentionDays"] = 7

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        get_route = respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=get_response))
        client = await _make_client()
        async with client:
            result = await audit.enable(client, _WS_ID, _WH_ID, retention_days=7)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body == {"state": "Enabled", "retentionDays": 7}

    assert get_route.called
    assert isinstance(result, AuditSettings)
    assert result.state == "Enabled"
    assert result.retention_days == 7


@pytest.mark.asyncio
async def test_enable_default_retention_is_zero() -> None:
    """enable with default retention_days=0 (unlimited) should send retentionDays=0."""
    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        client = await _make_client()
        async with client:
            await audit.enable(client, _WS_ID, _WH_ID)

    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body == {"state": "Enabled", "retentionDays": 0}


@pytest.mark.asyncio
async def test_enable_negative_retention_raises_value_error() -> None:
    """enable should raise ValueError if retention_days < 0."""
    client = await _make_client()
    async with client:
        with pytest.raises(ValueError, match="retention_days"):
            await audit.enable(client, _WS_ID, _WH_ID, retention_days=-1)


@pytest.mark.asyncio
async def test_enable_403_raises_permission_denied() -> None:
    """enable should propagate PermissionDenied on 403 from PATCH."""
    with respx.mock:
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await audit.enable(client, _WS_ID, _WH_ID)


# ---------------------------------------------------------------------------
# disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_patches_with_disabled_state() -> None:
    """disable should PATCH with state=Disabled, then GET and return fresh state."""
    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        get_route = respx.get(_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=AUDIT_SETTINGS_DISABLED_PAYLOAD)
        )
        client = await _make_client()
        async with client:
            result = await audit.disable(client, _WS_ID, _WH_ID)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body == {"state": "Disabled"}

    assert get_route.called
    assert isinstance(result, AuditSettings)
    assert result.state == "Disabled"


@pytest.mark.asyncio
async def test_disable_403_raises_permission_denied() -> None:
    """disable should propagate PermissionDenied on 403 from PATCH."""
    with respx.mock:
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await audit.disable(client, _WS_ID, _WH_ID)


# ---------------------------------------------------------------------------
# set_action_groups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_action_groups_patches_with_enabled_state_and_groups() -> None:
    """set_action_groups should PATCH with state=Enabled + auditActionsAndGroups, then GET."""
    groups = ["BATCH_COMPLETED_GROUP", "FAILED_DATABASE_AUTHENTICATION_GROUP"]
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["auditActionsAndGroups"] = groups

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        get_route = respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=updated))
        client = await _make_client()
        async with client:
            result = await audit.set_action_groups(client, _WS_ID, _WH_ID, groups)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body == {"state": "Enabled", "auditActionsAndGroups": groups}

    assert get_route.called
    assert isinstance(result, AuditSettings)
    assert result.action_groups == groups


@pytest.mark.asyncio
async def test_set_action_groups_empty_list_is_valid() -> None:
    """set_action_groups with an empty list should be accepted (clears all groups)."""
    with respx.mock:
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=AUDIT_SETTINGS_DISABLED_PAYLOAD)
        )
        client = await _make_client()
        async with client:
            result = await audit.set_action_groups(client, _WS_ID, _WH_ID, [])

    assert isinstance(result, AuditSettings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_group",
    [
        "batch completed group",  # whitespace
        "batch\tcompleted",  # tab
        "lowercase_group",  # lower-case letters
        "MIXED_Group",  # mixed case
        "GROUP-NAME",  # hyphen
        "GROUP123",  # digits not allowed by ^[A-Z_]+$
    ],
)
async def test_set_action_groups_invalid_name_raises_value_error(bad_group: str) -> None:
    """set_action_groups should raise ValueError for names that don't match ^[A-Z_]+$."""
    client = await _make_client()
    async with client:
        with pytest.raises(ValueError, match="action_group"):
            await audit.set_action_groups(client, _WS_ID, _WH_ID, [bad_group])


@pytest.mark.asyncio
async def test_set_action_groups_403_raises_permission_denied() -> None:
    """set_action_groups should propagate PermissionDenied on 403 from PATCH."""
    with respx.mock:
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await audit.set_action_groups(client, _WS_ID, _WH_ID, ["BATCH_COMPLETED_GROUP"])


@pytest.mark.asyncio
async def test_set_action_groups_works_on_fresh_warehouse() -> None:
    """set_action_groups via PATCH works on freshly-created warehouses without a prior enable().

    Regression: the previous implementation used POST which returned EntityNotFound (404)
    on fresh warehouses.  PATCH with state=Enabled is idempotent and always works.
    """
    groups = ["BATCH_COMPLETED_GROUP"]
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["auditActionsAndGroups"] = groups

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=updated))
        client = await _make_client()
        async with client:
            result = await audit.set_action_groups(client, _WS_ID, _WH_ID, groups)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body["state"] == "Enabled"
    assert sent_body["auditActionsAndGroups"] == groups
    assert isinstance(result, AuditSettings)
    assert result.action_groups == groups
