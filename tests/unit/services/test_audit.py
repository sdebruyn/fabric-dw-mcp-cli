"""Tests for services.audit — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx

from fabric_dw.exceptions import PermissionDeniedError
from fabric_dw.models import AuditSettings, WarehouseKind
from fabric_dw.services import audit
from fabric_dw.services.audit import _validate_action_group
from tests.unit.services._helpers import _make_client

# ---------------------------------------------------------------------------
# Constants — Warehouse (existing)
# ---------------------------------------------------------------------------

_WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_WH_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")

_AUDIT_PATH = f"/workspaces/{_WS_ID}/warehouses/{_WH_ID}/settings/sqlAudit"
_AUDIT_URL = f"https://api.fabric.microsoft.com/v1{_AUDIT_PATH}"

# ---------------------------------------------------------------------------
# Constants — SQL Analytics Endpoint
# ---------------------------------------------------------------------------

_EP_ID = UUID("e1f2a3b4-c5d6-7890-ef01-234567890abc")
_EP_AUDIT_PATH = f"/workspaces/{_WS_ID}/sqlEndpoints/{_EP_ID}/settings/sqlAudit"
_EP_AUDIT_URL = f"https://api.fabric.microsoft.com/v1{_EP_AUDIT_PATH}"

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
    """enable (re-enable) should PATCH with state, retentionDays, and auditActionsAndGroups.

    When auditing is already enabled, enable() preserves the current action groups
    by including auditActionsAndGroups in the PATCH body.  Two GETs are issued:
    one pre-flight (to read current state/groups) and one re-fetch after PATCH.
    """
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
    assert sent_body == {
        "state": "Enabled",
        "retentionDays": 7,
        "auditActionsAndGroups": [
            "BATCH_COMPLETED_GROUP",
            "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
        ],
    }

    assert get_route.called
    assert isinstance(result, AuditSettings)
    assert result.state == "Enabled"
    assert result.retention_days == 7


async def test_enable_default_retention_is_zero() -> None:
    """enable with default retention_days=0 (unlimited) should send retentionDays=0.

    When auditing is already enabled (AUDIT_SETTINGS_PAYLOAD), the PATCH body
    also includes auditActionsAndGroups to preserve the current groups.
    """
    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        client = await _make_client()
        async with client:
            await audit.enable(client, _WS_ID, _WH_ID)

    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body == {
        "state": "Enabled",
        "retentionDays": 0,
        "auditActionsAndGroups": [
            "BATCH_COMPLETED_GROUP",
            "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
        ],
    }


async def test_enable_negative_retention_raises_value_error() -> None:
    """enable should raise ValueError if retention_days < 0."""
    client = await _make_client()
    async with client:
        with pytest.raises(ValueError, match="retention_days"):
            await audit.enable(client, _WS_ID, _WH_ID, retention_days=-1)


async def test_enable_403_raises_permission_denied() -> None:
    """enable should propagate PermissionDeniedError on 403 from PATCH.

    enable issues a pre-flight GET (which succeeds here) before the PATCH
    that returns 403.
    """
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await audit.enable(client, _WS_ID, _WH_ID)


async def test_enable_get_403_raises_permission_denied() -> None:
    """enable should propagate PermissionDeniedError on 403 from the pre-flight GET.

    The pre-flight GET added by this fix is a new 403 surface.  This test
    confirms the error propagates before the PATCH is ever attempted.
    """
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await audit.enable(client, _WS_ID, _WH_ID)

    assert not patch_route.called


# ---------------------------------------------------------------------------
# disable
# ---------------------------------------------------------------------------


async def test_disable_patches_with_disabled_state() -> None:
    """disable should PATCH with state=Disabled (preserving retentionDays and groups), then GET.

    The pre-flight GET returns the current settings; both retentionDays and
    auditActionsAndGroups from that GET must appear in the PATCH body so the
    Fabric API does not silently reset them to defaults.  Two GETs are issued:
    one pre-flight and one re-fetch after PATCH.

    Non-default values (retentionDays=90, one custom group) are used so that
    a buggy implementation that hardcodes zeros/empty would fail the value assertions.
    """
    # Non-default pre-PATCH state: currently Enabled with custom retention + groups.
    _pre_patch = {
        "state": "Enabled",
        "retentionDays": 90,
        "auditActionsAndGroups": ["BATCH_COMPLETED_GROUP"],
    }
    # Post-PATCH re-fetch returns the same retention/groups with state flipped.
    _post_patch = {**_pre_patch, "state": "Disabled"}

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        # Two GETs: pre-flight reads current state, re-fetch reads post-PATCH state.
        get_route = respx.get(_AUDIT_URL).mock(
            side_effect=[
                httpx.Response(200, json=_pre_patch),  # pre-flight GET
                httpx.Response(200, json=_post_patch),  # re-fetch after PATCH
            ]
        )
        client = await _make_client()
        async with client:
            result = await audit.disable(client, _WS_ID, _WH_ID)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    # Regression guard: retentionDays and auditActionsAndGroups must be preserved
    # (sourced from the pre-flight GET) so the Fabric API does not reset them.
    # Non-default values prove the pre-flight GET is actually used.
    assert sent_body == {
        "state": "Disabled",
        "retentionDays": 90,
        "auditActionsAndGroups": ["BATCH_COMPLETED_GROUP"],
    }

    # Two GETs must be issued: one pre-flight + one re-fetch after PATCH.
    assert get_route.call_count == 2
    assert isinstance(result, AuditSettings)
    assert result.state == "Disabled"


async def test_disable_403_raises_permission_denied() -> None:
    """disable should propagate PermissionDeniedError on 403 from PATCH.

    disable performs a pre-flight GET (which succeeds here) before sending the
    PATCH that returns 403.
    """
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await audit.disable(client, _WS_ID, _WH_ID)


async def test_disable_get_403_raises_permission_denied() -> None:
    """disable should propagate PermissionDeniedError on 403 from the pre-flight GET.

    The pre-flight GET added by this fix is a new 403 surface.  This test
    confirms the error propagates before the PATCH is ever attempted.
    """
    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await audit.disable(client, _WS_ID, _WH_ID)

    assert not patch_route.called


# ---------------------------------------------------------------------------
# set_action_groups
# ---------------------------------------------------------------------------


async def test_set_action_groups_patches_with_enabled_state_and_groups() -> None:
    """set_action_groups should GET current state, PATCH with state, retentionDays, and groups,
    then return authoritative constructed state (no re-GET after PATCH).

    Regression guard: retentionDays must be included in the PATCH body alongside
    state=Enabled and auditActionsAndGroups so the Fabric API does not silently
    reset retentionDays to its default value (fix for #780).
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
    assert sent_body == {
        "state": "Enabled",
        "retentionDays": current_payload["retentionDays"],
        "auditActionsAndGroups": groups,
    }

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


async def test_set_action_groups_ensure_enabled_false_preserves_current_state() -> None:
    """set_action_groups with ensure_enabled=False round-trips the current state in the PATCH.

    The Fabric API resets any omitted field to its default value, so state must
    be included even when ensure_enabled=False.  The current state (from the
    pre-flight GET) is used so the effective audit state is not changed.
    retentionDays is also round-tripped to prevent it from being silently reset.

    Regression guard for #780: the old code omitted state on the ensure_enabled=False
    path, which caused the Fabric API to reset state to its default (Disabled).
    """
    groups = ["BATCH_COMPLETED_GROUP"]
    current_payload = AUDIT_SETTINGS_PAYLOAD.copy()  # state == "Enabled", retentionDays == 30

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
    # state and retentionDays must be round-tripped to prevent Fabric API resets.
    assert sent_body.get("state") == current_payload["state"]
    assert sent_body.get("retentionDays") == current_payload["retentionDays"]
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


async def test_set_retention_preserves_action_groups_and_state() -> None:
    """set_retention PATCH must include auditActionsAndGroups and state alongside retentionDays.

    Regression guard: the Fabric API silently resets auditActionsAndGroups to
    defaults when the field is omitted from a partial PATCH.  set_retention must
    round-trip the current groups (sourced from the _require_enabled pre-flight GET)
    to prevent data-loss.  Two GETs are issued: one pre-flight (via _require_enabled)
    and one re-fetch after the PATCH.
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
    # Primary assertion: PATCH body must include all three fields so no data is lost.
    assert sent_body == {
        "state": "Enabled",
        "retentionDays": 90,
        "auditActionsAndGroups": [
            "BATCH_COMPLETED_GROUP",
            "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
        ],
    }

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


# ---------------------------------------------------------------------------
# enable — re-enable and first-time enable (group preservation / no-clobber)
# ---------------------------------------------------------------------------


async def test_enable_while_already_enabled_preserves_action_groups() -> None:
    """enable called while auditing is already enabled must not clobber action groups.

    Regression guard: calling enable() to bump retention on an already-enabled
    audit must not wipe the existing auditActionsAndGroups.  The pre-flight GET
    sees state=Enabled, so the PATCH body includes the current groups.
    """
    custom_groups = ["BATCH_COMPLETED_GROUP", "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"]
    current_payload = {
        "state": "Enabled",
        "retentionDays": 30,
        "auditActionsAndGroups": custom_groups,
    }
    updated_payload = {
        "state": "Enabled",
        "retentionDays": 90,
        "auditActionsAndGroups": custom_groups,
    }

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(
            side_effect=[
                httpx.Response(200, json=current_payload),  # pre-flight GET
                httpx.Response(200, json=updated_payload),  # re-fetch after PATCH
            ]
        )
        client = await _make_client()
        async with client:
            result = await audit.enable(client, _WS_ID, _WH_ID, retention_days=90)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    # Primary assertion: groups must be round-tripped so they are not lost.
    assert sent_body["auditActionsAndGroups"] == custom_groups
    assert sent_body["state"] == "Enabled"
    assert sent_body["retentionDays"] == 90
    assert result.action_groups == custom_groups


async def test_enable_while_disabled_sends_empty_action_groups() -> None:
    """enable on a disabled audit sends auditActionsAndGroups=[] in the PATCH body.

    The Fabric API resets any omitted field to defaults, so auditActionsAndGroups
    is always round-tripped.  When audit is Disabled the model field defaults to
    an empty list (default_factory=list), so the PATCH body includes [] rather
    than omitting the field.  This is the safe no-op value for a first-time enable.
    """
    enabled_response = {
        "state": "Enabled",
        "retentionDays": 0,
        "auditActionsAndGroups": [],
    }

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(
            side_effect=[
                httpx.Response(200, json=AUDIT_SETTINGS_DISABLED_PAYLOAD),  # pre-flight GET
                httpx.Response(200, json=enabled_response),  # re-fetch after PATCH
            ]
        )
        client = await _make_client()
        async with client:
            await audit.enable(client, _WS_ID, _WH_ID)

    sent_body = json.loads(patch_route.calls[0].request.content)
    # auditActionsAndGroups is always present (empty list from the Disabled pre-flight GET).
    assert sent_body == {"state": "Enabled", "retentionDays": 0, "auditActionsAndGroups": []}


# ---------------------------------------------------------------------------
# SQL Analytics Endpoint — kind-aware routing
# Verifies the 'sqlEndpoints' collection segment is used (not 'warehouses').
# See: _EP_AUDIT_URL / _EP_AUDIT_PATH constants above.
# ---------------------------------------------------------------------------


async def test_endpoint_get_settings_uses_sql_endpoints_collection() -> None:
    """get_settings with SQL_ENDPOINT kind must call /sqlEndpoints/{id}/settings/sqlAudit."""
    with respx.mock:
        ep_route = respx.get(_EP_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD)
        )
        # Ensure the warehouse path is NOT called.
        wh_route = respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.get_settings(client, _WS_ID, _EP_ID, WarehouseKind.SQL_ENDPOINT)

    assert ep_route.called, "Expected GET on /sqlEndpoints/ path"
    assert not wh_route.called, "Must NOT hit /warehouses/ path for SQL_ENDPOINT"
    assert isinstance(result, AuditSettings)
    assert result.state == "Enabled"


async def test_endpoint_enable_uses_sql_endpoints_collection() -> None:
    """enable with SQL_ENDPOINT kind must PATCH /sqlEndpoints/{id}/settings/sqlAudit.

    Two GETs are issued (pre-flight + re-fetch); the same mock handles both.
    The PATCH body includes auditActionsAndGroups because the pre-flight GET
    returns state=Enabled (AUDIT_SETTINGS_PAYLOAD).
    """
    with respx.mock:
        patch_route = respx.patch(_EP_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        get_route = respx.get(_EP_AUDIT_URL).mock(
            return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD)
        )
        client = await _make_client()
        async with client:
            result = await audit.enable(
                client, _WS_ID, _EP_ID, WarehouseKind.SQL_ENDPOINT, retention_days=7
            )

    assert patch_route.called
    assert get_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body == {
        "state": "Enabled",
        "retentionDays": 7,
        "auditActionsAndGroups": [
            "BATCH_COMPLETED_GROUP",
            "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
        ],
    }
    assert isinstance(result, AuditSettings)


async def test_endpoint_disable_uses_sql_endpoints_collection() -> None:
    """disable with SQL_ENDPOINT kind must PATCH /sqlEndpoints/{id}/settings/sqlAudit.

    Also verifies that the PATCH body preserves retentionDays and auditActionsAndGroups
    sourced from the pre-flight GET (fix for #780).  Non-default values are used so a
    buggy impl that hardcodes zeros/empty fails the value assertions.
    """
    # Non-default pre-PATCH state: currently Enabled with custom retention + groups.
    _ep_pre_patch = {
        "state": "Enabled",
        "retentionDays": 42,
        "auditActionsAndGroups": ["BATCH_COMPLETED_GROUP"],
    }
    _ep_post_patch = {**_ep_pre_patch, "state": "Disabled"}

    with respx.mock:
        patch_route = respx.patch(_EP_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        # Two GETs: pre-flight reads current state, re-fetch reads post-PATCH state.
        get_route = respx.get(_EP_AUDIT_URL).mock(
            side_effect=[
                httpx.Response(200, json=_ep_pre_patch),  # pre-flight GET
                httpx.Response(200, json=_ep_post_patch),  # re-fetch after PATCH
            ]
        )
        client = await _make_client()
        async with client:
            result = await audit.disable(client, _WS_ID, _EP_ID, WarehouseKind.SQL_ENDPOINT)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    # Non-default values prove the pre-flight GET is actually used and the correct
    # URL segment (/sqlEndpoints/) is used for both GET and PATCH.
    assert sent_body == {
        "state": "Disabled",
        "retentionDays": 42,
        "auditActionsAndGroups": ["BATCH_COMPLETED_GROUP"],
    }
    # Two GETs must be issued: one pre-flight + one re-fetch after PATCH.
    assert get_route.call_count == 2
    assert isinstance(result, AuditSettings)
    assert result.state == "Disabled"


async def test_endpoint_set_retention_uses_sql_endpoints_collection() -> None:
    """set_retention with SQL_ENDPOINT kind must PATCH /sqlEndpoints/{id}/settings/sqlAudit.

    Also verifies that the PATCH body preserves auditActionsAndGroups and state.
    """
    enabled = AUDIT_SETTINGS_PAYLOAD.copy()
    updated = AUDIT_SETTINGS_PAYLOAD.copy()
    updated["retentionDays"] = 14

    with respx.mock:
        patch_route = respx.patch(_EP_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_EP_AUDIT_URL).mock(
            side_effect=[
                httpx.Response(200, json=enabled),  # pre-flight GET
                httpx.Response(200, json=updated),  # re-fetch after PATCH
            ]
        )
        client = await _make_client()
        async with client:
            result = await audit.set_retention(
                client, _WS_ID, _EP_ID, WarehouseKind.SQL_ENDPOINT, days=14
            )

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body == {
        "state": "Enabled",
        "retentionDays": 14,
        "auditActionsAndGroups": [
            "BATCH_COMPLETED_GROUP",
            "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
        ],
    }
    assert result.retention_days == 14


async def test_endpoint_set_action_groups_uses_sql_endpoints_collection() -> None:
    """set_action_groups with SQL_ENDPOINT kind must PATCH /sqlEndpoints/{id}/settings/sqlAudit."""
    groups = ["BATCH_COMPLETED_GROUP"]
    with respx.mock:
        respx.get(_EP_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        patch_route = respx.patch(_EP_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.set_action_groups(
                client, _WS_ID, _EP_ID, groups, WarehouseKind.SQL_ENDPOINT
            )

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body["auditActionsAndGroups"] == groups
    assert sent_body.get("state") == "Enabled"
    assert isinstance(result, AuditSettings)
    assert result.action_groups == groups


async def test_endpoint_add_action_group_uses_sql_endpoints_collection() -> None:
    """add_action_group with SQL_ENDPOINT kind must PATCH /sqlEndpoints/{id}/settings/sqlAudit."""
    new_group = "FAILED_DATABASE_AUTHENTICATION_GROUP"
    with respx.mock:
        respx.get(_EP_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        patch_route = respx.patch(_EP_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.add_action_group(
                client, _WS_ID, _EP_ID, new_group, WarehouseKind.SQL_ENDPOINT
            )

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert new_group in sent_body["auditActionsAndGroups"]
    assert new_group in result.action_groups


async def test_endpoint_remove_action_group_uses_sql_endpoints_collection() -> None:
    """remove_action_group with SQL_ENDPOINT kind must PATCH /sqlEndpoints/{id}/settings/sqlAudit.

    Verifies that the correct collection segment is used in the PATCH URL.
    """
    group_to_remove = "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    with respx.mock:
        respx.get(_EP_AUDIT_URL).mock(return_value=httpx.Response(200, json=AUDIT_SETTINGS_PAYLOAD))
        patch_route = respx.patch(_EP_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            result = await audit.remove_action_group(
                client, _WS_ID, _EP_ID, group_to_remove, WarehouseKind.SQL_ENDPOINT
            )

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert group_to_remove not in sent_body["auditActionsAndGroups"]
    assert group_to_remove not in result.action_groups


def test_audit_path_warehouse_uses_warehouses_segment() -> None:
    """_audit_path must use 'warehouses' segment for WAREHOUSE kind."""
    from fabric_dw.services.audit import _audit_path  # noqa: PLC0415

    path = _audit_path(_WS_ID, _WH_ID, WarehouseKind.WAREHOUSE)
    assert "/warehouses/" in path
    assert "/sqlEndpoints/" not in path


def test_audit_path_sql_endpoint_uses_sql_endpoints_segment() -> None:
    """_audit_path must use 'sqlEndpoints' segment for SQL_ENDPOINT kind."""
    from fabric_dw.services.audit import _audit_path  # noqa: PLC0415

    path = _audit_path(_WS_ID, _EP_ID, WarehouseKind.SQL_ENDPOINT)
    assert "/sqlEndpoints/" in path
    assert "/warehouses/" not in path
    assert str(_EP_ID) in path


def test_audit_path_snapshot_raises_value_error() -> None:
    """_audit_path must reject WarehouseKind.SNAPSHOT with a clear ValueError.

    SQL audit is not supported on warehouse snapshots; without this guard a
    snapshot would fall through to the /warehouses/ route and 404 cryptically.
    """
    from fabric_dw.services.audit import _audit_path  # noqa: PLC0415

    with pytest.raises(ValueError, match="snapshot"):
        _audit_path(_WS_ID, _WH_ID, WarehouseKind.SNAPSHOT)


async def test_get_settings_snapshot_raises_value_error() -> None:
    """get_settings must reject a SNAPSHOT-kind item before issuing any request."""
    with respx.mock:
        # No route registered: if a request were issued, respx would raise.
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="snapshot"):
                await audit.get_settings(client, _WS_ID, _WH_ID, WarehouseKind.SNAPSHOT)


async def test_enable_snapshot_raises_value_error() -> None:
    """enable must reject a SNAPSHOT-kind item before issuing any request."""
    with respx.mock:
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="snapshot"):
                await audit.enable(client, _WS_ID, _WH_ID, WarehouseKind.SNAPSHOT)


async def test_disable_snapshot_raises_value_error() -> None:
    """disable must reject a SNAPSHOT-kind item before issuing any request."""
    with respx.mock:
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="snapshot"):
                await audit.disable(client, _WS_ID, _WH_ID, WarehouseKind.SNAPSHOT)


async def test_set_action_groups_snapshot_raises_value_error() -> None:
    """set_action_groups must reject a SNAPSHOT-kind item before issuing any request."""
    with respx.mock:
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="snapshot"):
                await audit.set_action_groups(
                    client, _WS_ID, _WH_ID, ["BATCH_COMPLETED_GROUP"], WarehouseKind.SNAPSHOT
                )


# ---------------------------------------------------------------------------
# Regression tests — #780: partial-PATCH data-loss on group/disable mutations
# ---------------------------------------------------------------------------


async def test_add_action_group_preserves_state_and_retention_in_patch_body() -> None:
    """add_action_group PATCH body must include state and retentionDays alongside the group list.

    Regression guard for #780: the old code omitted state and retentionDays, causing the
    Fabric API to silently reset both fields to their defaults whenever a group was added.
    """
    new_group = "FAILED_DATABASE_AUTHENTICATION_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # state=Enabled, retentionDays=30

    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=existing))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            await audit.add_action_group(client, _WS_ID, _WH_ID, new_group)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    # Primary assertion: state and retentionDays must be round-tripped.
    assert sent_body.get("state") == existing["state"], (
        f"state must be preserved in PATCH body, got: {sent_body}"
    )
    assert sent_body.get("retentionDays") == existing["retentionDays"], (
        f"retentionDays must be preserved in PATCH body, got: {sent_body}"
    )
    assert new_group in sent_body.get("auditActionsAndGroups", [])


async def test_remove_action_group_preserves_state_and_retention_in_patch_body() -> None:
    """remove_action_group PATCH body must include state and retentionDays alongside the group list.

    Regression guard for #780: the old code omitted state and retentionDays, causing the
    Fabric API to silently reset both fields to their defaults whenever a group was removed.
    """
    group_to_remove = "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # state=Enabled, retentionDays=30

    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=existing))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            await audit.remove_action_group(client, _WS_ID, _WH_ID, group_to_remove)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    # Primary assertion: state and retentionDays must be round-tripped.
    assert sent_body.get("state") == existing["state"], (
        f"state must be preserved in PATCH body, got: {sent_body}"
    )
    assert sent_body.get("retentionDays") == existing["retentionDays"], (
        f"retentionDays must be preserved in PATCH body, got: {sent_body}"
    )
    assert group_to_remove not in sent_body.get("auditActionsAndGroups", [])


async def test_set_action_groups_preserves_retention_in_patch_body() -> None:
    """set_action_groups PATCH body must include retentionDays so it is not silently reset.

    Regression guard for #780: the old code omitted retentionDays from the PATCH body,
    causing the Fabric API to reset it to its default value on every set-groups call.
    """
    groups = ["BATCH_COMPLETED_GROUP"]
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # retentionDays=30

    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=existing))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            await audit.set_action_groups(client, _WS_ID, _WH_ID, groups)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body.get("retentionDays") == existing["retentionDays"], (
        f"retentionDays must be preserved in PATCH body, got: {sent_body}"
    )


async def test_disable_preserves_retention_and_groups_in_patch_body() -> None:
    """disable PATCH body must include retentionDays and auditActionsAndGroups.

    Regression guard for #780: the old code sent only state=Disabled, causing the
    Fabric API to silently reset retentionDays and auditActionsAndGroups to their
    defaults whenever auditing was disabled.  A subsequent re-enable would then
    start with blank retention and no custom action groups.
    """
    existing = AUDIT_SETTINGS_PAYLOAD.copy()  # state=Enabled, retentionDays=30, groups=[...]

    with respx.mock:
        # Single mock handles both the pre-flight GET and the re-fetch GET.
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=existing))
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        client = await _make_client()
        async with client:
            await audit.disable(client, _WS_ID, _WH_ID)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body.get("state") == "Disabled"
    # Primary assertions: retentionDays and groups must be round-tripped from the
    # pre-flight GET so the Fabric API does not silently reset them.
    assert sent_body.get("retentionDays") == existing["retentionDays"], (
        f"retentionDays must be preserved on disable, got: {sent_body}"
    )
    assert sent_body.get("auditActionsAndGroups") == existing["auditActionsAndGroups"], (
        f"auditActionsAndGroups must be preserved on disable, got: {sent_body}"
    )


# ---------------------------------------------------------------------------
# Regression tests — #853: partial GET body (missing retentionDays) crashes
# get_settings and disable via ValidationError
# ---------------------------------------------------------------------------


async def test_get_settings_partial_body_no_retention_days_defaults_to_zero() -> None:
    """get_settings must not raise when the API returns a body without retentionDays.

    The Fabric sqlAudit endpoint may return a partial body that omits
    retentionDays entirely.  Per Microsoft Learn, the field defaults to 0
    (unlimited retention).  Before #853, AuditSettings.retention_days was a
    required field, so a partial response caused ValidationError and crashed
    get_settings.
    """
    partial_payload = {"state": "Disabled"}  # retentionDays absent

    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=partial_payload))
        client = await _make_client()
        async with client:
            result = await audit.get_settings(client, _WS_ID, _WH_ID)

    assert isinstance(result, AuditSettings)
    assert result.retention_days == 0


async def test_get_settings_full_body_explicit_retention_days_is_honoured() -> None:
    """get_settings must honour an explicit retentionDays value in the response body."""
    payload = {"state": "Enabled", "retentionDays": 45, "auditActionsAndGroups": []}

    with respx.mock:
        respx.get(_AUDIT_URL).mock(return_value=httpx.Response(200, json=payload))
        client = await _make_client()
        async with client:
            result = await audit.get_settings(client, _WS_ID, _WH_ID)

    assert result.retention_days == 45


async def test_disable_partial_get_body_does_not_crash() -> None:
    """disable must not crash when the pre-flight GET returns a body without retentionDays.

    Regression guard for #853: before the fix, AuditSettings.retention_days was
    required, so the partial GET body caused a ValidationError inside get_settings,
    which is called by disable as its pre-flight read.  The fix makes retention_days
    optional with default 0 (unlimited), matching the documented API default.

    The PATCH body must forward retentionDays=0 (the resolved default) so the
    Fabric API does not receive an ambiguous partial body from our side either.
    """
    partial_pre_flight = {"state": "Enabled"}  # retentionDays absent
    post_patch = {"state": "Disabled", "retentionDays": 0, "auditActionsAndGroups": []}

    with respx.mock:
        patch_route = respx.patch(_AUDIT_URL).mock(return_value=httpx.Response(200, json={}))
        respx.get(_AUDIT_URL).mock(
            side_effect=[
                httpx.Response(200, json=partial_pre_flight),  # pre-flight GET
                httpx.Response(200, json=post_patch),  # re-fetch after PATCH
            ]
        )
        client = await _make_client()
        async with client:
            result = await audit.disable(client, _WS_ID, _WH_ID)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    # retentionDays must be 0 (the default resolved from the partial GET body).
    assert sent_body.get("retentionDays") == 0
    assert result.state == "Disabled"
