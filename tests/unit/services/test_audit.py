"""Tests for services.audit — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx

from fabric_dw.exceptions import PermissionDeniedError
from fabric_dw.models import AuditSettings
from fabric_dw.services import audit
from fabric_dw.services.audit import _validate_action_group
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
# _validate_action_group (shared validator — V12)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valid_name",
    [
        "BATCH_COMPLETED_GROUP",
        "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
        "A",
        "A1_B",
        "ABC123",
    ],
)
def test_validate_action_group_accepts_valid_names(valid_name: str) -> None:
    """_validate_action_group must NOT raise for names matching ^[A-Z0-9_]+$."""
    # Should not raise
    _validate_action_group(valid_name)


@pytest.mark.parametrize(
    "bad_name",
    [
        "batch_completed_group",  # lowercase letters
        "Group Name",  # whitespace
        "GROUP-NAME",  # hyphen
        "MIXED_Group",  # mixed case
        "GROUP\tNAME",  # tab
        "",  # empty string
    ],
)
def test_validate_action_group_rejects_invalid_names(bad_name: str) -> None:
    """_validate_action_group must raise ValueError for names not matching ^[A-Z0-9_]+$."""
    with pytest.raises(ValueError, match="action_group"):
        _validate_action_group(bad_name)


def test_validate_action_group_error_message_contains_name() -> None:
    """The error message from _validate_action_group must contain the offending name."""
    bad = "bad-name"
    with pytest.raises(ValueError, match=bad):
        _validate_action_group(bad)


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
    """get_settings should propagate PermissionDeniedError on 403."""
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
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
    """enable should propagate PermissionDeniedError on 403 from PATCH."""
    with respx.mock:
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
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
    """disable should propagate PermissionDeniedError on 403 from PATCH."""
    with respx.mock:
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await audit.disable(client, _WS_ID, _WH_ID)


# ---------------------------------------------------------------------------
# set_action_groups
# ---------------------------------------------------------------------------


async def test_set_action_groups_patches_with_enabled_state_and_groups() -> None:
    """set_action_groups should GET current state, PATCH with state=Enabled + auditActionsAndGroups,
    then return authoritative constructed state (no re-GET after PATCH).
    """
    groups = ["BATCH_COMPLETED_GROUP", "FAILED_DATABASE_AUTHENTICATION_GROUP"]
    current_payload = AUDIT_SETTINGS_PAYLOAD.copy()

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=current_payload)
        )
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.set_action_groups(client, _WS_ID, _WH_ID, groups)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body == {"state": "Enabled", "auditActionsAndGroups": groups}

    # Exactly one GET (pre-flight) — no re-GET after PATCH.
    assert get_route.call_count == 1
    assert isinstance(result, AuditSettings)
    assert result.action_groups == groups


async def test_set_action_groups_empty_list_is_valid() -> None:
    """set_action_groups with an empty list should be accepted (clears all groups)."""
    with respx.mock:
        # Pre-flight GET to obtain current settings, then PATCH; no re-GET.
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.set_action_groups(client, _WS_ID, _WH_ID, [])

    assert isinstance(result, AuditSettings)
    assert result.action_groups == []
    # ensure_enabled=True (default) + AUDIT_SETTINGS_PAYLOAD (state="Enabled") → state="Enabled"
    assert result.state == "Enabled"


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
    """set_action_groups should propagate PermissionDeniedError on 403 from PATCH.

    set_action_groups performs a pre-flight GET (which succeeds here) before
    sending the PATCH that returns 403.
    """
    with respx.mock:
        # Pre-flight GET succeeds; PATCH returns 403.
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await audit.set_action_groups(client, _WS_ID, _WH_ID, ["BATCH_COMPLETED_GROUP"])


async def test_set_action_groups_get_403_raises_permission_denied() -> None:
    """set_action_groups should propagate PermissionDeniedError on 403 from the pre-flight GET.

    The pre-flight GET added by this PR is a second 403 surface; this test covers it.
    """
    with respx.mock:
        # Pre-flight GET returns 403; PATCH is never reached.
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await audit.set_action_groups(client, _WS_ID, _WH_ID, ["BATCH_COMPLETED_GROUP"])


async def test_set_action_groups_disabled_audit_raises_when_ensure_enabled_false() -> None:
    """set_action_groups with ensure_enabled=False should raise ValueError when audit is Disabled.

    Consistent with add_action_group and remove_action_group which also raise
    ValueError("audit is disabled; enable first") when the current state is Disabled.
    When ensure_enabled=True (default) the function enables auditing, so the guard
    only applies to the ensure_enabled=False path.
    """
    with respx.mock:
        # Pre-flight GET returns disabled state; PATCH should never be reached.
        respx.get(_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=AUDIT_SETTINGS_DISABLED_PAYLOAD)
        )
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="audit is disabled; enable first"):
                await audit.set_action_groups(
                    client, _WS_ID, _WH_ID, ["BATCH_COMPLETED_GROUP"], ensure_enabled=False
                )

    assert not patch_route.called


async def test_set_action_groups_works_on_fresh_warehouse() -> None:
    """set_action_groups via PATCH works on freshly-created warehouses without a prior enable().

    Regression: the previous implementation used POST which returned EntityNotFound (404)
    on fresh warehouses.  PATCH with state=Enabled is idempotent and always works.

    Now performs a pre-flight GET (which may return empty/default settings on a fresh
    warehouse) and returns authoritative constructed state — no re-GET after PATCH.
    """
    groups = ["BATCH_COMPLETED_GROUP"]
    current_payload = AUDIT_SETTINGS_PAYLOAD.copy()

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=current_payload)
        )
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.set_action_groups(client, _WS_ID, _WH_ID, groups)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body["state"] == "Enabled"
    assert sent_body["auditActionsAndGroups"] == groups
    # Exactly one GET (pre-flight) — no re-GET after PATCH.
    assert get_route.call_count == 1
    assert isinstance(result, AuditSettings)
    assert result.action_groups == groups


async def test_set_action_groups_ensure_enabled_false_omits_state() -> None:
    """set_action_groups with ensure_enabled=False should NOT include state=Enabled in the PATCH.

    The returned state reflects the current state (from the pre-flight GET), not Enabled.
    """
    groups = ["BATCH_COMPLETED_GROUP"]
    current_payload = AUDIT_SETTINGS_PAYLOAD.copy()  # state == "Enabled"

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=current_payload)
        )
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.set_action_groups(
                client, _WS_ID, _WH_ID, groups, ensure_enabled=False
            )

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert "state" not in sent_body
    assert sent_body["auditActionsAndGroups"] == groups
    # Exactly one GET (pre-flight) — no re-GET after PATCH.
    assert get_route.call_count == 1
    assert isinstance(result, AuditSettings)
    assert result.action_groups == groups
    # With ensure_enabled=False the returned state mirrors the pre-PATCH current state.
    assert result.state == "Enabled"


async def test_set_action_groups_ensure_enabled_true_default_includes_state() -> None:
    """set_action_groups default (ensure_enabled=True) includes state=Enabled in the PATCH."""
    groups = ["BATCH_COMPLETED_GROUP"]
    current_payload = AUDIT_SETTINGS_PAYLOAD.copy()

    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=current_payload))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            await audit.set_action_groups(client, _WS_ID, _WH_ID, groups)

    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body.get("state") == "Enabled"


# ---------------------------------------------------------------------------
# add_action_group
# ---------------------------------------------------------------------------


async def test_add_action_group_adds_missing_group() -> None:
    """add_action_group should GET current groups, append the new one, PATCH, then return
    authoritative constructed state (no re-GET after PATCH).
    """
    new_group = "FAILED_DATABASE_AUTHENTICATION_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # has BATCH_COMPLETED_GROUP + SUCCESSFUL_...
    expected_groups = [
        "BATCH_COMPLETED_GROUP",
        "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
        new_group,
    ]

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=existing),  # one GET — read current state
        )
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.add_action_group(client, _WS_ID, _WH_ID, new_group)

    # Exactly one GET (pre-flight) — no re-GET after PATCH.
    assert get_route.call_count == 1
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
    """add_action_group should propagate PermissionDeniedError on 403 from GET."""
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await audit.add_action_group(client, _WS_ID, _WH_ID, "BATCH_COMPLETED_GROUP")


# ---------------------------------------------------------------------------
# remove_action_group
# ---------------------------------------------------------------------------


async def test_remove_action_group_removes_present_group() -> None:
    """remove_action_group sends the correct PATCH body with the target group removed.

    Verifies the PATCH request body: ``auditActionsAndGroups`` must contain exactly
    the groups that were present minus the removed one.  The call pattern (1 GET +
    1 PATCH) is a secondary assertion confirming no polling loop is triggered.
    """
    group_to_remove = "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # has both groups
    expected_groups = ["BATCH_COMPLETED_GROUP"]

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=existing),  # one GET — read current state
        )
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            await audit.remove_action_group(client, _WS_ID, _WH_ID, group_to_remove)

    assert get_route.call_count == 1  # exactly one GET — no polling
    assert patch_route.call_count == 1  # exactly one PATCH
    # Primary assertion: the PATCH body must contain the correct group list.
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body["auditActionsAndGroups"] == expected_groups
    assert group_to_remove not in sent_body["auditActionsAndGroups"]


async def test_remove_action_group_idempotent_when_not_present() -> None:
    """remove_action_group fast-path: no PATCH is issued when the group is already absent.

    Primary assertion: the PATCH route must not be called at all.  This test focuses
    on the *call pattern* of the fast-path; see
    ``test_remove_action_group_already_absent_returns_current_no_patch`` for the
    corresponding return-value assertion.
    """
    absent_group = "FAILED_DATABASE_AUTHENTICATION_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # does NOT have FAILED_DATABASE_AUTHENTICATION_GROUP

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=existing))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            await audit.remove_action_group(client, _WS_ID, _WH_ID, absent_group)

    # Primary: no PATCH must be sent when the group is absent.
    assert not patch_route.called
    assert get_route.call_count == 1  # only the pre-flight GET


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
    """remove_action_group should propagate PermissionDeniedError on 403 from GET."""
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await audit.remove_action_group(client, _WS_ID, _WH_ID, "BATCH_COMPLETED_GROUP")


async def test_remove_action_group_already_absent_returns_current_no_patch() -> None:
    """remove_action_group fast-path: if the group is not present, return current with no PATCH.

    Exactly one GET (the pre-flight read) should be issued; no PATCH is needed.
    """
    absent_group = "FAILED_DATABASE_AUTHENTICATION_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # does NOT contain FAILED_DATABASE_...

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=existing))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.remove_action_group(client, _WS_ID, _WH_ID, absent_group)

    assert get_route.call_count == 1  # exactly one GET — the pre-flight read
    assert not patch_route.called  # no PATCH — nothing to remove
    assert isinstance(result, AuditSettings)
    assert absent_group not in result.action_groups


async def test_remove_action_group_returns_authoritative_state_no_reget() -> None:
    """remove_action_group returns a locally-constructed AuditSettings without a re-GET.

    Primary assertion: the returned object reflects the post-PATCH membership
    (removed group absent, remaining groups preserved) even though the GET endpoint
    is eventually-consistent and is not polled after the PATCH.

    The call-pattern assertion (1 GET + 1 PATCH, no re-GET) is what distinguishes
    this test from ``test_remove_action_group_removes_present_group``, which focuses
    on the PATCH *body*; this test focuses on the *return value* and the absence of
    any subsequent GET call.
    """
    group_to_remove = "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # has both groups

    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=existing))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.remove_action_group(client, _WS_ID, _WH_ID, group_to_remove)

    # Call pattern: exactly 1 GET (pre-flight) + 1 PATCH; no subsequent re-GET.
    assert get_route.call_count == 1
    assert patch_route.call_count == 1
    # Primary: return value reflects locally-constructed post-PATCH state.
    assert isinstance(result, AuditSettings)
    assert group_to_remove not in result.action_groups  # removed group is gone
    assert "BATCH_COMPLETED_GROUP" in result.action_groups  # remaining groups preserved


# ---------------------------------------------------------------------------
# set_retention
# ---------------------------------------------------------------------------


async def test_set_retention_patches_with_retention_days_only() -> None:
    """set_retention should PATCH with only retentionDays (no state), then GET and return fresh.

    set_retention performs a pre-flight GET to verify audit is enabled, then
    sends a PATCH with only ``retentionDays``, then GETs fresh state.
    """
    enabled = AUDIT_SETTINGS_PAYLOAD.copy()
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["retentionDays"] = 90

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        get_route = respx.get(_AUDIT_URL).mock(
            side_effect=[
                httpx.Response(200, json=enabled),  # pre-flight GET (audit is enabled)
                httpx.Response(200, json=updated),  # re-fetch after PATCH
            ]
        )
        client = await _make_client()
        async with client:
            result = await audit.set_retention(client, _WS_ID, _WH_ID, days=90)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body == {"retentionDays": 90}
    assert "state" not in sent_body

    assert get_route.call_count == 2
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
    set_retention performs a pre-flight GET (audit enabled) before the PATCH.
    """
    enabled = AUDIT_SETTINGS_PAYLOAD.copy()  # state == "Enabled"
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["retentionDays"] = days

    with respx.mock:
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(
            side_effect=[
                httpx.Response(200, json=enabled),  # pre-flight GET
                httpx.Response(200, json=updated),  # re-fetch after PATCH
            ]
        )
        client = await _make_client()
        async with client:
            result = await audit.set_retention(client, _WS_ID, _WH_ID, days=days)

    assert result.retention_days == days


async def test_set_retention_disabled_audit_raises_value_error() -> None:
    """set_retention raises ValueError when audit is disabled (pre-flight GET check).

    Setting retention while auditing is disabled is meaningless — the Fabric
    service accepts the PATCH silently but the setting has no observable effect.
    A pre-flight GET is performed and ``ValueError`` is raised eagerly so callers
    get a clear signal to enable auditing first.
    """
    with respx.mock:
        get_route = respx.get(_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=AUDIT_SETTINGS_DISABLED_PAYLOAD)
        )
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="disabled"):
                await audit.set_retention(client, _WS_ID, _WH_ID, days=30)

    assert get_route.call_count == 1
    assert not patch_route.called


async def test_set_retention_403_raises_permission_denied() -> None:
    """set_retention should propagate PermissionDeniedError on 403 from PATCH.

    set_retention performs a pre-flight GET (which succeeds here — audit is
    enabled) before sending the PATCH that returns 403.
    """
    with respx.mock:
        # pre-flight GET succeeds (audit enabled), PATCH returns 403
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await audit.set_retention(client, _WS_ID, _WH_ID, days=30)
