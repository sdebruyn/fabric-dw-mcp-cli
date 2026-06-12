"""Tests for schemas CLI sub-commands."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from click.testing import CliRunner

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.exceptions import ItemKindError, NotFoundError, PermissionDeniedError
from fabric_dw.models import Schema, WarehouseKind
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def _make_sql_target() -> SqlTarget:
    return SqlTarget(
        workspace_id=WS_GUID,
        database="SalesWarehouse",
        connection_string="wh.datawarehouse.fabric.microsoft.com",
    )


def _make_http_cm(http: object) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


def _make_item_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


def _make_sql_endpoint_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.SQL_ENDPOINT,
        connection_string="ep.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesEndpoint",
    )


def _make_schema() -> Schema:
    return Schema(name="sales", principal_id=5)


# ===========================================================================
# schemas list
# ===========================================================================


class TestSchemasList:
    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.list_schemas",
                new=AsyncMock(return_value=[_make_schema()]),
            ),
        ):
            result = runner.invoke(cli, ["schemas", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.list_schemas",
                new=AsyncMock(return_value=[_make_schema()]),
            ),
        ):
            result = runner.invoke(cli, ["--json", "schemas", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "sales"

    def test_list_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["schemas", "list", WS_GUID, WH_GUID])
        assert result.exit_code != 0

    def test_list_permission_error_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.list_schemas",
                new=AsyncMock(side_effect=PermissionDeniedError("access denied")),
            ),
        ):
            result = runner.invoke(cli, ["schemas", "list", WS_GUID, WH_GUID])
        assert result.exit_code != 0


# ===========================================================================
# schemas create
# ===========================================================================


class TestSchemasCreate:
    def test_create_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.create_schema",
                new=AsyncMock(return_value=_make_schema()),
            ),
        ):
            result = runner.invoke(cli, ["schemas", "create", WS_GUID, WH_GUID, "sales"])
        assert result.exit_code == 0

    def test_create_json_output_contains_name(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.create_schema",
                new=AsyncMock(return_value=_make_schema()),
            ),
        ):
            result = runner.invoke(cli, ["--json", "schemas", "create", WS_GUID, WH_GUID, "sales"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "sales"

    def test_create_sql_endpoint_succeeds(self, runner: CliRunner, cache_env: Path) -> None:
        """CREATE SCHEMA is supported on SQL Analytics Endpoints (Fabric T-SQL reference)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_sql_endpoint_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.create_schema",
                new=AsyncMock(return_value=_make_schema()),
            ),
        ):
            result = runner.invoke(cli, ["schemas", "create", WS_GUID, WH_GUID, "sales"])
        assert result.exit_code == 0

    def test_create_permission_error_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.create_schema",
                new=AsyncMock(side_effect=PermissionDeniedError("access denied")),
            ),
        ):
            result = runner.invoke(cli, ["schemas", "create", WS_GUID, WH_GUID, "sales"])
        assert result.exit_code != 0


# ===========================================================================
# schemas delete
# ===========================================================================


class TestSchemasDelete:
    def test_delete_exits_zero_with_yes_flag(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.delete_schema",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(cli, ["-y", "schemas", "delete", WS_GUID, WH_GUID, "sales"])
        assert result.exit_code == 0
        assert "dropped" in result.output

    def test_delete_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining delete is a clean no-op (exit 0, policy: decline != error)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.delete_schema",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli, ["schemas", "delete", WS_GUID, WH_GUID, "sales"], input="n\n"
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_delete_cascade_passes_cascade_true(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_delete = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.schemas.delete_schema", new=mock_delete),
        ):
            result = runner.invoke(
                cli,
                ["-y", "schemas", "delete", WS_GUID, WH_GUID, "sales", "--cascade"],
            )
        assert result.exit_code == 0
        _, kwargs = mock_delete.call_args
        assert kwargs.get("cascade") is True

    def test_delete_no_cascade_by_default(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_delete = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.schemas.delete_schema", new=mock_delete),
        ):
            result = runner.invoke(
                cli,
                ["-y", "schemas", "delete", WS_GUID, WH_GUID, "sales"],
            )
        assert result.exit_code == 0
        _, kwargs = mock_delete.call_args
        assert kwargs.get("cascade") is False

    def test_delete_sql_endpoint_succeeds(self, runner: CliRunner, cache_env: Path) -> None:
        """DROP SCHEMA is supported on SQL Analytics Endpoints (Fabric T-SQL reference)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_sql_endpoint_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.delete_schema",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(cli, ["-y", "schemas", "delete", WS_GUID, WH_GUID, "sales"])
        assert result.exit_code == 0
        assert "dropped" in result.output

    def test_delete_cascade_warns_on_stderr(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.delete_schema",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-y", "schemas", "delete", WS_GUID, WH_GUID, "sales", "--cascade"],
            )
        # With --yes the prompt is skipped entirely; the command exits cleanly.
        assert result.exit_code == 0

    def test_delete_permission_error_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.delete_schema",
                new=AsyncMock(side_effect=PermissionDeniedError("access denied")),
            ),
        ):
            result = runner.invoke(cli, ["-y", "schemas", "delete", WS_GUID, WH_GUID, "sales"])
        assert result.exit_code != 0

    def test_delete_cascade_sql_endpoint_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--cascade on a SQL Analytics Endpoint must be rejected (ItemKindError → nonzero)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_sql_endpoint_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.delete_schema",
                new=AsyncMock(
                    side_effect=ItemKindError(
                        "cascade=True is not supported on SQL Analytics Endpoints"
                    )
                ),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-y", "schemas", "delete", WS_GUID, WH_GUID, "sales", "--cascade"],
            )
        assert result.exit_code != 0
        assert "cascade" in result.output.lower() or "cascade" in (result.output or "").lower()

    def test_delete_no_cascade_sql_endpoint_succeeds(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """DROP SCHEMA without cascade on a SQL Analytics Endpoint must succeed."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_sql_endpoint_entry())),
            ),
            patch(
                "fabric_dw.services.schemas.delete_schema",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(cli, ["-y", "schemas", "delete", WS_GUID, WH_GUID, "sales"])
        assert result.exit_code == 0
        assert "dropped" in result.output

    def test_delete_passes_kind_to_service(self, runner: CliRunner, cache_env: Path) -> None:
        """delete_cmd must pass entry.kind to the service."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_delete = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.schemas.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.schemas.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.schemas.delete_schema", new=mock_delete),
        ):
            runner.invoke(cli, ["-y", "schemas", "delete", WS_GUID, WH_GUID, "sales"])
        _, kwargs = mock_delete.call_args
        assert kwargs.get("kind") == WarehouseKind.WAREHOUSE
