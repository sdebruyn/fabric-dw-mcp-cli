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
from fabric_dw.exceptions import NotFoundError
from fabric_dw.models import FABRIC_DEFAULT_COLLATION
from tests.fixtures.api_payloads import WORKSPACE_GET_PAYLOAD

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WS_UUID = UUID(WS_GUID)


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect XDG_CACHE_HOME to a temp dir so cache files are isolated."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def _make_cm(http: object, _sql: object = None) -> object:
    """Return an async context manager that yields the http client."""

    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

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
            "fabric_dw.cli.commands.workspaces.build_http_client",
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
            "fabric_dw.cli.commands.workspaces.build_http_client",
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
            "fabric_dw.cli.commands.workspaces.build_http_client",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["workspaces", "get", WS_GUID])
        assert result.exit_code == 0

    def test_get_includes_collation_in_json_output(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
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
            "fabric_dw.cli.commands.workspaces.build_http_client",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["--json", "workspaces", "get", WS_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["defaultDataWarehouseCollation"] == "Latin1_General_100_BIN2_UTF8"

    def test_get_collation_null_coalesces_to_default_in_json_output(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, WORKSPACE_GET_PAYLOAD))
        with patch(
            "fabric_dw.cli.commands.workspaces.build_http_client",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["--json", "workspaces", "get", WS_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        # null/absent collation is coalesced to FABRIC_DEFAULT_COLLATION in the model
        assert parsed["defaultDataWarehouseCollation"] == FABRIC_DEFAULT_COLLATION

    def test_get_shows_collation_in_table_output(self, runner: CliRunner, cache_env: Path) -> None:
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
            "fabric_dw.cli.commands.workspaces.build_http_client",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["workspaces", "get", WS_GUID])
        assert result.exit_code == 0
        assert "Latin1_General_100_BIN2_UTF8" in result.output

    def test_get_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(side_effect=NotFoundError("not found"))
        with patch(
            "fabric_dw.cli.commands.workspaces.build_http_client",
            new=_make_cm(mock_http, None),
        ):
            result = runner.invoke(cli, ["workspaces", "get", WS_GUID])
        assert result.exit_code != 0


class TestWorkspacesSetCollation:
    """workspaces set-collation — happy path, confirmation, and error path."""

    def test_set_collation_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, "{}"))
        with patch(
            "fabric_dw.cli.commands.workspaces.build_http_client",
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
            "fabric_dw.cli.commands.workspaces.build_http_client",
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

    def test_set_collation_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining set-collation is a clean no-op (exit 0) — decline != error (L01)."""
        _ = cache_env
        mock_http = AsyncMock()
        with patch(
            "fabric_dw.cli.commands.workspaces.build_http_client",
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
        # User declined — this is a graceful no-op, not an error (exit 0).
        assert result.exit_code == 0
        assert "Aborted." in result.output


# ---------------------------------------------------------------------------
# Default fallback tests
# ---------------------------------------------------------------------------


class TestWorkspacesDefaultFallback:
    """Verify workspace default from config is used when arg is omitted."""

    def test_get_uses_config_default_workspace(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        runner.invoke(cli, ["config", "set", "workspace", WS_GUID])
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, WORKSPACE_GET_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.workspaces.build_http_client",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
        ):
            result = runner.invoke(cli, ["workspaces", "get"])
        assert result.exit_code == 0

    def test_get_missing_workspace_raises_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        result = runner.invoke(cli, ["workspaces", "get"])
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


# ---------------------------------------------------------------------------
# L04 — set-collation opens build_http_client exactly once
# ---------------------------------------------------------------------------


class TestSetCollationSingleHttpOpen:
    """L04: set-collation must open build_http_client exactly once (not twice)."""

    def test_set_collation_opens_http_client_once(self, runner: CliRunner, cache_env: Path) -> None:
        """build_http_client must be entered exactly once for set-collation."""
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
                "fabric_dw.cli.commands.workspaces.build_http_client",
                new=counting_cm,
            ),
            patch(
                "fabric_dw.cli.commands.workspaces.resolve_workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.services.workspaces.set_collation",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--yes", "workspaces", "set-collation", WS_GUID, "Latin1_General_100_BIN2_UTF8"],
            )

        assert result.exit_code == 0, result.output
        assert open_count == 1, f"build_http_client opened {open_count} times, expected 1"
