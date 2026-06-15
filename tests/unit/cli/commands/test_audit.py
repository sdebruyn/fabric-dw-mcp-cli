"""Tests for audit CLI sub-commands — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from click.testing import CliRunner

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import AuditSettings, WarehouseKind
from tests.fixtures.api_payloads import AUDIT_SETTINGS_PAYLOAD

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)


def _make_cm(http: object, _sql: object = None) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


def _make_audit_settings() -> AuditSettings:
    return AuditSettings.model_validate(json.loads(AUDIT_SETTINGS_PAYLOAD))


def _make_item_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


class TestAuditGet:
    """audit get — happy path and 404."""

    def test_get_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--json", "audit", "get", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["state"] == "Enabled"
        assert parsed["retentionDays"] == 30

    def test_get_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--json", "audit", "get", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_get_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["audit", "get", WS_GUID, WH_GUID])
        assert result.exit_code != 0


class TestAuditEnable:
    """audit enable — happy path."""

    def test_enable_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        mock_enable = AsyncMock(return_value=_make_audit_settings())
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch("fabric_dw.cli.commands.audit._audit_svc.enable", new=mock_enable),
        ):
            result = runner.invoke(cli, ["--json", "audit", "enable", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        mock_enable.assert_awaited_once()
        parsed = json.loads(result.output)
        assert parsed["state"] == "Enabled"

    def test_enable_with_retention_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        mock_enable = AsyncMock(return_value=_make_audit_settings())
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch("fabric_dw.cli.commands.audit._audit_svc.enable", new=mock_enable),
        ):
            result = runner.invoke(
                cli, ["audit", "enable", WS_GUID, WH_GUID, "--retention-days", "30"]
            )
        assert result.exit_code == 0
        mock_enable.assert_awaited_once()
        _, kwargs = mock_enable.call_args
        assert kwargs.get("retention_days") == 30

    def test_enable_with_unlimited_flag_exits_zero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--unlimited flag sets retention to 0 (service convention for unlimited)."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        audit_settings = AuditSettings.model_validate(json.loads(AUDIT_SETTINGS_PAYLOAD))
        mock_enable = AsyncMock(return_value=audit_settings)
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.cli.commands.audit._audit_svc.enable",
                new=mock_enable,
            ),
        ):
            result = runner.invoke(cli, ["audit", "enable", WS_GUID, WH_GUID, "--unlimited"])
        assert result.exit_code == 0
        mock_enable.assert_awaited_once()
        _, kwargs = mock_enable.call_args
        assert kwargs["retention_days"] == 0

    def test_enable_retention_and_unlimited_mutual_exclusion(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Providing both --retention-days and --unlimited is a usage error."""
        _ = cache_env
        result = runner.invoke(
            cli,
            ["audit", "enable", WS_GUID, WH_GUID, "--retention-days", "30", "--unlimited"],
        )
        assert result.exit_code != 0

    def test_enable_retention_days_zero_is_rejected(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--retention-days 0 must be rejected with a usage error (click.IntRange >= 1)."""
        _ = cache_env
        result = runner.invoke(cli, ["audit", "enable", WS_GUID, WH_GUID, "--retention-days", "0"])
        assert result.exit_code != 0
        # click.IntRange validation message; the hint to use --unlimited is in the --help text.
        assert "x>=1" in result.output or ">=1" in result.output or "range" in result.output

    def test_enable_retention_days_negative_is_rejected(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Negative --retention-days must be rejected with a usage error."""
        _ = cache_env
        result = runner.invoke(cli, ["audit", "enable", WS_GUID, WH_GUID, "--retention-days", "-1"])
        assert result.exit_code != 0
        assert "range" in result.output


class TestAuditDisable:
    """audit disable — happy path and confirmation."""

    def test_disable_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        mock_disable = AsyncMock(return_value=_make_audit_settings())
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch("fabric_dw.cli.commands.audit._audit_svc.disable", new=mock_disable),
        ):
            result = runner.invoke(cli, ["--yes", "audit", "disable", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        mock_disable.assert_awaited_once()

    def test_disable_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining disable is a clean no-op (exit 0, policy: decline != error)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["audit", "disable", WS_GUID, WH_GUID], input="n\n")
        assert result.exit_code == 0
        assert "Aborted." in result.output


class TestAuditSetRetention:
    """audit set-retention — happy path, out-of-range, disabled-audit."""

    def test_set_retention_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        mock_set_retention = AsyncMock(return_value=_make_audit_settings())
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch("fabric_dw.cli.commands.audit._audit_svc.set_retention", new=mock_set_retention),
        ):
            result = runner.invoke(
                cli, ["audit", "set-retention", WS_GUID, WH_GUID, "--days", "90"]
            )
        assert result.exit_code == 0
        mock_set_retention.assert_awaited_once()
        _, kwargs = mock_set_retention.call_args
        assert kwargs.get("days") == 90

    def test_set_retention_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "audit", "set-retention", WS_GUID, WH_GUID, "--days", "30"]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_set_retention_out_of_range_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.cli.commands.audit._audit_svc.set_retention",
                new=AsyncMock(side_effect=FabricError("retentionDays value is out of range")),
            ),
        ):
            result = runner.invoke(
                cli, ["audit", "set-retention", WS_GUID, WH_GUID, "--days", "9999"]
            )
        assert result.exit_code != 0

    def test_set_retention_zero_is_rejected(self, runner: CliRunner, cache_env: Path) -> None:
        """--days 0 must be rejected client-side (L24: >= 1 validation)."""
        _ = cache_env
        result = runner.invoke(cli, ["audit", "set-retention", WS_GUID, WH_GUID, "--days", "0"])
        assert result.exit_code != 0
        # click.IntRange validation message should appear.
        assert "range" in result.output

    def test_set_retention_negative_is_rejected(self, runner: CliRunner, cache_env: Path) -> None:
        """--days -1 must be rejected client-side (L24: >= 1 validation)."""
        _ = cache_env
        result = runner.invoke(cli, ["audit", "set-retention", WS_GUID, WH_GUID, "--days", "-1"])
        assert result.exit_code != 0
        assert "range" in result.output

    def test_set_retention_no_precheck_sends_patch_directly(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """set-retention no longer pre-checks audit state; PATCH is sent regardless.

        The old pre-flight GET was racy.  The new behaviour sends the PATCH and lets
        the server reject invalid states.  This test verifies the command succeeds when
        both PATCH and the follow-up GET return valid responses.
        """
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        mock_set_retention = AsyncMock(return_value=_make_audit_settings())
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch("fabric_dw.cli.commands.audit._audit_svc.set_retention", new=mock_set_retention),
        ):
            result = runner.invoke(
                cli, ["audit", "set-retention", WS_GUID, WH_GUID, "--days", "30"]
            )
        assert result.exit_code == 0
        # No pre-check GET — service is called exactly once (the PATCH)
        mock_set_retention.assert_awaited_once()


class TestAuditSetGroups:
    """audit set-groups — happy path."""

    def test_set_groups_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        mock_set_groups = AsyncMock(return_value=_make_audit_settings())
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch("fabric_dw.cli.commands.audit._audit_svc.set_action_groups", new=mock_set_groups),
        ):
            result = runner.invoke(
                cli,
                [
                    "audit",
                    "set-groups",
                    WS_GUID,
                    WH_GUID,
                    "--group",
                    "BATCH_COMPLETED_GROUP",
                    "--group",
                    "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
                ],
            )
        assert result.exit_code == 0
        mock_set_groups.assert_awaited_once()
        # Verify both groups were forwarded to the service
        groups_arg = mock_set_groups.call_args.args[3]
        assert "BATCH_COMPLETED_GROUP" in groups_arg
        assert "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP" in groups_arg

    def test_set_groups_invalid_name_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "audit",
                    "set-groups",
                    WS_GUID,
                    WH_GUID,
                    "--group",
                    "invalid-lowercase-group",
                ],
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Default fallback tests
# ---------------------------------------------------------------------------


class TestAuditAddGroup:
    """audit add-group — happy path and error cases."""

    def test_add_group_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        mock_add_group = AsyncMock(return_value=_make_audit_settings())
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch("fabric_dw.cli.commands.audit._audit_svc.add_action_group", new=mock_add_group),
        ):
            result = runner.invoke(
                cli,
                [
                    "audit",
                    "add-group",
                    WS_GUID,
                    WH_GUID,
                    "BATCH_COMPLETED_GROUP",
                ],
            )
        assert result.exit_code == 0
        mock_add_group.assert_awaited_once()
        # Group name must have been forwarded
        assert mock_add_group.call_args.args[3] == "BATCH_COMPLETED_GROUP"

    def test_add_group_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--json", "audit", "add-group", WS_GUID, WH_GUID, "BATCH_COMPLETED_GROUP"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_add_group_invalid_name_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["audit", "add-group", WS_GUID, WH_GUID, "invalid-lowercase"],
            )
        assert result.exit_code != 0

    def test_add_group_disabled_audit_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.cli.commands.audit._audit_svc.add_action_group",
                new=AsyncMock(side_effect=ValueError("audit is disabled; enable first")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["audit", "add-group", WS_GUID, WH_GUID, "BATCH_COMPLETED_GROUP"],
            )
        assert result.exit_code != 0


class TestAuditRemoveGroup:
    """audit remove-group — happy path and error cases."""

    def test_remove_group_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        settings = AuditSettings.model_validate(json.loads(AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(AsyncMock(), None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.cli.commands.audit._audit_svc.remove_action_group",
                new=AsyncMock(return_value=settings),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "audit",
                    "remove-group",
                    WS_GUID,
                    WH_GUID,
                    "BATCH_COMPLETED_GROUP",
                ],
            )
        assert result.exit_code == 0

    def test_remove_group_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        settings = AuditSettings.model_validate(json.loads(AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(AsyncMock(), None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.cli.commands.audit._audit_svc.remove_action_group",
                new=AsyncMock(return_value=settings),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--json", "audit", "remove-group", WS_GUID, WH_GUID, "BATCH_COMPLETED_GROUP"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_remove_group_invalid_name_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["audit", "remove-group", WS_GUID, WH_GUID, "invalid-lowercase"],
            )
        assert result.exit_code != 0

    def test_remove_group_disabled_audit_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.cli.commands.audit._audit_svc.remove_action_group",
                new=AsyncMock(side_effect=ValueError("audit is disabled; enable first")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["audit", "remove-group", WS_GUID, WH_GUID, "BATCH_COMPLETED_GROUP"],
            )
        assert result.exit_code != 0


class TestAuditDefaultFallback:
    """Verify that workspace/warehouse defaults from config are used when arg is omitted."""

    def test_get_uses_config_defaults(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        runner.invoke(cli, ["config", "set", "workspace", WS_GUID])
        runner.invoke(cli, ["config", "set", "warehouse", WH_GUID])
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        mock_resolve = AsyncMock(return_value=(WS_UUID, _make_item_entry()))
        with (
            patch(
                "fabric_dw.cli.commands.audit.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit.resolve_item",
                new=mock_resolve,
            ),
        ):
            result = runner.invoke(cli, ["--json", "audit", "get"])
        assert result.exit_code == 0
        # resolve_item was called — workspace/warehouse resolved from config defaults
        mock_resolve.assert_awaited_once()
        parsed = json.loads(result.output)
        assert parsed["state"] == "Enabled"

    def test_get_missing_both_raises_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        result = runner.invoke(cli, ["audit", "get"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int, text: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json = MagicMock(return_value=json.loads(text) if text and text.strip() else {})
    mock_resp.headers = {}
    return mock_resp
