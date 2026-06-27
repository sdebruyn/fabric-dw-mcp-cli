"""Respx wire-validation tests for audit CLI sub-commands.

Validates:
* The exact URL constructed by each CLI command path
* The HTTP method used
* The JSON request body shape (for PATCH)
* Parsed output and exit code

Pattern
-------
1. Patch ``fabric_dw.cli.commands.audit.build_http_client`` with ``_real_http_client_cm``
   from conftest (real FabricHttpClient backed by a fake credential).
2. Intercept all httpx calls with ``respx.mock``.
3. Assert on ``respx.calls`` and captured bodies.

Commands covered
----------------
* ``audit enable``    — PATCH /workspaces/{ws}/warehouses/{wh}/settings/sqlAudit
* ``audit disable``   — PATCH (same URL, body ``{"state":"Disabled"}``)
* ``audit set-retention`` — PATCH (same URL, body ``{"retentionDays": N}``)
* ``audit add-group`` — PATCH (same URL, body with ``auditActionsAndGroups``)
* ``audit remove-group`` — PATCH (same URL, body with trimmed ``auditActionsAndGroups``)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import respx
from click.testing import CliRunner

from fabric_dw.cli._main import cli
from tests.fixtures.api_payloads import AUDIT_SETTINGS_PAYLOAD
from tests.unit.cli.commands.conftest import _real_http_client_cm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"

_BASE = "https://api.fabric.microsoft.com/v1"
_ITEMS_URL = f"{_BASE}/workspaces/{WS_GUID}/items/{WH_GUID}"
_WAREHOUSE_URL = f"{_BASE}/workspaces/{WS_GUID}/warehouses/{WH_GUID}"
_AUDIT_URL = f"{_BASE}/workspaces/{WS_GUID}/warehouses/{WH_GUID}/settings/sqlAudit"

_ITEMS_GENERIC_RESPONSE = {
    "id": WH_GUID,
    "displayName": "SalesWarehouse",
    "type": "Warehouse",
    "workspaceId": WS_GUID,
}

_WAREHOUSE_DETAIL = {
    "id": WH_GUID,
    "displayName": "SalesWarehouse",
    "type": "Warehouse",
    "workspaceId": WS_GUID,
    "properties": {
        "connectionString": "saleswarehouse.datawarehouse.fabric.microsoft.com",
    },
}

_AUDIT_SETTINGS_ENABLED = json.loads(AUDIT_SETTINGS_PAYLOAD)
# Disabled version for disable/add-group/remove-group tests
_AUDIT_SETTINGS_DISABLED = {**_AUDIT_SETTINGS_ENABLED, "state": "Disabled"}


def _setup_resolver_mocks(mock_router: respx.MockRouter) -> None:
    """Register the two resolver GET calls needed for every audit command."""
    mock_router.get(_ITEMS_URL).mock(return_value=httpx.Response(200, json=_ITEMS_GENERIC_RESPONSE))
    mock_router.get(_WAREHOUSE_URL).mock(return_value=httpx.Response(200, json=_WAREHOUSE_DETAIL))


# ---------------------------------------------------------------------------
# audit enable
# ---------------------------------------------------------------------------


class TestAuditEnableRespx:
    """Wire-validate that ``audit enable`` issues PATCH to the correct URL."""

    def test_enable_issues_patch_to_audit_url(self, runner: CliRunner, cache_env: Path) -> None:
        """PATCH must target /workspaces/{ws}/warehouses/{wh}/settings/sqlAudit."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(204)

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_resolver_mocks(mock_router)
            audit_patch = mock_router.patch(_AUDIT_URL).mock(side_effect=_capture)
            # Re-fetch after PATCH
            mock_router.get(_AUDIT_URL).mock(
                return_value=httpx.Response(200, json=_AUDIT_SETTINGS_ENABLED)
            )

            with patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["-w", WS_GUID, "--json", "audit", "enable", WH_GUID],
                )

        assert result.exit_code == 0, result.output
        assert audit_patch.called, f"Expected PATCH {_AUDIT_URL} to be called"
        assert len(captured_bodies) == 1, f"Expected 1 PATCH body, got {len(captured_bodies)}"
        body = captured_bodies[0]
        assert body.get("state") == "Enabled", f"Expected state=Enabled in body: {body}"
        # Production always sends retentionDays; omitting it would silently regress.
        # Default (no --retention-days / no --unlimited) maps to 0 (unlimited retention).
        assert "retentionDays" in body, f"Expected retentionDays in PATCH body: {body}"
        assert body["retentionDays"] == 0, (
            f"Expected retentionDays=0 (unlimited) when no flag supplied: {body}"
        )

    def test_enable_with_retention_days_sends_correct_body(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """PATCH body must include retentionDays when --retention-days is passed."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(204)

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_resolver_mocks(mock_router)
            audit_patch = mock_router.patch(_AUDIT_URL).mock(side_effect=_capture)
            mock_router.get(_AUDIT_URL).mock(
                return_value=httpx.Response(200, json=_AUDIT_SETTINGS_ENABLED)
            )

            with patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "-w",
                        WS_GUID,
                        "--json",
                        "audit",
                        "enable",
                        WH_GUID,
                        "--retention-days",
                        "30",
                    ],
                )

        assert result.exit_code == 0, result.output
        assert audit_patch.called
        body = captured_bodies[0]
        assert body.get("state") == "Enabled"
        assert body.get("retentionDays") == 30, f"Expected retentionDays=30: {body}"


# ---------------------------------------------------------------------------
# audit disable
# ---------------------------------------------------------------------------


class TestAuditDisableRespx:
    """Wire-validate that ``audit disable`` issues PATCH with state=Disabled."""

    def test_disable_patch_body_has_disabled_state(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """PATCH body must contain state='Disabled'."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(204)

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_resolver_mocks(mock_router)
            audit_patch = mock_router.patch(_AUDIT_URL).mock(side_effect=_capture)
            mock_router.get(_AUDIT_URL).mock(
                return_value=httpx.Response(200, json=_AUDIT_SETTINGS_DISABLED)
            )

            with patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["-w", WS_GUID, "--yes", "--json", "audit", "disable", WH_GUID],
                )

        assert result.exit_code == 0, result.output
        assert audit_patch.called, f"Expected PATCH {_AUDIT_URL} to be called"
        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        assert body.get("state") == "Disabled", f"Expected state=Disabled in body: {body}"
        # Must NOT include retentionDays for a plain disable
        assert "retentionDays" not in body, f"disable body must not include retentionDays: {body}"

    def test_disable_uses_correct_http_method(self, runner: CliRunner, cache_env: Path) -> None:
        """The audit disable request must use PATCH, not POST or PUT."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            _setup_resolver_mocks(mock_router)
            audit_patch = mock_router.patch(_AUDIT_URL).mock(return_value=httpx.Response(204))
            mock_router.get(_AUDIT_URL).mock(
                return_value=httpx.Response(200, json=_AUDIT_SETTINGS_DISABLED)
            )

            with patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["-w", WS_GUID, "--yes", "audit", "disable", WH_GUID],
                )

        assert result.exit_code == 0, result.output
        assert audit_patch.called, (
            "Expected PATCH to audit URL — wrong method would miss this route"
        )


# ---------------------------------------------------------------------------
# audit set-retention
# ---------------------------------------------------------------------------


class TestAuditSetRetentionRespx:
    """Wire-validate that ``audit set-retention`` sends retentionDays in the body."""

    def test_set_retention_body_contains_days(self, runner: CliRunner, cache_env: Path) -> None:
        """PATCH body must include retentionDays with the supplied value."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(204)

        # set_retention does a pre-flight GET to verify audit is enabled,
        # then PATCH, then a second GET to re-fetch settings.
        def _get_audit(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_AUDIT_SETTINGS_ENABLED)

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_resolver_mocks(mock_router)
            mock_router.get(_AUDIT_URL).mock(side_effect=_get_audit)
            audit_patch = mock_router.patch(_AUDIT_URL).mock(side_effect=_capture)

            with patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["-w", WS_GUID, "--json", "audit", "set-retention", WH_GUID, "--days", "90"],
                )

        assert result.exit_code == 0, result.output
        assert audit_patch.called, f"Expected PATCH {_AUDIT_URL} to be called"
        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        assert body.get("retentionDays") == 90, f"Expected retentionDays=90 in PATCH body: {body}"
        # set-retention re-asserts state=Enabled and round-trips auditActionsAndGroups so the
        # Fabric API does not silently reset either field on a partial PATCH (fix for #765).
        assert body.get("state") == "Enabled", f"Expected state=Enabled in PATCH body: {body}"
        assert "auditActionsAndGroups" in body, (
            f"Expected auditActionsAndGroups in PATCH body to preserve groups: {body}"
        )
        existing_groups: list[str] = _AUDIT_SETTINGS_ENABLED["auditActionsAndGroups"]  # type: ignore[assignment]
        assert body["auditActionsAndGroups"] == existing_groups, (
            f"auditActionsAndGroups must match pre-flight GET groups: {body}"
        )

    def test_set_retention_correct_url(self, runner: CliRunner, cache_env: Path) -> None:
        """The PATCH must target the exact audit settings URL."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            _setup_resolver_mocks(mock_router)
            mock_router.get(_AUDIT_URL).mock(
                return_value=httpx.Response(200, json=_AUDIT_SETTINGS_ENABLED)
            )
            audit_patch = mock_router.patch(_AUDIT_URL).mock(return_value=httpx.Response(204))

            with patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["-w", WS_GUID, "audit", "set-retention", WH_GUID, "--days", "7"],
                )

        assert result.exit_code == 0, result.output
        assert audit_patch.called, f"Expected PATCH {_AUDIT_URL}"


# ---------------------------------------------------------------------------
# audit add-group
# ---------------------------------------------------------------------------


class TestAuditAddGroupRespx:
    """Wire-validate that ``audit add-group`` sends the updated group list via PATCH."""

    def test_add_group_body_contains_updated_groups(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """PATCH body must include auditActionsAndGroups with the new group appended."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture_patch(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(204)

        new_group = "FAILED_DATABASE_AUTHENTICATION_GROUP"
        existing_groups: list[str] = _AUDIT_SETTINGS_ENABLED["auditActionsAndGroups"]  # type: ignore[assignment]

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_resolver_mocks(mock_router)
            # add_action_group does a pre-flight GET (_require_enabled)
            mock_router.get(_AUDIT_URL).mock(
                return_value=httpx.Response(200, json=_AUDIT_SETTINGS_ENABLED)
            )
            audit_patch = mock_router.patch(_AUDIT_URL).mock(side_effect=_capture_patch)

            with patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["-w", WS_GUID, "--json", "audit", "add-group", WH_GUID, new_group],
                )

        assert result.exit_code == 0, result.output
        assert audit_patch.called, f"Expected PATCH {_AUDIT_URL}"
        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        groups = body.get("auditActionsAndGroups")
        assert isinstance(groups, list), f"Expected list in auditActionsAndGroups: {body}"
        assert new_group in groups, f"New group {new_group!r} not found in PATCH body: {body}"
        # Existing groups must be preserved
        for g in existing_groups:
            assert g in groups, f"Existing group {g!r} was dropped from PATCH body: {body}"

    def test_add_group_uses_patch_not_post(self, runner: CliRunner, cache_env: Path) -> None:
        """add-group must use PATCH (not POST) — wrong method would miss the route."""
        _ = cache_env
        new_group = "FAILED_DATABASE_AUTHENTICATION_GROUP"

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_resolver_mocks(mock_router)
            mock_router.get(_AUDIT_URL).mock(
                return_value=httpx.Response(200, json=_AUDIT_SETTINGS_ENABLED)
            )
            audit_patch = mock_router.patch(_AUDIT_URL).mock(return_value=httpx.Response(204))

            with patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["-w", WS_GUID, "audit", "add-group", WH_GUID, new_group],
                )

        assert result.exit_code == 0, result.output
        assert audit_patch.called


# ---------------------------------------------------------------------------
# audit remove-group
# ---------------------------------------------------------------------------


class TestAuditRemoveGroupRespx:
    """Wire-validate that ``audit remove-group`` sends the trimmed group list via PATCH."""

    def test_remove_group_body_excludes_removed_group(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """PATCH body must not include the removed group in auditActionsAndGroups."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture_patch(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(204)

        existing_groups: list[str] = _AUDIT_SETTINGS_ENABLED["auditActionsAndGroups"]  # type: ignore[assignment]
        group_to_remove = existing_groups[0]
        remaining = [g for g in existing_groups if g != group_to_remove]

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_resolver_mocks(mock_router)
            mock_router.get(_AUDIT_URL).mock(
                return_value=httpx.Response(200, json=_AUDIT_SETTINGS_ENABLED)
            )
            audit_patch = mock_router.patch(_AUDIT_URL).mock(side_effect=_capture_patch)

            with patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["-w", WS_GUID, "--json", "audit", "remove-group", WH_GUID, group_to_remove],
                )

        assert result.exit_code == 0, result.output
        assert audit_patch.called, f"Expected PATCH {_AUDIT_URL}"
        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        groups = body.get("auditActionsAndGroups")
        assert isinstance(groups, list), f"Expected list in auditActionsAndGroups: {body}"
        assert group_to_remove not in groups, (
            f"Removed group {group_to_remove!r} still present in PATCH body: {body}"
        )
        for g in remaining:
            assert g in groups, f"Remaining group {g!r} was dropped from PATCH body: {body}"

    def test_remove_group_uses_patch_method(self, runner: CliRunner, cache_env: Path) -> None:
        """remove-group must use PATCH — wrong method would produce a 405/routing miss."""
        _ = cache_env
        group_to_remove = _AUDIT_SETTINGS_ENABLED["auditActionsAndGroups"][0]  # type: ignore[index]

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_resolver_mocks(mock_router)
            mock_router.get(_AUDIT_URL).mock(
                return_value=httpx.Response(200, json=_AUDIT_SETTINGS_ENABLED)
            )
            audit_patch = mock_router.patch(_AUDIT_URL).mock(return_value=httpx.Response(204))

            with patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["-w", WS_GUID, "audit", "remove-group", WH_GUID, group_to_remove],
                )

        assert result.exit_code == 0, result.output
        assert audit_patch.called
