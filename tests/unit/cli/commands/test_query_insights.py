"""Tests for query-insights CLI sub-commands (TDD)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from click.testing import CliRunner

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.exceptions import NotFound, PermissionDenied
from fabric_dw.models import (
    ExecRequestHistory,
    ExecSessionHistory,
    FrequentlyRunQuery,
    LongRunningQuery,
    SqlPoolInsight,
    WarehouseKind,
)

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


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


def _make_request_history_row() -> ExecRequestHistory:
    return ExecRequestHistory.model_validate(
        {
            "status": "Succeeded",
            "session_id": 42,
            "total_elapsed_time_ms": 1500,
            "submit_time": _NOW.isoformat(),
        }
    )


def _make_session_history_row() -> ExecSessionHistory:
    return ExecSessionHistory.model_validate(
        {
            "session_id": 1,
            "connection_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "session_start_time": _NOW.isoformat(),
            "login_name": "user@example.com",
            "status": "Succeeded",
            "total_query_elapsed_time_ms": 2000,
            "last_request_start_time": _NOW.isoformat(),
            "is_user_process": True,
            "prev_error": 0,
            "group_id": 1,
            "text_size": 4096,
            "date_first": 7,
            "quoted_identifier": True,
            "arithabort": True,
            "ansi_null_dflt_on": True,
            "ansi_defaults": False,
            "ansi_warnings": True,
            "ansi_padding": True,
            "ansi_nulls": True,
            "concat_null_yields_null": True,
            "transaction_isolation_level": 2,
            "lock_timeout": -1,
            "deadlock_priority": 0,
        }
    )


def _make_frequent_query_row() -> FrequentlyRunQuery:
    return FrequentlyRunQuery.model_validate(
        {
            "number_of_runs": 42,
            "avg_total_elapsed_time_ms": 1500,
            "last_run_total_elapsed_time_ms": 1200,
            "min_run_total_elapsed_time_ms": 800,
            "max_run_total_elapsed_time_ms": 2000,
            "number_of_successful_runs": 40,
            "number_of_failed_runs": 1,
            "number_of_cancelled_runs": 1,
        }
    )


def _make_long_running_row() -> LongRunningQuery:
    return LongRunningQuery.model_validate(
        {
            "median_total_elapsed_time_ms": 30000,
            "number_of_runs": 5,
            "last_run_total_elapsed_time_ms": 28000,
        }
    )


def _make_pool_insight_row() -> SqlPoolInsight:
    return SqlPoolInsight.model_validate(
        {
            "sql_pool_name": "SELECT",
            "timestamp": _NOW.isoformat(),
            "max_resource_percentage": 100,
            "is_optimized_for_reads": True,
            "current_workspace_capacity": "F4",
            "is_pool_under_pressure": False,
        }
    )


# ---------------------------------------------------------------------------
# request-history
# ---------------------------------------------------------------------------


class TestRequestHistory:
    def test_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_request_history",
                new=AsyncMock(return_value=[_make_request_history_row()]),
            ),
        ):
            result = runner.invoke(cli, ["query-insights", "request-history", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_request_history",
                new=AsyncMock(return_value=[_make_request_history_row()]),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--json", "query-insights", "request-history", WS_GUID, WH_GUID],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(side_effect=NotFound("not found")),
            ),
        ):
            result = runner.invoke(cli, ["query-insights", "request-history", WS_GUID, WH_GUID])
        assert result.exit_code != 0

    def test_permission_denied_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_request_history",
                new=AsyncMock(side_effect=PermissionDenied("no permission")),
            ),
        ):
            result = runner.invoke(cli, ["query-insights", "request-history", WS_GUID, WH_GUID])
        assert result.exit_code != 0

    def test_invalid_since_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "query-insights",
                "request-history",
                WS_GUID,
                WH_GUID,
                "--since",
                "not-a-date",
            ],
        )
        assert result.exit_code != 0

    def test_invalid_until_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "query-insights",
                "request-history",
                WS_GUID,
                WH_GUID,
                "--until",
                "not-a-date",
            ],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# session-history
# ---------------------------------------------------------------------------


class TestSessionHistory:
    def test_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_session_history",
                new=AsyncMock(return_value=[_make_session_history_row()]),
            ),
        ):
            result = runner.invoke(cli, ["query-insights", "session-history", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_session_history",
                new=AsyncMock(return_value=[_make_session_history_row()]),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--json", "query-insights", "session-history", WS_GUID, WH_GUID],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# frequent
# ---------------------------------------------------------------------------


class TestFrequent:
    def test_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_frequent_queries",
                new=AsyncMock(return_value=[_make_frequent_query_row()]),
            ),
        ):
            result = runner.invoke(cli, ["query-insights", "frequent", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_frequent_queries",
                new=AsyncMock(return_value=[_make_frequent_query_row()]),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--json", "query-insights", "frequent", WS_GUID, WH_GUID],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# long-running
# ---------------------------------------------------------------------------


class TestLongRunning:
    def test_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_long_running_queries",
                new=AsyncMock(return_value=[_make_long_running_row()]),
            ),
        ):
            result = runner.invoke(cli, ["query-insights", "long-running", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_long_running_queries",
                new=AsyncMock(return_value=[_make_long_running_row()]),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--json", "query-insights", "long-running", WS_GUID, WH_GUID],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# pool-insights
# ---------------------------------------------------------------------------


class TestPoolInsights:
    def test_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_sql_pool_insights",
                new=AsyncMock(return_value=[_make_pool_insight_row()]),
            ),
        ):
            result = runner.invoke(cli, ["query-insights", "pool-insights", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_sql_pool_insights",
                new=AsyncMock(return_value=[_make_pool_insight_row()]),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--json", "query-insights", "pool-insights", WS_GUID, WH_GUID],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# Default fallback: workspace / warehouse from config
# ---------------------------------------------------------------------------


class TestQueryInsightsDefaultFallback:
    def test_request_history_uses_config_defaults(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        runner.invoke(cli, ["config", "set", "workspace", WS_GUID])
        runner.invoke(cli, ["config", "set", "warehouse", WH_GUID])
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.query_insights._build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.query_insights._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_request_history",
                new=AsyncMock(return_value=[_make_request_history_row()]),
            ),
        ):
            result = runner.invoke(cli, ["query-insights", "request-history"])
        assert result.exit_code == 0

    def test_missing_workspace_raises_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        result = runner.invoke(cli, ["query-insights", "request-history"])
        assert result.exit_code != 0
