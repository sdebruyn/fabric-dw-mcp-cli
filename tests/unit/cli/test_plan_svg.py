"""Unit tests for the SVG renderer (_plan_svg).

All tests run offline — graphviz is NOT required to be installed.  The system
``dot`` binary is always mocked so CI passes without the optional dependency.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest

from fabric_dw.cli._plan_parse import PlanOperator, parse_showplan
from fabric_dw.cli._plan_svg import _MISSING_BINARY_MSG, render_plan_svg

_NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan"

_FIXTURE_XML = (
    f'<ShowPlanXML xmlns="{_NS}" Version="1.6" Build="16.0.0.0">'
    f"<BatchSequence><Batch><Statements>"
    f'<StmtSimple StatementText="SELECT 1" StatementId="1">'
    f"<QueryPlan>"
    f'<RelOp NodeId="0" PhysicalOp="Clustered Index Scan"'
    f' LogicalOp="Clustered Index Scan"'
    f' EstimateRows="1000" EstimatedTotalSubtreeCost="0.5" Parallel="0">'
    f'<IndexScan Ordered="false"/>'
    f"</RelOp>"
    f"</QueryPlan>"
    f"</StmtSimple>"
    f"</Statements></Batch></BatchSequence>"
    f"</ShowPlanXML>"
)

_FAKE_SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><text>plan</text></svg>"


def _make_proc(returncode: int = 0, stdout: bytes = _FAKE_SVG, stderr: bytes = b"") -> MagicMock:
    """Build a mock CompletedProcess returned by subprocess.run."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class TestRenderPlanSvgMissingBinary:
    """Verify actionable errors when the dot binary is absent."""

    def test_raises_usage_error_when_which_returns_none(self) -> None:
        """UsageError with install hint when shutil.which("dot") returns None."""
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value=None),
            pytest.raises(click.UsageError, match="graphviz"),
        ):
            render_plan_svg([])

    def test_error_message_contains_install_hint(self) -> None:
        """The UsageError message must include the Graphviz download URL."""
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value=None),
            pytest.raises(click.UsageError) as exc_info,
        ):
            render_plan_svg([])
        assert "graphviz.org" in exc_info.value.format_message()

    def test_error_message_matches_constant(self) -> None:
        """The UsageError message must match the module-level constant."""
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value=None),
            pytest.raises(click.UsageError) as exc_info,
        ):
            render_plan_svg([])
        assert _MISSING_BINARY_MSG in exc_info.value.format_message()

    def test_raises_usage_error_on_file_not_found(self) -> None:
        """Race condition: binary disappears between which() and run()."""
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                side_effect=FileNotFoundError("dot: not found"),
            ),
            pytest.raises(click.UsageError, match="graphviz"),
        ):
            render_plan_svg([PlanOperator()])


class TestRenderPlanSvgSubprocessError:
    """Verify actionable errors when dot exits non-zero."""

    def test_raises_click_exception_on_nonzero_exit(self) -> None:
        """dot non-zero exit must raise ClickException with the stderr output."""
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_make_proc(returncode=1, stderr=b"syntax error in input"),
            ),
            pytest.raises(click.ClickException, match="syntax error"),
        ):
            render_plan_svg([PlanOperator()])

    def test_nonzero_exit_message_includes_status(self) -> None:
        """ClickException message includes the non-zero exit status."""
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_make_proc(returncode=2, stderr=b"oops"),
            ),
            pytest.raises(click.ClickException) as exc_info,
        ):
            render_plan_svg([PlanOperator()])
        assert "2" in exc_info.value.format_message()


class TestRenderPlanSvgHappyPath:
    """Verify the happy-path: DOT piped to dot -Tsvg, SVG bytes returned."""

    def test_returns_svg_bytes(self) -> None:
        """render_plan_svg returns the raw SVG bytes from dot stdout."""
        operators = parse_showplan(_FIXTURE_XML)
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_make_proc(stdout=_FAKE_SVG),
            ) as mock_run,
        ):
            result = render_plan_svg(operators)

        assert result == _FAKE_SVG
        mock_run.assert_called_once()

    def test_dot_called_with_tsvg_flag(self) -> None:
        """The subprocess must be called with ['dot', '-Tsvg'] (no shell=True)."""
        operators = parse_showplan(_FIXTURE_XML)
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_make_proc(),
            ) as mock_run,
        ):
            render_plan_svg(operators)

        call_kwargs = mock_run.call_args
        assert call_kwargs[0][0] == ["dot", "-Tsvg"]
        # Never use shell=True with user-influenced data
        assert call_kwargs[1].get("shell") is not True

    def test_dot_receives_dot_text_via_stdin(self) -> None:
        """The DOT text must be passed to dot via stdin (the input= kwarg)."""
        operators = parse_showplan(_FIXTURE_XML)
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_make_proc(),
            ) as mock_run,
        ):
            render_plan_svg(operators)

        call_kwargs = mock_run.call_args
        stdin_data = call_kwargs[1].get("input") or call_kwargs[0][1]
        assert b"digraph" in stdin_data

    def test_empty_operators_still_calls_dot(self) -> None:
        """Even with no operators the DOT comment is piped to dot (no short-circuit)."""
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_make_proc(stdout=_FAKE_SVG),
            ) as mock_run,
        ):
            result = render_plan_svg([])

        assert result == _FAKE_SVG
        mock_run.assert_called_once()

    def test_svg_bytes_written_to_file(self, tmp_path: Path) -> None:
        """SVG bytes are written verbatim to the output file when a path is given."""
        operators = parse_showplan(_FIXTURE_XML)
        out_file = tmp_path / "plan.svg"

        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_make_proc(stdout=_FAKE_SVG),
            ),
        ):
            svg_bytes = render_plan_svg(operators)

        out_file.write_bytes(svg_bytes)
        assert out_file.read_bytes() == _FAKE_SVG
