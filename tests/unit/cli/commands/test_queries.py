"""Tests for queries CLI sub-commands — stateless SQL helper (TDD)."""

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
from fabric_dw.exceptions import FabricError, NotFoundError, PermissionDeniedError
from fabric_dw.models import RunningQuery, WarehouseKind
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)


def _make_http_cm(http: object) -> object:
    """Build an asynccontextmanager that yields just the http client."""

    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


def _make_sql_target() -> SqlTarget:
    return SqlTarget(
        workspace_id=WS_GUID,
        database="SalesWarehouse",
        connection_string="wh.datawarehouse.fabric.microsoft.com",
    )


def _make_item_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


def _make_running_query() -> RunningQuery:
    return RunningQuery.model_validate(
        {
            "session_id": 42,
            "request_id": "req-001",
            "status": "running",
            "start_time": "2024-03-15T10:00:00",
            "total_elapsed_time": 5000,
            "login_name": "user@example.com",
            "command": "SELECT",
            "query_text": None,
        }
    )


class TestQueriesList:
    """queries list — happy path and error path."""

    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.queries.list_running",
                new=AsyncMock(return_value=[_make_running_query()]),
            ),
        ):
            result = runner.invoke(cli, ["queries", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.queries.list_running",
                new=AsyncMock(return_value=[_make_running_query()]),
            ),
        ):
            result = runner.invoke(cli, ["--json", "queries", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.queries.build_sql_target",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["queries", "list", WS_GUID, WH_GUID])
        assert result.exit_code != 0


class TestQueriesKill:
    """queries kill — happy path, confirmation, and permission denied."""

    def test_kill_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.queries.kill",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "queries", "kill", WS_GUID, WH_GUID, "42"])
        assert result.exit_code == 0

    def test_kill_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining kill is a clean no-op (exit 0, policy: decline != error)."""
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
        ):
            result = runner.invoke(cli, ["queries", "kill", WS_GUID, WH_GUID, "42"], input="n\n")
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_kill_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
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
                "fabric_dw.services.queries.kill",
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "queries", "kill", WS_GUID, WH_GUID, "42"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Default fallback tests
# ---------------------------------------------------------------------------


class TestQueriesDefaultFallback:
    """Verify workspace/warehouse defaults from config are used when arg is omitted."""

    def test_list_uses_config_defaults(
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
                "fabric_dw.cli.commands.queries.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.queries.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.queries.list_running",
                new=AsyncMock(return_value=[_make_running_query()]),
            ),
        ):
            result = runner.invoke(cli, ["queries", "list"])
        assert result.exit_code == 0

    def test_list_missing_workspace_raises_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        result = runner.invoke(cli, ["queries", "list"])
        assert result.exit_code != 0


class TestQueriesListConnections:
    """queries list-connections — happy path + FabricError (lines 89-101)."""

    def test_list_connections_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.queries.list_connections",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["queries", "list-connections", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_list_connections_fabric_error_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.queries.build_sql_target",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["queries", "list-connections", WS_GUID, WH_GUID])
        assert result.exit_code != 0


class TestQueriesRequestHistory:
    """queries request-history — happy path."""

    def test_request_history_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["queries", "request-history", WS_GUID, WH_GUID])
        assert result.exit_code == 0


class TestQueriesSessionHistory:
    """queries session-history — happy path + FabricError (lines 210-211)."""

    def test_session_history_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["queries", "session-history", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_session_history_fabric_error_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.queries.build_sql_target",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["queries", "session-history", WS_GUID, WH_GUID])
        assert result.exit_code != 0


class TestQueriesFrequent:
    """queries frequent — happy path + FabricError (lines 251-252)."""

    def test_frequent_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["queries", "frequent", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_frequent_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.queries.build_sql_target",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["queries", "frequent", WS_GUID, WH_GUID])
        assert result.exit_code != 0


class TestQueriesLongRunning:
    """queries long-running — happy path + FabricError (lines 292-293)."""

    def test_long_running_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["queries", "long-running", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_long_running_fabric_error_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.queries.build_sql_target",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["queries", "long-running", WS_GUID, WH_GUID])
        assert result.exit_code != 0
