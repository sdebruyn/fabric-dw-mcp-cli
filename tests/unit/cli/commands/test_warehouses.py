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
from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import FABRIC_DEFAULT_COLLATION, Warehouse, WarehouseKind
from tests.fixtures.api_payloads import (
    WAREHOUSE_CREATE_202_PAYLOAD,
    WAREHOUSE_GET_PAYLOAD,
    WAREHOUSE_OPERATION_SUCCEEDED_PAYLOAD,
)

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)


def _make_cm(http: object, _sql: object = None) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


def _make_warehouse(name: str = "SalesWarehouse") -> Warehouse:
    return Warehouse.model_validate(
        {
            "id": WH_GUID,
            "displayName": name,
            "workspaceId": WS_GUID,
            "kind": WarehouseKind.WAREHOUSE,
            "connectionString": "wh.datawarehouse.fabric.microsoft.com",
        }
    )


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
            result = runner.invoke(cli, ["-w", WS_GUID, "warehouses", "list"])
        assert result.exit_code == 0
        # iter_paginated must have been called with a URL containing the resolved workspace UUID,
        # proving workspace resolution happened and the list call reached the service layer.
        # This assert would fail if workspace_id were wrong or the call were never made.
        calls = mock_http.iter_paginated.call_args_list
        assert len(calls) >= 1
        called_path = calls[0].args[1] if calls[0].args else str(calls[0])
        assert str(WS_UUID) in called_path

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
            result = runner.invoke(cli, ["--json", "-w", WS_GUID, "warehouses", "list"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


class TestWarehousesListHideWorkspaceId:
    """(a) Single-workspace table hides Workspace ID; -A keeps it; --json unchanged."""

    @staticmethod
    def _wh_item(name: str = "SalesWarehouse") -> dict[str, object]:
        return {
            "id": WH_GUID,
            "displayName": name,
            "type": "Warehouse",
            "workspaceId": WS_GUID,
            "properties": {"connectionString": "srv.datawarehouse.fabric.microsoft.com"},
        }

    def test_single_workspace_table_hides_workspace_id(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Human table for a single workspace must NOT contain the workspace GUID."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(
            side_effect=lambda _base, path: (
                _async_iter([self._wh_item()]) if "warehouses" in path else _async_iter([])
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
            result = runner.invoke(cli, ["-w", WS_GUID, "warehouses", "list"])
        assert result.exit_code == 0, result.output
        # The warehouse id (primary GUID column, always no_wrap) is shown, but
        # the redundant workspace GUID column is dropped.  Only the *first* GUID
        # column keeps no_wrap/min_width=36; secondary columns may be truncated
        # on narrow terminals, so we assert on the primary GUID value and on the
        # absence of the workspace GUID header.  WS_GUID and WH_GUID are distinct.
        assert WH_GUID in result.output
        assert WS_GUID not in result.output

    def test_single_workspace_json_keeps_workspace_id(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--json for a single workspace must STILL include workspace_id (machine-readable)."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(
            side_effect=lambda _base, path: (
                _async_iter([self._wh_item()]) if "warehouses" in path else _async_iter([])
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
            result = runner.invoke(cli, ["--json", "-w", WS_GUID, "warehouses", "list"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed[0]["workspaceId"] == WS_GUID

    def test_all_workspaces_table_keeps_workspace_id(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """-A table must KEEP the Workspace ID column (rows span workspaces)."""
        _ = cache_env
        wh = Warehouse.model_validate(
            {
                "id": WH_GUID,
                "displayName": "SalesWarehouse",
                "workspaceId": WS_GUID,
                "kind": WarehouseKind.WAREHOUSE,
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
            result = runner.invoke(cli, ["warehouses", "list", "-A"])
        assert result.exit_code == 0, result.output
        # The workspaceId column must NOT be dropped from the -A table.  The
        # primary GUID column (id) keeps its full no_wrap width; the secondary
        # GUID column (workspaceId) may be truncated on a narrow CliRunner
        # terminal, so we check for the column-header prefix rather than its full
        # text, and verify the warehouse id (primary GUID) is still intact.
        assert WH_GUID in result.output
        # "worksp" covers "workspaceId" even when truncated to "worksp…"
        assert "worksp" in result.output.lower()


class TestWarehousesListAllWithConfigDefault:
    """-A must NOT clash with a configured-default workspace (only explicit -w conflicts)."""

    def test_all_workspaces_with_config_default_exits_zero(
        self,
        runner: CliRunner,
        cache_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Passing -A together with a configured default workspace must succeed.

        The mutual-exclusion guard only fires when an explicit -w is supplied.
        A workspace set via FABRIC_DW_DEFAULT_WORKSPACE (or config-file default)
        is NOT the same as an explicit -w, so -A must win and the command must
        exit 0.
        """
        _ = cache_env
        # Simulate a configured default workspace via the environment variable.
        monkeypatch.setenv("FABRIC_DW_DEFAULT_WORKSPACE", WS_GUID)
        wh = Warehouse.model_validate(
            {
                "id": WH_GUID,
                "displayName": "SalesWarehouse",
                "workspaceId": WS_GUID,
                "kind": WarehouseKind.WAREHOUSE,
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
            # No -w flag; the workspace default is set only via env var.
            result = runner.invoke(cli, ["warehouses", "list", "-A"])
        assert result.exit_code == 0, result.output


class TestWarehousesListWarehousesOnly:
    """(b) --warehouses-only excludes SQL endpoints and skips the sqlEndpoints fetch."""

    def test_warehouses_only_skips_sql_endpoints_fetch(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """With --warehouses-only the service must NOT page /sqlEndpoints."""
        _ = cache_env
        wh_item = {
            "id": WH_GUID,
            "displayName": "SalesWarehouse",
            "type": "Warehouse",
            "workspaceId": WS_GUID,
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
            result = runner.invoke(
                cli, ["--json", "-w", WS_GUID, "warehouses", "list", "--warehouses-only"]
            )
        assert result.exit_code == 0, result.output
        # Only the /warehouses endpoint may be paged — never /sqlEndpoints.
        called_paths = [c.args[1] for c in mock_http.iter_paginated.call_args_list if c.args]
        assert any("warehouses" in p for p in called_paths)
        assert all("sqlEndpoints" not in p for p in called_paths)
        parsed = json.loads(result.output)
        assert all(row["kind"] == WarehouseKind.WAREHOUSE for row in parsed)

    def test_default_includes_sql_endpoints_fetch(self, runner: CliRunner, cache_env: Path) -> None:
        """Without the flag, the default behaviour still pages /sqlEndpoints."""
        _ = cache_env
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
            result = runner.invoke(cli, ["--json", "-w", WS_GUID, "warehouses", "list"])
        assert result.exit_code == 0, result.output
        called_paths = [c.args[1] for c in mock_http.iter_paginated.call_args_list if c.args]
        assert any("sqlEndpoints" in p for p in called_paths)

    def test_warehouses_only_all_workspaces(self, runner: CliRunner, cache_env: Path) -> None:
        """--warehouses-only is threaded through the -A path to the service layer."""
        _ = cache_env
        wh = Warehouse.model_validate(
            {
                "id": WH_GUID,
                "displayName": "SalesWarehouse",
                "workspaceId": WS_GUID,
                "kind": WarehouseKind.WAREHOUSE,
            }
        )
        mock_http = AsyncMock()
        mock_list_all = AsyncMock(return_value=[wh])
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.services.warehouses.list_all_workspaces",
                new=mock_list_all,
            ),
        ):
            result = runner.invoke(cli, ["--json", "warehouses", "list", "-A", "--warehouses-only"])
        assert result.exit_code == 0, result.output
        # The service must have been called with warehouses_only=True.
        assert mock_list_all.await_args is not None
        assert mock_list_all.await_args.kwargs.get("warehouses_only") is True


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
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--json", "-w", WS_GUID, "warehouses", "get", WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["displayName"] == "SalesWarehouse"
        assert parsed["id"] == WH_GUID

    def test_get_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "warehouses", "get", WH_GUID])
        assert result.exit_code != 0


class TestWarehousesGetCollationDefault:
    """warehouses get — Fabric default collation display when API returns null."""

    #: Warehouse GET payload where the API omits/nulls the collation.
    _NULL_COLLATION_PAYLOAD = json.dumps(
        {
            "id": WH_GUID,
            "displayName": "SalesWarehouse",
            "type": "Warehouse",
            "workspaceId": WS_GUID,
            "properties": {
                "connectionString": "saleswarehouse.datawarehouse.fabric.microsoft.com",
                "defaultCollation": None,
            },
        }
    )

    #: Warehouse GET payload with an explicit, non-default collation.
    _EXPLICIT_COLLATION_PAYLOAD = json.dumps(
        {
            "id": WH_GUID,
            "displayName": "SalesWarehouse",
            "type": "Warehouse",
            "workspaceId": WS_GUID,
            "properties": {
                "connectionString": "saleswarehouse.datawarehouse.fabric.microsoft.com",
                "defaultCollation": "Latin1_General_100_CI_AS_KS_WS_SC_UTF8",
            },
        }
    )

    def _invoke(self, runner: CliRunner, payload: str, *, json_flag: bool) -> str:
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, payload))
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            args = ["-w", WS_GUID, "warehouses", "get", WH_GUID]
            if json_flag:
                args = ["--json", *args]
            result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        return result.output

    def test_human_output_shows_default_when_collation_null(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Human output shows the effective default collation when the API returns null.

        The ``(default)`` suffix is intentionally absent: the model now always
        coalesces null/empty collation to FABRIC_DEFAULT_COLLATION, so the
        rendered value is indistinguishable from an explicitly-set default.
        """
        _ = cache_env
        output = self._invoke(runner, self._NULL_COLLATION_PAYLOAD, json_flag=False)
        assert FABRIC_DEFAULT_COLLATION in output
        assert "(default)" not in output

    def test_json_output_coalesces_null_to_default_collation(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--json surfaces the effective collation (null coalesced to default in the model)."""
        _ = cache_env
        output = self._invoke(runner, self._NULL_COLLATION_PAYLOAD, json_flag=True)
        parsed = json.loads(output)
        assert parsed["defaultCollation"] == FABRIC_DEFAULT_COLLATION

    def test_human_output_shows_explicit_collation_unchanged(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """An explicit collation is shown verbatim, with no '(default)' suffix."""
        _ = cache_env
        output = self._invoke(runner, self._EXPLICIT_COLLATION_PAYLOAD, json_flag=False)
        assert "Latin1_General_100_CI_AS_KS_WS_SC_UTF8" in output
        assert "(default)" not in output

    def test_json_output_keeps_explicit_collation(self, runner: CliRunner, cache_env: Path) -> None:
        """--json keeps an explicit collation verbatim."""
        _ = cache_env
        output = self._invoke(runner, self._EXPLICIT_COLLATION_PAYLOAD, json_flag=True)
        parsed = json.loads(output)
        assert parsed["defaultCollation"] == "Latin1_General_100_CI_AS_KS_WS_SC_UTF8"


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
            result = runner.invoke(cli, ["-w", WS_GUID, "warehouses", "create", "NewWarehouse"])
        assert result.exit_code == 0


class TestWarehousesRename:
    """warehouses rename — happy path and decline."""

    def test_rename_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, WAREHOUSE_GET_PAYLOAD))
        _cache = LookupCache(path=cache_env / "lookup.json")
        mock_rename = AsyncMock(return_value=_make_warehouse("NewName"))
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
            patch("fabric_dw.services.warehouses.rename", new=mock_rename),
        ):
            result = runner.invoke(
                cli,
                ["--json", "--yes", "-w", WS_GUID, "warehouses", "rename", WH_GUID, "NewName"],
            )
        assert result.exit_code == 0
        mock_rename.assert_awaited_once()
        data = json.loads(result.output)
        assert data["displayName"] == "NewName"

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
                "fabric_dw.cli.commands.warehouses.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "warehouses", "rename", WH_GUID, "NewName"],
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
        mock_delete = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
            patch("fabric_dw.services.warehouses.delete", new=mock_delete),
        ):
            result = runner.invoke(cli, ["--yes", "-w", WS_GUID, "warehouses", "delete", WH_GUID])
        assert result.exit_code == 0
        mock_delete.assert_awaited_once()

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
                "fabric_dw.cli.commands.warehouses.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "warehouses", "delete", WH_GUID], input="n\n"
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_delete_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        """--json must emit machine-readable status for delete (L20)."""
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
                "fabric_dw.cli.commands.warehouses.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "--json", "-w", WS_GUID, "warehouses", "delete", WH_GUID]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "deleted"


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
            result = runner.invoke(cli, ["-w", WS_GUID, "warehouses", "list", "-A"])
        assert result.exit_code != 0


class TestWarehousesTakeover:
    """warehouses takeover — happy path and SQL endpoint refusal."""

    def test_takeover_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, "{}"))
        mock_takeover = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(WarehouseKind.WAREHOUSE))),
            ),
            patch("fabric_dw.services.ownership.takeover", new=mock_takeover),
        ):
            result = runner.invoke(cli, ["--yes", "-w", WS_GUID, "warehouses", "takeover", WH_GUID])
        assert result.exit_code == 0
        mock_takeover.assert_awaited_once()

    def test_takeover_sql_endpoint_refused(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(WarehouseKind.SQL_ENDPOINT))),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "-w", WS_GUID, "warehouses", "takeover", WH_GUID])
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
        """warehouses list works with an explicit WORKSPACE arg."""
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
            result = runner.invoke(cli, ["--json", "-w", WS_GUID, "warehouses", "list"])
        assert result.exit_code == 0
        # Empty list renders as valid JSON array when no warehouses exist
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_uses_config_default_workspace(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """warehouses list honours the configured default-workspace (L17 regression guard)."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        setup = runner.invoke(cli, ["config", "set", "workspace", WS_GUID])
        assert setup.exit_code == 0
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(return_value=_async_iter([]))
        mock_ws_id = AsyncMock(return_value=WS_UUID)
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=mock_ws_id,
            ),
        ):
            # No WORKSPACE argument — must fall back to config default.
            result = runner.invoke(cli, ["--json", "warehouses", "list"])
        assert result.exit_code == 0
        # Resolver must have been called (workspace resolved from config default)
        mock_ws_id.assert_awaited_once()
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_missing_workspace_raises_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        result = runner.invoke(cli, ["warehouses", "list"])
        assert result.exit_code != 0


class TestWarehousesListFabricError:
    """warehouses list — FabricError branch (line 70-71)."""

    def test_list_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "warehouses", "list"])
        assert result.exit_code != 0


class TestWarehousesCreateError:
    """warehouses create — FabricError branch (lines 120-121)."""

    def test_create_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.services.warehouses.create",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "warehouses", "create", "NewWarehouse"])
        assert result.exit_code != 0


class TestWarehousesRenameError:
    """warehouses rename — FabricError branch (lines 161-162)."""

    def test_rename_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
            patch(
                "fabric_dw.services.warehouses.rename",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--yes", "-w", WS_GUID, "warehouses", "rename", WH_GUID, "NewName"],
            )
        assert result.exit_code != 0


class TestWarehousesDeleteError:
    """warehouses delete — FabricError branch (lines 192-193)."""

    def test_delete_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(), _cache)),
            ),
            patch(
                "fabric_dw.services.warehouses.delete",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "-w", WS_GUID, "warehouses", "delete", WH_GUID])
        assert result.exit_code != 0


class TestWarehousesTakeoverErrors:
    """warehouses takeover — FabricError and abort branches (lines 211-212, 220-221, 225-226)."""

    def test_takeover_resolve_fabric_error_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """FabricError during _resolve_item (line 211-212)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(side_effect=FabricError("resolve error")),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "-w", WS_GUID, "warehouses", "takeover", WH_GUID])
        assert result.exit_code != 0

    def test_takeover_declined_prints_aborted(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining confirmation prints 'Aborted.' (lines 220-221)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(WarehouseKind.WAREHOUSE))),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "warehouses", "takeover", WH_GUID],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_takeover_fabric_error_after_confirm_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """FabricError from ownership service (lines 225-226)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(WarehouseKind.WAREHOUSE))),
            ),
            patch(
                "fabric_dw.services.ownership.takeover",
                new=AsyncMock(side_effect=FabricError("takeover failed")),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "-w", WS_GUID, "warehouses", "takeover", WH_GUID])
        assert result.exit_code != 0


class TestWarehousesPermissions:
    """warehouses permissions — happy path + FabricError (lines 241-251)."""

    def test_permissions_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        from fabric_dw.models import (  # noqa: PLC0415
            ItemAccess,
            ItemAccessDetail,
            ItemAccessPrincipal,
        )

        principal = ItemAccessPrincipal.model_validate(
            {
                "id": str(WH_UUID),
                "displayName": "Alice",
                "type": "User",
                "userDetails": {"userPrincipalName": "alice@example.com"},
            }
        )
        detail = ItemAccessDetail.model_validate(
            {"permissions": ["Read"], "additionalPermissions": []}
        )
        access = ItemAccess.model_validate(
            {
                "principal": principal.model_dump(by_alias=True, mode="json"),
                "itemAccessDetails": detail.model_dump(by_alias=True, mode="json"),
            }
        )

        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.permissions.list_item_access",
                new=AsyncMock(return_value=[access]),
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "-w", WS_GUID, "warehouses", "permissions", WH_GUID]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        # Alice's principal must appear in the rendered permissions list
        assert any(p.get("principal", {}).get("displayName") == "Alice" for p in parsed)

    def test_permissions_fabric_error_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "warehouses", "permissions", WH_GUID])
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


# ---------------------------------------------------------------------------
# L04 — takeover and set-collation open build_http_client exactly once
# ---------------------------------------------------------------------------


class TestTakeoverSingleHttpOpen:
    """L04: takeover must open build_http_client exactly once (not twice)."""

    def test_takeover_opens_http_client_once(self, runner: CliRunner, cache_env: Path) -> None:
        """build_http_client must be entered exactly once for takeover."""
        _ = cache_env
        open_count = 0
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, "{}"))

        @asynccontextmanager
        async def counting_cm(_ctx: object) -> AsyncIterator[object]:
            nonlocal open_count
            open_count += 1
            yield mock_http

        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=counting_cm,
            ),
            patch(
                "fabric_dw.cli.commands.warehouses.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry(WarehouseKind.WAREHOUSE))),
            ),
            patch(
                "fabric_dw.services.ownership.takeover",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "-w", WS_GUID, "warehouses", "takeover", WH_GUID])

        assert result.exit_code == 0, result.output
        assert open_count == 1, f"build_http_client opened {open_count} times, expected 1"
