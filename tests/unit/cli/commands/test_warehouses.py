"""Tests for warehouses CLI sub-commands — written BEFORE the implementation (TDD)."""

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
from tests.fixtures.api_payloads import (
    WAREHOUSE_CREATE_202_PAYLOAD,
    WAREHOUSE_GET_PAYLOAD,
    WAREHOUSE_OPERATION_SUCCEEDED_PAYLOAD,
)

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


def _make_item_entry(kind: WarehouseKind = WarehouseKind.WAREHOUSE) -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=kind,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


class TestWarehousesList:
    """warehouses list — happy path."""

    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(
            return_value=_make_response(
                200,
                json.dumps({"value": []}),
            )
        )
        mock_http.iter_paginated = MagicMock(return_value=_async_iter([]))
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
        ):
            result = runner.invoke(cli, ["warehouses", "list", WS_GUID])
        assert result.exit_code == 0

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        wh_item = {
            "id": WH_GUID,
            "displayName": "SalesWarehouse",
            "description": "desc",
            "type": "Warehouse",
            "workspaceId": WS_GUID,
            "properties": {"connectionString": "srv.datawarehouse.fabric.microsoft.com"},
        }
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(
            side_effect=lambda _base, path: (
                _async_iter([wh_item]) if "warehouses" in path else _async_iter([])
            )
        )
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
        ):
            result = runner.invoke(cli, ["--json", "warehouses", "list", WS_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


class TestWarehousesGet:
    """warehouses get — happy path and 404."""

    def test_get_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, WAREHOUSE_GET_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["warehouses", "get", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_get_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item",
                new=AsyncMock(side_effect=NotFound("not found")),
            ),
        ):
            result = runner.invoke(cli, ["warehouses", "get", WS_GUID, WH_GUID])
        assert result.exit_code != 0


class TestWarehousesCreate:
    """warehouses create — happy path."""

    def test_create_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        create_resp = _make_response(202, WAREHOUSE_CREATE_202_PAYLOAD)
        create_resp.headers = {"Location": "https://api.fabric.microsoft.com/v1/operations/op-123"}
        mock_http.request = AsyncMock(
            side_effect=[
                create_resp,
                _make_response(200, WAREHOUSE_GET_PAYLOAD),
            ]
        )
        mock_http.poll_operation = AsyncMock(
            return_value=json.loads(WAREHOUSE_OPERATION_SUCCEEDED_PAYLOAD)
        )
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
        ):
            result = runner.invoke(cli, ["warehouses", "create", WS_GUID, "NewWarehouse"])
        assert result.exit_code == 0


class TestWarehousesRename:
    """warehouses rename — happy path and decline."""

    def test_rename_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, WAREHOUSE_GET_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--yes", "warehouses", "rename", WS_GUID, WH_GUID, "NewName"],
            )
        assert result.exit_code == 0

    def test_rename_declined_aborts(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["warehouses", "rename", WS_GUID, WH_GUID, "NewName"],
                input="n\n",
            )
        assert result.exit_code != 0


class TestWarehousesDelete:
    """warehouses delete — happy path and decline."""

    def test_delete_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(204, ""))
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "warehouses", "delete", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_delete_declined_aborts(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["warehouses", "delete", WS_GUID, WH_GUID], input="n\n")
        assert result.exit_code != 0


class TestWarehousesTakeover:
    """warehouses takeover — happy path and SQL endpoint refusal."""

    def test_takeover_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, "{}"))
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(WarehouseKind.WAREHOUSE))),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "warehouses", "takeover", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_takeover_sql_endpoint_refused(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(WarehouseKind.SQL_ENDPOINT))),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "warehouses", "takeover", WS_GUID, WH_GUID])
        assert result.exit_code != 0
        assert "SQL Analytics Endpoint" in result.output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
