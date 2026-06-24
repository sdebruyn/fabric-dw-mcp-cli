"""Tests for functions CLI sub-commands."""

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
from fabric_dw.models import FunctionDetails, FunctionKind, WarehouseKind
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


def _make_item_entry(*, kind: WarehouseKind = WarehouseKind.WAREHOUSE) -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=kind,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


def _make_fn(*, with_definition: bool = False) -> FunctionDetails:
    return FunctionDetails(
        schema_name="dbo",
        name="fn_clean",
        qualified_name="dbo.fn_clean",
        kind=FunctionKind.SCALAR,
        is_inlineable=True,
        definition="(@x NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN @x END"
        if with_definition
        else None,
        parameters=[],
        created=_NOW,
        modified=_NOW,
    )


# ===========================================================================
# functions list
# ===========================================================================


class TestFunctionsList:
    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.functions.list_functions",
                new=AsyncMock(return_value=[_make_fn()]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "functions", "list", WH_GUID])
        assert result.exit_code == 0

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.functions.list_functions",
                new=AsyncMock(return_value=[_make_fn()]),
            ),
        ):
            result = runner.invoke(cli, ["--json", "-w", WS_GUID, "functions", "list", WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_with_schema_filter(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_list = AsyncMock(return_value=[_make_fn()])
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.list_functions", new=mock_list),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "functions", "list", WH_GUID, "--schema", "dbo"]
            )
        assert result.exit_code == 0
        mock_list.assert_awaited_once()

    def test_list_with_kind_scalar(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_list = AsyncMock(return_value=[_make_fn()])
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.list_functions", new=mock_list),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "functions", "list", WH_GUID, "--kind", "scalar"]
            )
        assert result.exit_code == 0
        _, kwargs = mock_list.call_args
        assert kwargs.get("kind") == "scalar"

    def test_list_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "functions", "list", WH_GUID])
        assert result.exit_code != 0

    def test_list_works_on_sql_endpoint(self, runner: CliRunner, cache_env: Path) -> None:
        """list on a SQL Analytics Endpoint must succeed — no DW-only guard."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(
                    return_value=(
                        _make_sql_target(),
                        _make_item_entry(kind=WarehouseKind.SQL_ENDPOINT),
                    )
                ),
            ),
            patch(
                "fabric_dw.services.functions.list_functions",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "functions", "list", WH_GUID])
        assert result.exit_code == 0


# ===========================================================================
# functions get
# ===========================================================================


class TestFunctionsGet:
    def test_get_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.functions.get_function",
                new=AsyncMock(return_value=_make_fn(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "functions", "get", WH_GUID, "dbo.fn_clean"]
            )
        assert result.exit_code == 0

    def test_get_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.functions.get_function",
                new=AsyncMock(return_value=_make_fn(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "-w", WS_GUID, "functions", "get", WH_GUID, "dbo.fn_clean"]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "fn_clean"
        assert parsed["kind"] == "scalar"

    def test_get_missing_dot_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "functions", "get", WH_GUID, "nodot"])
        assert result.exit_code != 0


# ===========================================================================
# functions create
# ===========================================================================


class TestFunctionsCreate:
    def test_create_with_inline_body(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_create = AsyncMock(return_value=_make_fn(with_definition=True))
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.create_function", new=mock_create),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "functions",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.fn_clean",
                    "--body",
                    "(@x NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN @x END",
                ],
            )
        assert result.exit_code == 0
        mock_create.assert_awaited_once()

    def test_create_with_from_file(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        sql_file = tmp_path / "fn.sql"
        sql_file.write_text("(@x INT) RETURNS INT AS BEGIN RETURN @x * 2 END")
        mock_http = AsyncMock()
        mock_create = AsyncMock(return_value=_make_fn(with_definition=True))
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.create_function", new=mock_create),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "functions",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.fn_clean",
                    "--from-file",
                    str(sql_file),
                ],
            )
        assert result.exit_code == 0
        mock_create.assert_awaited_once()

    def test_create_without_body_or_file_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "functions", "create", WH_GUID, "--name", "dbo.fn_clean"],
        )
        assert result.exit_code != 0

    def test_create_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.functions.create_function",
                new=AsyncMock(return_value=_make_fn(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "-w",
                    WS_GUID,
                    "functions",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.fn_clean",
                    "--body",
                    "(@x INT) RETURNS INT AS BEGIN RETURN @x END",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "fn_clean"

    def test_create_works_on_sql_endpoint(self, runner: CliRunner, cache_env: Path) -> None:
        """create on a SQL Analytics Endpoint must succeed — no endpoint guard."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(
                    return_value=(
                        _make_sql_target(),
                        _make_item_entry(kind=WarehouseKind.SQL_ENDPOINT),
                    )
                ),
            ),
            patch(
                "fabric_dw.services.functions.create_function",
                new=AsyncMock(return_value=_make_fn(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "functions",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.fn_clean",
                    "--body",
                    "...",
                ],
            )
        assert result.exit_code == 0


# ===========================================================================
# functions update
# ===========================================================================


class TestFunctionsUpdate:
    def test_update_with_yes_flag(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_update = AsyncMock(return_value=_make_fn(with_definition=True))
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.update_function", new=mock_update),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "functions",
                    "update",
                    WH_GUID,
                    "dbo.fn_clean",
                    "--body",
                    "(@x INT) RETURNS INT AS BEGIN RETURN @x * 2 END",
                ],
            )
        assert result.exit_code == 0
        mock_update.assert_awaited_once()

    def test_update_aborted_without_yes(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.update_function", new=AsyncMock()),
        ):
            # Decline the prompt
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "functions",
                    "update",
                    WH_GUID,
                    "dbo.fn_clean",
                    "--body",
                    "...",
                ],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted" in result.output

    def test_update_permission_denied(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.functions.update_function",
                new=AsyncMock(side_effect=PermissionDeniedError("permission denied")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "functions",
                    "update",
                    WH_GUID,
                    "dbo.fn_clean",
                    "--body",
                    "...",
                ],
            )
        assert result.exit_code != 0


# ===========================================================================
# functions drop
# ===========================================================================


class TestFunctionsDrop:
    def test_drop_with_yes_flag(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_drop = AsyncMock(return_value=True)
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.drop_function", new=mock_drop),
        ):
            result = runner.invoke(
                cli, ["--yes", "-w", WS_GUID, "functions", "drop", WH_GUID, "dbo.fn_clean"]
            )
        assert result.exit_code == 0
        assert "dropped" in result.output
        mock_drop.assert_awaited_once()

    def test_drop_json_output_dropped(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.functions.drop_function",
                new=AsyncMock(return_value=True),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--json", "--yes", "-w", WS_GUID, "functions", "drop", WH_GUID, "dbo.fn_clean"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "dropped"

    def test_drop_aborted_without_yes(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.drop_function", new=AsyncMock()),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "functions", "drop", WH_GUID, "dbo.fn_clean"],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted" in result.output

    def test_drop_if_exists_existing_function(self, runner: CliRunner, cache_env: Path) -> None:
        """--if-exists on an existing function reports 'dropped'."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_drop = AsyncMock(return_value=True)
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.drop_function", new=mock_drop),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "functions",
                    "drop",
                    WH_GUID,
                    "dbo.fn_clean",
                    "--if-exists",
                ],
            )
        assert result.exit_code == 0
        assert "dropped" in result.output
        _, kwargs = mock_drop.call_args
        assert kwargs.get("if_exists") is True

    def test_drop_if_exists_missing_function_prints_no_op(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--if-exists on a non-existent function prints a no-op message, not 'dropped'."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_drop = AsyncMock(return_value=False)
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.drop_function", new=mock_drop),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "functions",
                    "drop",
                    WH_GUID,
                    "dbo.fn_clean",
                    "--if-exists",
                ],
            )
        assert result.exit_code == 0
        assert "dropped" not in result.output
        assert "does not exist" in result.output

    def test_drop_if_exists_missing_function_json_not_found(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--if-exists on a non-existent function with --json reports status 'not_found'."""
        _ = cache_env
        mock_http = AsyncMock()
        mock_drop = AsyncMock(return_value=False)
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.drop_function", new=mock_drop),
        ):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "--yes",
                    "-w",
                    WS_GUID,
                    "functions",
                    "drop",
                    WH_GUID,
                    "dbo.fn_clean",
                    "--if-exists",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["status"] == "not_found"


# ===========================================================================
# functions rename
# ===========================================================================


class TestFunctionsRename:
    def test_rename_with_yes_flag(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_rename = AsyncMock(return_value=_make_fn())
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.rename_function", new=mock_rename),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "functions",
                    "rename",
                    WH_GUID,
                    "dbo.fn_clean",
                    "--new-name",
                    "fn_sanitize",
                ],
            )
        assert result.exit_code == 0
        mock_rename.assert_awaited_once()

    def test_rename_passes_qualified_and_new_name(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_rename = AsyncMock(return_value=_make_fn())
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.rename_function", new=mock_rename),
        ):
            runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "functions",
                    "rename",
                    WH_GUID,
                    "dbo.fn_clean",
                    "--new-name",
                    "fn_sanitize",
                ],
            )
        args, _kwargs = mock_rename.call_args
        # qualified_name is positional arg 1, new_name is positional arg 2
        assert args[1] == "dbo.fn_clean"
        assert args[2] == "fn_sanitize"

    def test_rename_aborted_without_yes(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.functions.rename_function", new=AsyncMock()),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "functions",
                    "rename",
                    WH_GUID,
                    "dbo.fn_clean",
                    "--new-name",
                    "fn_sanitize",
                ],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted" in result.output

    def test_rename_missing_new_name_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["--yes", "-w", WS_GUID, "functions", "rename", WH_GUID, "dbo.fn_clean"],
        )
        assert result.exit_code != 0

    def test_rename_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.functions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.functions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.functions.rename_function",
                new=AsyncMock(return_value=_make_fn()),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "--yes",
                    "-w",
                    WS_GUID,
                    "functions",
                    "rename",
                    WH_GUID,
                    "dbo.fn_clean",
                    "--new-name",
                    "fn_sanitize",
                ],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "fn_clean"
