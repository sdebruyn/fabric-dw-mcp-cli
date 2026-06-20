"""Tests for parse_duration, AGO_OPTION, and resolve_since in _utils.py.

Tests follow the TDD spec from issue #623.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import click
import pytest
from click.testing import CliRunner

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.cli.commands._utils import parse_duration, resolve_since
from fabric_dw.models import (
    ExecRequestHistory,
    ExecSessionHistory,
    FrequentlyRunQuery,
    LongRunningQuery,
    SqlPoolInsight,
    WarehouseKind,
)
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers shared with existing test_query_insights.py style
# ---------------------------------------------------------------------------


def _make_sql_target() -> SqlTarget:
    return SqlTarget(
        workspace_id=WS_GUID,
        database="SalesWarehouse",
        connection_string="wh.datawarehouse.fabric.microsoft.com",
    )


def _make_item_entry() -> ItemEntry:
    from uuid import UUID  # noqa: PLC0415

    return ItemEntry(
        id=UUID(WH_GUID),
        kind=WarehouseKind.WAREHOUSE,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


def _make_http_cm(http: object) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


def _make_request_history_row() -> ExecRequestHistory:
    return ExecRequestHistory.model_validate(
        {
            "status": "Succeeded",
            "session_id": 42,
            "total_elapsed_time_ms": 1500,
            "submit_time": _NOW.isoformat(),
            "row_count": 0,
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
            "original_security_id": b"\x01\x00",
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
            "number_of_canceled_runs": 1,
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
# parse_duration — valid single-unit forms
# ---------------------------------------------------------------------------


class TestParseDurationValidSingles:
    """parse_duration accepts single-unit strings for every supported unit."""

    def test_seconds(self) -> None:
        assert parse_duration("60s") == timedelta(seconds=60)

    def test_minutes(self) -> None:
        assert parse_duration("90m") == timedelta(minutes=90)

    def test_hours(self) -> None:
        assert parse_duration("1h") == timedelta(hours=1)

    def test_days(self) -> None:
        assert parse_duration("2d") == timedelta(days=2)

    def test_weeks(self) -> None:
        assert parse_duration("1w") == timedelta(weeks=1)

    def test_large_seconds(self) -> None:
        assert parse_duration("3600s") == timedelta(hours=1)

    def test_large_minutes(self) -> None:
        assert parse_duration("120m") == timedelta(hours=2)


# ---------------------------------------------------------------------------
# parse_duration — valid compound forms
# ---------------------------------------------------------------------------


class TestParseDurationValidCompounds:
    """parse_duration accepts compound strings like 1h30m or 2d12h."""

    def test_hours_and_minutes(self) -> None:
        assert parse_duration("1h30m") == timedelta(hours=1, minutes=30)

    def test_days_and_hours(self) -> None:
        assert parse_duration("2d12h") == timedelta(days=2, hours=12)

    def test_weeks_and_days(self) -> None:
        assert parse_duration("1w2d") == timedelta(weeks=1, days=2)

    def test_hours_minutes_seconds(self) -> None:
        assert parse_duration("1h30m45s") == timedelta(hours=1, minutes=30, seconds=45)

    def test_days_hours_minutes(self) -> None:
        assert parse_duration("1d2h3m") == timedelta(days=1, hours=2, minutes=3)


# ---------------------------------------------------------------------------
# parse_duration — invalid forms → UsageError
# ---------------------------------------------------------------------------


class TestParseDurationInvalid:
    """parse_duration raises click.UsageError for each invalid form."""

    def test_empty_string_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("")

    def test_bare_number_no_unit_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("60")

    def test_unknown_unit_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("1x")

    def test_fractional_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("1.5h")

    def test_negative_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("-5m")

    def test_zero_seconds_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("0s")

    def test_zero_minutes_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("0m")

    def test_zero_hours_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("0h")

    def test_error_message_contains_examples(self) -> None:
        with pytest.raises(click.UsageError) as exc_info:
            parse_duration("bad")
        msg = str(exc_info.value)
        # The error must contain at least one example.
        assert any(unit in msg for unit in ("1h", "90m", "3600s", "2d"))

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("  ")

    def test_unit_before_number_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("h1")

    def test_compound_with_unknown_unit_raises(self) -> None:
        with pytest.raises(click.UsageError):
            parse_duration("1h30x")

    def test_duplicate_hours_raises(self) -> None:
        with pytest.raises(click.UsageError, match="duplicate"):
            parse_duration("1h1h")

    def test_duplicate_minutes_raises(self) -> None:
        with pytest.raises(click.UsageError, match="duplicate"):
            parse_duration("30m30m")

    def test_duplicate_hours_different_values_raises(self) -> None:
        with pytest.raises(click.UsageError, match="duplicate"):
            parse_duration("2h1h")

    def test_duplicate_seconds_raises(self) -> None:
        with pytest.raises(click.UsageError, match="duplicate"):
            parse_duration("10s5s")

    def test_duplicate_days_raises(self) -> None:
        with pytest.raises(click.UsageError, match="duplicate"):
            parse_duration("1d2d")


# ---------------------------------------------------------------------------
# resolve_since
# ---------------------------------------------------------------------------


class TestResolveSince:
    """resolve_since merges --since and --ago into a single datetime | None."""

    def test_both_set_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="mutually exclusive"):
            resolve_since("2024-01-01T00:00:00", "1h")

    def test_ago_set_returns_now_minus_delta(self) -> None:
        before = datetime.now(UTC)
        result = resolve_since(None, "1h")
        after = datetime.now(UTC)
        assert result is not None
        expected_low = before - timedelta(hours=1)
        expected_high = after - timedelta(hours=1)
        assert expected_low <= result <= expected_high

    def test_ago_result_is_tz_aware_utc(self) -> None:
        result = resolve_since(None, "30m")
        assert result is not None
        assert result.tzinfo is not None
        # Should be within a small tolerance of now - 30m.
        expected = datetime.now(UTC) - timedelta(minutes=30)
        assert abs((result - expected).total_seconds()) < 5

    def test_since_set_returns_parsed_datetime(self) -> None:
        result = resolve_since("2024-06-01T10:00:00Z", None)
        assert result is not None
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 1

    def test_both_none_returns_none(self) -> None:
        assert resolve_since(None, None) is None

    def test_invalid_since_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError):
            resolve_since("not-a-date", None)

    def test_invalid_ago_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError):
            resolve_since(None, "bad")


# ---------------------------------------------------------------------------
# Per-command: queries history --ago
# ---------------------------------------------------------------------------


class TestHistoryCmdAgo:
    """queries history accepts --ago and rejects --since + --ago together."""

    def test_ago_1h_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.queries.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_request_history",
                new=AsyncMock(return_value=[_make_request_history_row()]),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "queries", "history", WH_GUID, "--ago", "1h"]
            )
        assert result.exit_code == 0

    def test_since_and_ago_together_errors(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "queries",
                "history",
                WH_GUID,
                "--since",
                "2024-01-01T00:00:00Z",
                "--ago",
                "1h",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# Per-command: queries sessions --ago
# ---------------------------------------------------------------------------


class TestSessionsCmdAgo:
    """queries sessions accepts --ago and rejects --since + --ago together."""

    def test_ago_90m_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.queries.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_session_history",
                new=AsyncMock(return_value=[_make_session_history_row()]),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "queries", "sessions", WH_GUID, "--ago", "90m"]
            )
        assert result.exit_code == 0

    def test_since_and_ago_together_errors(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "queries",
                "sessions",
                WH_GUID,
                "--since",
                "2024-01-01T00:00:00Z",
                "--ago",
                "90m",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# Per-command: queries frequent --ago
# ---------------------------------------------------------------------------


class TestFrequentCmdAgo:
    """queries frequent accepts --ago and rejects --since + --ago together."""

    def test_ago_2d_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.queries.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_frequent_queries",
                new=AsyncMock(return_value=[_make_frequent_query_row()]),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "queries", "frequent", WH_GUID, "--ago", "2d"]
            )
        assert result.exit_code == 0

    def test_since_and_ago_together_errors(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "queries",
                "frequent",
                WH_GUID,
                "--since",
                "2024-01-01T00:00:00Z",
                "--ago",
                "2d",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# Per-command: queries long-running --ago
# ---------------------------------------------------------------------------


class TestLongRunningCmdAgo:
    """queries long-running accepts --ago and rejects --since + --ago together."""

    def test_ago_3600s_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.queries.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_long_running_queries",
                new=AsyncMock(return_value=[_make_long_running_row()]),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "queries", "long-running", WH_GUID, "--ago", "3600s"]
            )
        assert result.exit_code == 0

    def test_since_and_ago_together_errors(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "queries",
                "long-running",
                WH_GUID,
                "--since",
                "2024-01-01T00:00:00Z",
                "--ago",
                "3600s",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output


# ---------------------------------------------------------------------------
# Per-command: sql-pools insights --ago
# ---------------------------------------------------------------------------


class TestSqlPoolsInsightsCmdAgo:
    """sql-pools insights accepts --ago and rejects --since + --ago together."""

    def test_ago_1w_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.query_insights.list_sql_pool_insights",
                new=AsyncMock(return_value=[_make_pool_insight_row()]),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "sql-pools", "insights", WH_GUID, "--ago", "1w"]
            )
        assert result.exit_code == 0

    def test_since_and_ago_together_errors(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "sql-pools",
                "insights",
                WH_GUID,
                "--since",
                "2024-01-01T00:00:00Z",
                "--ago",
                "1w",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output
