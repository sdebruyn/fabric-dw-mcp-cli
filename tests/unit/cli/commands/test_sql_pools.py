"""Tests for sql-pools CLI sub-commands (Azure-CLI-style sub-resource interface)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import cli
from fabric_dw.exceptions import (
    AlreadyExistsError,
    FabricError,
    NotFoundError,
    PermissionDeniedError,
)
from fabric_dw.models import SqlPool, SqlPoolsConfiguration
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WS_UUID = UUID(WS_GUID)

POOLS_PAYLOAD = {
    "customSQLPoolsEnabled": True,
    "customSQLPools": [
        {
            "name": "Default",
            "isDefault": True,
            "maxResourcePercentage": 100,
            "optimizeForReads": False,
        }
    ],
}

POOLS_DISABLED_PAYLOAD = {
    "customSQLPoolsEnabled": False,
    "customSQLPools": [
        {
            "name": "Default",
            "isDefault": True,
            "maxResourcePercentage": 100,
            "optimizeForReads": False,
        }
    ],
}

POOLS_EMPTY_PAYLOAD = {
    "customSQLPoolsEnabled": True,
    "customSQLPools": [],
}

MULTI_POOL_PAYLOAD = {
    "customSQLPoolsEnabled": True,
    "customSQLPools": [
        {
            "name": "ETL",
            "isDefault": False,
            "maxResourcePercentage": 40,
            "optimizeForReads": False,
            "classifier": {"type": "Application Name", "value": ["ETL"]},
        },
        {
            "name": "Reporting",
            "isDefault": True,
            "maxResourcePercentage": 60,
            "optimizeForReads": True,
            "classifier": {"type": "Application Name", "value": ["Reports"]},
        },
    ],
}

_CONFIG = SqlPoolsConfiguration.model_validate(POOLS_PAYLOAD)
_CONFIG_DISABLED = SqlPoolsConfiguration.model_validate(POOLS_DISABLED_PAYLOAD)
_CONFIG_EMPTY = SqlPoolsConfiguration.model_validate(POOLS_EMPTY_PAYLOAD)
_CONFIG_MULTI = SqlPoolsConfiguration.model_validate(MULTI_POOL_PAYLOAD)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def _make_http_cm(http: object) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


class TestSqlPoolsGet:
    def test_get_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(return_value=_CONFIG),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "get", WS_GUID])
        assert result.exit_code == 0

    def test_get_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(return_value=_CONFIG),
            ),
        ):
            result = runner.invoke(cli, ["--json", "sql-pools", "get", WS_GUID])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["customSQLPoolsEnabled"] is True
        assert len(data["customSQLPools"]) == 1

    def test_get_403_shows_permission_hint(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(side_effect=PermissionDeniedError("403")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "get", WS_GUID])
        assert result.exit_code != 0
        assert "admin" in result.output.lower()


class TestSqlPoolsList:
    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(return_value=_CONFIG_MULTI),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "list", WS_GUID])
        assert result.exit_code == 0

    def test_list_json_shows_pool_array(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(return_value=_CONFIG_MULTI),
            ),
        ):
            result = runner.invoke(cli, ["--json", "sql-pools", "list", WS_GUID])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        names = [p["name"] for p in data]
        assert "ETL" in names
        assert "Reporting" in names


class TestSqlPoolsShow:
    def test_show_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(return_value=_CONFIG_MULTI),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "show", WS_GUID, "--name", "ETL"])
        assert result.exit_code == 0

    def test_show_missing_pool_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(return_value=_CONFIG_MULTI),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "show", WS_GUID, "--name", "DoesNotExist"])
        assert result.exit_code != 0


class TestSqlPoolsCreate:
    def test_create_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_create = AsyncMock(return_value=_CONFIG)
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.create_pool",
                new=mock_create,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "sql-pools",
                    "create",
                    WS_GUID,
                    "--name",
                    "NewPool",
                    "--max-percent",
                    "30",
                    "--classifier-type",
                    "Application Name",
                    "--classifier-value",
                    "App1",
                ],
            )
        assert result.exit_code == 0
        mock_create.assert_awaited_once()

    def test_create_with_multiple_classifier_values(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_create = AsyncMock(return_value=_CONFIG)
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.create_pool",
                new=mock_create,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "sql-pools",
                    "create",
                    WS_GUID,
                    "--name",
                    "NewPool",
                    "--max-percent",
                    "30",
                    "--classifier-type",
                    "Application Name",
                    "--classifier-value",
                    "App1",
                    "--classifier-value",
                    "App2",
                ],
            )
        assert result.exit_code == 0
        call_args = mock_create.call_args
        pool: SqlPool = call_args.args[2]
        assert pool.classifier is not None
        assert pool.classifier.value == ["App1", "App2"]

    def test_create_already_exists_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.create_pool",
                new=AsyncMock(side_effect=AlreadyExistsError("pool 'Default' already exists")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "sql-pools",
                    "create",
                    WS_GUID,
                    "--name",
                    "Default",
                    "--max-percent",
                    "100",
                ],
            )
        assert result.exit_code != 0
        assert "already exists" in result.output.lower() or "Default" in result.output

    def test_create_with_default_flag(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_create = AsyncMock(return_value=_CONFIG)
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.create_pool",
                new=mock_create,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "sql-pools",
                    "create",
                    WS_GUID,
                    "--name",
                    "Defaults",
                    "--max-percent",
                    "100",
                    "--default",
                ],
            )
        assert result.exit_code == 0
        call_args = mock_create.call_args
        pool: SqlPool = call_args.args[2]
        assert pool.is_default is True


class TestSqlPoolsUpdate:
    def test_update_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_update = AsyncMock(return_value=_CONFIG)
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.update_pool",
                new=mock_update,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "sql-pools",
                    "update",
                    WS_GUID,
                    "--name",
                    "Default",
                    "--max-percent",
                    "50",
                ],
            )
        assert result.exit_code == 0
        mock_update.assert_awaited_once()

    def test_update_not_found_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.update_pool",
                new=AsyncMock(side_effect=NotFoundError("pool 'NoPool' not found")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "sql-pools",
                    "update",
                    WS_GUID,
                    "--name",
                    "NoPool",
                    "--max-percent",
                    "50",
                ],
            )
        assert result.exit_code != 0

    def test_update_partial_flags_passed_correctly(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_update = AsyncMock(return_value=_CONFIG)
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.update_pool",
                new=mock_update,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "sql-pools",
                    "update",
                    WS_GUID,
                    "--name",
                    "Default",
                    "--no-optimize-for-reads",
                ],
            )
        assert result.exit_code == 0
        _, kwargs = mock_update.call_args
        assert kwargs.get("optimize_for_reads") is False
        assert kwargs.get("max_resource_percentage") is None

    def test_update_with_is_default_toggle(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_update = AsyncMock(return_value=_CONFIG)
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.update_pool",
                new=mock_update,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "sql-pools",
                    "update",
                    WS_GUID,
                    "--name",
                    "Default",
                    "--no-default",
                ],
            )
        assert result.exit_code == 0
        _, kwargs = mock_update.call_args
        assert kwargs.get("is_default") is False


class TestSqlPoolsDelete:
    def test_delete_exits_zero_with_yes(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_delete = AsyncMock(return_value=_CONFIG_EMPTY)
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.delete_pool",
                new=mock_delete,
            ),
        ):
            result = runner.invoke(
                cli,
                ["-y", "sql-pools", "delete", WS_GUID, "--name", "Default"],
            )
        assert result.exit_code == 0
        mock_delete.assert_awaited_once()

    def test_delete_not_found_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.delete_pool",
                new=AsyncMock(side_effect=NotFoundError("pool 'NoPool' not found")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-y", "sql-pools", "delete", WS_GUID, "--name", "NoPool"],
            )
        assert result.exit_code != 0

    def test_delete_403_shows_permission_hint(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.delete_pool",
                new=AsyncMock(side_effect=PermissionDeniedError("403")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-y", "sql-pools", "delete", WS_GUID, "--name", "Default"],
            )
        assert result.exit_code != 0
        assert "admin" in result.output.lower()


class TestSqlPoolsEnable:
    def test_enable_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.enable",
                new=AsyncMock(return_value=_CONFIG),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "enable", WS_GUID])
        assert result.exit_code == 0

    def test_enable_403_shows_permission_hint(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.enable",
                new=AsyncMock(side_effect=PermissionDeniedError("403")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "enable", WS_GUID])
        assert result.exit_code != 0
        assert "admin" in result.output.lower()


class TestSqlPoolsDisable:
    def test_disable_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.disable",
                new=AsyncMock(return_value=_CONFIG_DISABLED),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "disable", WS_GUID])
        assert result.exit_code == 0


class TestSqlPoolsGetFabricError:
    """get_cmd — FabricError branch (line 70-71)."""

    def test_get_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "get", WS_GUID])
        assert result.exit_code != 0
        assert "server error" in result.output


class TestSqlPoolsListErrors:
    """list_cmd — PermissionDeniedError and FabricError branches (lines 92-95)."""

    def test_list_403_shows_permission_hint(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(side_effect=PermissionDeniedError("403")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "list", WS_GUID])
        assert result.exit_code != 0
        assert "admin" in result.output.lower()

    def test_list_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "list", WS_GUID])
        assert result.exit_code != 0


class TestSqlPoolsShowErrors:
    """show_cmd — PermissionDeniedError and FabricError branches (lines 115-118)."""

    def test_show_403_shows_permission_hint(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(side_effect=PermissionDeniedError("403")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "show", WS_GUID, "--name", "Default"])
        assert result.exit_code != 0
        assert "admin" in result.output.lower()

    def test_show_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "show", WS_GUID, "--name", "Default"])
        assert result.exit_code != 0


class TestSqlPoolsCreateErrors:
    """create_cmd — ValueError, PermissionDeniedError, FabricError branches (204-209)."""

    def test_create_permission_denied_shows_hint(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.create_pool",
                new=AsyncMock(side_effect=PermissionDeniedError("403")),
            ),
        ):
            result = runner.invoke(
                cli, ["sql-pools", "create", WS_GUID, "--name", "NewPool", "--max-percent", "50"]
            )
        assert result.exit_code != 0
        assert "admin" in result.output.lower()

    def test_create_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.create_pool",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(
                cli, ["sql-pools", "create", WS_GUID, "--name", "NewPool", "--max-percent", "50"]
            )
        assert result.exit_code != 0

    def test_create_value_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        """ValueError from service surfaces as ClickException (line 205)."""
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.create_pool",
                new=AsyncMock(side_effect=ValueError("bad config")),
            ),
        ):
            result = runner.invoke(
                cli, ["sql-pools", "create", WS_GUID, "--name", "NewPool", "--max-percent", "50"]
            )
        assert result.exit_code != 0
        assert "Invalid pool configuration" in result.output


class TestSqlPoolsUpdateErrors:
    """update_cmd — ValueError, PermissionDeniedError, FabricError branches (285-290)."""

    def test_update_permission_denied_shows_hint(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.update_pool",
                new=AsyncMock(side_effect=PermissionDeniedError("403")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql-pools", "update", WS_GUID, "--name", "Default", "--max-percent", "50"],
            )
        assert result.exit_code != 0
        assert "admin" in result.output.lower()

    def test_update_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.update_pool",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql-pools", "update", WS_GUID, "--name", "Default", "--max-percent", "50"],
            )
        assert result.exit_code != 0

    def test_update_value_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        """ValueError from service surfaces as ClickException (line 286)."""
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.update_pool",
                new=AsyncMock(side_effect=ValueError("bad config")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql-pools", "update", WS_GUID, "--name", "Default", "--max-percent", "50"],
            )
        assert result.exit_code != 0
        assert "Invalid pool configuration" in result.output


class TestSqlPoolsDeleteAbort:
    """delete_cmd — abort (no-confirm) branch (lines 307-308)."""

    def test_delete_declined_prints_aborted(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli, ["sql-pools", "delete", WS_GUID, "--name", "Default"], input="n\n"
        )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_delete_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.delete_pool",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["-y", "sql-pools", "delete", WS_GUID, "--name", "Default"])
        assert result.exit_code != 0


class TestSqlPoolsEnableFabricError:
    """enable_cmd — FabricError branch (line 344-345)."""

    def test_enable_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.enable",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "enable", WS_GUID])
        assert result.exit_code != 0


class TestSqlPoolsDisableErrors:
    """disable_cmd — PermissionDeniedError and FabricError branches (lines 366-369)."""

    def test_disable_403_shows_permission_hint(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.disable",
                new=AsyncMock(side_effect=PermissionDeniedError("403")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "disable", WS_GUID])
        assert result.exit_code != 0
        assert "admin" in result.output.lower()

    def test_disable_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.disable",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "disable", WS_GUID])
        assert result.exit_code != 0


class TestSqlPoolsInsights:
    """insights_cmd — happy path + FabricError branch (lines 461-462)."""

    def _make_sql_target(self) -> SqlTarget:
        return SqlTarget(
            workspace_id=WS_GUID,
            database="SalesWarehouse",
            connection_string="wh.datawarehouse.fabric.microsoft.com",
        )

    def test_insights_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.build_sql_target",
                new=AsyncMock(return_value=(self._make_sql_target(), AsyncMock())),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._qi_svc.list_sql_pool_insights",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "insights", WS_GUID, WS_GUID])
        assert result.exit_code == 0

    def test_insights_with_since_and_until(self, runner: CliRunner, cache_env: Path) -> None:
        """Passes --since and --until to exercise the _parse_iso line 410."""
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.build_sql_target",
                new=AsyncMock(return_value=(self._make_sql_target(), AsyncMock())),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._qi_svc.list_sql_pool_insights",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "sql-pools",
                    "insights",
                    WS_GUID,
                    WS_GUID,
                    "--since",
                    "2024-01-01T00:00:00",
                    "--until",
                    "2024-12-31T23:59:59",
                ],
            )
        assert result.exit_code == 0

    def test_insights_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools.build_sql_target",
                new=AsyncMock(side_effect=FabricError("server error")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "insights", WS_GUID, WS_GUID])
        assert result.exit_code != 0
