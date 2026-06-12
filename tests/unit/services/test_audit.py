"""Tests for services.audit — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx

from fabric_dw.exceptions import PermissionDenied
from fabric_dw.models import AuditSettings
from fabric_dw.services import audit
from tests.unit.services._helpers import _make_client

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


# ---------------------------------------------------------------------------
# get_settings
# ---------------------------------------------------------------------------


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


async def test_enable_negative_retention_raises_value_error() -> None:
    """enable should raise ValueError if retention_days < 0."""
    client = await _make_client()
    async with client:
        with pytest.raises(ValueError, match="retention_days"):
            await audit.enable(client, _WS_ID, _WH_ID, retention_days=-1)


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


@pytest.mark.parametrize(
    "bad_group",
    [
        "batch completed group",  # whitespace
        "batch\tcompleted",  # tab
        "lowercase_group",  # lower-case letters
        "MIXED_Group",  # mixed case
        "GROUP-NAME",  # hyphen
    ],
)
async def test_set_action_groups_invalid_name_raises_value_error(bad_group: str) -> None:
    """set_action_groups should raise ValueError for names that don't match ^[A-Z0-9_]+$."""
    client = await _make_client()
    async with client:
        with pytest.raises(ValueError, match="action_group"):
            await audit.set_action_groups(client, _WS_ID, _WH_ID, [bad_group])


async def test_set_action_groups_403_raises_permission_denied() -> None:
    """set_action_groups should propagate PermissionDenied on 403 from PATCH."""
    with respx.mock:
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await audit.set_action_groups(client, _WS_ID, _WH_ID, ["BATCH_COMPLETED_GROUP"])


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


async def test_set_action_groups_ensure_enabled_false_omits_state() -> None:
    """set_action_groups with ensure_enabled=False should NOT include state=Enabled in the PATCH."""
    groups = ["BATCH_COMPLETED_GROUP"]
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["auditActionsAndGroups"] = groups

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=updated))
        client = await _make_client()
        async with client:
            result = await audit.set_action_groups(
                client, _WS_ID, _WH_ID, groups, ensure_enabled=False
            )

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert "state" not in sent_body
    assert sent_body["auditActionsAndGroups"] == groups
    assert isinstance(result, AuditSettings)


async def test_set_action_groups_ensure_enabled_true_default_includes_state() -> None:
    """set_action_groups default (ensure_enabled=True) includes state=Enabled in the PATCH."""
    groups = ["BATCH_COMPLETED_GROUP"]
    updated = AUDIT_SETTINGS_PAYLOAD.copy()

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=updated))
        client = await _make_client()
        async with client:
            await audit.set_action_groups(client, _WS_ID, _WH_ID, groups)

    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body.get("state") == "Enabled"


# ---------------------------------------------------------------------------
# add_action_group
# ---------------------------------------------------------------------------


async def test_add_action_group_adds_missing_group() -> None:
    """add_action_group should GET current groups, append the new one, then PATCH."""
    new_group = "FAILED_DATABASE_AUTHENTICATION_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # has BATCH_COMPLETED_GROUP + SUCCESSFUL_...
    expected_groups = [
        "BATCH_COMPLETED_GROUP",
        "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
        new_group,
    ]
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["auditActionsAndGroups"] = expected_groups

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(
            side_effect=[
                httpx.Response(200, json=existing),  # first GET — read current state
                httpx.Response(200, json=updated),  # second GET — after PATCH
            ]
        )
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.add_action_group(client, _WS_ID, _WH_ID, new_group)

    assert get_route.call_count == 2
    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body["auditActionsAndGroups"] == expected_groups
    assert isinstance(result, AuditSettings)
    assert new_group in result.action_groups


async def test_add_action_group_idempotent_when_already_present() -> None:
    """add_action_group should not PATCH if the group is already present."""
    existing_group = "BATCH_COMPLETED_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # already contains BATCH_COMPLETED_GROUP

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=existing))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.add_action_group(client, _WS_ID, _WH_ID, existing_group)

    assert get_route.call_count == 1  # only one GET — no PATCH needed
    assert not patch_route.called
    assert isinstance(result, AuditSettings)
    assert existing_group in result.action_groups


async def test_add_action_group_disabled_raises_value_error() -> None:
    """add_action_group should raise ValueError when audit is disabled."""
    disabled = AUDIT_SETTINGS_DISABLED_PAYLOAD.copy()

    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=disabled))
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="audit is disabled"):
                await audit.add_action_group(client, _WS_ID, _WH_ID, "BATCH_COMPLETED_GROUP")


@pytest.mark.parametrize(
    "bad_group",
    [
        "batch completed group",  # whitespace
        "lowercase_group",  # lower-case letters
        "GROUP-NAME",  # hyphen
    ],
)
async def test_add_action_group_invalid_name_raises_value_error(bad_group: str) -> None:
    """add_action_group should raise ValueError for names that don't match ^[A-Z0-9_]+$."""
    client = await _make_client()
    async with client:
        with pytest.raises(ValueError, match="action_group"):
            await audit.add_action_group(client, _WS_ID, _WH_ID, bad_group)


async def test_add_action_group_403_raises_permission_denied() -> None:
    """add_action_group should propagate PermissionDenied on 403 from GET."""
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await audit.add_action_group(client, _WS_ID, _WH_ID, "BATCH_COMPLETED_GROUP")


# ---------------------------------------------------------------------------
# remove_action_group
# ---------------------------------------------------------------------------


async def test_remove_action_group_removes_present_group() -> None:
    """remove_action_group should GET current groups, remove the target, then PATCH."""
    group_to_remove = "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # has both groups
    expected_groups = ["BATCH_COMPLETED_GROUP"]
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["auditActionsAndGroups"] = expected_groups

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(
            side_effect=[
                httpx.Response(200, json=existing),  # first GET — read current state
                httpx.Response(200, json=updated),  # second GET — after PATCH
            ]
        )
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.remove_action_group(client, _WS_ID, _WH_ID, group_to_remove)

    assert get_route.call_count == 2
    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert group_to_remove not in sent_body["auditActionsAndGroups"]
    assert isinstance(result, AuditSettings)


async def test_remove_action_group_idempotent_when_not_present() -> None:
    """remove_action_group should not PATCH if the group is not present."""
    absent_group = "FAILED_DATABASE_AUTHENTICATION_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # does NOT have FAILED_DATABASE_AUTHENTICATION_GROUP

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=existing))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.remove_action_group(client, _WS_ID, _WH_ID, absent_group)

    assert get_route.call_count == 1  # only one GET — no PATCH needed
    assert not patch_route.called
    assert isinstance(result, AuditSettings)


async def test_remove_action_group_disabled_raises_value_error() -> None:
    """remove_action_group should raise ValueError when audit is disabled."""
    disabled = AUDIT_SETTINGS_DISABLED_PAYLOAD.copy()

    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=disabled))
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="audit is disabled"):
                await audit.remove_action_group(client, _WS_ID, _WH_ID, "BATCH_COMPLETED_GROUP")


@pytest.mark.parametrize(
    "bad_group",
    [
        "batch completed group",  # whitespace
        "lowercase_group",  # lower-case letters
        "GROUP-NAME",  # hyphen
    ],
)
async def test_remove_action_group_invalid_name_raises_value_error(bad_group: str) -> None:
    """remove_action_group should raise ValueError for names that don't match ^[A-Z0-9_]+$."""
    client = await _make_client()
    async with client:
        with pytest.raises(ValueError, match="action_group"):
            await audit.remove_action_group(client, _WS_ID, _WH_ID, bad_group)


async def test_remove_action_group_403_raises_permission_denied() -> None:
    """remove_action_group should propagate PermissionDenied on 403 from GET."""
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await audit.remove_action_group(client, _WS_ID, _WH_ID, "BATCH_COMPLETED_GROUP")


# ---------------------------------------------------------------------------
# set_retention
# ---------------------------------------------------------------------------


async def test_set_retention_patches_with_retention_days_only() -> None:
    """set_retention should PATCH with only retentionDays (no state), then GET and return fresh."""
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["retentionDays"] = 90

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        get_route = respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=updated))
        client = await _make_client()
        async with client:
            result = await audit.set_retention(client, _WS_ID, _WH_ID, days=90)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body == {"retentionDays": 90}
    assert "state" not in sent_body

    assert get_route.called
    assert isinstance(result, AuditSettings)
    assert result.retention_days == 90


@pytest.mark.parametrize("days", [0, -1, -100])
async def test_set_retention_out_of_range_raises_value_error(days: int) -> None:
    """set_retention should raise ValueError when days < 1."""
    client = await _make_client()
    async with client:
        with pytest.raises(ValueError, match="days"):
            await audit.set_retention(client, _WS_ID, _WH_ID, days=days)


@pytest.mark.parametrize("days", [1, 3650])
async def test_set_retention_boundary_values_are_accepted(days: int) -> None:
    """set_retention should accept boundary values: 1 (minimum) and 3650 (large plausible value).

    The API does not document an upper bound, so any value >= 1 is valid client-side.
    """
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["retentionDays"] = days

    with respx.mock:
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=updated))
        client = await _make_client()
        async with client:
            result = await audit.set_retention(client, _WS_ID, _WH_ID, days=days)

    assert result.retention_days == days


async def test_set_retention_disabled_audit_sends_patch_to_server() -> None:
    """set_retention no longer pre-checks audit state; it sends PATCH and lets the server decide.

    The old pre-flight GET was racy (another caller could disable audit between the
    GET and the PATCH).  The new behaviour sends the PATCH directly and lets the
    server reject invalid states.
    """
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["retentionDays"] = 30

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=updated))
        client = await _make_client()
        async with client:
            result = await audit.set_retention(client, _WS_ID, _WH_ID, days=30)

    assert patch_route.called
    assert isinstance(result, AuditSettings)


async def test_set_retention_403_raises_permission_denied() -> None:
    """set_retention should propagate PermissionDenied on 403 from PATCH."""
    with respx.mock:
        # GET succeeds (audit enabled), PATCH returns 403
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await audit.set_retention(client, _WS_ID, _WH_ID, days=30)
