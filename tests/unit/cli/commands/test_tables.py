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
from fabric_dw.cli.commands.tables import _load_cmd_local, _parse_column_spec
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

    def test_from_json_jsonl_exits_zero(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        json_file = tmp_path / "data.jsonl"
        json_file.write_text('{"id": 1, "name": "Alice"}\n{"id": 2, "name": "Bob"}\n')
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
                "fabric_dw.services.tables.create_table_from_json",
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
                    "--from-json",
                    str(json_file),
                ],
            )
        assert result.exit_code == 0, result.output
        mock_create.assert_awaited_once()

    def test_from_json_array_exits_zero(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        json_file = tmp_path / "data.json"
        json_file.write_text('[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]')
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
                "fabric_dw.services.tables.create_table_from_json",
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
                    "--from-json",
                    str(json_file),
                ],
            )
        assert result.exit_code == 0, result.output
        mock_create.assert_awaited_once()

    def test_from_json_bad_file_fails(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        json_file = tmp_path / "bad.jsonl"
        json_file.write_text("{not valid json")
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
                [
                    "-w",
                    WS_GUID,
                    "tables",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.sales",
                    "--from-json",
                    str(json_file),
                ],
            )
        assert result.exit_code != 0

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

    def test_csv_and_json_mutually_exclusive(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        csv_file = tmp_path / "data.csv"
        json_file = tmp_path / "data.jsonl"
        csv_file.write_text("id\n1\n")
        json_file.write_text('{"id": 1}\n')
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
                "--from-csv",
                str(csv_file),
                "--from-json",
                str(json_file),
            ],
        )
        assert result.exit_code != 0

    def test_parquet_and_json_mutually_exclusive(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        parquet_file = tmp_path / "data.parquet"
        json_file = tmp_path / "data.jsonl"
        pq.write_table(pa.table({"id": pa.array([], type=pa.int32())}), str(parquet_file))
        json_file.write_text('{"id": 1}\n')
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
                "--from-json",
                str(json_file),
            ],
        )
        assert result.exit_code != 0

    def test_json_and_column_mutually_exclusive(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        json_file = tmp_path / "data.jsonl"
        json_file.write_text('{"id": 1}\n')
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
                "--from-json",
                str(json_file),
                "--column",
                "extra:INT",
            ],
        )
        assert result.exit_code != 0

    def test_ctas_and_json_mutually_exclusive(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        json_file = tmp_path / "data.jsonl"
        json_file.write_text('{"id": 1}\n')
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
                "--from-json",
                str(json_file),
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

    def test_all_varchar_requires_csv_or_json(self, runner: CliRunner, cache_env: Path) -> None:
        """--all-varchar without --from-csv or --from-json is a usage error."""
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

    def test_all_varchar_satisfied_by_from_json(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """--all-varchar is accepted together with --from-json."""
        _ = cache_env
        json_file = tmp_path / "data.jsonl"
        json_file.write_text('{"id": 1, "name": "Alice"}\n')
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
                "fabric_dw.services.tables.create_table_from_json",
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
                    "--from-json",
                    str(json_file),
                    "--all-varchar",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_create.assert_awaited_once()
        assert mock_create.call_args.kwargs["all_varchar"] is True


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

    def test_if_exists_truncate_without_create_invokes_truncate(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--if-exists truncate without --create truncates the table then loads (fix #711).

        truncate/replace operate on an existing table and do not require --create.
        """
        from fabric_dw.http_client import FabricHttpClient  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_result = CopyIntoResult(rows_loaded=1, rows_rejected=0, target="dbo.sales")
        mock_http = AsyncMock(spec=FabricHttpClient)
        mock_entry = _make_item_entry()

        with (
            patch("fabric_dw.cli.commands.tables.build_http_client", new=_make_http_cm(mock_http)),
            patch(
                "fabric_dw.cli.commands.tables.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, mock_entry)),
            ),
            patch(
                "fabric_dw.cli.commands.tables.load_local_file",
                new=AsyncMock(return_value=mock_result),
            ),
            patch(
                "fabric_dw.auth.get_credential",
                return_value=MagicMock(close=AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.tables.infer_file_format",
                return_value="csv",
            ),
            patch(
                "fabric_dw.services.load._truncate_table_sql",
                new=AsyncMock(),
            ) as mock_trunc,
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
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

        assert result.exit_code == 0, result.output
        mock_trunc.assert_awaited_once()

    def test_if_exists_replace_without_create_invokes_drop(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--if-exists replace without --create drops the table then loads (fix #711).

        truncate/replace operate on an existing table and do not require --create.
        """
        from fabric_dw.http_client import FabricHttpClient  # noqa: PLC0415

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id\n1\n", encoding="utf-8")

        mock_result = CopyIntoResult(rows_loaded=1, rows_rejected=0, target="dbo.sales")
        mock_http = AsyncMock(spec=FabricHttpClient)
        mock_entry = _make_item_entry()

        with (
            patch("fabric_dw.cli.commands.tables.build_http_client", new=_make_http_cm(mock_http)),
            patch(
                "fabric_dw.cli.commands.tables.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, mock_entry)),
            ),
            patch(
                "fabric_dw.cli.commands.tables.load_local_file",
                new=AsyncMock(return_value=mock_result),
            ),
            patch(
                "fabric_dw.auth.get_credential",
                return_value=MagicMock(close=AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.tables.infer_file_format",
                return_value="csv",
            ),
            patch(
                "fabric_dw.services.load._drop_table_sql",
                new=AsyncMock(),
            ) as mock_drop,
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
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

        assert result.exit_code == 0, result.output
        mock_drop.assert_awaited_once()

    def test_if_exists_truncate_with_url_raises_usage_error(self, runner: CliRunner) -> None:
        """--if-exists truncate with --url raises UsageError (not supported for URL path)."""
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
# _load_cmd_create_and_load — storage credential lifecycle (#714)
# ===========================================================================


class TestLoadCmdCreateAndLoadStorageCredential:
    """Verify that _load_cmd_create_and_load closes the storage-scope credential.

    Every load path must close the Azure Identity credential after use so that
    the internal aiohttp.ClientSession is released and no 'Unclosed client
    session' ResourceWarning is emitted.  The create-and-load path (--create)
    previously omitted this close() call.
    """

    def _make_ctx(self) -> CliContext:
        return CliContext(auth=CredentialMode.DEFAULT)

    @pytest.mark.asyncio
    async def test_storage_credential_is_closed_on_success(self, tmp_path: Path) -> None:
        """The storage-scope credential must be closed after create_and_load returns."""
        from fabric_dw.cli.commands.tables import _load_cmd_create_and_load  # noqa: PLC0415

        local = tmp_path / "data.parquet"
        local.write_bytes(b"PAR1")

        fake_result = CopyIntoResult(rows_loaded=2, rows_rejected=0, target="dbo.t")
        close_spy = AsyncMock()
        mock_cred = MagicMock()
        mock_cred.close = close_spy

        with (
            patch("fabric_dw.auth.get_credential", return_value=mock_cred),
            patch(
                "fabric_dw.cli.commands.tables.create_and_load",
                new=AsyncMock(return_value=fake_result),
            ),
            patch(
                "fabric_dw.cli.commands.tables.infer_file_format",
                return_value="parquet",
            ),
        ):
            result = await _load_cmd_create_and_load(
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
                if_exists="fail",
                all_varchar=False,
                varchar_length=8000,
                sample_rows=1000,
                cleanup_on_failure=False,
            )

        assert result == fake_result
        close_spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_storage_credential_is_closed_even_when_load_raises(self, tmp_path: Path) -> None:
        """The storage-scope credential must be closed even when create_and_load raises."""
        from fabric_dw.cli.commands.tables import _load_cmd_create_and_load  # noqa: PLC0415

        local = tmp_path / "data.parquet"
        local.write_bytes(b"PAR1")

        close_spy = AsyncMock()
        mock_cred = MagicMock()
        mock_cred.close = close_spy

        with (
            patch("fabric_dw.auth.get_credential", return_value=mock_cred),
            patch(
                "fabric_dw.cli.commands.tables.create_and_load",
                new=AsyncMock(side_effect=RuntimeError("load failed")),
            ),
            patch(
                "fabric_dw.cli.commands.tables.infer_file_format",
                return_value="parquet",
            ),
            pytest.raises(RuntimeError, match="load failed"),
        ):
            await _load_cmd_create_and_load(
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
                if_exists="fail",
                all_varchar=False,
                varchar_length=8000,
                sample_rows=1000,
                cleanup_on_failure=False,
            )

        close_spy.assert_awaited_once()


# ===========================================================================
# copy_into_from_url — error propagation (#713)
# ===========================================================================


class TestCopyIntoFromUrlErrorPropagation:
    """Verify that COPY INTO errors surface actionable detail to the user (#713).

    Previously, unmapped driver errors were wrapped in a FabricError with a
    generic 'details suppressed' message.  Now:
    - Mapped errors (PermissionDenied, Auth, NotFound) re-raise the mapped type.
    - Unmapped errors with a ddbc_error attribute include the server-side detail.
    - Unmapped errors without ddbc_error keep the safe fallback message.
    """

    @pytest.mark.asyncio
    async def test_mapped_error_is_reraised_as_mapped_type(self) -> None:
        """A driver error that map_driver_error recognises must raise the mapped type."""
        from fabric_dw.exceptions import PermissionDeniedError  # noqa: PLC0415
        from fabric_dw.services.load import copy_into_from_url  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws",
            database="db",
            connection_string="server.fabric.microsoft.com",
        )

        perm_error = PermissionDeniedError("SELECT permission denied")

        with (
            patch("fabric_dw.services.load.run_query", side_effect=perm_error),
            pytest.raises(PermissionDeniedError),
        ):
            await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
                file_type="PARQUET",
            )

    @pytest.mark.asyncio
    async def test_unmapped_error_with_ddbc_error_includes_server_detail(self) -> None:
        """An unmapped driver error with ddbc_error must include the server-side message."""
        from fabric_dw.exceptions import FabricError  # noqa: PLC0415
        from fabric_dw.services.load import copy_into_from_url  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws",
            database="db",
            connection_string="server.fabric.microsoft.com",
        )

        raw_exc = RuntimeError("raw driver error with embedded SQL")
        raw_exc.ddbc_error = "Column 'missing_col' does not exist in table 'dbo.t'"  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

        with (
            patch("fabric_dw.services.load.run_query", side_effect=raw_exc),
            patch("fabric_dw.services.load.map_driver_error", return_value=None),
            pytest.raises(FabricError) as exc_info,
        ):
            await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
                file_type="PARQUET",
            )

        assert "missing_col" in str(exc_info.value), "ddbc_error detail must appear in message"
        assert "suppressed" not in str(exc_info.value), "message must not say 'suppressed'"

    @pytest.mark.asyncio
    async def test_unmapped_error_without_ddbc_error_uses_safe_fallback(self) -> None:
        """An unmapped driver error without ddbc_error keeps the safe fallback message."""
        from fabric_dw.exceptions import FabricError  # noqa: PLC0415
        from fabric_dw.services.load import copy_into_from_url  # noqa: PLC0415
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        target = SqlTarget(
            workspace_id="ws",
            database="db",
            connection_string="server.fabric.microsoft.com",
        )

        secret = "super-secret-token"  # noqa: S105
        raw_exc = RuntimeError(f"COPY INTO ... CREDENTIAL=(SECRET='{secret}')")

        with (
            patch("fabric_dw.services.load.run_query", side_effect=raw_exc),
            patch("fabric_dw.services.load.map_driver_error", return_value=None),
            pytest.raises(FabricError) as exc_info,
        ):
            await copy_into_from_url(
                target,
                "dbo",
                "t",
                "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
                file_type="PARQUET",
            )

        error_text = str(exc_info.value)
        assert secret not in error_text, "raw exception text (with secret) must not leak"
        assert "suppressed" in error_text, "safe fallback message must be used"


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


# ===========================================================================
# tables cluster-by
# ===========================================================================


class TestTablesClusterBy:
    def test_requires_confirmation_without_yes(self, runner: CliRunner, cache_env: Path) -> None:
        """Without --yes, the user is prompted; declining aborts cleanly (exit 0)."""
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
                ["-w", WS_GUID, "tables", "cluster-by", WH_GUID, "dbo.sales"],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output
        # The dependent-views warning (sp_rename caveat) must NOT appear on abort —
        # it is only emitted when the swap actually proceeds.
        # Note: confirm_destructive itself prints "WARNING: <prompt>" — that is distinct
        # from the sp_rename caveat and is expected to appear on every invocation.
        assert "sp_rename" not in result.output

    def test_yes_flag_skips_confirmation(self, runner: CliRunner, cache_env: Path) -> None:
        """--yes bypasses the prompt and calls recluster_table."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_recluster = AsyncMock(return_value=_make_table())
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.tables.recluster_table", new=mock_recluster),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--yes",
                    "tables",
                    "cluster-by",
                    WH_GUID,
                    "dbo.sales",
                    "--cluster-by",
                    "CustomerID",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_recluster.assert_awaited_once()

    def test_happy_path_passes_cluster_by_cols(self, runner: CliRunner, cache_env: Path) -> None:
        """cluster_by columns are passed correctly to the service."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_recluster = AsyncMock(return_value=_make_table())
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.tables.recluster_table", new=mock_recluster),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--yes",
                    "tables",
                    "cluster-by",
                    WH_GUID,
                    "dbo.sales",
                    "--cluster-by",
                    "CustomerID",
                    "--cluster-by",
                    "SaleDate",
                ],
            )
        assert result.exit_code == 0, result.output
        _tgt, schema, table_name = mock_recluster.call_args.args
        assert schema == "dbo"
        assert table_name == "sales"
        assert mock_recluster.call_args.kwargs["cluster_by"] == ["CustomerID", "SaleDate"]

    def test_no_cluster_by_passes_none(self, runner: CliRunner, cache_env: Path) -> None:
        """Omitting --cluster-by passes None (remove clustering)."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_recluster = AsyncMock(return_value=_make_table())
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.tables.recluster_table", new=mock_recluster),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "--yes", "tables", "cluster-by", WH_GUID, "dbo.sales"],
            )
        assert result.exit_code == 0, result.output
        assert mock_recluster.call_args.kwargs.get("cluster_by") is None

    def test_sql_endpoint_rejected(self, runner: CliRunner, cache_env: Path) -> None:
        """SQL Analytics Endpoints are rejected (ItemKindError from service)."""
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
                "fabric_dw.services.tables.recluster_table",
                new=AsyncMock(side_effect=ItemKindError("clustering")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "--yes", "tables", "cluster-by", SE_GUID, "dbo.sales"],
            )
        assert result.exit_code != 0

    def test_always_prints_warning(self, runner: CliRunner, cache_env: Path) -> None:
        """The dependent-views warning is emitted when the swap proceeds; not --verbose gated."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_recluster = AsyncMock(return_value=_make_table())
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.tables.recluster_table", new=mock_recluster),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "--yes", "tables", "cluster-by", WH_GUID, "dbo.sales"],
            )
        assert result.exit_code == 0, result.output
        # The warning is emitted via click.echo(..., err=True); CliRunner captures
        # both stdout and stderr in result.output by default (mix_stderr=True is the
        # default on the runner fixture).
        assert "WARNING" in result.output
        assert "sp_rename" in result.output


# ===========================================================================
# tables health-check
# ===========================================================================


class TestTablesHealthCheck:
    def test_health_check_exits_zero_on_endpoint(self, runner: CliRunner, cache_env: Path) -> None:
        """health-check succeeds on a SQL Analytics Endpoint and renders table output."""
        _ = cache_env
        mock_http = AsyncMock()
        fake_cols = ["issue", "severity"]
        fake_rows: list[tuple[object, ...]] = [("small files", "medium")]
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
                "fabric_dw.services.tables.get_table_health_metrics",
                new=AsyncMock(return_value=(fake_cols, fake_rows)),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "tables", "health-check", SE_GUID, "dbo.FactSales"]
            )
        assert result.exit_code == 0, result.output

    def test_health_check_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        """health-check with --json returns a list of dicts."""
        _ = cache_env
        mock_http = AsyncMock()
        fake_cols = ["issue", "severity"]
        fake_rows: list[tuple[object, ...]] = [("fragmentation", "high")]
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
                "fabric_dw.services.tables.get_table_health_metrics",
                new=AsyncMock(return_value=(fake_cols, fake_rows)),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "--json", "tables", "health-check", SE_GUID, "dbo.FactSales"],
            )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["issue"] == "fragmentation"
        assert parsed[0]["severity"] == "high"

    def test_health_check_warehouse_raises_click_exception(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """health-check on a Warehouse raises via the real _assert_sql_endpoint guard.

        The test passes a Warehouse-kind entry so that health_check_cmd forwards
        kind=WarehouseKind.WAREHOUSE to the real service function, which calls
        _assert_sql_endpoint and raises ItemKindError.  This verifies the CLI
        wiring (kind=entry.kind) rather than just patching the service.
        """
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.tables.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.tables.build_sql_target",
                # Warehouse entry — kind=WarehouseKind.WAREHOUSE
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            # Do NOT mock the service — let _assert_sql_endpoint fire for real.
            # Patch open_connection so no real DB call is attempted.
            patch("fabric_dw.sql.open_connection", return_value=MagicMock()),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "tables", "health-check", WH_GUID, "dbo.FactSales"]
            )
        assert result.exit_code != 0
        assert "SQL Analytics Endpoints" in result.output

    def test_health_check_empty_result(self, runner: CliRunner, cache_env: Path) -> None:
        """health-check with an empty result set exits zero."""
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
                "fabric_dw.services.tables.get_table_health_metrics",
                new=AsyncMock(return_value=([], [])),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "--json", "tables", "health-check", SE_GUID, "dbo.FactSales"],
            )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed == []
