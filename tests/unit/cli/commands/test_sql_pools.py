"""Tests for sql-pools CLI sub-commands (Azure-CLI-style sub-resource interface)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

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
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "sql-pools", "get"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["customSQLPoolsEnabled"] is True
        assert len(data["customSQLPools"]) == 1

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
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "sql-pools", "get"])
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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "get"])
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
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "sql-pools", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        pool_names = [p["name"] for p in data]
        assert "ETL" in pool_names
        assert "Reporting" in pool_names

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
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "sql-pools", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        names = [p["name"] for p in data]
        assert "ETL" in names
        assert "Reporting" in names

    def test_list_empty_human_shows_default_pools_note(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """No custom pools => human output explains the default SELECT/NON-SELECT pools."""
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
                new=AsyncMock(return_value=_CONFIG_EMPTY),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "list"])
        assert result.exit_code == 0
        out = result.output
        assert "No custom SQL pools" in out
        assert "default" in out.lower()
        assert "SELECT" in out
        assert "NON-SELECT" in out
        assert "50%" in out

    def test_list_empty_disabled_shows_default_pools_note(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """customSQLPoolsEnabled=False with no pools also shows the default note."""
        _ = cache_env
        disabled_empty = SqlPoolsConfiguration.model_validate(
            {"customSQLPoolsEnabled": False, "customSQLPools": []}
        )
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
                new=AsyncMock(return_value=disabled_empty),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "list"])
        assert result.exit_code == 0
        assert "NON-SELECT" in result.output

    def test_list_empty_json_default_workload_shape(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """No custom pools => JSON is honest: empty customSQLPools + default indicators."""
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
                new=AsyncMock(return_value=_CONFIG_EMPTY),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "sql-pools", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        # Honest: no fabricated custom pools.
        assert data["customSQLPools"] == []
        assert data["default_workload_active"] is True
        default_pools = data["default_pools"]
        assert isinstance(default_pools, list)
        names = [p["name"] for p in default_pools]
        assert names == ["SELECT", "NON-SELECT"]
        assert all(p["maxResourcePercentage"] == 50 for p in default_pools)
        assert all(p["isDefault"] is True for p in default_pools)


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
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--json", "sql-pools", "show", "--name", "ETL"]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["name"] == "ETL"
        assert data["maxResourcePercentage"] == 40

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
            result = runner.invoke(
                cli, ["-w", WS_GUID, "sql-pools", "show", "--name", "DoesNotExist"]
            )
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
                    "-w",
                    WS_GUID,
                    "sql-pools",
                    "create",
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
                    "-w",
                    WS_GUID,
                    "sql-pools",
                    "create",
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
                    "-w",
                    WS_GUID,
                    "sql-pools",
                    "create",
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
                    "-w",
                    WS_GUID,
                    "sql-pools",
                    "create",
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
                    "-w",
                    WS_GUID,
                    "sql-pools",
                    "update",
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
                    "-w",
                    WS_GUID,
                    "sql-pools",
                    "update",
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
                    "-w",
                    WS_GUID,
                    "sql-pools",
                    "update",
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
                    "-w",
                    WS_GUID,
                    "sql-pools",
                    "update",
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
                ["-w", WS_GUID, "-y", "sql-pools", "delete", "--name", "Default"],
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
                ["-w", WS_GUID, "-y", "sql-pools", "delete", "--name", "NoPool"],
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
                ["-w", WS_GUID, "-y", "sql-pools", "delete", "--name", "Default"],
            )
        assert result.exit_code != 0
        assert "admin" in result.output.lower()


class TestSqlPoolsEnable:
    def test_enable_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_enable = AsyncMock(return_value=_CONFIG)
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
                new=mock_enable,
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "sql-pools", "enable"])
        assert result.exit_code == 0
        mock_enable.assert_awaited_once()
        data = json.loads(result.output)
        assert data["customSQLPoolsEnabled"] is True

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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "enable"])
        assert result.exit_code != 0
        assert "admin" in result.output.lower()


class TestSqlPoolsDisable:
    def test_disable_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_disable = AsyncMock(return_value=_CONFIG_DISABLED)
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
                new=mock_disable,
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "sql-pools", "disable"])
        assert result.exit_code == 0
        mock_disable.assert_awaited_once()
        data = json.loads(result.output)
        assert data["customSQLPoolsEnabled"] is False


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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "get"])
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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "list"])
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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "list"])
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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "show", "--name", "Default"])
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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "show", "--name", "Default"])
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
                cli,
                ["-w", WS_GUID, "sql-pools", "create", "--name", "NewPool", "--max-percent", "50"],
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
                cli,
                ["-w", WS_GUID, "sql-pools", "create", "--name", "NewPool", "--max-percent", "50"],
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
                cli,
                ["-w", WS_GUID, "sql-pools", "create", "--name", "NewPool", "--max-percent", "50"],
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
                ["-w", WS_GUID, "sql-pools", "update", "--name", "Default", "--max-percent", "50"],
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
                ["-w", WS_GUID, "sql-pools", "update", "--name", "Default", "--max-percent", "50"],
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
                ["-w", WS_GUID, "sql-pools", "update", "--name", "Default", "--max-percent", "50"],
            )
        assert result.exit_code != 0
        assert "Invalid pool configuration" in result.output


class TestSqlPoolsDeleteAbort:
    """delete_cmd — abort (no-confirm) branch (lines 307-308)."""

    def test_delete_declined_prints_aborted(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli, ["-w", WS_GUID, "sql-pools", "delete", "--name", "Default"], input="n\n"
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
            result = runner.invoke(
                cli, ["-w", WS_GUID, "-y", "sql-pools", "delete", "--name", "Default"]
            )
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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "enable"])
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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "disable"])
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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "disable"])
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
        mock_insights = AsyncMock(return_value=[])
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
                new=mock_insights,
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "sql-pools", "insights", WS_GUID])
        assert result.exit_code == 0
        mock_insights.assert_awaited_once()
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_insights_with_since_and_until(self, runner: CliRunner, cache_env: Path) -> None:
        """Passes --since and --until to exercise the _parse_iso line 410."""
        _ = cache_env
        mock_insights = AsyncMock(return_value=[])
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
                new=mock_insights,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "sql-pools",
                    "insights",
                    WS_GUID,
                    "--since",
                    "2024-01-01T00:00:00",
                    "--until",
                    "2024-12-31T23:59:59",
                ],
            )
        assert result.exit_code == 0
        # since/until must have been parsed to the exact datetime values and forwarded.
        # These asserts would fail if parse_iso_datetime were skipped or wrong values passed.
        mock_insights.assert_awaited_once()
        _, kwargs = mock_insights.call_args
        # The CLI uses parse_iso_optional(assume_utc=False), so naive input stays naive.
        expected_since = datetime(2024, 1, 1, 0, 0, 0)  # noqa: DTZ001 — testing naive CLI output
        expected_until = datetime(2024, 12, 31, 23, 59, 59)  # noqa: DTZ001
        assert kwargs.get("since") == expected_since, (
            f"Expected since={expected_since!r}, got {kwargs.get('since')!r}"
        )
        assert kwargs.get("until") == expected_until, (
            f"Expected until={expected_until!r}, got {kwargs.get('until')!r}"
        )

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
            result = runner.invoke(cli, ["-w", WS_GUID, "sql-pools", "insights", WS_GUID])
        assert result.exit_code != 0
