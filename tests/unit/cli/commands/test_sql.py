"""Tests for sql CLI sub-commands — TDD."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import click
import pytest
from click.testing import CliRunner

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.exceptions import NotFound, PermissionDenied
from fabric_dw.models import SqlResult, WarehouseKind
from fabric_dw.sql import SqlTarget

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


def _make_sql_result() -> SqlResult:
    return SqlResult(columns=["id", "name"], rows=[[1, "foo"], [2, "bar"]], rowcount=2)


def _make_empty_sql_result() -> SqlResult:
    return SqlResult(columns=[], rows=[], rowcount=3)


class TestSqlExec:
    """sql exec — happy paths."""

    def test_exec_query_flag_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_exec.execute",
                new=AsyncMock(return_value=_make_sql_result()),
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql", "exec", WS_GUID, WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code == 0

    def test_exec_outputs_table_by_default(self, runner: CliRunner, cache_env: Path) -> None:
        """sql exec defaults to Rich table output (--json on root switches to JSON)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_exec.execute",
                new=AsyncMock(return_value=_make_sql_result()),
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql", "exec", WS_GUID, WH_GUID, "-q", "SELECT id, name FROM t"],
            )
        assert result.exit_code == 0
        # Default is table — output must NOT be parseable as JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.output)

    def test_exec_outputs_json_with_json_flag(self, runner: CliRunner, cache_env: Path) -> None:
        """Global --json flag triggers JSON output for sql exec."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_exec.execute",
                new=AsyncMock(return_value=_make_sql_result()),
            ),
        ):
            result = runner.invoke(
                cli,
                ["--json", "sql", "exec", WS_GUID, WH_GUID, "-q", "SELECT id, name FROM t"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["columns"] == ["id", "name"]
        assert parsed["rows"] == [[1, "foo"], [2, "bar"]]
        assert parsed["rowcount"] == 2

    def test_exec_file_flag_reads_file(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        sql_file = tmp_path / "query.sql"
        sql_file.write_text("SELECT 1 AS n")
        mock_http = AsyncMock()
        captured_query: list[str] = []

        async def _capture_execute(_target: object, query: str, **_kwargs: object) -> SqlResult:
            captured_query.append(query)
            return _make_sql_result()

        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_exec.execute",
                new=_capture_execute,
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql", "exec", WS_GUID, WH_GUID, "-f", str(sql_file)],
            )
        assert result.exit_code == 0
        assert captured_query[0] == "SELECT 1 AS n"

    def test_exec_file_flag_strips_utf8_bom(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """Files saved with UTF-8 BOM (e.g. from SSMS/ADS) must not pass the BOM to execute."""
        _ = cache_env
        sql_file = tmp_path / "query_bom.sql"
        # Write with explicit UTF-8 BOM prefix (U+FEFF)
        sql_file.write_bytes(b"\xef\xbb\xbfSELECT 1 AS n")
        mock_http = AsyncMock()
        captured_query: list[str] = []

        async def _capture_execute(_target: object, query: str, **_kwargs: object) -> SqlResult:
            captured_query.append(query)
            return _make_sql_result()

        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_exec.execute",
                new=_capture_execute,
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql", "exec", WS_GUID, WH_GUID, "-f", str(sql_file)],
            )
        assert result.exit_code == 0
        assert captured_query[0][0] == "S", (
            f"BOM not stripped: first char is {captured_query[0][0]!r}"
        )

    def test_exec_default_renders_table(self, runner: CliRunner, cache_env: Path) -> None:
        """sql exec default output is a Rich table (no --table flag needed anymore)."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_exec.execute",
                new=AsyncMock(return_value=_make_sql_result()),
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql", "exec", WS_GUID, WH_GUID, "-q", "SELECT id, name FROM t"],
            )
        assert result.exit_code == 0
        # Default is table; JSON flag not set so output must NOT be parseable as JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.output)

    def test_exec_dml_no_rows_shows_rowcount(self, runner: CliRunner, cache_env: Path) -> None:
        """DML with no result rows prints rowcount message."""
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_exec.execute",
                new=AsyncMock(return_value=_make_empty_sql_result()),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "sql",
                    "exec",
                    WS_GUID,
                    WH_GUID,
                    "-q",
                    "INSERT INTO t VALUES (1)",
                ],
            )
        assert result.exit_code == 0
        assert "rowcount" in result.output


class TestSqlExecErrors:
    """sql exec — error paths."""

    def test_exec_no_query_or_file_is_error(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["sql", "exec", WS_GUID, WH_GUID])
        assert result.exit_code != 0

    def test_exec_both_query_and_file_is_error(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        sql_file = tmp_path / "q.sql"
        sql_file.write_text("SELECT 1")
        result = runner.invoke(
            cli,
            ["sql", "exec", WS_GUID, WH_GUID, "-q", "SELECT 1", "-f", str(sql_file)],
        )
        assert result.exit_code != 0

    def test_exec_permission_denied_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_item_entry())),
            ),
            patch(
                "fabric_dw.services.sql_exec.execute",
                new=AsyncMock(side_effect=PermissionDenied("no perms")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql", "exec", WS_GUID, WH_GUID, "-q", "SELECT * FROM sensitive"],
            )
        assert result.exit_code != 0

    def test_exec_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(side_effect=NotFound("not found")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql", "exec", WS_GUID, WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code != 0

    def test_exec_no_connection_string_returns_nonzero(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        # build_sql_target raises ClickException when connection_string is None;
        # simulate that real behaviour so the test exercises the intended guard path.
        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(
                    side_effect=click.ClickException(
                        "Item 'SalesWarehouse' has no connection string."
                    )
                ),
            ),
        ):
            result = runner.invoke(
                cli,
                ["sql", "exec", WS_GUID, WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code != 0
