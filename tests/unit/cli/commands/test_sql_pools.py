"""Tests for sql-pools CLI sub-commands."""

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
from fabric_dw.exceptions import PermissionDenied
from fabric_dw.models import SqlPoolsConfiguration

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

_CONFIG = SqlPoolsConfiguration.model_validate(POOLS_PAYLOAD)
_CONFIG_DISABLED = SqlPoolsConfiguration.model_validate(POOLS_DISABLED_PAYLOAD)


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
                "fabric_dw.cli.commands.sql_pools._build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._resolve_workspace",
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
                "fabric_dw.cli.commands.sql_pools._build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._resolve_workspace",
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
                "fabric_dw.cli.commands.sql_pools._build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._resolve_workspace",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.get_configuration",
                new=AsyncMock(side_effect=PermissionDenied("403")),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "get", WS_GUID])
        assert result.exit_code != 0
        assert "admin" in result.output.lower()


class TestSqlPoolsSet:
    def test_set_requires_from_file(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["sql-pools", "set", WS_GUID])
        assert result.exit_code != 0
        assert "from-file" in result.output.lower() or "missing" in result.output.lower()

    def test_set_applies_from_file_with_yes(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        pool_file = tmp_path / "pools.json"
        pool_file.write_text(json.dumps(POOLS_PAYLOAD))

        mock_update = AsyncMock(return_value=_CONFIG)
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools._build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._resolve_workspace",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.update_configuration",
                new=mock_update,
            ),
        ):
            result = runner.invoke(
                cli, ["-y", "sql-pools", "set", WS_GUID, "--from-file", str(pool_file)]
            )
        assert result.exit_code == 0
        mock_update.assert_awaited_once()

    def test_set_invalid_json_file_exits_nonzero(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{not valid json}")
        result = runner.invoke(
            cli, ["-y", "sql-pools", "set", WS_GUID, "--from-file", str(bad_file)]
        )
        assert result.exit_code != 0

    def test_set_invalid_config_exits_nonzero(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        bad_payload = {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {"name": "A", "isDefault": True, "maxResourcePercentage": 101},
            ],
        }
        bad_file = tmp_path / "bad_config.json"
        bad_file.write_text(json.dumps(bad_payload))
        result = runner.invoke(
            cli, ["-y", "sql-pools", "set", WS_GUID, "--from-file", str(bad_file)]
        )
        assert result.exit_code != 0


class TestSqlPoolsEnable:
    def test_enable_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.sql_pools._build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._resolve_workspace",
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
                "fabric_dw.cli.commands.sql_pools._build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._resolve_workspace",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.enable",
                new=AsyncMock(side_effect=PermissionDenied("403")),
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
                "fabric_dw.cli.commands.sql_pools._build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._resolve_workspace",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.cli.commands.sql_pools._svc.disable",
                new=AsyncMock(return_value=_CONFIG_DISABLED),
            ),
        ):
            result = runner.invoke(cli, ["sql-pools", "disable", WS_GUID])
        assert result.exit_code == 0
