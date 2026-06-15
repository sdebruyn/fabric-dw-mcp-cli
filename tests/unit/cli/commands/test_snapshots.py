"""Tests for snapshots CLI sub-commands — stateless SQL helper (TDD)."""

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
from fabric_dw.exceptions import NotFoundError, PermissionDeniedError
from fabric_dw.models import WarehouseKind
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
SNAP_GUID = "f6a7b8c9-d0e1-2345-f012-34567890abcd"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)
SNAP_UUID = UUID(SNAP_GUID)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


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


def _make_wh_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


def _make_snap_entry() -> ItemEntry:
    return ItemEntry(
        id=SNAP_UUID,
        kind=WarehouseKind.SNAPSHOT,
        connection_string=None,
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse_Snapshot_20240315",
    )


_SNAPSHOT_DETAIL = {
    "id": SNAP_GUID,
    "displayName": "SalesWarehouse_Snapshot_20240315",
    "type": "WarehouseSnapshot",
    "workspaceId": WS_GUID,
    "properties": {
        "parentWarehouseId": WH_GUID,
        "snapshotDateTime": "2024-03-15T08:00:00Z",
        "connectionString": "snap.datawarehouse.fabric.microsoft.com",
    },
}


class TestSnapshotsList:
    """snapshots list — happy path and error path."""

    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(return_value=_async_iter([]))
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--json", "snapshots", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(return_value=_async_iter([]))
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--json", "snapshots", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.resolve_item",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["snapshots", "list", WS_GUID, WH_GUID])
        assert result.exit_code != 0


class TestSnapshotsCreate:
    """snapshots create — happy path."""

    def test_create_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        create_resp = _make_response(202, "{}")
        create_resp.headers = {"Location": "https://api.fabric.microsoft.com/v1/operations/op-123"}
        mock_http.request = AsyncMock(
            side_effect=[
                create_resp,
                _make_response(200, json.dumps(_SNAPSHOT_DETAIL)),
            ]
        )
        mock_http.poll_operation = AsyncMock(
            return_value={
                "status": "Succeeded",
                "resourceId": SNAP_GUID,
            }
        )
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            result = runner.invoke(
                cli,
                ["--json", "snapshots", "create", WS_GUID, WH_GUID, "MySnapshot"],
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == SNAP_GUID
        assert data["displayName"] == "SalesWarehouse_Snapshot_20240315"


class TestSnapshotsCreateDatetime:
    """snapshots create --snapshot-dt validation."""

    def test_bad_snapshot_dt_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        """A malformed --snapshot-dt must produce a non-zero exit and show an error."""
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "snapshots",
                "create",
                WS_GUID,
                WH_GUID,
                "MySnapshot",
                "--snapshot-dt",
                "not-a-date",
            ],
        )
        assert result.exit_code != 0
        assert "not-a-date" in result.output
        assert "--snapshot-dt" in result.output

    def test_bad_snapshot_dt_shows_expected_format(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Error message must mention the expected ISO-8601 format."""
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "snapshots",
                "create",
                WS_GUID,
                WH_GUID,
                "MySnapshot",
                "--snapshot-dt",
                "2024-99-99",
            ],
        )
        assert result.exit_code != 0
        assert "ISO-8601" in result.output or "2024-" in result.output


class TestSnapshotsRename:
    """snapshots rename — happy path."""

    def test_rename_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(
            return_value=_make_response(200, json.dumps(_SNAPSHOT_DETAIL))
        )
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_snap_entry(), _cache)),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--json", "snapshots", "rename", WS_GUID, SNAP_GUID, "NewSnapshotName"],
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == SNAP_GUID
        assert "displayName" in data


class TestSnapshotsDelete:
    """snapshots delete — happy path and decline."""

    def test_delete_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(204, ""))
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_snap_entry(), _cache)),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "snapshots", "delete", SNAP_GUID, WS_GUID])
        assert result.exit_code == 0
        mock_http.request.assert_awaited_once()
        assert "deleted" in result.output or "SalesWarehouse_Snapshot_20240315" in result.output

    def test_delete_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining delete is a clean no-op (exit 0, policy: decline != error)."""
        _ = cache_env
        mock_http = AsyncMock()
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_snap_entry(), _cache)),
            ),
        ):
            result = runner.invoke(cli, ["snapshots", "delete", SNAP_GUID, WS_GUID], input="n\n")
        assert result.exit_code == 0
        assert "Aborted." in result.output


class TestSnapshotsRoll:
    """snapshots roll — happy path, confirmation, and error."""

    def test_roll_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_roll = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.snapshots.roll_timestamp",
                new=mock_roll,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "snapshots",
                    "roll",
                    WS_GUID,
                    WH_GUID,
                    "SalesWarehouse_Snapshot_20240315",
                ],
            )
        assert result.exit_code == 0
        mock_roll.assert_awaited_once()
        assert "rolled" in result.output or "SalesWarehouse_Snapshot_20240315" in result.output

    def test_roll_with_at_flag_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_roll = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.snapshots.roll_timestamp",
                new=mock_roll,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "snapshots",
                    "roll",
                    WS_GUID,
                    WH_GUID,
                    "SalesWarehouse_Snapshot_20240315",
                    "--at",
                    "2024-03-15T12:00:00Z",
                ],
            )
        assert result.exit_code == 0
        mock_roll.assert_awaited_once()
        # The --at datetime must have been parsed and forwarded to the service.
        _args, _kwargs = mock_roll.call_args
        assert _args[2] is not None  # new_dt positional arg

    def test_roll_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining roll is a clean no-op (exit 0, policy: decline != error)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "snapshots",
                    "roll",
                    WS_GUID,
                    WH_GUID,
                    "SalesWarehouse_Snapshot_20240315",
                ],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_roll_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.snapshots.roll_timestamp",
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "snapshots",
                    "roll",
                    WS_GUID,
                    WH_GUID,
                    "SalesWarehouse_Snapshot_20240315",
                ],
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Default fallback tests
# ---------------------------------------------------------------------------


class TestSnapshotsDefaultFallback:
    """Verify workspace default from config is used when arg is omitted."""

    def test_list_uses_config_default_workspace(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        runner.invoke(cli, ["config", "set", "workspace", WS_GUID])
        runner.invoke(cli, ["config", "set", "warehouse", WH_GUID])
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(return_value=_async_iter([]))
        mock_resolve = AsyncMock(return_value=(WS_UUID, _make_wh_entry()))
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.resolve_item",
                new=mock_resolve,
            ),
        ):
            result = runner.invoke(cli, ["--json", "snapshots", "list"])
        assert result.exit_code == 0
        # resolve_item must have been called (config defaults were picked up)
        mock_resolve.assert_awaited_once()
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_missing_workspace_raises_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        result = runner.invoke(cli, ["snapshots", "list"])
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
    mock_resp.json = MagicMock(
        return_value=json.loads(text) if text and text.strip() and text.strip() != "" else {}
    )
    mock_resp.headers = {}
    return mock_resp


# ---------------------------------------------------------------------------
# L08 — rename/delete use default workspace from env (align with list/create)
# ---------------------------------------------------------------------------


class TestSnapshotsRenameDefaultWorkspace:
    """L08: snapshots rename must honour FABRIC_DW_DEFAULT_WORKSPACE when workspace is omitted."""

    def test_rename_uses_env_default_workspace(
        self, runner: CliRunner, cache_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When FABRIC_DW_DEFAULT_WORKSPACE is set, snapshots rename must not require it as arg."""
        monkeypatch.setenv("FABRIC_DW_DEFAULT_WORKSPACE", WS_GUID)
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(
            return_value=_make_response(200, json.dumps(_SNAPSHOT_DETAIL))
        )
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_snap_entry(), _cache)),
            ),
        ):
            # Omit workspace positional arg — should fall back to env var
            result = runner.invoke(
                cli,
                ["snapshots", "rename", SNAP_GUID, "NewSnapshotName"],
            )
        assert result.exit_code == 0, result.output


class TestSnapshotsDeleteDefaultWorkspace:
    """L08: snapshots delete must honour FABRIC_DW_DEFAULT_WORKSPACE when workspace is omitted."""

    def test_delete_uses_env_default_workspace(
        self, runner: CliRunner, cache_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When FABRIC_DW_DEFAULT_WORKSPACE is set, snapshots delete must not require it as arg."""
        monkeypatch.setenv("FABRIC_DW_DEFAULT_WORKSPACE", WS_GUID)
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(204, ""))
        _cache = LookupCache(path=cache_env / "lookup.json")
        with (
            patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.snapshots.resolve_item_with_cache",
                new=AsyncMock(return_value=(WS_UUID, _make_snap_entry(), _cache)),
            ),
        ):
            # Omit workspace positional arg — should fall back to env var
            result = runner.invoke(
                cli,
                ["--yes", "snapshots", "delete", SNAP_GUID],
            )
        assert result.exit_code == 0, result.output
