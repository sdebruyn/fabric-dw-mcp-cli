"""Tests for tables CLI sub-commands."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import click
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from fabric_dw.auth import CredentialMode
from fabric_dw.cache import ItemEntry
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._main import cli
from fabric_dw.cli.commands.tables import _load_cmd_local, _parse_column_spec, _parse_schema_file
from fabric_dw.exceptions import ItemKindError, NotFoundError, PermissionDeniedError
from fabric_dw.models import CopyIntoResult, Table, WarehouseKind
from fabric_dw.sql import SqlTarget

SE_GUID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
SE_UUID = UUID(SE_GUID)

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


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
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "tables", "list", WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "sales"
        assert parsed[0]["schema_name"] == "dbo"

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
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "tables", "list", WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["qualified_name"] == "dbo.sales"

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
            result = runner.invoke(
                cli, ["-w", WS_GUID, "tables", "list", WH_GUID, "--schema", "dbo"]
            )
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
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "tables", "list", WH_GUID])
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
            result = runner.invoke(cli, ["-w", WS_GUID, "tables", "read", WH_GUID, "dbo.sales"])
        assert result.exit_code == 0
        # Default output is JSON; row data must appear
        parsed = json.loads(result.output)
        assert parsed[0]["id"] == 1
        assert parsed[0]["name"] == "Alice"

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
            result = runner.invoke(cli, ["-w", WS_GUID, "tables", "read", WH_GUID, "dbo.sales"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed[0]["id"] == 42

    def test_read_csv_requires_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli, ["-w", WS_GUID, "tables", "read", WH_GUID, "dbo.sales", "--format", "csv"]
        )
        assert result.exit_code != 0

    def test_read_parquet_requires_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli, ["-w", WS_GUID, "tables", "read", WH_GUID, "dbo.sales", "--format", "parquet"]
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
                    "-w",
                    WS_GUID,
                    "tables",
                    "read",
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
        content = out_file.read_text()
        assert "id" in content  # CSV header row present

    def test_read_explicit_format_beats_json_flag(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """--format csv must win over --json (L13 precedence regression guard)."""
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
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "read",
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
        # Verify the file is actually CSV (not JSON) — the --format flag won.
        content = out_file.read_text()
        assert "id" in content  # CSV header
        assert not content.strip().startswith("[")  # not JSON array

    def test_read_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "tables", "read", WH_GUID, "nodot"])
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
                new=AsyncMock(side_effect=NotFoundError("table not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "tables", "read", WH_GUID, "dbo.missing"])
        assert result.exit_code != 0


# ===========================================================================
# tables count
# ===========================================================================


class TestTablesCount:
    def test_count_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.tables.count_table_rows",
                new=AsyncMock(return_value=42),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "tables", "count", WH_GUID, "dbo.sales"])
        assert result.exit_code == 0

    def test_count_renders_row_count(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.tables.count_table_rows",
                new=AsyncMock(return_value=99),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--json", "tables", "count", WH_GUID, "dbo.sales"]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["schema"] == "dbo"
        assert parsed["name"] == "sales"
        assert parsed["row_count"] == 99

    def test_count_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "tables", "count", WH_GUID, "nodot"])
        assert result.exit_code != 0

    def test_count_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.tables.count_table_rows",
                new=AsyncMock(side_effect=NotFoundError("table not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "tables", "count", WH_GUID, "dbo.missing"])
        assert result.exit_code != 0


# ===========================================================================
# tables create
# ===========================================================================


class TestTablesCreate:
    def test_create_with_select_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_create = AsyncMock(return_value=_make_table())
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
                new=mock_create,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--select",
                    "SELECT id FROM src.raw",
                ],
            )
        assert result.exit_code == 0
        mock_create.assert_awaited_once()
        parsed = json.loads(result.output)
        assert parsed["name"] == "sales"

    def test_create_with_file(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
        _ = cache_env
        sql_file = tmp_path / "ctas.sql"
        sql_file.write_text("SELECT id FROM src.raw")
        mock_http = AsyncMock()
        mock_create = AsyncMock(return_value=_make_table())
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
                new=mock_create,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--from-file",
                    str(sql_file),
                ],
            )
        assert result.exit_code == 0
        # Verify SQL from file was passed to the service
        mock_create.assert_awaited_once()
        # 4th positional arg is the select_body
        assert "SELECT id FROM src.raw" in mock_create.call_args.args[3]
        parsed = json.loads(result.output)
        assert parsed["name"] == "sales"

    def test_create_no_select_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "tables", "create", WH_GUID, "--name", "dbo.sales"],
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
                "-w",
                WS_GUID,
                "tables",
                "create",
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
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "tables",
                    "create",
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
        mock_create = AsyncMock(return_value=_make_table())
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
                new=mock_create,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--select",
                    "SELECT id FROM src.raw",
                ],
            )
        assert result.exit_code == 0
        # Must have actually called create (not short-circuited by DDL guard)
        mock_create.assert_awaited_once()
        parsed = json.loads(result.output)
        assert parsed["qualified_name"] == "dbo.sales"

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
                    "-w",
                    WS_GUID,
                    "tables",
                    "create",
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
        mock_delete = AsyncMock(return_value=None)
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
                new=mock_delete,
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--yes", "tables", "delete", WH_GUID, "dbo.sales"]
            )
        assert result.exit_code == 0
        mock_delete.assert_awaited_once()

    def test_delete_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining delete is a clean no-op (exit 0, policy: decline != error)."""
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
                ["-w", WS_GUID, "tables", "delete", WH_GUID, "dbo.sales"],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_delete_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "tables", "delete", WH_GUID, "nodot"])
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
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--yes", "tables", "delete", WH_GUID, "dbo.sales"]
            )
        assert result.exit_code != 0

    def test_delete_warehouse_item_allowed(self, runner: CliRunner, cache_env: Path) -> None:
        """Warehouse items should not be blocked by the SQL Endpoint guard."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_delete = AsyncMock(return_value=None)
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
                new=mock_delete,
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--yes", "tables", "delete", WH_GUID, "dbo.sales"]
            )
        assert result.exit_code == 0
        # Must have proceeded to actual delete (DDL guard passed)
        mock_delete.assert_awaited_once()

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
                cli, ["-w", WS_GUID, "--yes", "tables", "delete", SE_GUID, "dbo.sales"]
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
        mock_clear = AsyncMock(return_value=None)
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
                new=mock_clear,
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--yes", "tables", "clear", WH_GUID, "dbo.sales"]
            )
        assert result.exit_code == 0
        mock_clear.assert_awaited_once()

    def test_clear_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining clear is a clean no-op (exit 0, policy: decline != error)."""
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
                ["-w", WS_GUID, "tables", "clear", WH_GUID, "dbo.sales"],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_clear_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "tables", "clear", WH_GUID, "nodot"])
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
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--yes", "tables", "clear", WH_GUID, "dbo.sales"]
            )
        assert result.exit_code != 0

    def test_clear_warehouse_item_allowed(self, runner: CliRunner, cache_env: Path) -> None:
        """Warehouse items should not be blocked by the SQL Endpoint guard."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_clear = AsyncMock(return_value=None)
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
                new=mock_clear,
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--yes", "tables", "clear", WH_GUID, "dbo.sales"]
            )
        assert result.exit_code == 0
        # Must have proceeded to actual clear (DDL guard passed)
        mock_clear.assert_awaited_once()

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
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--yes", "tables", "clear", SE_GUID, "dbo.sales"]
            )
        assert result.exit_code != 0
        assert "read-only" in result.output


# ===========================================================================
# tables clone
# ===========================================================================


class TestTablesClone:
    def test_clone_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        clone_result = Table(
            schema_name="dbo",
            name="sales_clone",
            qualified_name="dbo.sales_clone",
            created=_NOW,
            modified=_NOW,
        )
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
                "fabric_dw.services.tables.clone_table",
                new=AsyncMock(return_value=clone_result),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "clone",
                    WH_GUID,
                    "--source",
                    "dbo.source_tbl",
                    "--name",
                    "dbo.sales_clone",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "sales_clone"

    def test_clone_json_output(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.tables.clone_table",
                new=AsyncMock(
                    return_value=Table(
                        schema_name="dbo",
                        name="sales_clone",
                        qualified_name="dbo.sales_clone",
                        created=_NOW,
                        modified=_NOW,
                    )
                ),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "clone",
                    WH_GUID,
                    "--source",
                    "dbo.source_tbl",
                    "--name",
                    "dbo.sales_clone",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "sales_clone"

    def test_clone_with_at_timestamp(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_clone = AsyncMock(return_value=_make_table())
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.tables.clone_table", new=mock_clone),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "tables",
                    "clone",
                    WH_GUID,
                    "--source",
                    "dbo.source_tbl",
                    "--name",
                    "dbo.sales_clone",
                    "--at",
                    "2024-05-20T14:00:00",
                ],
            )
        assert result.exit_code == 0
        _, kwargs = mock_clone.call_args
        assert kwargs["at"] is not None

    def test_clone_bad_at_timestamp_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "tables",
                "clone",
                WH_GUID,
                "--source",
                "dbo.source_tbl",
                "--name",
                "dbo.sales_clone",
                "--at",
                "not-a-date",
            ],
        )
        assert result.exit_code != 0

    def test_clone_missing_source_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "tables", "clone", WH_GUID, "--name", "dbo.sales_clone"],
        )
        assert result.exit_code != 0

    def test_clone_missing_name_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "tables", "clone", WH_GUID, "--source", "dbo.source_tbl"],
        )
        assert result.exit_code != 0

    def test_clone_bad_source_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "tables",
                "clone",
                WH_GUID,
                "--source",
                "nodot",
                "--name",
                "dbo.sales_clone",
            ],
        )
        assert result.exit_code != 0

    def test_clone_bad_name_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "tables",
                "clone",
                WH_GUID,
                "--source",
                "dbo.source_tbl",
                "--name",
                "nodot",
            ],
        )
        assert result.exit_code != 0

    def test_clone_sql_endpoint_rejected(self, runner: CliRunner, cache_env: Path) -> None:
        """SQL Endpoint items must be rejected before issuing DDL."""
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
                    "-w",
                    WS_GUID,
                    "tables",
                    "clone",
                    SE_GUID,
                    "--source",
                    "dbo.source_tbl",
                    "--name",
                    "dbo.sales_clone",
                ],
            )
        assert result.exit_code != 0
        assert "read-only" in result.output

    def test_clone_warehouse_item_allowed(self, runner: CliRunner, cache_env: Path) -> None:
        """Warehouse items should not be blocked by the SQL Endpoint guard."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_clone = AsyncMock(return_value=_make_table())
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
                "fabric_dw.services.tables.clone_table",
                new=mock_clone,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "tables",
                    "clone",
                    WH_GUID,
                    "--source",
                    "dbo.source_tbl",
                    "--name",
                    "dbo.sales_clone",
                ],
            )
        assert result.exit_code == 0
        # DDL guard passed — clone must have been invoked
        mock_clone.assert_awaited_once()

    def test_clone_permission_denied_returns_nonzero(
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
                "fabric_dw.services.tables.clone_table",
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "tables",
                    "clone",
                    WH_GUID,
                    "--source",
                    "dbo.source_tbl",
                    "--name",
                    "dbo.sales_clone",
                ],
            )
        assert result.exit_code != 0


class TestTablesRename:
    def test_rename_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        renamed = Table(
            schema_name="dbo",
            name="sales_v2",
            qualified_name="dbo.sales_v2",
            created=_NOW,
            modified=_NOW,
        )
        mock_rename = AsyncMock(return_value=renamed)
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
                "fabric_dw.services.tables.rename_table",
                new=mock_rename,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "rename",
                    WH_GUID,
                    "dbo.sales",
                    "--new-name",
                    "sales_v2",
                ],
            )
        assert result.exit_code == 0
        mock_rename.assert_awaited_once()
        parsed = json.loads(result.output)
        assert parsed["name"] == "sales_v2"

    def test_rename_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        renamed = Table(
            schema_name="dbo",
            name="sales_v2",
            qualified_name="dbo.sales_v2",
            created=_NOW,
            modified=_NOW,
        )
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
                "fabric_dw.services.tables.rename_table",
                new=AsyncMock(return_value=renamed),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "rename",
                    WH_GUID,
                    "dbo.sales",
                    "--new-name",
                    "sales_v2",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "sales_v2"

    def test_rename_missing_new_name_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "tables", "rename", WH_GUID, "dbo.sales"])
        assert result.exit_code != 0

    def test_rename_undotted_qualified_name_fails_before_io(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """An undotted QUALIFIED_NAME must yield a UsageError before any I/O is performed."""
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "tables", "rename", WH_GUID, "nodot", "--new-name", "sales_v2"],
        )
        assert result.exit_code != 0
        assert "table" in result.output.lower() or "Usage" in result.output

    def test_rename_schema_qualified_new_name_fails(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Service must reject schema-qualified new names."""
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
                "fabric_dw.services.tables.rename_table",
                new=AsyncMock(side_effect=ValueError("schema-qualified")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "tables",
                    "rename",
                    WH_GUID,
                    "dbo.sales",
                    "--new-name",
                    "other.sales_v2",
                ],
            )
        assert result.exit_code != 0

    def test_rename_sql_endpoint_rejected(self, runner: CliRunner, cache_env: Path) -> None:
        """SQL Endpoint items must be rejected by the service-layer guard."""
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
            patch(
                "fabric_dw.services.tables.rename_table",
                new=AsyncMock(side_effect=ItemKindError("read-only")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "tables", "rename", SE_GUID, "dbo.sales", "--new-name", "sales_v2"],
            )
        assert result.exit_code != 0
        assert "read-only" in result.output

    def test_rename_permission_denied_returns_nonzero(
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
                "fabric_dw.services.tables.rename_table",
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "tables", "rename", WH_GUID, "dbo.sales", "--new-name", "sales_v2"],
            )
        assert result.exit_code != 0


# ===========================================================================
# tables create — empty DDL path
# ===========================================================================


class TestTablesCreateEmpty:
    """Tests for the empty-DDL source flags on tables create."""

    def test_column_flag_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_create_empty = AsyncMock(return_value=_make_table())
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
                "fabric_dw.services.tables.create_empty_table",
                new=mock_create_empty,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--column",
                    "id:INT:notnull",
                    "--column",
                    "name:VARCHAR(100)",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_create_empty.assert_awaited_once()
        parsed = json.loads(result.output)
        assert parsed["name"] == "sales"

    def test_from_schema_file_exits_zero(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        schema_file = tmp_path / "schema.json"
        schema_file.write_text(
            '[{"name": "id", "type": "INT", "nullable": false}, '
            '{"name": "label", "type": "VARCHAR(100)"}]'
        )
        mock_http = AsyncMock()
        mock_create_empty = AsyncMock(return_value=_make_table())
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
                "fabric_dw.services.tables.create_empty_table",
                new=mock_create_empty,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--from-schema",
                    str(schema_file),
                ],
            )
        assert result.exit_code == 0, result.output
        mock_create_empty.assert_awaited_once()

    def test_from_parquet_exits_zero(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        parquet_file = tmp_path / "data.parquet"
        schema = pa.schema([pa.field("id", pa.int32()), pa.field("name", pa.string())])
        pq.write_table(
            pa.table(
                {"id": pa.array([], type=pa.int32()), "name": pa.array([], type=pa.string())},
                schema=schema,
            ),
            str(parquet_file),
        )
        mock_http = AsyncMock()
        mock_create_empty = AsyncMock(return_value=_make_table())
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
                "fabric_dw.services.tables.create_table_from_parquet",
                new=mock_create_empty,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--from-parquet",
                    str(parquet_file),
                ],
            )
        assert result.exit_code == 0, result.output
        mock_create_empty.assert_awaited_once()

    def test_from_csv_exits_zero(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
        _ = cache_env
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id,name\n1,Alice\n2,Bob\n")
        mock_http = AsyncMock()
        mock_create_empty = AsyncMock(return_value=_make_table())
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
                "fabric_dw.services.tables.create_table_from_csv",
                new=mock_create_empty,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "tables",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--from-csv",
                    str(csv_file),
                ],
            )
        assert result.exit_code == 0, result.output
        mock_create_empty.assert_awaited_once()

    def test_no_source_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "tables", "create", WH_GUID, "--name", "dbo.sales"],
        )
        assert result.exit_code != 0

    def test_ctas_and_parquet_mutually_exclusive(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        parquet_file = tmp_path / "data.parquet"
        pq.write_table(pa.table({"id": pa.array([], type=pa.int32())}), str(parquet_file))
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "tables",
                "create",
                WH_GUID,
                "--name",
                "dbo.sales",
                "--select",
                "SELECT 1",
                "--from-parquet",
                str(parquet_file),
            ],
        )
        assert result.exit_code != 0

    def test_parquet_and_csv_mutually_exclusive(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        parquet_file = tmp_path / "data.parquet"
        csv_file = tmp_path / "data.csv"
        pq.write_table(pa.table({"id": pa.array([], type=pa.int32())}), str(parquet_file))
        csv_file.write_text("id\n1\n")
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "tables",
                "create",
                WH_GUID,
                "--name",
                "dbo.sales",
                "--from-parquet",
                str(parquet_file),
                "--from-csv",
                str(csv_file),
            ],
        )
        assert result.exit_code != 0

    def test_parse_column_spec_null(self) -> None:
        spec = _parse_column_spec("my_col:VARCHAR(100):null")
        assert spec.name == "my_col"
        assert spec.sql_type == "VARCHAR(100)"
        assert spec.nullable is True

    def test_parse_column_spec_notnull(self) -> None:
        spec = _parse_column_spec("id:INT:notnull")
        assert spec.name == "id"
        assert spec.sql_type == "INT"
        assert spec.nullable is False

    def test_parse_column_spec_default_nullable(self) -> None:
        spec = _parse_column_spec("label:BIGINT")
        assert spec.nullable is True

    def test_parse_column_spec_invalid_format(self) -> None:
        with pytest.raises(click.UsageError):
            _parse_column_spec("notype")

    def test_parse_schema_file_valid(self, tmp_path: Path) -> None:
        f = tmp_path / "s.json"
        f.write_text(
            '[{"name": "id", "type": "INT"},'
            ' {"name": "lbl", "type": "VARCHAR(100)", "nullable": false}]'
        )
        specs = _parse_schema_file(str(f))
        assert len(specs) == 2
        assert specs[0].name == "id"
        assert specs[1].nullable is False

    def test_parse_schema_file_missing(self) -> None:
        with pytest.raises(click.UsageError, match="not found"):
            _parse_schema_file("/nonexistent/path.json")

    def test_parse_schema_file_invalid_json(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("not json")
        with pytest.raises(click.UsageError, match="Invalid JSON"):
            _parse_schema_file(str(f))

    def test_all_varchar_requires_from_csv(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "tables",
                "create",
                WH_GUID,
                "--name",
                "dbo.sales",
                "--column",
                "id:INT",
                "--all-varchar",
            ],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# tables load --create (create-and-load)
# ---------------------------------------------------------------------------


class TestLoadCreateAndLoad:
    """Tests for 'tables load --create' (auto-create + load)."""

    def test_create_with_url_raises_usage_error(self, runner: CliRunner) -> None:
        """--create is not supported with --url."""
        result = runner.invoke(
            cli,
            [
                "-w",
                "ws",
                "tables",
                "load",
                "wh",
                "dbo.sales",
                "--url",
                "https://example.com/f.parquet",
                "--create",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "local files" in result.output.lower() or "file" in result.output.lower()

    def test_all_varchar_without_create_raises_usage_error(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--all-varchar requires --create."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        result = runner.invoke(
            cli,
            [
                "-w",
                "ws",
                "tables",
                "load",
                "wh",
                "dbo.sales",
                "--file",
                str(csv_file),
                "--all-varchar",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code != 0

    def test_create_and_load_happy_path(self, runner: CliRunner, tmp_path: Path) -> None:
        """--create with a local Parquet file invokes create_and_load."""
        from fabric_dw.http_client import FabricHttpClient  # noqa: PLC0415
        from fabric_dw.models import CopyIntoResult  # noqa: PLC0415

        parquet_file = tmp_path / "data.parquet"
        parquet_file.write_bytes(b"PAR1")

        mock_result = CopyIntoResult(rows_loaded=5, rows_rejected=0, target="dbo.sales")
        mock_http = AsyncMock(spec=FabricHttpClient)
        mock_entry = _make_item_entry()

        _http_patch = "fabric_dw.cli.commands.tables.build_http_client"
        with (
            patch(_http_patch, new=_make_http_cm(mock_http)),
            patch(
                "fabric_dw.cli.commands.tables.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, mock_entry)),
            ),
            patch(
                "fabric_dw.cli.commands.tables.create_and_load",
                new=AsyncMock(return_value=mock_result),
            ) as mock_cal,
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    "ws",
                    "tables",
                    "load",
                    "wh",
                    "dbo.sales",
                    "--file",
                    str(parquet_file),
                    "--create",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        assert "5" in result.output
        mock_cal.assert_called_once()

    def test_create_and_load_if_exists_replace_requires_confirmation(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--if-exists replace requires destructive confirmation; 'n' aborts."""
        parquet_file = tmp_path / "data.parquet"
        parquet_file.write_bytes(b"PAR1")

        from fabric_dw.http_client import FabricHttpClient  # noqa: PLC0415

        mock_http = AsyncMock(spec=FabricHttpClient)
        mock_entry = _make_item_entry()

        _http_patch = "fabric_dw.cli.commands.tables.build_http_client"
        with (
            patch(_http_patch, new=_make_http_cm(mock_http)),
            patch(
                "fabric_dw.cli.commands.tables.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, mock_entry)),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    "ws",
                    "tables",
                    "load",
                    "wh",
                    "dbo.sales",
                    "--file",
                    str(parquet_file),
                    "--create",
                    "--if-exists",
                    "replace",
                ],
                input="n\n",
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert "aborted" in result.output.lower()

    def test_create_and_load_if_exists_replace_with_yes_flag(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--if-exists replace with -y skips confirmation."""
        from fabric_dw.http_client import FabricHttpClient  # noqa: PLC0415
        from fabric_dw.models import CopyIntoResult  # noqa: PLC0415

        parquet_file = tmp_path / "data.parquet"
        parquet_file.write_bytes(b"PAR1")

        mock_result = CopyIntoResult(rows_loaded=2, rows_rejected=0, target="dbo.sales")
        mock_http = AsyncMock(spec=FabricHttpClient)
        mock_entry = _make_item_entry()

        _http_patch = "fabric_dw.cli.commands.tables.build_http_client"
        with (
            patch(_http_patch, new=_make_http_cm(mock_http)),
            patch(
                "fabric_dw.cli.commands.tables.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, mock_entry)),
            ),
            patch(
                "fabric_dw.cli.commands.tables.create_and_load",
                new=AsyncMock(return_value=mock_result),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    "ws",
                    "-y",
                    "tables",
                    "load",
                    "wh",
                    "dbo.sales",
                    "--file",
                    str(parquet_file),
                    "--create",
                    "--if-exists",
                    "replace",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        assert "2" in result.output

    def test_if_exists_truncate_without_create_raises_usage_error(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--if-exists truncate without --create raises UsageError."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        result = runner.invoke(
            cli,
            [
                "-w",
                "ws",
                "tables",
                "load",
                "wh",
                "dbo.sales",
                "--file",
                str(csv_file),
                "--if-exists",
                "truncate",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "truncate" in result.output.lower() or "create" in result.output.lower()

    def test_if_exists_replace_without_create_raises_usage_error(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--if-exists replace without --create raises UsageError."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        result = runner.invoke(
            cli,
            [
                "-w",
                "ws",
                "tables",
                "load",
                "wh",
                "dbo.sales",
                "--file",
                str(csv_file),
                "--if-exists",
                "replace",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "replace" in result.output.lower() or "create" in result.output.lower()

    def test_if_exists_truncate_with_url_raises_usage_error(self, runner: CliRunner) -> None:
        """--if-exists truncate with --url raises UsageError (no --create for URL path)."""
        result = runner.invoke(
            cli,
            [
                "-w",
                "ws",
                "tables",
                "load",
                "wh",
                "dbo.sales",
                "--url",
                "https://example.com/f.parquet",
                "--if-exists",
                "truncate",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code != 0


# ===========================================================================
# _load_cmd_local — storage credential lifecycle
# ===========================================================================


class TestLoadCmdLocalStorageCredential:
    """Verify that _load_cmd_local closes the storage-scope credential."""

    def _make_ctx(self) -> CliContext:
        return CliContext(auth=CredentialMode.DEFAULT)

    @pytest.mark.asyncio
    async def test_storage_credential_is_closed_on_success(self, tmp_path: Path) -> None:
        """The storage-scope credential returned by get_credential must be closed
        after load_local_file returns successfully.

        A second independent credential is created inside _load_cmd_local for the
        OneLake upload.  Without an explicit close() call its internal
        aiohttp.ClientSession leaks — the same ResourceWarning this PR fixes on the
        primary FabricHttpClient credential.
        """
        local = tmp_path / "data.parquet"
        local.write_bytes(b"PAR1")  # minimal non-empty file

        fake_result = CopyIntoResult(rows_loaded=1, rows_rejected=0, target="dbo.t")
        close_spy = AsyncMock()
        mock_cred = MagicMock()
        mock_cred.close = close_spy

        with (
            patch(
                "fabric_dw.auth.get_credential",
                return_value=mock_cred,
            ),
            patch(
                "fabric_dw.cli.commands.tables.load_local_file",
                new=AsyncMock(return_value=fake_result),
            ),
            patch(
                "fabric_dw.cli.commands.tables.infer_file_format",
                return_value="parquet",
            ),
        ):
            result = await _load_cmd_local(
                ctx=self._make_ctx(),
                http=MagicMock(),
                ws_id=WS_UUID,
                sql_target=_make_sql_target(),
                entry=_make_item_entry(),
                schema="dbo",
                table_name="t",
                file_path=str(local),
                fmt=None,
                csv_kw={},
                staging_lakehouse_name=None,
                keep_staging=False,
                max_errors=None,
                rejected_row_location=None,
            )

        assert result == fake_result
        close_spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_storage_credential_is_closed_even_when_load_raises(self, tmp_path: Path) -> None:
        """The storage-scope credential must be closed even when load_local_file
        raises, so the aiohttp session is never leaked on the error path.
        """
        local = tmp_path / "data.parquet"
        local.write_bytes(b"PAR1")

        close_spy = AsyncMock()
        mock_cred = MagicMock()
        mock_cred.close = close_spy

        with (
            patch(
                "fabric_dw.auth.get_credential",
                return_value=mock_cred,
            ),
            patch(
                "fabric_dw.cli.commands.tables.load_local_file",
                new=AsyncMock(side_effect=RuntimeError("upload failed")),
            ),
            patch(
                "fabric_dw.cli.commands.tables.infer_file_format",
                return_value="parquet",
            ),
            pytest.raises(RuntimeError, match="upload failed"),
        ):
            await _load_cmd_local(
                ctx=self._make_ctx(),
                http=MagicMock(),
                ws_id=WS_UUID,
                sql_target=_make_sql_target(),
                entry=_make_item_entry(),
                schema="dbo",
                table_name="t",
                file_path=str(local),
                fmt=None,
                csv_kw={},
                staging_lakehouse_name=None,
                keep_staging=False,
                max_errors=None,
                rejected_row_location=None,
            )

        close_spy.assert_awaited_once()


# ===========================================================================
# tables columns
# ===========================================================================

_COLUMNS_RESULT = [
    {
        "ordinal": 1,
        "name": "id",
        "data_type": "INT",
        "nullable": False,
        "collation_name": None,
        "is_identity": True,
        "is_computed": False,
    },
    {
        "ordinal": 2,
        "name": "amount",
        "data_type": "DECIMAL(18,2)",
        "nullable": True,
        "collation_name": None,
        "is_identity": False,
        "is_computed": False,
    },
]


class TestTablesColumns:
    def test_columns_happy_path(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.cli.commands.tables._get_columns",
                new=AsyncMock(return_value=_COLUMNS_RESULT),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "tables", "columns", WH_GUID, "dbo.sales"])
        assert result.exit_code == 0, result.output

    def test_columns_json_output(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.cli.commands.tables._get_columns",
                new=AsyncMock(return_value=_COLUMNS_RESULT),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--json", "tables", "columns", WH_GUID, "dbo.sales"]
            )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "id"
        assert parsed[0]["data_type"] == "INT"
        assert parsed[1]["data_type"] == "DECIMAL(18,2)"

    def test_columns_not_found(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.cli.commands.tables._get_columns",
                new=AsyncMock(side_effect=NotFoundError("Table [dbo].[ghost] not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "tables", "columns", WH_GUID, "dbo.ghost"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_columns_on_sql_endpoint(self, runner: CliRunner, cache_env: Path) -> None:
        """columns works on SQL Analytics Endpoints (no endpoint guard)."""
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
            patch(
                "fabric_dw.cli.commands.tables._get_columns",
                new=AsyncMock(return_value=_COLUMNS_RESULT),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--json", "tables", "columns", SE_GUID, "dbo.sales"]
            )
        assert result.exit_code == 0, result.output
