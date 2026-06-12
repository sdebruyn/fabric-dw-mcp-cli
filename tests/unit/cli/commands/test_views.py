"""Tests for views CLI sub-commands."""

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
from fabric_dw.exceptions import NotFoundError, PermissionDeniedError
from fabric_dw.models import View, WarehouseKind
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
            result = runner.invoke(cli, ["views", "list", WS_GUID, WH_GUID])
        assert result.exit_code == 0

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
            result = runner.invoke(cli, ["--json", "views", "list", WS_GUID, WH_GUID])
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
            result = runner.invoke(cli, ["views", "list", WS_GUID, WH_GUID, "--schema", "dbo"])
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
            result = runner.invoke(cli, ["views", "list", WS_GUID, WH_GUID])
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
            result = runner.invoke(cli, ["views", "read", WS_GUID, WH_GUID, "dbo.vw_sales"])
        assert result.exit_code == 0

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
            result = runner.invoke(cli, ["views", "read", WS_GUID, WH_GUID, "dbo.vw_sales"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed[0]["id"] == 42

    def test_read_csv_requires_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli, ["views", "read", WS_GUID, WH_GUID, "dbo.vw_sales", "--format", "csv"]
        )
        assert result.exit_code != 0

    def test_read_parquet_requires_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli, ["views", "read", WS_GUID, WH_GUID, "dbo.vw_sales", "--format", "parquet"]
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
                    "views",
                    "read",
                    WS_GUID,
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

    def test_read_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["views", "read", WS_GUID, WH_GUID, "nodot"])
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
            result = runner.invoke(cli, ["views", "read", WS_GUID, WH_GUID, "dbo.vw_missing"])
        assert result.exit_code != 0


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
            result = runner.invoke(cli, ["views", "get", WS_GUID, WH_GUID, "dbo.vw_sales"])
        assert result.exit_code == 0

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
                cli, ["--json", "views", "get", WS_GUID, WH_GUID, "dbo.vw_sales"]
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["name"] == "vw_sales"

    def test_get_bad_qualified_name_exits_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["views", "get", WS_GUID, WH_GUID, "no_dot_here"])
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
            result = runner.invoke(cli, ["views", "get", WS_GUID, WH_GUID, "dbo.vw_missing"])
        assert result.exit_code != 0


# ===========================================================================
# views create
# ===========================================================================


class TestViewsCreate:
    def test_create_with_select_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                new=AsyncMock(return_value=_make_view(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "views",
                    "create",
                    WS_GUID,
                    WH_GUID,
                    "--name",
                    "dbo.vw_sales",
                    "--select",
                    "SELECT id FROM dbo.sales",
                ],
            )
        assert result.exit_code == 0

    def test_create_with_file(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
        _ = cache_env
        sql_file = tmp_path / "view.sql"
        sql_file.write_text("SELECT id FROM dbo.sales")
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
                new=AsyncMock(return_value=_make_view(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "views",
                    "create",
                    WS_GUID,
                    WH_GUID,
                    "--name",
                    "dbo.vw_sales",
                    "--from-file",
                    str(sql_file),
                ],
            )
        assert result.exit_code == 0

    def test_create_no_select_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            ["views", "create", WS_GUID, WH_GUID, "--name", "dbo.vw_sales"],
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
                "views",
                "create",
                WS_GUID,
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
                    "views",
                    "create",
                    WS_GUID,
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
                    "views",
                    "create",
                    WS_GUID,
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
                new=AsyncMock(return_value=_make_view(with_definition=True)),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "--yes",
                    "views",
                    "update",
                    WS_GUID,
                    WH_GUID,
                    "dbo.vw_sales",
                    "--select",
                    "SELECT id FROM dbo.sales",
                ],
            )
        assert result.exit_code == 0

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
                [
                    "views",
                    "update",
                    WS_GUID,
                    WH_GUID,
                    "dbo.vw_sales",
                    "--select",
                    "SELECT 1",
                ],
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
                    "--yes",
                    "views",
                    "update",
                    WS_GUID,
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
                new=AsyncMock(return_value=None),
            ),
        ):
            result = runner.invoke(
                cli, ["--yes", "views", "drop", WS_GUID, WH_GUID, "dbo.vw_sales"]
            )
        assert result.exit_code == 0

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
                ["views", "drop", WS_GUID, WH_GUID, "dbo.vw_sales"],
                input="n\n",
            )
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_drop_bad_qualified_name_exits_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["views", "drop", WS_GUID, WH_GUID, "no_dot"])
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
                cli, ["--yes", "views", "drop", WS_GUID, WH_GUID, "dbo.vw_sales"]
            )
        assert result.exit_code != 0
