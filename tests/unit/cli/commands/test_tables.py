"""Tests for tables CLI sub-commands."""

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
from fabric_dw.exceptions import NotFound, PermissionDenied
from fabric_dw.models import Table, WarehouseKind
from fabric_dw.sql import SqlTarget

SE_GUID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
SE_UUID = UUID(SE_GUID)

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
        id=SE_UUID,
        kind=WarehouseKind.SQL_ENDPOINT,
        connection_string="se.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesLakehouse",
    )


def _make_table() -> Table:
    return Table(
        schema_name="dbo",
        name="sales",
        qualified_name="dbo.sales",
        created=_NOW,
        modified=_NOW,
    )


# ===========================================================================
# tables list
# ===========================================================================


class TestTablesList:
    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.list_tables",
                new=AsyncMock(return_value=[_make_table()]),
            ),
        ):
            result = runner.invoke(cli, ["tables", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.list_tables",
                new=AsyncMock(return_value=[_make_table()]),
            ),
        ):
            result = runner.invoke(cli, ["--json", "tables", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_with_schema_filter(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_list = AsyncMock(return_value=[_make_table()])
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.tables.list_tables", new=mock_list),
        ):
            result = runner.invoke(cli, ["tables", "list", WS_GUID, WH_GUID, "--schema", "dbo"])
        assert result.exit_code == 0
        mock_list.assert_awaited_once()

    def test_list_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(side_effect=NotFound("not found")),
            ),
        ):
            result = runner.invoke(cli, ["tables", "list", WS_GUID, WH_GUID])
        assert result.exit_code != 0


# ===========================================================================
# tables read
# ===========================================================================


class TestTablesRead:
    def test_read_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.read_table",
                new=AsyncMock(return_value=(["id", "name"], [(1, "Alice")])),
            ),
        ):
            result = runner.invoke(cli, ["tables", "read", WS_GUID, WH_GUID, "dbo.sales"])
        assert result.exit_code == 0

    def test_read_json_output_to_stdout(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.read_table",
                new=AsyncMock(return_value=(["id"], [(42,)])),
            ),
        ):
            result = runner.invoke(cli, ["tables", "read", WS_GUID, WH_GUID, "dbo.sales"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed[0]["id"] == 42

    def test_read_csv_requires_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli, ["tables", "read", WS_GUID, WH_GUID, "dbo.sales", "--format", "csv"]
        )
        assert result.exit_code != 0

    def test_read_parquet_requires_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli, ["tables", "read", WS_GUID, WH_GUID, "dbo.sales", "--format", "parquet"]
        )
        assert result.exit_code != 0

    def test_read_csv_with_output(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
        _ = cache_env
        out_file = tmp_path / "out.csv"
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.read_table",
                new=AsyncMock(return_value=(["id"], [(1,)])),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "tables",
                    "read",
                    WS_GUID,
                    WH_GUID,
                    "dbo.sales",
                    "--format",
                    "csv",
                    "--output",
                    str(out_file),
                ],
            )
        assert result.exit_code == 0
        assert out_file.exists()

    def test_read_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["tables", "read", WS_GUID, WH_GUID, "nodot"])
        assert result.exit_code != 0

    def test_read_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.read_table",
                new=AsyncMock(side_effect=NotFound("table not found")),
            ),
        ):
            result = runner.invoke(cli, ["tables", "read", WS_GUID, WH_GUID, "dbo.missing"])
        assert result.exit_code != 0


# ===========================================================================
# tables create
# ===========================================================================


class TestTablesCreate:
    def test_create_with_select_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.create_table",
                new=AsyncMock(return_value=_make_table()),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "tables",
                    "create",
                    WS_GUID,
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--select",
                    "SELECT id FROM src.raw",
                ],
            )
        assert result.exit_code == 0

    def test_create_with_file(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
        _ = cache_env
        sql_file = tmp_path / "ctas.sql"
        sql_file.write_text("SELECT id FROM src.raw")
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.create_table",
                new=AsyncMock(return_value=_make_table()),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "tables",
                    "create",
                    WS_GUID,
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--from-file",
                    str(sql_file),
                ],
            )
        assert result.exit_code == 0

    def test_create_no_select_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["tables", "create", WS_GUID, WH_GUID, "--name", "dbo.sales"],
        )
        assert result.exit_code != 0

    def test_create_both_select_and_file_fails(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        sql_file = tmp_path / "q.sql"
        sql_file.write_text("SELECT 1")
        result = runner.invoke(
            cli,
            [
                "tables",
                "create",
                WS_GUID,
                WH_GUID,
                "--name",
                "dbo.sales",
                "--select",
                "SELECT 1",
                "--from-file",
                str(sql_file),
            ],
        )
        assert result.exit_code != 0

    def test_create_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.create_table",
                new=AsyncMock(side_effect=PermissionDenied("no permission")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "tables",
                    "create",
                    WS_GUID,
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--select",
                    "SELECT 1",
                ],
            )
        assert result.exit_code != 0

    def test_create_warehouse_item_allowed(self, runner: CliRunner, cache_env: Path) -> None:
        """Warehouse items should not be blocked by the SQL Endpoint guard."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.create_table",
                new=AsyncMock(return_value=_make_table()),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "tables",
                    "create",
                    WS_GUID,
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--select",
                    "SELECT id FROM src.raw",
                ],
            )
        assert result.exit_code == 0

    def test_create_sql_endpoint_rejected(self, runner: CliRunner, cache_env: Path) -> None:
        """SQL Endpoint items must be rejected by the service-layer guard before issuing DDL."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_sql_endpoint_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "tables",
                    "create",
                    WS_GUID,
                    SE_GUID,
                    "--name",
                    "dbo.sales",
                    "--select",
                    "SELECT id FROM src.raw",
                ],
            )
        assert result.exit_code != 0
        assert "read-only" in result.output


# ===========================================================================
# tables delete
# ===========================================================================


class TestTablesDelete:
    def test_delete_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.delete_table",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "tables", "delete", WS_GUID, WH_GUID, "dbo.sales"]
            )
        assert result.exit_code == 0

    def test_delete_declined_aborts(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["tables", "delete", WS_GUID, WH_GUID, "dbo.sales"],
                input="n\n",
            )
        assert result.exit_code != 0

    def test_delete_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["tables", "delete", WS_GUID, WH_GUID, "nodot"])
        assert result.exit_code != 0

    def test_delete_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.delete_table",
                new=AsyncMock(side_effect=PermissionDenied("no permission")),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "tables", "delete", WS_GUID, WH_GUID, "dbo.sales"]
            )
        assert result.exit_code != 0

    def test_delete_warehouse_item_allowed(self, runner: CliRunner, cache_env: Path) -> None:
        """Warehouse items should not be blocked by the SQL Endpoint guard."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.delete_table",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "tables", "delete", WS_GUID, WH_GUID, "dbo.sales"]
            )
        assert result.exit_code == 0

    def test_delete_sql_endpoint_rejected(self, runner: CliRunner, cache_env: Path) -> None:
        """SQL Endpoint items must be rejected by the service-layer guard before issuing DDL."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_sql_endpoint_entry())),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "tables", "delete", WS_GUID, SE_GUID, "dbo.sales"]
            )
        assert result.exit_code != 0
        assert "read-only" in result.output


# ===========================================================================
# tables clear
# ===========================================================================


class TestTablesClear:
    def test_clear_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.clear_table",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "tables", "clear", WS_GUID, WH_GUID, "dbo.sales"])
        assert result.exit_code == 0

    def test_clear_declined_aborts(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["tables", "clear", WS_GUID, WH_GUID, "dbo.sales"],
                input="n\n",
            )
        assert result.exit_code != 0

    def test_clear_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["tables", "clear", WS_GUID, WH_GUID, "nodot"])
        assert result.exit_code != 0

    def test_clear_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.clear_table",
                new=AsyncMock(side_effect=PermissionDenied("no permission")),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "tables", "clear", WS_GUID, WH_GUID, "dbo.sales"])
        assert result.exit_code != 0

    def test_clear_warehouse_item_allowed(self, runner: CliRunner, cache_env: Path) -> None:
        """Warehouse items should not be blocked by the SQL Endpoint guard."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.tables.clear_table",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "tables", "clear", WS_GUID, WH_GUID, "dbo.sales"])
        assert result.exit_code == 0

    def test_clear_sql_endpoint_rejected(self, runner: CliRunner, cache_env: Path) -> None:
        """SQL Endpoint items must be rejected by the service-layer guard before issuing DDL."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_sql_endpoint_entry())),
            ),
        ):
            result = runner.invoke(cli, ["--yes", "tables", "clear", WS_GUID, SE_GUID, "dbo.sales"])
        assert result.exit_code != 0
        assert "read-only" in result.output
