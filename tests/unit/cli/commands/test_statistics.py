"""Unit tests for the statistics CLI sub-commands."""

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
from fabric_dw.exceptions import ItemKindError, NotFoundError
from fabric_dw.models import Statistic, StatisticDetails, StatisticHistogramStep, WarehouseKind
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
SE_GUID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)
SE_UUID = UUID(SE_GUID)

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


def _make_sql_endpoint_entry() -> ItemEntry:
    return ItemEntry(
        id=SE_UUID,
        kind=WarehouseKind.SQL_ENDPOINT,
        connection_string="se.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesLakehouse",
    )


def _make_statistic() -> Statistic:
    return Statistic(
        name="stat_sales_id",
        qualified_table="dbo.sales",
        column="id",
        auto_created=False,
        user_created=True,
        last_updated=_NOW,
        generation_method=None,
    )


def _make_statistic_details() -> StatisticDetails:
    return StatisticDetails(
        stat_header=None,
        density_vector=[],
        histogram=[
            StatisticHistogramStep(
                range_hi_key="100",
                range_rows=50.0,
                eq_rows=10.0,
                distinct_range_rows=5.0,
                avg_range_rows=10.0,
            ),
            StatisticHistogramStep(
                range_hi_key="200",
                range_rows=100.0,
                eq_rows=20.0,
                distinct_range_rows=10.0,
                avg_range_rows=10.0,
            ),
        ],
    )


# ===========================================================================
# statistics list
# ===========================================================================


class TestStatisticsList:
    def test_list_exits_zero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.list_statistics",
                new=AsyncMock(return_value=[_make_statistic()]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "statistics", "list", WH_GUID])
        assert result.exit_code == 0

    def test_list_json_output(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.list_statistics",
                new=AsyncMock(return_value=[_make_statistic()]),
            ),
        ):
            result = runner.invoke(cli, ["--json", "-w", WS_GUID, "statistics", "list", WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_with_schema_filter(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        mock_list = AsyncMock(return_value=[_make_statistic()])
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.statistics.list_statistics", new=mock_list),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "statistics", "list", WH_GUID, "--schema", "dbo"],
            )
        assert result.exit_code == 0
        mock_list.assert_awaited_once()

    def test_list_user_only_flag(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        mock_list = AsyncMock(return_value=[_make_statistic()])
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.statistics.list_statistics", new=mock_list),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "statistics", "list", WH_GUID, "--user-only"],
            )
        assert result.exit_code == 0
        _, kwargs = mock_list.call_args
        assert kwargs.get("user_only") is True

    def test_list_error_returns_nonzero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "statistics", "list", WH_GUID])
        assert result.exit_code != 0


# ===========================================================================
# statistics show
# ===========================================================================


class TestStatisticsShow:
    def test_show_exits_zero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.show_statistics",
                new=AsyncMock(return_value=_make_statistic_details()),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "statistics", "show", WH_GUID, "dbo.sales", "stat_sales_id"],
            )
        assert result.exit_code == 0

    def test_show_histogram_flag(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        mock_show = AsyncMock(return_value=_make_statistic_details())
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.statistics.show_statistics", new=mock_show),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "statistics",
                    "show",
                    WH_GUID,
                    "dbo.sales",
                    "stat_sales_id",
                    "--histogram",
                ],
            )
        assert result.exit_code == 0
        _, kwargs = mock_show.call_args
        assert kwargs.get("histogram_only") is True

    def test_show_bad_qualified_table_returns_nonzero(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "statistics", "show", WH_GUID, "nodot", "stat"],
        )
        assert result.exit_code != 0

    def test_show_json_output_is_valid_json(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.show_statistics",
                new=AsyncMock(return_value=_make_statistic_details()),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "-w",
                    WS_GUID,
                    "statistics",
                    "show",
                    WH_GUID,
                    "dbo.sales",
                    "stat_sales_id",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "histogram" in parsed
        assert isinstance(parsed["histogram"], list)
        assert len(parsed["histogram"]) == 2

    def test_show_json_structure_unchanged(self, runner: CliRunner) -> None:
        """JSON output from show must be byte-identical to model_dump serialization."""
        mock_http = AsyncMock()
        details = _make_statistic_details()
        expected = json.loads(
            json.dumps(details.model_dump(by_alias=True, mode="json"), indent=2, default=str)
        )
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.show_statistics",
                new=AsyncMock(return_value=details),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "-w",
                    WS_GUID,
                    "statistics",
                    "show",
                    WH_GUID,
                    "dbo.sales",
                    "stat_sales_id",
                ],
            )
        assert result.exit_code == 0
        actual = json.loads(result.output)
        assert actual == expected

    def test_show_non_json_contains_histogram_headers(self, runner: CliRunner) -> None:
        """Non-JSON output must include histogram column headers."""
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.show_statistics",
                new=AsyncMock(return_value=_make_statistic_details()),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "statistics", "show", WH_GUID, "dbo.sales", "stat_sales_id"],
            )
        assert result.exit_code == 0
        assert "RANGE_HI_KEY" in result.output
        assert "EQ_ROWS" in result.output


# ===========================================================================
# statistics create
# ===========================================================================


class TestStatisticsCreate:
    def test_create_exits_zero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.create_statistics",
                new=AsyncMock(return_value=_make_statistic()),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "statistics",
                    "create",
                    WH_GUID,
                    "--table",
                    "dbo.sales",
                    "--column",
                    "id",
                    "--name",
                    "stat_sales_id",
                ],
            )
        assert result.exit_code == 0

    def test_create_without_name_raises_usage_error(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "statistics",
                    "create",
                    WH_GUID,
                    "--table",
                    "dbo.sales",
                    "--column",
                    "id",
                ],
            )
        assert result.exit_code != 0

    def test_create_with_sample_percent(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        mock_create = AsyncMock(return_value=_make_statistic())
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.statistics.create_statistics", new=mock_create),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "statistics",
                    "create",
                    WH_GUID,
                    "--table",
                    "dbo.sales",
                    "--column",
                    "id",
                    "--name",
                    "s",
                    "--sample-percent",
                    "50",
                ],
            )
        assert result.exit_code == 0
        _, kwargs = mock_create.call_args
        assert kwargs.get("sample_percent") == 50

    def test_create_sql_endpoint_rejected(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_sql_endpoint_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.create_statistics",
                new=AsyncMock(side_effect=ItemKindError("read-only")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "statistics",
                    "create",
                    SE_GUID,
                    "--table",
                    "dbo.sales",
                    "--column",
                    "id",
                    "--name",
                    "s",
                ],
            )
        assert result.exit_code != 0


# ===========================================================================
# statistics update
# ===========================================================================


class TestStatisticsUpdate:
    def test_update_exits_zero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.update_statistics",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "statistics",
                    "update",
                    WH_GUID,
                    "dbo.sales",
                    "stat_sales_id",
                ],
            )
        assert result.exit_code == 0

    def test_update_json_output(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.update_statistics",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "-w",
                    WS_GUID,
                    "statistics",
                    "update",
                    WH_GUID,
                    "dbo.sales",
                    "stat_sales_id",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "updated"

    def test_update_sql_endpoint_rejected(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_sql_endpoint_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.update_statistics",
                new=AsyncMock(side_effect=ItemKindError("read-only")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "statistics", "update", SE_GUID, "dbo.sales", "s"],
            )
        assert result.exit_code != 0


# ===========================================================================
# statistics delete
# ===========================================================================


class TestStatisticsDelete:
    def test_delete_with_yes_flag_exits_zero(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.drop_statistics",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "statistics",
                    "delete",
                    WH_GUID,
                    "dbo.sales",
                    "stat_sales_id",
                ],
            )
        assert result.exit_code == 0

    def test_delete_json_output(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.drop_statistics",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "--yes",
                    "-w",
                    WS_GUID,
                    "statistics",
                    "delete",
                    WH_GUID,
                    "dbo.sales",
                    "stat_sales_id",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "dropped"

    def test_delete_aborted_without_yes(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "statistics", "delete", WH_GUID, "dbo.sales", "s"],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted" in result.output

    def test_delete_sql_endpoint_rejected(self, runner: CliRunner) -> None:
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.statistics.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.statistics.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_sql_endpoint_entry())),
            ),
            patch(
                "fabric_dw.services.statistics.drop_statistics",
                new=AsyncMock(side_effect=ItemKindError("read-only")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--yes", "-w", WS_GUID, "statistics", "delete", SE_GUID, "dbo.sales", "s"],
            )
        assert result.exit_code != 0
