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

from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.cli._main import cli
from fabric_dw.exceptions import NotFoundError
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


def _make_cm(http: object, _sql: object = None) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

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
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
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
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
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
                "fabric_dw.cli.commands.warehouses.build_http_client",
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
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item",
                new=AsyncMock(side_effect=NotFoundError("not found")),
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
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
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
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--yes", "warehouses", "rename", WS_GUID, WH_GUID, "NewName"],
            )
        assert result.exit_code == 0

    def test_rename_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining rename is a clean no-op (exit 0, policy: decline != error)."""
        _ = cache_env
        mock_http = AsyncMock()
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
        ):
            result = runner.invoke(
                cli,
                ["warehouses", "rename", WS_GUID, WH_GUID, "NewName"],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output


class TestWarehousesDelete:
    """warehouses delete — happy path and decline."""

    def test_delete_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(204, ""))
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "warehouses", "delete", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_delete_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining delete is a clean no-op (exit 0, policy: decline != error)."""
        _ = cache_env
        mock_http = AsyncMock()
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses._resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
        ):
            result = runner.invoke(cli, ["warehouses", "delete", WS_GUID, WH_GUID], input="n\n")
        assert result.exit_code == 0
        assert "Aborted." in result.output


class TestWarehousesListAllWorkspaces:
    """warehouses list --all-workspaces / -A."""

    def test_list_with_all_workspaces_flag(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        from fabric_dw.models import Warehouse, WarehouseKind  # noqa: PLC0415

        wh = Warehouse.model_validate(
            {
                "id": WH_GUID,
                "displayName": "SalesWarehouse",
                "workspaceId": WS_GUID,
                "kind": WarehouseKind.WAREHOUSE,
                "connectionString": "srv.datawarehouse.fabric.microsoft.com",
            }
        )
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.services.warehouses.list_all_workspaces",
                new=AsyncMock(return_value=[wh]),
            ),
        ):
            result = runner.invoke(cli, ["--json", "warehouses", "list", "-A"])
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
            "fabric_dw.cli.commands.warehouses.build_http_client",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["warehouses", "list", WS_GUID, "-A"])
        assert result.exit_code != 0


class TestWarehousesTakeover:
    """warehouses takeover — happy path and SQL endpoint refusal."""

    def test_takeover_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, "{}"))
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
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
                "fabric_dw.cli.commands.warehouses.build_http_client",
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
# Default fallback tests
# ---------------------------------------------------------------------------


class TestWarehousesDefaultFallback:
    """Verify that workspace/warehouse defaults from config are used when arg is omitted."""

    def test_list_explicit_workspace_arg_exits_zero(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """warehouses list requires an explicit WORKSPACE or -A (no config-default fallback)."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(return_value=_async_iter([]))
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
        ):
            result = runner.invoke(cli, ["warehouses", "list", WS_GUID])
        assert result.exit_code == 0

    def test_list_missing_workspace_raises_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        result = runner.invoke(cli, ["warehouses", "list"])
        assert result.exit_code != 0


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
