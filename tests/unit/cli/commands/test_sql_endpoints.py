"""Tests for endpoints CLI sub-commands — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from click.testing import CliRunner
from rich.console import Console

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.cli._render import render_refresh_table as _render_refresh_table
from fabric_dw.exceptions import NotFoundError
from fabric_dw.models import TableSyncStatus, WarehouseKind

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
EP_GUID = "e5f6a7b8-c9d0-1234-ef01-234567890abc"
WS_UUID = UUID(WS_GUID)
EP_UUID = UUID(EP_GUID)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def _make_cm(http: object, _sql: object = None) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


def _make_item_entry(kind: WarehouseKind = WarehouseKind.SQL_ENDPOINT) -> ItemEntry:
    return ItemEntry(
        id=EP_UUID,
        kind=kind,
        connection_string="lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesLakehouse",
    )


async def _async_iter_coro(items: list[object]):  # type: ignore[no-untyped-def]
    for item in items:
        yield item


def _async_iter(items: list[object]):  # type: ignore[no-untyped-def]
    return _async_iter_coro(items)


def _make_response(status_code: int, text: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json = MagicMock(return_value=json.loads(text) if text and text.strip() else {})
    mock_resp.headers = {}
    mock_resp.text = text
    return mock_resp


_ENDPOINT_JSON = json.dumps(
    {
        "id": EP_GUID,
        "displayName": "SalesLakehouse",
        "description": "SQL endpoint for sales lakehouse",
        "workspaceId": WS_GUID,
        "kind": "SQLEndpoint",
        "connectionString": "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
        "defaultCollation": None,
        "createdDate": None,
    }
)


class TestEndpointsList:
    """endpoints list — happy path."""

    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(return_value=_async_iter([]))
        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "list", WS_GUID])
        assert result.exit_code == 0

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        ep_item = {
            "id": EP_GUID,
            "displayName": "SalesLakehouse",
            "description": "SQL endpoint",
            "type": "SQLEndpoint",
            "workspaceId": WS_GUID,
            "properties": {
                "sqlEndpointProperties": {
                    "connectionString": "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
                    "id": "f6a7b8c9-d0e1-2345-f012-34567890abcd",
                    "provisioningStatus": "Success",
                }
            },
        }
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(return_value=_async_iter([ep_item]))
        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
        ):
            result = runner.invoke(cli, ["--json", "sql-endpoints", "list", WS_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


class TestEndpointsListAllWorkspaces:
    """endpoints list --all-workspaces / -A."""

    def test_list_with_all_workspaces_flag(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        from fabric_dw.models import Warehouse, WarehouseKind  # noqa: PLC0415

        ep = Warehouse.model_validate(
            {
                "id": EP_GUID,
                "displayName": "SalesLakehouse",
                "workspaceId": WS_GUID,
                "kind": WarehouseKind.SQL_ENDPOINT,
                "connectionString": "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
            }
        )
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.services.sql_endpoints.list_all_workspaces",
                new=AsyncMock(return_value=[ep]),
            ),
        ):
            result = runner.invoke(cli, ["--json", "sql-endpoints", "list", "-A"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["workspaceId"] == WS_GUID

    def test_list_both_workspace_and_all_workspaces_errors(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with patch(
            "fabric_dw.cli.commands.sql_endpoints.build_http_client",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "list", WS_GUID, "-A"])
        assert result.exit_code != 0

    def test_list_uses_config_default_workspace(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sql-endpoints list honours the configured default-workspace (L17 regression guard)."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        setup = runner.invoke(cli, ["config", "set", "workspace", WS_GUID])
        assert setup.exit_code == 0
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(return_value=_async_iter([]))
        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
        ):
            # No WORKSPACE argument — must fall back to config default.
            result = runner.invoke(cli, ["sql-endpoints", "list"])
        assert result.exit_code == 0


class TestEndpointsGet:
    """endpoints get — happy path and 404."""

    def test_get_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, _ENDPOINT_JSON))
        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.sql_endpoints.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "get", WS_GUID, EP_GUID])
        assert result.exit_code == 0

    def test_get_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.sql_endpoints.resolve_item",
                new=AsyncMock(side_effect=NotFoundError("endpoint not found")),
            ),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "get", WS_GUID, EP_GUID])
        assert result.exit_code != 0


def _make_table_sync_statuses() -> list[TableSyncStatus]:
    return [
        TableSyncStatus.model_validate(
            {
                "tableName": "Table1",
                "status": "Success",
                "startDateTime": "2025-08-08T10:31:22.270Z",
                "endDateTime": "2025-08-08T10:36:54.965Z",
                "lastSuccessfulSyncDateTime": "2025-08-08T10:36:54.965Z",
            }
        ),
        TableSyncStatus.model_validate(
            {
                "tableName": "Table2",
                "status": "Failure",
                "startDateTime": "2025-08-08T10:31:22.270Z",
                "endDateTime": "2025-08-08T10:43:02.532Z",
                "error": {"errorCode": "TokenError", "message": "Auth failed"},
                "lastSuccessfulSyncDateTime": "2025-08-07T10:44:27.263Z",
            }
        ),
    ]


class TestEndpointsRefresh:
    """endpoints refresh — happy path and error cases."""

    def test_refresh_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()

        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.sql_endpoints.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_endpoints.refresh_metadata",
                new=AsyncMock(return_value=_make_table_sync_statuses()),
            ),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "refresh", WS_GUID, EP_GUID])
        assert result.exit_code == 0

    def test_refresh_default_renders_rich_table(self, runner: CliRunner, cache_env: Path) -> None:
        """Without --json, the output must contain the table name columns."""
        _ = cache_env
        mock_http = AsyncMock()

        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.sql_endpoints.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_endpoints.refresh_metadata",
                new=AsyncMock(return_value=_make_table_sync_statuses()),
            ),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "refresh", WS_GUID, EP_GUID])
        assert result.exit_code == 0
        # Rich table output must not be valid JSON
        try:
            json.loads(result.output)
            is_json = True
        except (json.JSONDecodeError, ValueError):
            is_json = False
        assert not is_json, "Expected Rich table output, got JSON"

    def test_refresh_json_flag_emits_json(self, runner: CliRunner, cache_env: Path) -> None:
        """With --json, output must be a valid JSON array."""
        _ = cache_env
        mock_http = AsyncMock()

        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.sql_endpoints.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_endpoints.refresh_metadata",
                new=AsyncMock(return_value=_make_table_sync_statuses()),
            ),
        ):
            result = runner.invoke(cli, ["--json", "sql-endpoints", "refresh", WS_GUID, EP_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["tableName"] == "Table1"
        assert parsed[0]["status"] == "Success"

    def test_refresh_recreate_tables_flag_passed_to_service(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--recreate-tables must call refresh_metadata with recreate_tables=True."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_refresh = AsyncMock(return_value=_make_table_sync_statuses())

        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.sql_endpoints.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_endpoints.refresh_metadata",
                new=mock_refresh,
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql-endpoints", "refresh", "--recreate-tables", WS_GUID, EP_GUID],
            )
        assert result.exit_code == 0
        mock_refresh.assert_called_once()
        _, kwargs = mock_refresh.call_args
        assert kwargs.get("recreate_tables") is True

    def test_refresh_no_recreate_tables_default_false(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Without --recreate-tables, service must be called with recreate_tables=False."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_refresh = AsyncMock(return_value=_make_table_sync_statuses())

        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.sql_endpoints.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_endpoints.refresh_metadata",
                new=mock_refresh,
            ),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "refresh", WS_GUID, EP_GUID])
        assert result.exit_code == 0
        mock_refresh.assert_called_once()
        _, kwargs = mock_refresh.call_args
        assert kwargs.get("recreate_tables") is False

    def test_refresh_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql_endpoints.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.sql_endpoints.resolve_item",
                new=AsyncMock(side_effect=NotFoundError("endpoint not found")),
            ),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "refresh", WS_GUID, EP_GUID])
        assert result.exit_code != 0


class TestRenderRefreshTable:
    """Unit tests for the _render_refresh_table helper."""

    def test_empty_list_does_not_crash(self) -> None:
        """_render_refresh_table([]) must not raise and must render an empty table."""
        buf = StringIO()
        con = Console(file=buf, width=120)
        _render_refresh_table([], console=con)
        output = buf.getvalue()
        # The title must still appear even with no rows.
        assert "Metadata Refresh Results" in output
