"""Unit tests for the settings CLI sub-commands."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from click.testing import CliRunner

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.exceptions import FabricError
from fabric_dw.models import WarehouseKind, WarehouseSettings
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_sql_target() -> SqlTarget:
    return SqlTarget(
        workspace_id=WS_GUID,
        database="SalesWarehouse",
        connection_string="wh.datawarehouse.fabric.microsoft.com",
    )


def _make_http_cm(http: object) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


def _make_item_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


def _make_settings(
    *,
    result_set_caching: bool = True,
    days: int = 7,
) -> WarehouseSettings:
    return WarehouseSettings(
        database="SalesWarehouse",
        result_set_caching=result_set_caching,
        time_travel_retention_days=days,
        time_travel_retention_cutoff_date=_NOW,
    )


# ===========================================================================
# settings show
# ===========================================================================


class TestSettingsShow:
    def test_show_exits_zero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.settings.get_settings",
                new=AsyncMock(return_value=_make_settings()),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "settings", "show", WH_GUID])
        assert result.exit_code == 0

    def test_show_json_output(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.settings.get_settings",
                new=AsyncMock(return_value=_make_settings()),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "settings", "show", WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["database"] == "SalesWarehouse"
        assert parsed["result_set_caching"] is True
        assert parsed["time_travel_retention_days"] == 7

    def test_show_fabric_error_returns_nonzero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(side_effect=FabricError("connection error")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "settings", "show", WH_GUID])
        assert result.exit_code != 0


# ===========================================================================
# settings result-set-caching
# ===========================================================================


class TestResultSetCaching:
    def test_enable_exits_zero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.settings.set_result_set_caching",
                new=AsyncMock(return_value=_make_settings(result_set_caching=True)),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "settings", "result-set-caching", WH_GUID, "on"]
            )
        assert result.exit_code == 0
        assert "enabled" in result.output

    def test_disable_exits_zero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.settings.set_result_set_caching",
                new=AsyncMock(return_value=_make_settings(result_set_caching=False)),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "settings", "result-set-caching", WH_GUID, "off"]
            )
        assert result.exit_code == 0
        assert "disabled" in result.output

    def test_state_case_insensitive(self, runner: CliRunner) -> None:
        """ON / OFF are accepted case-insensitively."""
        mock_http = AsyncMock()
        mock_svc = AsyncMock(return_value=_make_settings(result_set_caching=True))
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.settings.set_result_set_caching", new=mock_svc),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "settings", "result-set-caching", WH_GUID, "ON"]
            )
        assert result.exit_code == 0
        _, kwargs = mock_svc.call_args
        assert kwargs.get("enabled") is True

    def test_json_output_includes_settings(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.settings.set_result_set_caching",
                new=AsyncMock(return_value=_make_settings(result_set_caching=True)),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "--json", "settings", "result-set-caching", WH_GUID, "on"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["result_set_caching"] is True

    def test_invalid_state_returns_nonzero(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli, ["-w", WS_GUID, "settings", "result-set-caching", WH_GUID, "maybe"]
        )
        assert result.exit_code != 0

    def test_fabric_error_returns_nonzero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(side_effect=FabricError("permission denied")),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "settings", "result-set-caching", WH_GUID, "on"]
            )
        assert result.exit_code != 0


# ===========================================================================
# settings retention
# ===========================================================================


class TestRetention:
    def test_valid_days_exits_zero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.settings.set_time_travel_retention",
                new=AsyncMock(return_value=_make_settings(days=30)),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "settings", "retention", WH_GUID, "--days", "30"]
            )
        assert result.exit_code == 0
        assert "30" in result.output

    def test_json_output_includes_settings(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.settings.set_time_travel_retention",
                new=AsyncMock(return_value=_make_settings(days=14)),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "--json", "settings", "retention", WH_GUID, "--days", "14"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["time_travel_retention_days"] == 14

    def test_days_required(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["-w", WS_GUID, "settings", "retention", WH_GUID])
        assert result.exit_code != 0

    @pytest.mark.parametrize("days", [0, 121])
    def test_out_of_range_days_returns_nonzero(self, runner: CliRunner, days: int) -> None:
        result = runner.invoke(
            cli, ["-w", WS_GUID, "settings", "retention", WH_GUID, "--days", str(days)]
        )
        assert result.exit_code != 0

    def test_boundary_1_accepted(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.settings.set_time_travel_retention",
                new=AsyncMock(return_value=_make_settings(days=1)),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "settings", "retention", WH_GUID, "--days", "1"]
            )
        assert result.exit_code == 0

    def test_boundary_120_accepted(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.settings.set_time_travel_retention",
                new=AsyncMock(return_value=_make_settings(days=120)),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "settings", "retention", WH_GUID, "--days", "120"]
            )
        assert result.exit_code == 0

    def test_fabric_error_returns_nonzero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.settings.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.settings.build_sql_target",
                new=AsyncMock(side_effect=FabricError("permission denied")),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "settings", "retention", WH_GUID, "--days", "7"]
            )
        assert result.exit_code != 0
