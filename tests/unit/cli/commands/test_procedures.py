"""Tests for procedures CLI sub-commands."""

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
from fabric_dw.models import StoredProcedure, WarehouseKind
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


def _make_proc(*, with_definition: bool = False) -> StoredProcedure:
    return StoredProcedure(
        schema_name="dbo",
        name="usp_load",
        qualified_name="dbo.usp_load",
        definition="BEGIN SELECT 1 END" if with_definition else None,
        created=_NOW,
        modified=_NOW,
    )


# ===========================================================================
# procedures list
# ===========================================================================


class TestProceduresList:
    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.list_procedures",
                new=AsyncMock(return_value=[_make_proc()]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "procedures", "list", WH_GUID])
        assert result.exit_code == 0

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.list_procedures",
                new=AsyncMock(return_value=[_make_proc()]),
            ),
        ):
            result = runner.invoke(cli, ["--json", "-w", WS_GUID, "procedures", "list", WH_GUID])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_with_schema_filter(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_list = AsyncMock(return_value=[_make_proc()])
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.procedures.list_procedures", new=mock_list),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "procedures", "list", WH_GUID, "--schema", "dbo"]
            )
        assert result.exit_code == 0
        mock_list.assert_awaited_once()

    def test_list_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "procedures", "list", WH_GUID])
        assert result.exit_code != 0

    def test_list_works_on_sql_endpoint(self, runner: CliRunner, cache_env: Path) -> None:
        """list on a SQL Analytics Endpoint must succeed — no DW-only guard."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(
                    return_value=(
                        _make_sql_target(),
                        _make_item_entry(kind=WarehouseKind.SQL_ENDPOINT),
                    )
                ),
            ),
            patch(
                "fabric_dw.services.procedures.list_procedures",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "procedures", "list", WH_GUID])
        assert result.exit_code == 0


# ===========================================================================
# procedures get
# ===========================================================================


class TestProceduresGet:
    def test_get_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.get_procedure",
                new=AsyncMock(return_value=_make_proc(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "procedures", "get", WH_GUID, "dbo.usp_load"]
            )
        assert result.exit_code == 0

    def test_get_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.get_procedure",
                new=AsyncMock(return_value=_make_proc(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "-w", WS_GUID, "procedures", "get", WH_GUID, "dbo.usp_load"]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "usp_load"

    def test_get_bad_qualified_name_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "procedures", "get", WH_GUID, "no_dot_here"])
        assert result.exit_code != 0

    def test_get_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.get_procedure",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "procedures", "get", WH_GUID, "dbo.usp_missing"]
            )
        assert result.exit_code != 0


# ===========================================================================
# procedures create
# ===========================================================================


class TestProceduresCreate:
    def test_create_with_body_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.create_procedure",
                new=AsyncMock(return_value=_make_proc(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "procedures",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.usp_load",
                    "--body",
                    "BEGIN SELECT 1 END",
                ],
            )
        assert result.exit_code == 0

    def test_create_with_file(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
        _ = cache_env
        sql_file = tmp_path / "proc.sql"
        sql_file.write_text("BEGIN SELECT 1 END")
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.create_procedure",
                new=AsyncMock(return_value=_make_proc(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "procedures",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.usp_load",
                    "--from-file",
                    str(sql_file),
                ],
            )
        assert result.exit_code == 0

    def test_create_no_body_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "procedures", "create", WH_GUID, "--name", "dbo.usp_load"],
        )
        assert result.exit_code != 0

    def test_create_both_body_and_file_fails(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        sql_file = tmp_path / "proc.sql"
        sql_file.write_text("BEGIN END")
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "procedures",
                "create",
                WH_GUID,
                "--name",
                "dbo.usp_load",
                "--body",
                "BEGIN END",
                "--from-file",
                str(sql_file),
            ],
        )
        assert result.exit_code != 0

    def test_create_from_file_strips_utf8_sig_bom(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """Files with UTF-8-sig BOM must be decoded transparently."""
        _ = cache_env
        sql_file = tmp_path / "proc_bom.sql"
        sql_file.write_bytes(b"\xef\xbb\xbfBEGIN SELECT 1 END")
        mock_http = AsyncMock()
        captured_body: list[str] = []

        async def _capture(
            _target: object,
            _schema: object,
            _proc_name: object,
            body: str,
            **_kw: object,
        ) -> StoredProcedure:
            captured_body.append(body)
            return _make_proc(with_definition=True)

        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.procedures.create_procedure", new=_capture),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "procedures",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.usp_load",
                    "--from-file",
                    str(sql_file),
                ],
            )
        assert result.exit_code == 0
        assert captured_body == ["BEGIN SELECT 1 END"]

    def test_create_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.create_procedure",
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "procedures",
                    "create",
                    WH_GUID,
                    "--name",
                    "dbo.usp_load",
                    "--body",
                    "BEGIN END",
                ],
            )
        assert result.exit_code != 0


# ===========================================================================
# procedures update
# ===========================================================================


class TestProceduresUpdate:
    def test_update_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.update_procedure",
                new=AsyncMock(return_value=_make_proc(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "procedures",
                    "update",
                    WH_GUID,
                    "dbo.usp_load",
                    "--body",
                    "BEGIN SELECT 2 END",
                ],
            )
        assert result.exit_code == 0

    def test_update_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining update is a clean no-op (exit 0)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "procedures",
                    "update",
                    WH_GUID,
                    "dbo.usp_load",
                    "--body",
                    "BEGIN END",
                ],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_update_from_file_strips_utf8_sig_bom(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """Files with UTF-8-sig BOM must be decoded transparently."""
        _ = cache_env
        sql_file = tmp_path / "proc_update_bom.sql"
        sql_file.write_bytes(b"\xef\xbb\xbfBEGIN SELECT 2 END")
        captured_body: list[str] = []

        async def _capture(
            _target: object,
            _schema: object,
            _proc_name: object,
            body: str,
            **_kw: object,
        ) -> StoredProcedure:
            captured_body.append(body)
            return _make_proc(with_definition=True)

        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.procedures.update_procedure", new=_capture),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "procedures",
                    "update",
                    WH_GUID,
                    "dbo.usp_load",
                    "--from-file",
                    str(sql_file),
                ],
            )
        assert result.exit_code == 0
        assert captured_body == ["BEGIN SELECT 2 END"]


# ===========================================================================
# procedures drop
# ===========================================================================


class TestProceduresDrop:
    def test_drop_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.drop_procedure",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "-w", WS_GUID, "procedures", "drop", WH_GUID, "dbo.usp_load"]
            )
        assert result.exit_code == 0

    def test_drop_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining drop is a clean no-op (exit 0)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "procedures", "drop", WH_GUID, "dbo.usp_load"],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_drop_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "procedures", "drop", WH_GUID, "no_dot"])
        assert result.exit_code != 0

    def test_drop_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.drop_procedure",
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "-w", WS_GUID, "procedures", "drop", WH_GUID, "dbo.usp_load"]
            )
        assert result.exit_code != 0


# ===========================================================================
# procedures transfer
# ===========================================================================


class TestProceduresTransfer:
    def test_transfer_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        moved = StoredProcedure(
            schema_name="archive",
            name="usp_load",
            qualified_name="archive.usp_load",
            definition=None,
            created=_NOW,
            modified=_NOW,
        )
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.transfer_procedure",
                new=AsyncMock(return_value=moved),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "procedures",
                    "transfer",
                    WH_GUID,
                    "dbo.usp_load",
                    "--target-schema",
                    "archive",
                ],
            )
        assert result.exit_code == 0

    def test_transfer_declined_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        """Declining transfer is a clean no-op (exit 0)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "procedures",
                    "transfer",
                    WH_GUID,
                    "dbo.usp_load",
                    "--target-schema",
                    "archive",
                ],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_transfer_forwards_args(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        mock_transfer = AsyncMock(
            return_value=StoredProcedure(
                schema_name="archive",
                name="usp_load",
                qualified_name="archive.usp_load",
                definition=None,
                created=_NOW,
                modified=_NOW,
            )
        )
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.procedures.transfer_procedure", new=mock_transfer),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "procedures",
                    "transfer",
                    WH_GUID,
                    "dbo.usp_load",
                    "--target-schema",
                    "archive",
                ],
            )
        assert result.exit_code == 0
        mock_transfer.assert_awaited_once()
        args, _kwargs = mock_transfer.call_args
        assert args[1] == "dbo.usp_load"
        assert args[2] == "archive"

    def test_transfer_works_on_sql_endpoint(self, runner: CliRunner, cache_env: Path) -> None:
        """transfer on a SQL Analytics Endpoint must succeed — no DW-only guard."""
        _ = cache_env
        mock_http = AsyncMock()
        moved = StoredProcedure(
            schema_name="archive",
            name="usp_load",
            qualified_name="archive.usp_load",
            definition=None,
            created=_NOW,
            modified=_NOW,
        )
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(
                    return_value=(
                        _make_sql_target(),
                        _make_item_entry(kind=WarehouseKind.SQL_ENDPOINT),
                    )
                ),
            ),
            patch(
                "fabric_dw.services.procedures.transfer_procedure",
                new=AsyncMock(return_value=moved),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "procedures",
                    "transfer",
                    WH_GUID,
                    "dbo.usp_load",
                    "--target-schema",
                    "archive",
                ],
            )
        assert result.exit_code == 0

    def test_transfer_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "procedures",
                "transfer",
                WH_GUID,
                "no_dot",
                "--target-schema",
                "archive",
            ],
        )
        assert result.exit_code != 0

    def test_transfer_missing_target_schema_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "procedures", "transfer", WH_GUID, "dbo.usp_load"],
        )
        assert result.exit_code != 0

    def test_transfer_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.procedures.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.procedures.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.procedures.transfer_procedure",
                new=AsyncMock(side_effect=PermissionDeniedError("no permission")),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "-w",
                    WS_GUID,
                    "procedures",
                    "transfer",
                    WH_GUID,
                    "dbo.usp_load",
                    "--target-schema",
                    "archive",
                ],
            )
        assert result.exit_code != 0
