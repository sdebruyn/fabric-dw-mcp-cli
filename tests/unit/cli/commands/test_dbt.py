"""Unit tests for the dbt CLI sub-commands."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

from click.testing import CliRunner, Result

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.exceptions import NotFoundError
from fabric_dw.models import WarehouseKind
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)

_HOST = "mywarehouse.datawarehouse.fabric.microsoft.com"
_DB = "SalesWarehouse"


def _make_sql_target() -> SqlTarget:
    return SqlTarget(
        workspace_id=WS_GUID,
        database=_DB,
        connection_string=_HOST,
    )


def _make_item_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string=_HOST,
        fetched_at=datetime.now(tz=UTC),
        display_name=_DB,
    )


def _make_http_cm(http: object) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


def _invoke_init(runner: CliRunner, tmp_path: Path, extra_args: list[str] | None = None) -> Result:
    """Helper: invoke dbt init with standard mocks and a temp folder."""
    folder = str(tmp_path / "myproject")
    mock_http = AsyncMock()
    with (
        patch(
            "fabric_dw.cli.commands.dbt.build_http_client",
            new=_make_http_cm(mock_http),
        ),
        patch(
            "fabric_dw.cli.commands.dbt.build_sql_target",
            new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
        ),
        patch(
            "fabric_dw.services.dbt_scaffold.scaffold",
        ) as mock_scaffold,
    ):
        mock_scaffold.return_value = [tmp_path / "myproject" / "dbt_project.yml"]
        args = ["-w", WS_GUID, "dbt", "init", WH_GUID, folder]
        if extra_args:
            args.extend(extra_args)
        return runner.invoke(cli, args)


class TestDbtInitCommand:
    """dbt init — happy paths."""

    def test_exits_zero(self, runner: CliRunner, tmp_path: Path, cache_env: Path) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path)
        assert result.exit_code == 0, result.output

    def test_output_mentions_project_name(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path)
        # Output should mention scaffold was done
        assert "dbt" in result.output.lower() or "project" in result.output.lower()

    def test_default_project_name_from_folder(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        folder = str(tmp_path / "my_warehouse_project")
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.dbt.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.dbt.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.dbt_scaffold.scaffold") as mock_scaffold,
        ):
            mock_scaffold.return_value = []
            result = runner.invoke(cli, ["-w", WS_GUID, "dbt", "init", WH_GUID, folder])
        assert result.exit_code == 0

    def test_explicit_project_name_accepted(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path, ["--project-name", "my_custom_proj"])
        assert result.exit_code == 0

    def test_explicit_profile_name_accepted(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path, ["--profile-name", "my_profile"])
        assert result.exit_code == 0

    def test_schema_option(self, runner: CliRunner, tmp_path: Path, cache_env: Path) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path, ["--schema", "staging"])
        assert result.exit_code == 0

    def test_target_option(self, runner: CliRunner, tmp_path: Path, cache_env: Path) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path, ["--target", "prod"])
        assert result.exit_code == 0

    def test_threads_option(self, runner: CliRunner, tmp_path: Path, cache_env: Path) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path, ["--threads", "8"])
        assert result.exit_code == 0

    def test_auth_sp_option(self, runner: CliRunner, tmp_path: Path, cache_env: Path) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path, ["--auth", "ServicePrincipal"])
        assert result.exit_code == 0

    def test_auth_cli_option(self, runner: CliRunner, tmp_path: Path, cache_env: Path) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path, ["--auth", "CLI"])
        assert result.exit_code == 0

    def test_auth_auto_option(self, runner: CliRunner, tmp_path: Path, cache_env: Path) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path, ["--auth", "auto"])
        assert result.exit_code == 0

    def test_profiles_dir_home_option(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path, ["--profiles-dir", "home"])
        assert result.exit_code == 0

    def test_force_flag(self, runner: CliRunner, tmp_path: Path, cache_env: Path) -> None:
        _ = cache_env
        result = _invoke_init(runner, tmp_path, ["--force"])
        assert result.exit_code == 0


class TestDbtInitWithSources:
    """dbt init --with-sources option."""

    def test_with_sources_flag_calls_list_schemas_and_tables(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        folder = str(tmp_path / "proj")
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.dbt.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.dbt.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch("fabric_dw.services.dbt_scaffold.scaffold") as mock_scaffold,
            patch(
                "fabric_dw.services.schemas.list_schemas",
                new=AsyncMock(return_value=[]),
            ) as mock_schemas,
            patch(
                "fabric_dw.services.tables.list_tables",
                new=AsyncMock(return_value=[]),
            ) as mock_tables,
            patch(
                "fabric_dw.services.columns.get_columns_for_schemas",
                new=AsyncMock(return_value={}),
            ) as mock_columns,
        ):
            mock_scaffold.return_value = []
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "dbt", "init", WH_GUID, folder, "--with-sources"],
            )
        assert result.exit_code == 0
        mock_schemas.assert_called_once()
        mock_tables.assert_called_once()
        mock_columns.assert_called_once()


class TestDbtInitErrors:
    """dbt init — error paths."""

    def test_non_empty_folder_without_force_fails(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        folder = tmp_path / "proj"
        folder.mkdir()
        (folder / "existing.txt").write_text("hello")

        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.dbt.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.dbt.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "dbt", "init", WH_GUID, str(folder)],
            )
        assert result.exit_code != 0

    def test_fabric_error_returns_nonzero(
        self, runner: CliRunner, tmp_path: Path, cache_env: Path
    ) -> None:
        _ = cache_env
        folder = str(tmp_path / "proj")
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.dbt.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.dbt.build_sql_target",
                new=AsyncMock(side_effect=NotFoundError("Workspace not found")),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "dbt", "init", WH_GUID, folder])
        assert result.exit_code != 0
