"""Tests for endpoints CLI sub-commands — written BEFORE the implementation (TDD)."""

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
from fabric_dw.exceptions import NotFound
from fabric_dw.models import WarehouseKind

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


def _make_cm(http: object, sql: object) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[tuple[object, object]]:
        yield http, sql

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
                "fabric_dw.cli.commands.endpoints._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.endpoints.Resolver.workspace_id",
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
                "fabric_dw.cli.commands.endpoints._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.endpoints.Resolver.workspace_id",
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
                "fabric_dw.cli.commands.endpoints._build_clients",
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
            "fabric_dw.cli.commands.endpoints._build_clients",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "list", WS_GUID, "-A"])
        assert result.exit_code != 0


class TestEndpointsGet:
    """endpoints get — happy path and 404."""

    def test_get_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, _ENDPOINT_JSON))
        with (
            patch(
                "fabric_dw.cli.commands.endpoints._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.endpoints._resolve_item",
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
                "fabric_dw.cli.commands.endpoints._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.endpoints._resolve_item",
                new=AsyncMock(side_effect=NotFound("endpoint not found")),
            ),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "get", WS_GUID, EP_GUID])
        assert result.exit_code != 0


class TestEndpointsRefresh:
    """endpoints refresh — happy path (LRO)."""

    def test_refresh_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(
            return_value=_make_response(
                202,
                json.dumps({}),
            )
        )
        mock_http.poll_operation = AsyncMock(
            return_value={"status": "Succeeded", "percentComplete": 100}
        )

        _make_response_with_location = MagicMock()
        _make_response_with_location.status_code = 202
        _make_response_with_location.json = MagicMock(return_value={})
        _make_response_with_location.headers = {
            "Location": "https://api.fabric.microsoft.com/v1/operations/op-123"
        }
        _make_response_with_location.text = "{}"
        mock_http.request = AsyncMock(return_value=_make_response_with_location)

        with (
            patch(
                "fabric_dw.cli.commands.endpoints._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.endpoints._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "refresh", WS_GUID, EP_GUID])
        assert result.exit_code == 0

    def test_refresh_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.endpoints._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.endpoints._resolve_item",
                new=AsyncMock(side_effect=NotFound("endpoint not found")),
            ),
        ):
            result = runner.invoke(cli, ["sql-endpoints", "refresh", WS_GUID, EP_GUID])
        assert result.exit_code != 0
