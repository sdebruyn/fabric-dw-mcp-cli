"""Tests for workspaces CLI sub-commands — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import cli
from fabric_dw.exceptions import NotFound
from tests.fixtures.api_payloads import WORKSPACE_GET_PAYLOAD

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WS_UUID = UUID(WS_GUID)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect XDG_CACHE_HOME to a temp dir so cache files are isolated."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def _make_cm(http: object, sql: object) -> object:
    """Return an async context manager that yields (http, sql)."""

    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[tuple[object, object]]:
        yield http, sql

    return _cm


class TestWorkspacesList:
    """workspaces list — happy path and error path."""

    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(
            return_value=_async_iter(
                [
                    {
                        "id": WS_GUID,
                        "displayName": "AnalyticsWorkspace",
                        "description": "desc",
                        "type": "Workspace",
                        "capacityId": None,
                    }
                ]
            )
        )
        with patch(
            "fabric_dw.cli.commands.workspaces._build_clients",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["workspaces", "list"])
        assert result.exit_code == 0

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.iter_paginated = MagicMock(
            return_value=_async_iter(
                [
                    {
                        "id": WS_GUID,
                        "displayName": "AnalyticsWorkspace",
                        "description": None,
                        "type": "Workspace",
                        "capacityId": None,
                    }
                ]
            )
        )
        with patch(
            "fabric_dw.cli.commands.workspaces._build_clients",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["--json", "workspaces", "list"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert len(parsed) == 1


class TestWorkspacesGet:
    """workspaces get — happy path and error path."""

    def test_get_by_guid_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, WORKSPACE_GET_PAYLOAD))
        with patch(
            "fabric_dw.cli.commands.workspaces._build_clients",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["workspaces", "get", WS_GUID])
        assert result.exit_code == 0

    def test_get_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(side_effect=NotFound("not found"))
        with patch(
            "fabric_dw.cli.commands.workspaces._build_clients",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["workspaces", "get", WS_GUID])
        assert result.exit_code != 0


class TestWorkspacesGetCollation:
    """workspaces get-collation — happy path and no collation field."""

    def test_get_collation_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(
            return_value=_make_response(
                200,
                json.dumps(
                    {
                        "id": WS_GUID,
                        "displayName": "AnalyticsWorkspace",
                        "defaultDataWarehouseCollation": "Latin1_General_100_BIN2_UTF8",
                    }
                ),
            )
        )
        with patch(
            "fabric_dw.cli.commands.workspaces._build_clients",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["workspaces", "get-collation", WS_GUID])
        assert result.exit_code == 0

    def test_get_collation_none_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, WORKSPACE_GET_PAYLOAD))
        with patch(
            "fabric_dw.cli.commands.workspaces._build_clients",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["workspaces", "get-collation", WS_GUID])
        assert result.exit_code == 0


class TestWorkspacesSetCollation:
    """workspaces set-collation — happy path, confirmation, and error path."""

    def test_set_collation_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, "{}"))
        with patch(
            "fabric_dw.cli.commands.workspaces._build_clients",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "workspaces",
                    "set-collation",
                    WS_GUID,
                    "Latin1_General_100_BIN2_UTF8",
                ],
            )
        assert result.exit_code == 0

    def test_set_collation_invalid_value_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with patch(
            "fabric_dw.cli.commands.workspaces._build_clients",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "workspaces",
                    "set-collation",
                    WS_GUID,
                    "INVALID_COLLATION",
                ],
            )
        assert result.exit_code != 0

    def test_set_collation_declined_aborts(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with patch(
            "fabric_dw.cli.commands.workspaces._build_clients",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(
                cli,
                [
                    "workspaces",
                    "set-collation",
                    WS_GUID,
                    "Latin1_General_100_BIN2_UTF8",
                ],
                input="n\n",
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _async_iter_coro(items: list[object]):  # type: ignore[no-untyped-def]
    for item in items:
        yield item


def _async_iter(items: list[object]):  # type: ignore[no-untyped-def]
    """Return an async iterable over *items*."""
    return _async_iter_coro(items)


def _make_response(status_code: int, text: str) -> MagicMock:
    """Return a MagicMock that looks like an httpx Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json = MagicMock(return_value=json.loads(text) if text.strip() else {})
    mock_resp.headers = {}
    return mock_resp
