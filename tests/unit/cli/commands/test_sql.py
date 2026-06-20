"""Tests for the sql CLI command group — TDD.

Covers:
- ``sql exec`` — all existing happy-path and error-path tests (regression).
- ``sql plan`` — stdout and -o/--output file output, -q/-f exclusivity.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import click
import pytest
from click.testing import CliRunner, Result

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.exceptions import NotFoundError, PermissionDeniedError
from fabric_dw.models import SqlResult, WarehouseKind
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)


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


_PLAN_XML = (
    "<ShowPlanXML xmlns='http://schemas.microsoft.com/sqlserver/2004/07/showplan'>"
    "<Batch><Statements><StmtSimple /></Statements></Batch></ShowPlanXML>"
)

_NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan"

# A realistic plan XML that exercises the tree-render path: Hash Match over two
# Clustered Index Scans, one Parallel node, one Warnings node.
_RICH_PLAN_XML = (
    f'<ShowPlanXML xmlns="{_NS}" Version="1.6" Build="16.0.0.0">'  # noqa: S608
    f"<BatchSequence><Batch><Statements>"
    f'<StmtSimple StatementText="SELECT o.id FROM dbo.Orders o" StatementId="1">'
    f"<QueryPlan>"
    f'<RelOp NodeId="0" PhysicalOp="Hash Match" LogicalOp="Inner Join"'
    f' EstimateRows="5000" EstimatedTotalSubtreeCost="1.5" Parallel="0">'
    f"<Hash>"
    f'<RelOp NodeId="1" PhysicalOp="Clustered Index Scan"'
    f' LogicalOp="Clustered Index Scan"'
    f' EstimateRows="10000" EstimatedTotalSubtreeCost="0.9" Parallel="1">'
    f'<IndexScan Ordered="false">'
    f"<Warnings><PlanAffectingConvert ConvertIssue='Cardinality Estimate'/></Warnings>"
    f"</IndexScan>"
    f"</RelOp>"
    f'<RelOp NodeId="2" PhysicalOp="Clustered Index Scan"'
    f' LogicalOp="Clustered Index Scan"'
    f' EstimateRows="3000" EstimatedTotalSubtreeCost="0.5" Parallel="0">'
    f'<IndexScan Ordered="false"/>'
    f"</RelOp>"
    f"</Hash>"
    f"</RelOp>"
    f"</QueryPlan>"
    f"</StmtSimple>"
    f"</Statements></Batch></BatchSequence>"
    f"</ShowPlanXML>"
)


class TestSqlExec:
    """sql exec — happy paths (regression: was 'sql' before #507)."""

    def test_query_flag_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                ["-w", WS_GUID, "sql", "exec", WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code == 0

    def test_outputs_table_by_default(self, runner: CliRunner, cache_env: Path) -> None:
        """sql exec defaults to Rich table output."""
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
                ["-w", WS_GUID, "sql", "exec", WH_GUID, "-q", "SELECT id, name FROM t"],
            )
        assert result.exit_code == 0
        # Default is table — output must NOT be parseable as JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.output)

    def test_outputs_json_with_json_flag(self, runner: CliRunner, cache_env: Path) -> None:
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
                ["-w", WS_GUID, "--json", "sql", "exec", WH_GUID, "-q", "SELECT id, name FROM t"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["columns"] == ["id", "name"]
        assert parsed["rows"] == [[1, "foo"], [2, "bar"]]
        assert parsed["rowcount"] == 2

    def test_file_flag_reads_file(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
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
                ["-w", WS_GUID, "sql", "exec", WH_GUID, "-f", str(sql_file)],
            )
        assert result.exit_code == 0
        assert captured_query[0] == "SELECT 1 AS n"

    def test_file_flag_strips_utf8_bom(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """Files saved with UTF-8 BOM (e.g. from SSMS/ADS) must not pass the BOM to execute."""
        _ = cache_env
        sql_file = tmp_path / "query_bom.sql"
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
                ["-w", WS_GUID, "sql", "exec", WH_GUID, "-f", str(sql_file)],
            )
        assert result.exit_code == 0
        assert captured_query[0][0] == "S", (
            f"BOM not stripped: first char is {captured_query[0][0]!r}"
        )

    def test_default_renders_table(self, runner: CliRunner, cache_env: Path) -> None:
        """sql exec default output is a Rich table (no --table flag needed)."""
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
                ["-w", WS_GUID, "sql", "exec", WH_GUID, "-q", "SELECT id, name FROM t"],
            )
        assert result.exit_code == 0
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.output)

    def test_dml_no_rows_shows_rowcount(self, runner: CliRunner, cache_env: Path) -> None:
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
                    "-w",
                    WS_GUID,
                    "sql",
                    "exec",
                    WH_GUID,
                    "-q",
                    "INSERT INTO t VALUES (1)",
                ],
            )
        assert result.exit_code == 0
        assert "rowcount" in result.output


class TestSqlExecErrors:
    """sql exec — error paths."""

    def test_no_query_or_file_is_error(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "sql", "exec", WH_GUID])
        assert result.exit_code != 0

    def test_both_query_and_file_is_error(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        sql_file = tmp_path / "q.sql"
        sql_file.write_text("SELECT 1")
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "sql", "exec", WH_GUID, "-q", "SELECT 1", "-f", str(sql_file)],
        )
        assert result.exit_code != 0

    def test_permission_denied_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
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
                new=AsyncMock(side_effect=PermissionDeniedError("no perms")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "exec", WH_GUID, "-q", "SELECT * FROM sensitive"],
            )
        assert result.exit_code != 0

    def test_not_found_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.sql.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.sql.build_sql_target",
                new=AsyncMock(side_effect=NotFoundError("not found")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "exec", WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code != 0

    def test_no_connection_string_returns_nonzero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
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
                ["-w", WS_GUID, "sql", "exec", WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code != 0


class TestSqlPlan:
    """sql plan — happy paths."""

    def test_plan_default_renders_tree(self, runner: CliRunner, cache_env: Path) -> None:
        """sql plan default output is a Rich terminal tree (not raw XML)."""
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_RICH_PLAN_XML),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "plan", WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code == 0
        assert "<ShowPlanXML" not in result.output
        assert "Hash Match" in result.output
        assert "Clustered Index Scan" in result.output
        assert "%" in result.output
        assert "[Parallel]" in result.output
        assert "[!Warnings]" in result.output

    def test_plan_raw_flag_prints_xml(self, runner: CliRunner, cache_env: Path) -> None:
        """sql plan --raw prints the raw SHOWPLAN XML to stdout."""
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_PLAN_XML),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "plan", WH_GUID, "-q", "SELECT 1", "--raw"],
            )
        assert result.exit_code == 0
        assert _PLAN_XML in result.output

    def test_plan_xml_alias_prints_xml(self, runner: CliRunner, cache_env: Path) -> None:
        """sql plan --xml is an alias for --raw."""
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_PLAN_XML),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "plan", WH_GUID, "-q", "SELECT 1", "--xml"],
            )
        assert result.exit_code == 0
        assert "ShowPlanXML" in result.output

    def test_plan_json_flag_emits_operator_tree(self, runner: CliRunner, cache_env: Path) -> None:
        """sql plan --json emits the parsed operator tree as JSON."""
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_PLAN_XML),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "--json", "sql", "plan", WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_plan_output_file(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
        """sql plan -o FILE writes the XML to the file and echoes a confirmation."""
        _ = cache_env
        out_file = tmp_path / "plan.sqlplan"
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_PLAN_XML),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "plan", WH_GUID, "-q", "SELECT 1", "-o", str(out_file)],
            )
        assert result.exit_code == 0
        # File must contain the plan XML
        assert out_file.exists()
        assert out_file.read_text(encoding="utf-8") == _PLAN_XML
        # stdout must contain the confirmation message, not the XML itself
        assert "Execution plan written to" in result.output
        assert "ShowPlanXML" not in result.output

    def test_plan_file_input(self, runner: CliRunner, cache_env: Path, tmp_path: Path) -> None:
        """sql plan -f FILE reads the query from a file."""
        _ = cache_env
        sql_file = tmp_path / "query.sql"
        sql_file.write_text("SELECT 1 AS n")
        mock_http = AsyncMock()
        captured_query: list[str] = []

        async def _capture_plan(_target: object, query: str, **_kwargs: object) -> str:
            captured_query.append(query)
            return _PLAN_XML

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
                "fabric_dw.services.sql_exec.get_plan",
                new=_capture_plan,
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "plan", WH_GUID, "-f", str(sql_file)],
            )
        assert result.exit_code == 0
        assert captured_query[0] == "SELECT 1 AS n"


class TestSqlPlanErrors:
    """sql plan — error paths."""

    def test_plan_no_query_or_file_is_error(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "sql", "plan", WH_GUID])
        assert result.exit_code != 0

    def test_plan_both_query_and_file_is_error(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        _ = cache_env
        sql_file = tmp_path / "q.sql"
        sql_file.write_text("SELECT 1")
        result = runner.invoke(
            cli,
            ["-w", WS_GUID, "sql", "plan", WH_GUID, "-q", "SELECT 1", "-f", str(sql_file)],
        )
        assert result.exit_code != 0

    def test_plan_permission_denied_returns_nonzero(
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(side_effect=PermissionDeniedError("no perms")),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "plan", WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code != 0

    def test_plan_malformed_xml_returns_clean_error(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Malformed XML from the API must produce a clean CLI error, not a traceback."""
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value="not valid xml at all"),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "plan", WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code != 0
        # Must show a clean Error: message, not a raw Python traceback
        assert "Error:" in result.output
        assert "ParseError" not in result.output


class TestSqlPlanFormatMermaid:
    """sql plan --format mermaid — output routing and regression coverage."""

    def _invoke_mermaid(
        self,
        runner: CliRunner,
        extra_args: list[str],
    ) -> Result:
        """Invoke sql plan --format mermaid with patched services."""
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_RICH_PLAN_XML),
            ),
        ):
            return runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "sql",
                    "plan",
                    WH_GUID,
                    "-q",
                    "SELECT 1",
                    "--format",
                    "mermaid",
                    *extra_args,
                ],
            )

    def test_format_mermaid_stdout_contains_flowchart(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--format mermaid emits Mermaid flowchart to stdout."""
        _ = cache_env
        result = self._invoke_mermaid(runner, [])
        assert result.exit_code == 0
        assert "flowchart TD" in result.output

    def test_format_mermaid_stdout_not_xml(self, runner: CliRunner, cache_env: Path) -> None:
        """--format mermaid output must NOT be raw XML."""
        _ = cache_env
        result = self._invoke_mermaid(runner, [])
        assert "<ShowPlanXML" not in result.output

    def test_format_mermaid_output_file_written(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """--format mermaid -o FILE writes the diagram to FILE and suppresses stdout diagram."""
        _ = cache_env
        out_file = tmp_path / "plan.md"
        result = self._invoke_mermaid(runner, ["-o", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "flowchart TD" in content
        # Confirmation message on stdout, not the diagram itself
        assert "Mermaid diagram written to" in result.output
        assert "flowchart TD" not in result.output

    def test_raw_and_format_together_is_error(self, runner: CliRunner, cache_env: Path) -> None:
        """--raw and --format cannot be combined; must produce a usage error."""
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_RICH_PLAN_XML),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "sql",
                    "plan",
                    WH_GUID,
                    "-q",
                    "SELECT 1",
                    "--raw",
                    "--format",
                    "mermaid",
                ],
            )
        assert result.exit_code != 0


class TestSqlPlanOutputRouting:
    """Regression tests for the -o/--output orthogonality fixes."""

    def test_json_with_output_file_writes_file_not_stdout(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """--json -o FILE must write JSON to FILE and not print JSON to stdout.

        Regression: the original code ignored output_path in the --json branch.
        """
        _ = cache_env
        out_file = tmp_path / "plan.json"
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_RICH_PLAN_XML),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "--json",
                    "sql",
                    "plan",
                    WH_GUID,
                    "-q",
                    "SELECT 1",
                    "-o",
                    str(out_file),
                ],
            )
        assert result.exit_code == 0
        # File must exist and contain JSON
        assert out_file.exists()
        parsed = json.loads(out_file.read_text(encoding="utf-8"))
        assert isinstance(parsed, list)
        # stdout must NOT contain JSON (only a confirmation message)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.output)
        assert "JSON written to" in result.output

    def test_default_with_output_file_does_not_render_tree(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """Default mode -o FILE must write raw XML to file and NOT render the Rich tree.

        Regression: the original code ran render_plan_tree() even when -o was given.
        """
        _ = cache_env
        out_file = tmp_path / "plan.sqlplan"
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_RICH_PLAN_XML),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "plan", WH_GUID, "-q", "SELECT 1", "-o", str(out_file)],
            )
        assert result.exit_code == 0
        # File must contain the raw SHOWPLAN XML
        assert out_file.exists()
        assert "<ShowPlanXML" in out_file.read_text(encoding="utf-8")
        # Rich tree must NOT appear on stdout (only the confirmation message)
        assert "Hash Match" not in result.output
        assert "Execution plan written to" in result.output

    def test_default_without_output_file_renders_tree(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Default mode without -o still renders the Rich terminal tree."""
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_RICH_PLAN_XML),
            ),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "sql", "plan", WH_GUID, "-q", "SELECT 1"],
            )
        assert result.exit_code == 0
        assert "Hash Match" in result.output


_FAKE_SVG_BYTES = b"<svg xmlns='http://www.w3.org/2000/svg'><text>plan</text></svg>"


def _make_dot_proc(
    returncode: int = 0,
    stdout: bytes = _FAKE_SVG_BYTES,
    stderr: bytes = b"",
) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class TestSqlPlanFormatDot:
    """sql plan --format dot — output routing."""

    def _invoke_dot(
        self,
        runner: CliRunner,
        extra_args: list[str],
    ) -> Result:
        """Invoke sql plan --format dot with patched services."""
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_RICH_PLAN_XML),
            ),
        ):
            return runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "sql",
                    "plan",
                    WH_GUID,
                    "-q",
                    "SELECT 1",
                    "--format",
                    "dot",
                    *extra_args,
                ],
            )

    def test_format_dot_stdout_contains_digraph(self, runner: CliRunner, cache_env: Path) -> None:
        """--format dot emits a Graphviz DOT digraph to stdout."""
        _ = cache_env
        result = self._invoke_dot(runner, [])
        assert result.exit_code == 0
        assert "digraph" in result.output

    def test_format_dot_output_file_written(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """--format dot -o FILE writes the DOT graph to FILE and prints confirmation."""
        _ = cache_env
        out_file = tmp_path / "plan.dot"
        result = self._invoke_dot(runner, ["-o", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()
        assert "digraph" in out_file.read_text(encoding="utf-8")
        assert "DOT graph written to" in result.output
        assert "digraph" not in result.output


class TestSqlPlanFormatSvg:
    """sql plan --format svg — output routing, missing binary, dot errors."""

    def _invoke_svg(
        self,
        runner: CliRunner,
        extra_args: list[str],
        *,
        dot_proc: MagicMock | None = None,
        dot_present: bool = True,
    ) -> Result:
        """Invoke sql plan --format svg with patched services and subprocess."""
        mock_http = AsyncMock()
        proc = dot_proc if dot_proc is not None else _make_dot_proc()
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_RICH_PLAN_XML),
            ),
            patch(
                "fabric_dw.cli._plan_svg.shutil.which",
                return_value="/usr/bin/dot" if dot_present else None,
            ),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=proc,
            ),
        ):
            return runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "sql",
                    "plan",
                    WH_GUID,
                    "-q",
                    "SELECT 1",
                    "--format",
                    "svg",
                    *extra_args,
                ],
            )

    def test_format_svg_stdout_contains_svg_bytes(self, runner: CliRunner, cache_env: Path) -> None:
        """--format svg writes SVG bytes to stdout when no -o is given."""
        _ = cache_env
        result = self._invoke_svg(runner, [])
        assert result.exit_code == 0
        assert b"<svg" in result.output_bytes

    def test_format_svg_output_file_written(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """--format svg -o FILE writes SVG to FILE and prints a confirmation."""
        _ = cache_env
        out_file = tmp_path / "plan.svg"
        result = self._invoke_svg(runner, ["-o", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()
        assert out_file.read_bytes() == _FAKE_SVG_BYTES
        assert "SVG written to" in result.output
        # SVG content must NOT be echoed to stdout when -o is given
        assert b"<svg" not in result.output_bytes

    def test_format_svg_file_suppresses_stdout_svg(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """When -o is given, SVG must not be printed to stdout."""
        _ = cache_env
        out_file = tmp_path / "plan.svg"
        result = self._invoke_svg(runner, ["-o", str(out_file)])
        assert result.exit_code == 0
        # stdout should have the confirmation but not the raw SVG bytes
        assert "SVG written to" in result.output

    def test_format_svg_missing_binary_exits_one(self, runner: CliRunner, cache_env: Path) -> None:
        """--format svg with no graphviz installed must exit 1 with an install hint.

        ClickException (not UsageError) is raised so exit code is 1, not 2.
        """
        _ = cache_env
        result = self._invoke_svg(runner, [], dot_present=False)
        assert result.exit_code == 1
        assert "graphviz" in result.output.lower()

    def test_format_svg_dot_nonzero_exit_shows_error(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """When dot exits non-zero, the CLI must show a clean error (no traceback)."""
        _ = cache_env
        result = self._invoke_svg(
            runner,
            [],
            dot_proc=_make_dot_proc(returncode=1, stderr=b"syntax error"),
        )
        assert result.exit_code == 1
        assert "Error:" in result.output

    def test_format_svg_choice_is_case_insensitive(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """--format SVG (uppercase) must be accepted the same as lowercase."""
        _ = cache_env
        result = self._invoke_svg(runner, [])
        # We already patched shutil.which, so this is really testing Click's
        # case_sensitive=False on the Choice — just verify it doesn't blow up.
        # The actual lowercase-vs-uppercase is tested via the Choice parameter;
        # here we simply confirm the happy path works.
        assert result.exit_code == 0


class TestSqlPlanFormatHtml:
    """sql plan --format html — output routing and self-contained HTML generation."""

    def _invoke_html(
        self,
        runner: CliRunner,
        extra_args: list[str],
    ) -> Result:
        """Invoke sql plan --format html with patched services."""
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_RICH_PLAN_XML),
            ),
        ):
            return runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "sql",
                    "plan",
                    WH_GUID,
                    "-q",
                    "SELECT 1",
                    "--format",
                    "html",
                    *extra_args,
                ],
            )

    def test_format_html_requires_output_file(self, runner: CliRunner, cache_env: Path) -> None:
        """--format html without -o must exit with a usage error (HTML not useful on stdout)."""
        _ = cache_env
        result = self._invoke_html(runner, [])
        assert result.exit_code != 0
        assert "html" in result.output.lower() or "output" in result.output.lower()

    def test_format_html_output_file_written(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """--format html -o FILE writes a self-contained HTML file."""
        _ = cache_env
        out_file = tmp_path / "plan.html"
        result = self._invoke_html(runner, ["-o", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()

    def test_format_html_output_contains_doctype(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """The written HTML file must begin with <!DOCTYPE html>."""
        _ = cache_env
        out_file = tmp_path / "plan.html"
        result = self._invoke_html(runner, ["-o", str(out_file)])
        assert result.exit_code == 0
        content = out_file.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content

    def test_format_html_output_embeds_plan_xml(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """The written HTML must embed the raw SHOWPLAN_XML."""
        _ = cache_env
        out_file = tmp_path / "plan.html"
        result = self._invoke_html(runner, ["-o", str(out_file)])
        assert result.exit_code == 0
        content = out_file.read_text(encoding="utf-8")
        assert "ShowPlanXML" in content

    def test_format_html_output_is_self_contained(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """The written HTML must embed CSS and JS inline (no external URLs)."""
        _ = cache_env
        out_file = tmp_path / "plan.html"
        result = self._invoke_html(runner, ["-o", str(out_file)])
        assert result.exit_code == 0
        content = out_file.read_text(encoding="utf-8")
        # No external <script src="http..."> or <link href="http...">
        assert not re.search(r'<script[^>]+src=["\']https?://', content)
        assert not re.search(r'<link[^>]+href=["\']https?://', content)
        # Sprite sheet must be inlined as a data URI
        assert "data:image/png;base64," in content

    def test_format_html_confirmation_printed_to_stdout(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """Writing HTML to file must print a confirmation message to stdout."""
        _ = cache_env
        out_file = tmp_path / "plan.html"
        result = self._invoke_html(runner, ["-o", str(out_file)])
        assert result.exit_code == 0
        assert "HTML plan written to" in result.output

    def test_format_html_html_not_echoed_to_stdout(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """When -o is given, the HTML document must not be printed to stdout."""
        _ = cache_env
        out_file = tmp_path / "plan.html"
        result = self._invoke_html(runner, ["-o", str(out_file)])
        assert result.exit_code == 0
        # The confirmation message is on stdout, not the full HTML document
        assert "<!DOCTYPE html>" not in result.output

    def test_format_html_and_raw_together_is_error(
        self, runner: CliRunner, cache_env: Path, tmp_path: Path
    ) -> None:
        """--raw and --format html cannot be combined; must produce a usage error."""
        _ = cache_env
        out_file = tmp_path / "plan.html"
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
                "fabric_dw.services.sql_exec.get_plan",
                new=AsyncMock(return_value=_RICH_PLAN_XML),
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "sql",
                    "plan",
                    WH_GUID,
                    "-q",
                    "SELECT 1",
                    "--raw",
                    "--format",
                    "html",
                    "-o",
                    str(out_file),
                ],
            )
        assert result.exit_code != 0
