"""Tests for views CLI sub-commands."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

from click.testing import CliRunner

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.exceptions import NotFoundError, PermissionDeniedError
from fabric_dw.models import View, WarehouseKind
from fabric_dw.sql import SqlTarget

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


def _make_view(*, with_definition: bool = False) -> View:
    return View(
        schema_name="dbo",
        name="vw_sales",
        qualified_name="dbo.vw_sales",
        definition="SELECT id FROM dbo.sales" if with_definition else None,
        created=_NOW,
        modified=_NOW,
    )


# ===========================================================================
# views list
# ===========================================================================


class TestViewsList:
    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.list_views",
                new=AsyncMock(return_value=[_make_view()]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "views", "list", WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "vw_sales"
        assert parsed[0]["schema_name"] == "dbo"

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.list_views",
                new=AsyncMock(return_value=[_make_view()]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "--json", "views", "list", WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_with_schema_filter(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_list = AsyncMock(return_value=[_make_view()])
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.views.list_views", new=mock_list),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "views", "list", WH_GUID, "--schema", "dbo"]
            )
        assert result.exit_code == 0
        mock_list.assert_awaited_once()

    def test_list_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "views", "list", WH_GUID])
        assert result.exit_code != 0


# ===========================================================================
# views read
# ===========================================================================


class TestViewsRead:
    def test_read_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.read_view",
                new=AsyncMock(return_value=(["id", "name"], [(1, "Alice")])),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "views", "read", WH_GUID, "dbo.vw_sales"])
        assert result.exit_code == 0
        # Default output is JSON
        parsed = json.loads(result.output)
        assert parsed[0]["id"] == 1
        assert parsed[0]["name"] == "Alice"

    def test_read_json_output_to_stdout(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.read_view",
                new=AsyncMock(return_value=(["id"], [(42,)])),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "views", "read", WH_GUID, "dbo.vw_sales"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed[0]["id"] == 42

    def test_read_csv_requires_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli, ["-w", WS_GUID, "views", "read", WH_GUID, "dbo.vw_sales", "--format", "csv"]
        )
        assert result.exit_code != 0

    def test_read_parquet_requires_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli, ["-w", WS_GUID, "views", "read", WH_GUID, "dbo.vw_sales", "--format", "parquet"]
        )
        assert result.exit_code != 0

    def test_read_csv_with_output(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
        _ = cache_env
        out_file = tmp_path / "out.csv"
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.read_view",
                new=AsyncMock(return_value=(["id"], [(1,)])),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "views",
                    "read",
                    WH_GUID,
                    "dbo.vw_sales",
                    "--format",
                    "csv",
                    "--output",
                    str(out_file),
                ],
            )
        assert result.exit_code == 0
        assert out_file.exists()
        content = out_file.read_text()
        assert "id" in content  # CSV header present

    def test_read_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "views", "read", WH_GUID, "nodot"])
        assert result.exit_code != 0

    def test_read_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.read_view",
                new=AsyncMock(side_effect=NotFoundError("view not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "views", "read", WH_GUID, "dbo.vw_missing"])
        assert result.exit_code != 0

    # -- time-travel options --

    def test_read_with_as_of_passes_datetime_to_service(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--as-of is parsed and threaded to the service as a datetime."""
        _ = cache_env
        mock_read = AsyncMock(return_value=(["id"], [(1,)]))
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.views.read_view", new=mock_read),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "views",
                    "read",
                    WH_GUID,
                    "dbo.vw_sales",
                    "--as-of",
                    "2024-03-15T10:30:00",
                ],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_read.call_args
        assert kwargs.get("as_of") is not None
        assert isinstance(kwargs["as_of"], datetime)

    def test_read_with_ago_passes_datetime_to_service(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--ago is parsed into a datetime and threaded to the service."""
        _ = cache_env
        mock_read = AsyncMock(return_value=(["id"], [(1,)]))
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.views.read_view", new=mock_read),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "views", "read", WH_GUID, "dbo.vw_sales", "--ago", "1h"],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_read.call_args
        assert kwargs.get("as_of") is not None
        assert isinstance(kwargs["as_of"], datetime)

    def test_read_with_both_as_of_and_ago_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--as-of and --ago together exit nonzero (mutually exclusive)."""
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "views",
                "read",
                WH_GUID,
                "dbo.vw_sales",
                "--as-of",
                "2024-01-01T00:00:00",
                "--ago",
                "1h",
            ],
        )
        assert result.exit_code != 0

    def test_read_both_error_names_as_of_and_ago(self, runner: CliRunner, cache_env: Path) -> None:
        """Error for --as-of + --ago names both options correctly (not --since)."""
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "views",
                "read",
                WH_GUID,
                "dbo.vw_sales",
                "--as-of",
                "2024-01-01T00:00:00",
                "--ago",
                "1h",
            ],
        )
        assert result.exit_code != 0
        assert "--as-of" in result.output
        assert "--ago" in result.output
        assert "--since" not in result.output

    def test_read_without_time_travel_passes_none_as_of(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Without --as-of or --ago, service is called with as_of=None."""
        _ = cache_env
        mock_read = AsyncMock(return_value=(["id"], [(1,)]))
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.views.read_view", new=mock_read),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "views", "read", WH_GUID, "dbo.vw_sales"])
        assert result.exit_code == 0, result.output
        _, kwargs = mock_read.call_args
        assert kwargs.get("as_of") is None


# ===========================================================================
# views count
# ===========================================================================


class TestViewsCount:
    def test_count_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.count_view_rows",
                new=AsyncMock(return_value=42),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "views", "count", WH_GUID, "dbo.vw_sales"])
        assert result.exit_code == 0

    def test_count_renders_row_count(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.count_view_rows",
                new=AsyncMock(return_value=7),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--json", "views", "count", WH_GUID, "dbo.vw_sales"]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["schema"] == "dbo"
        assert parsed["name"] == "vw_sales"
        assert parsed["row_count"] == 7

    def test_count_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "views", "count", WH_GUID, "nodot"])
        assert result.exit_code != 0

    def test_count_fabric_error_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.count_view_rows",
                new=AsyncMock(side_effect=NotFoundError("view not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "views", "count", WH_GUID, "dbo.missing"])
        assert result.exit_code != 0

    def test_count_with_as_of_passes_datetime_to_service(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--as-of is parsed into a datetime and threaded to count_view_rows."""
        _ = cache_env
        mock_count = AsyncMock(return_value=5)
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.views.count_view_rows", new=mock_count),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "views",
                    "count",
                    WH_GUID,
                    "dbo.vw_sales",
                    "--as-of",
                    "2024-03-15T10:30:00",
                ],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_count.call_args
        assert kwargs.get("as_of") is not None
        assert isinstance(kwargs["as_of"], datetime)

    def test_count_with_ago_passes_datetime_to_service(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--ago is parsed into a datetime and threaded to count_view_rows."""
        _ = cache_env
        mock_count = AsyncMock(return_value=3)
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.views.count_view_rows", new=mock_count),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "views", "count", WH_GUID, "dbo.vw_sales", "--ago", "1h"],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_count.call_args
        assert kwargs.get("as_of") is not None
        assert isinstance(kwargs["as_of"], datetime)

    def test_count_with_both_as_of_and_ago_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--as-of and --ago together exit nonzero (mutually exclusive)."""
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "views",
                "count",
                WH_GUID,
                "dbo.vw_sales",
                "--as-of",
                "2024-01-01T00:00:00",
                "--ago",
                "1h",
            ],
        )
        assert result.exit_code != 0
        assert "--as-of" in result.output
        assert "--ago" in result.output
        assert "--since" not in result.output

    def test_count_without_time_travel_passes_none_as_of(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Without --as-of or --ago, service is called with as_of=None."""
        _ = cache_env
        mock_count = AsyncMock(return_value=0)
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.views.count_view_rows", new=mock_count),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "views", "count", WH_GUID, "dbo.vw_sales"])
        assert result.exit_code == 0, result.output
        _, kwargs = mock_count.call_args
        assert kwargs.get("as_of") is None


# ===========================================================================
# views get
# ===========================================================================


class TestViewsGet:
    def test_get_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.get_view",
                new=AsyncMock(return_value=_make_view(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "--json", "views", "get", WH_GUID, "dbo.vw_sales"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "vw_sales"
        assert "SELECT id FROM dbo.sales" in parsed.get("definition", "")

    def test_get_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.get_view",
                new=AsyncMock(return_value=_make_view(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--json", "views", "get", WH_GUID, "dbo.vw_sales"]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "vw_sales"

    def test_get_bad_qualified_name_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "views", "get", WH_GUID, "no_dot_here"])
        assert result.exit_code != 0

    def test_get_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.get_view",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "views", "get", WH_GUID, "dbo.vw_missing"])
        assert result.exit_code != 0


# ===========================================================================
# views create
# ===========================================================================


class TestViewsCreate:
    def test_create_with_select_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_create = AsyncMock(return_value=_make_view(with_definition=True))
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.create_view",
                new=mock_create,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "views",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.vw_sales",
                    "--select",
                    "SELECT id FROM dbo.sales",
                ],
            )
        assert result.exit_code == 0
        mock_create.assert_awaited_once()
        parsed = json.loads(result.output)
        assert parsed["name"] == "vw_sales"

    def test_create_with_file(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
        _ = cache_env
        sql_file = tmp_path / "view.sql"
        sql_file.write_text("SELECT id FROM dbo.sales")
        mock_http = AsyncMock()
        mock_create = AsyncMock(return_value=_make_view(with_definition=True))
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.create_view",
                new=mock_create,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "views",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.vw_sales",
                    "--from-file",
                    str(sql_file),
                ],
            )
        assert result.exit_code == 0
        mock_create.assert_awaited_once()
        parsed = json.loads(result.output)
        assert parsed["name"] == "vw_sales"

    def test_create_no_select_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "views", "create", WH_GUID, "--name", "dbo.vw_sales"],
        )
        assert result.exit_code != 0

    def test_create_both_select_and_file_fails(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        sql_file = tmp_path / "view.sql"
        sql_file.write_text("SELECT 1")
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "views",
                "create",
                WH_GUID,
                "--name",
                "dbo.vw_sales",
                "--select",
                "SELECT 1",
                "--from-file",
                str(sql_file),
            ],
        )
        assert result.exit_code != 0

    def test_create_from_file_strips_utf8_sig_bom(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """Files with UTF-8-sig BOM (\xef\xbb\xbf) must be decoded transparently."""
        _ = cache_env
        sql_file = tmp_path / "view_bom.sql"
        sql_file.write_bytes(b"\xef\xbb\xbfSELECT id FROM dbo.sales")
        mock_http = AsyncMock()
        captured_body: list[str] = []

        async def _capture(
            _target: object, _schema: object, _view_name: object, body: str, **_kw: object
        ) -> View:
            captured_body.append(body)
            return _make_view(with_definition=True)

        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.views.create_view", new=_capture),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "views",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.vw_sales",
                    "--from-file",
                    str(sql_file),
                ],
            )
        assert result.exit_code == 0
        assert captured_body == ["SELECT id FROM dbo.sales"]

    def test_create_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.create_view",
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "views",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.vw_sales",
                    "--select",
                    "SELECT 1",
                ],
            )
        assert result.exit_code != 0


# ===========================================================================
# views update
# ===========================================================================


class TestViewsUpdate:
    def test_update_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_update = AsyncMock(return_value=_make_view(with_definition=True))
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.update_view",
                new=mock_update,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--yes",
                    "--json",
                    "views",
                    "update",
                    WH_GUID,
                    "dbo.vw_sales",
                    "--select",
                    "SELECT id FROM dbo.sales",
                ],
            )
        assert result.exit_code == 0
        mock_update.assert_awaited_once()
        parsed = json.loads(result.output)
        assert parsed["name"] == "vw_sales"

    def test_update_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining update is a clean no-op (exit 0, policy: decline != error)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "views", "update", WH_GUID, "dbo.vw_sales", "--select", "SELECT 1"],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_update_from_file_strips_utf8_sig_bom(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """Files with UTF-8-sig BOM (\xef\xbb\xbf) must be decoded transparently."""
        _ = cache_env
        sql_file = tmp_path / "view_update_bom.sql"
        sql_file.write_bytes(b"\xef\xbb\xbfSELECT id FROM dbo.sales")
        captured_body: list[str] = []

        async def _capture(
            _target: object, _schema: object, _view_name: object, body: str, **_kw: object
        ) -> View:
            captured_body.append(body)
            return _make_view(with_definition=True)

        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.views.update_view", new=_capture),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--yes",
                    "views",
                    "update",
                    WH_GUID,
                    "dbo.vw_sales",
                    "--from-file",
                    str(sql_file),
                ],
            )
        assert result.exit_code == 0
        assert captured_body == ["SELECT id FROM dbo.sales"]


# ===========================================================================
# views drop
# ===========================================================================


class TestViewsDrop:
    def test_drop_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_drop = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.drop_view",
                new=mock_drop,
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--yes", "views", "drop", WH_GUID, "dbo.vw_sales"]
            )
        assert result.exit_code == 0
        mock_drop.assert_awaited_once()

    def test_drop_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining drop is a clean no-op (exit 0, policy: decline != error)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "views", "drop", WH_GUID, "dbo.vw_sales"],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_drop_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "views", "drop", WH_GUID, "no_dot"])
        assert result.exit_code != 0

    def test_drop_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.drop_view",
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--yes", "views", "drop", WH_GUID, "dbo.vw_sales"]
            )
        assert result.exit_code != 0


# ===========================================================================
# views rename
# ===========================================================================


class TestViewsRename:
    def _make_renamed_view(self) -> View:
        return View(
            schema_name="dbo",
            name="vw_revenue",
            qualified_name="dbo.vw_revenue",
            definition=None,
            created=_NOW,
            modified=_NOW,
        )

    def test_rename_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_rename = AsyncMock(return_value=self._make_renamed_view())
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.rename_view",
                new=mock_rename,
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--yes",
                    "--json",
                    "views",
                    "rename",
                    WH_GUID,
                    "dbo.vw_sales",
                    "--new-name",
                    "vw_revenue",
                ],
            )
        assert result.exit_code == 0
        mock_rename.assert_awaited_once()
        parsed = json.loads(result.output)
        assert parsed["name"] == "vw_revenue"

    def test_rename_json_output_contains_new_name(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.rename_view",
                new=AsyncMock(return_value=self._make_renamed_view()),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--yes",
                    "--json",
                    "views",
                    "rename",
                    WH_GUID,
                    "dbo.vw_sales",
                    "--new-name",
                    "vw_revenue",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "vw_revenue"

    def test_rename_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining rename is a clean no-op (exit 0)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "views",
                    "rename",
                    WH_GUID,
                    "dbo.vw_sales",
                    "--new-name",
                    "vw_revenue",
                ],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_rename_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "views", "rename", WH_GUID, "nodot", "--new-name", "vw_revenue"],
        )
        assert result.exit_code != 0

    def test_rename_missing_new_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "views", "rename", WH_GUID, "dbo.vw_sales"],
        )
        assert result.exit_code != 0

    def test_rename_service_error_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.views.rename_view",
                new=AsyncMock(side_effect=ValueError("new_name must be a bare identifier")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--yes",
                    "views",
                    "rename",
                    WH_GUID,
                    "dbo.vw_sales",
                    "--new-name",
                    "other.vw_revenue",
                ],
            )
        assert result.exit_code != 0


# ===========================================================================
# views columns
# ===========================================================================

_VIEW_COLUMNS_RESULT = [
    {
        "ordinal": 1,
        "name": "id",
        "data_type": "INT",
        "nullable": False,
        "collation_name": None,
        "is_identity": False,
        "is_computed": False,
    },
    {
        "ordinal": 2,
        "name": "label",
        "data_type": "NVARCHAR(200)",
        "nullable": True,
        "collation_name": "Latin1_General_CI_AS",
        "is_identity": False,
        "is_computed": False,
    },
]


class TestViewsColumns:
    def test_columns_happy_path(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.cli.commands.views._get_columns",
                new=AsyncMock(return_value=_VIEW_COLUMNS_RESULT),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "views", "columns", WH_GUID, "dbo.vw_sales"]
            )
        assert result.exit_code == 0, result.output

    def test_columns_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.cli.commands.views._get_columns",
                new=AsyncMock(return_value=_VIEW_COLUMNS_RESULT),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "--json", "views", "columns", WH_GUID, "dbo.vw_sales"]
            )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "id"
        assert parsed[1]["data_type"] == "NVARCHAR(200)"

    def test_columns_not_found(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.views.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.views.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.cli.commands.views._get_columns",
                new=AsyncMock(side_effect=NotFoundError("View [dbo].[ghost] not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "views", "columns", WH_GUID, "dbo.ghost"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()
