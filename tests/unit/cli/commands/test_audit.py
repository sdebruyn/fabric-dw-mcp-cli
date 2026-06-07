"""Tests for audit CLI sub-commands — written BEFORE the implementation (TDD)."""

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
from tests.fixtures.api_payloads import AUDIT_SETTINGS_PAYLOAD

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


def _make_item_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


class TestAuditGet:
    """audit get — happy path and 404."""

    def test_get_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["audit", "get", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_get_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--json", "audit", "get", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_get_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit._resolve_item",
                new=AsyncMock(side_effect=NotFound("not found")),
            ),
        ):
            result = runner.invoke(cli, ["audit", "get", WS_GUID, WH_GUID])
        assert result.exit_code != 0


class TestAuditEnable:
    """audit enable — happy path."""

    def test_enable_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["audit", "enable", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_enable_with_retention_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli, ["audit", "enable", WS_GUID, WH_GUID, "--retention-days", "30"]
            )
        assert result.exit_code == 0


class TestAuditDisable:
    """audit disable — happy path and confirmation."""

    def test_disable_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "audit", "disable", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_disable_declined_aborts(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(cli, ["audit", "disable", WS_GUID, WH_GUID], input="n\n")
        assert result.exit_code != 0


class TestAuditSetGroups:
    """audit set-groups — happy path."""

    def test_set_groups_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_make_response(200, AUDIT_SETTINGS_PAYLOAD))
        with (
            patch(
                "fabric_dw.cli.commands.audit._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "audit",
                    "set-groups",
                    WS_GUID,
                    WH_GUID,
                    "--group",
                    "BATCH_COMPLETED_GROUP",
                    "--group",
                    "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
                ],
            )
        assert result.exit_code == 0

    def test_set_groups_invalid_name_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.audit._build_clients",
                new=_make_cm(mock_http, None),
            ),
            patch(
                "fabric_dw.cli.commands.audit._resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "audit",
                    "set-groups",
                    WS_GUID,
                    WH_GUID,
                    "--group",
                    "invalid-lowercase-group",
                ],
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int, text: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json = MagicMock(return_value=json.loads(text) if text and text.strip() else {})
    mock_resp.headers = {}
    return mock_resp
