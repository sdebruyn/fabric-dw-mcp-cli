"""Tests for queries CLI sub-commands — written BEFORE the implementation (TDD)."""

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
from fabric_dw.models import WarehouseKind

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def _make_cm(http: object, sql: object) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[tuple[object, object]]:
        yield http, sql

    return _cm


def _make_item_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


_RUNNING_QUERY_ROW = {
    "session_id": 42,
    "request_id": "req-001",
    "status": "running",
    "start_time": "2024-03-15T10:00:00",
    "total_elapsed_time": 5000,
    "login_name": "user@example.com",
    "command": "SELECT",
    "query_text": None,
}


class TestQueriesList:
    """queries list — happy path and error path."""

    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_sql = AsyncMock()
        mock_sql.execute = AsyncMock(return_value=[_RUNNING_QUERY_ROW])
        with (
            patch(
                "fabric_dw.cli.commands.queries._build_clients",
                new=_make_cm(mock_http, mock_sql),
            ),
            patch(
                "fabric_dw.cli.commands.queries._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["queries", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_sql = AsyncMock()
        mock_sql.execute = AsyncMock(return_value=[_RUNNING_QUERY_ROW])
        with (
            patch(
                "fabric_dw.cli.commands.queries._build_clients",
                new=_make_cm(mock_http, mock_sql),
            ),
            patch(
                "fabric_dw.cli.commands.queries._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--json", "queries", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_sql = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries._build_clients",
                new=_make_cm(mock_http, mock_sql),
            ),
            patch(
                "fabric_dw.cli.commands.queries._resolve_item",
                new=AsyncMock(side_effect=NotFound("not found")),
            ),
        ):
            result = runner.invoke(cli, ["queries", "list", WS_GUID, WH_GUID])
        assert result.exit_code != 0


class TestQueriesKill:
    """queries kill — happy path, confirmation, and permission denied."""

    def test_kill_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_sql = AsyncMock()
        mock_sql.execute_nonquery = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.queries._build_clients",
                new=_make_cm(mock_http, mock_sql),
            ),
            patch(
                "fabric_dw.cli.commands.queries._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "queries", "kill", WS_GUID, WH_GUID, "42"])
        assert result.exit_code == 0

    def test_kill_declined_aborts(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_sql = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.queries._build_clients",
                new=_make_cm(mock_http, mock_sql),
            ),
            patch(
                "fabric_dw.cli.commands.queries._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["queries", "kill", WS_GUID, WH_GUID, "42"], input="n\n")
        assert result.exit_code != 0

    def test_kill_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_sql = AsyncMock()
        mock_sql.execute_nonquery = AsyncMock(side_effect=PermissionDenied("no permission"))
        with (
            patch(
                "fabric_dw.cli.commands.queries._build_clients",
                new=_make_cm(mock_http, mock_sql),
            ),
            patch(
                "fabric_dw.cli.commands.queries._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "queries", "kill", WS_GUID, WH_GUID, "42"])
        assert result.exit_code != 0
