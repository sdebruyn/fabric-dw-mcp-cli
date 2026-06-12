"""Tests for the restore-points CLI sub-commands.

Pattern: CliRunner + mocked service layer (same style as test_snapshots.py,
test_warehouses.py, etc.).  The service and http-client boundaries are patched
so no real HTTP occurs.
"""

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
from fabric_dw.exceptions import FabricError, NotFoundError, PermissionDeniedError
from fabric_dw.models import RestorePoint, WarehouseKind

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
RP_ID = "1726617378000"

WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_cm(http: object) -> object:
    """Build an asynccontextmanager that yields the mock http client."""

    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


def _make_wh_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


def _make_restore_point(
    rp_id: str = RP_ID,
    name: str = "RestorePoint_20240315",
    creation_mode: str = "UserDefined",
    event_dt: str = "2024-03-15T06:00:00Z",
) -> RestorePoint:
    return RestorePoint.model_validate(
        {
            "id": rp_id,
            "displayName": name,
            "description": "Test restore point",
            "creationMode": creation_mode,
            "creationDetails": {"eventDateTime": event_dt},
        }
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestRestorePointsList:
    """restore-points list — happy path, json output, and error paths."""

    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        rp = _make_restore_point()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.list_points",
                new=AsyncMock(return_value=[rp]),
            ),
        ):
            result = runner.invoke(cli, ["restore-points", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_list_json_output_is_list(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        rp = _make_restore_point()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.list_points",
                new=AsyncMock(return_value=[rp]),
            ),
        ):
            result = runner.invoke(cli, ["--json", "restore-points", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["id"] == RP_ID

    def test_list_empty_result_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.list_points",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["restore-points", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_list_fabric_error_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
            ),
        ):
            result = runner.invoke(cli, ["restore-points", "list", WS_GUID, WH_GUID])
        assert result.exit_code != 0

    def test_list_permission_denied_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(side_effect=PermissionDeniedError("forbidden")),
            ),
        ):
            result = runner.invoke(cli, ["restore-points", "list", WS_GUID, WH_GUID])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


class TestRestorePointsGet:
    """restore-points get — happy path and error paths."""

    def test_get_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        rp = _make_restore_point()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.get_point",
                new=AsyncMock(return_value=rp),
            ),
        ):
            result = runner.invoke(cli, ["restore-points", "get", WS_GUID, WH_GUID, RP_ID])
        assert result.exit_code == 0

    def test_get_json_output_has_id(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        rp = _make_restore_point()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.get_point",
                new=AsyncMock(return_value=rp),
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "restore-points", "get", WS_GUID, WH_GUID, RP_ID]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["id"] == RP_ID

    def test_get_not_found_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.get_point",
                new=AsyncMock(side_effect=NotFoundError("restore point not found")),
            ),
        ):
            result = runner.invoke(cli, ["restore-points", "get", WS_GUID, WH_GUID, RP_ID])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


class TestRestorePointsCreate:
    """restore-points create — happy path with and without options."""

    def test_create_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        rp = _make_restore_point()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.create_point",
                new=AsyncMock(return_value=rp),
            ),
        ):
            result = runner.invoke(cli, ["restore-points", "create", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_create_with_name_and_description(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        rp = _make_restore_point()
        create_mock = AsyncMock(return_value=rp)
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch("fabric_dw.services.restore.create_point", new=create_mock),
        ):
            result = runner.invoke(
                cli,
                [
                    "restore-points",
                    "create",
                    WS_GUID,
                    WH_GUID,
                    "--name",
                    "MyRestorePoint",
                    "--description",
                    "Before migration",
                ],
            )
        assert result.exit_code == 0
        # Verify the name and description were forwarded to the service.
        create_mock.assert_awaited_once()
        _, kwargs = create_mock.call_args
        assert kwargs.get("name") == "MyRestorePoint"
        assert kwargs.get("description") == "Before migration"

    def test_create_fabric_error_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.create_point",
                new=AsyncMock(side_effect=FabricError("create failed", status=500)),
            ),
        ):
            result = runner.invoke(cli, ["restore-points", "create", WS_GUID, WH_GUID])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


class TestRestorePointsRename:
    """restore-points rename — happy path and error path."""

    def test_rename_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        rp = _make_restore_point(name="NewName")
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.update_point",
                new=AsyncMock(return_value=rp),
            ),
        ):
            result = runner.invoke(
                cli,
                ["restore-points", "rename", WS_GUID, WH_GUID, RP_ID, "NewName"],
            )
        assert result.exit_code == 0

    def test_rename_with_description(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        rp = _make_restore_point(name="NewName")
        update_mock = AsyncMock(return_value=rp)
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch("fabric_dw.services.restore.update_point", new=update_mock),
        ):
            result = runner.invoke(
                cli,
                [
                    "restore-points",
                    "rename",
                    WS_GUID,
                    WH_GUID,
                    RP_ID,
                    "NewName",
                    "--description",
                    "Updated description",
                ],
            )
        assert result.exit_code == 0
        _, kwargs = update_mock.call_args
        assert kwargs.get("description") == "Updated description"

    def test_rename_fabric_error_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.update_point",
                new=AsyncMock(side_effect=FabricError("update failed")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["restore-points", "rename", WS_GUID, WH_GUID, RP_ID, "NewName"],
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestRestorePointsDelete:
    """restore-points delete — confirm path, decline path, and --yes flag."""

    def test_delete_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.delete_point",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "restore-points", "delete", WS_GUID, WH_GUID, RP_ID]
            )
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    def test_delete_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining delete is a clean no-op (exit 0, policy: decline != error)."""
        _ = cache_env
        mock_http = AsyncMock()
        delete_mock = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch("fabric_dw.services.restore.delete_point", new=delete_mock),
        ):
            result = runner.invoke(
                cli,
                ["restore-points", "delete", WS_GUID, WH_GUID, RP_ID],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output
        # Service must NOT have been called.
        delete_mock.assert_not_awaited()

    def test_delete_confirmed_calls_service(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        delete_mock = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch("fabric_dw.services.restore.delete_point", new=delete_mock),
        ):
            result = runner.invoke(
                cli,
                ["restore-points", "delete", WS_GUID, WH_GUID, RP_ID],
                input="y\n",
            )
        assert result.exit_code == 0
        delete_mock.assert_awaited_once()

    def test_delete_fabric_error_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.delete_point",
                new=AsyncMock(side_effect=FabricError("delete failed")),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "restore-points", "delete", WS_GUID, WH_GUID, RP_ID]
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


class TestRestorePointsRestore:
    """restore-points restore — --yes flag, decline, and type-name confirm path."""

    def test_restore_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.restore_in_place",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "restore-points", "restore", WS_GUID, WH_GUID, RP_ID]
            )
        assert result.exit_code == 0
        assert "restored" in result.output.lower()

    def test_restore_type_name_confirm_correct(self, runner: CliRunner, cache_env: Path) -> None:
        """Typing the correct warehouse name in interactive mode proceeds."""
        _ = cache_env
        mock_http = AsyncMock()
        rp = _make_restore_point()
        restore_mock = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch("fabric_dw.services.restore.get_point", new=AsyncMock(return_value=rp)),
            patch("fabric_dw.services.restore.restore_in_place", new=restore_mock),
        ):
            # The prompt asks for the warehouse name: "SalesWarehouse"
            result = runner.invoke(
                cli,
                ["restore-points", "restore", WS_GUID, "SalesWarehouse", RP_ID],
                input="SalesWarehouse\n",
            )
        assert result.exit_code == 0
        restore_mock.assert_awaited_once()

    def test_restore_type_name_confirm_wrong_aborts(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Typing the wrong name aborts without calling the service."""
        _ = cache_env
        mock_http = AsyncMock()
        rp = _make_restore_point()
        restore_mock = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch("fabric_dw.services.restore.get_point", new=AsyncMock(return_value=rp)),
            patch("fabric_dw.services.restore.restore_in_place", new=restore_mock),
        ):
            result = runner.invoke(
                cli,
                ["restore-points", "restore", WS_GUID, "SalesWarehouse", RP_ID],
                input="WrongName\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output
        restore_mock.assert_not_awaited()

    def test_restore_fabric_error_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.restore_in_place",
                new=AsyncMock(side_effect=FabricError("restore failed", status=500)),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "restore-points", "restore", WS_GUID, WH_GUID, RP_ID]
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Config-default fallback
# ---------------------------------------------------------------------------


class TestRestorePointsDefaultFallback:
    """Workspace/warehouse defaults are resolved from config when args are omitted."""

    def test_list_uses_config_defaults(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        # Seed the config defaults.
        runner.invoke(cli, ["config", "set", "workspace", WS_GUID])
        runner.invoke(cli, ["config", "set", "warehouse", WH_GUID])
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.restore_points.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.restore.list_points",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["restore-points", "list"])
        assert result.exit_code == 0

    def test_list_missing_workspace_raises_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        result = runner.invoke(cli, ["restore-points", "list"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _make_mock_response(status_code: int, text: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json = MagicMock(return_value=json.loads(text) if text and text.strip() else {})
    mock_resp.headers = {}
    return mock_resp
