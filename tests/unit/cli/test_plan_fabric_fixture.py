"""Regression tests against real Fabric DW SHOWPLAN_XML fixtures.

Captures TPC-H Q5 and Q9 estimated plans obtained live from a Microsoft
Fabric Data Warehouse (warehouse ``tpch``, SF 0.1 dataset).  The plans
exercise the Fabric-specific ``Compute To Control Node`` data-movement
operator — Fabric's MPP equivalent of SQL Server's "Gather/Distribute/
Repartition Streams" operators, which do NOT appear in Fabric estimated
plans.

Also covers two additional live-captured plans (sanitized, tenant identifiers
removed):
- ``plan508_move.sqlplan``:  simple GROUP BY exercising Compute To Control
  Node (LogicalOp "Move") and Hash Match/Aggregate.
- ``plan508_parallelism.sqlplan``:  complex query on sys catalog tables,
  exercising Concatenation, Filter, Index Scan, Clustered Index Seek, and
  TopN Sort — a broader operator surface than the TPC-H plans.

The fixtures live under ``tests/unit/cli/fixtures/`` and are committed as
static files.  No live Fabric API access is required for any test here.

Renderers verified for every fixture
-------------------------------------
- tree   : ``render_plan_tree``  (Rich terminal tree)
- json   : ``operator_to_dict``  (JSON-serialisable dict)
- mermaid: ``render_plan_mermaid``
- dot    : ``render_plan_dot``
- html   : ``render_plan_html``  (raw XML passed in)
- svg    : DOT text fed to ``render_plan_svg``; the system ``dot`` binary
           is always mocked so CI passes without Graphviz installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from fabric_dw.cli._plan_dot import render_plan_dot
from fabric_dw.cli._plan_html import render_plan_html
from fabric_dw.cli._plan_mermaid import render_plan_mermaid
from fabric_dw.cli._plan_parse import PlanOperator, parse_showplan
from fabric_dw.cli._plan_render import operator_to_dict, render_plan_tree
from fabric_dw.cli._plan_svg import render_plan_svg

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

_Q5_XML = (_FIXTURES_DIR / "tpch_q5_fabric.sqlplan").read_text(encoding="utf-8")
_Q9_XML = (_FIXTURES_DIR / "tpch_q9_fabric.sqlplan").read_text(encoding="utf-8")
_MOVE_XML = (_FIXTURES_DIR / "plan508_move.sqlplan").read_text(encoding="utf-8")
_PARA_XML = (_FIXTURES_DIR / "plan508_parallelism.sqlplan").read_text(encoding="utf-8")

# Operator types that must appear in a TPC-H Q5 / Q9 Fabric plan.
# "Compute To Control Node" is Fabric's data-movement operator; the others
# are standard plan operators exercised by the 6-way TPC-H joins.
_EXPECTED_OPS: frozenset[str] = frozenset(
    {
        "Compute To Control Node",
        "Hash Match",
        "Clustered Index Scan",
        "Compute Scalar",
        "Sort",
    }
)

_FAKE_SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><text>plan</text></svg>"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_ops(root: PlanOperator) -> set[str]:
    """Collect physical_op names from *root* and all descendants."""
    result: set[str] = {root.physical_op}
    for child in root.children:
        result |= _all_ops(child)
    return result


def _parse(plan_xml: str) -> list[PlanOperator]:
    return parse_showplan(plan_xml)


def _render_tree_str(operators: list[PlanOperator]) -> str:
    console = Console(record=True, highlight=False, width=160)
    render_plan_tree(operators, console=console)
    return console.export_text()


def _mock_dot(stdout: bytes = _FAKE_SVG) -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = stdout
    proc.stderr = b""
    return proc


# ---------------------------------------------------------------------------
# Parametrised fixture data
# ---------------------------------------------------------------------------

_FIXTURE_CASES = [
    pytest.param(_Q5_XML, id="tpch_q5"),
    pytest.param(_Q9_XML, id="tpch_q9"),
]

# All four fixtures: used for renderer smoke tests that only require
# "renders without error" and do not assert specific operator names.
_ALL_FIXTURE_CASES = [
    pytest.param(_Q5_XML, id="tpch_q5"),
    pytest.param(_Q9_XML, id="tpch_q9"),
    pytest.param(_MOVE_XML, id="plan508_move"),
    pytest.param(_PARA_XML, id="plan508_parallelism"),
]

# plan508 cases grouped for move / parallelism-surface tests.
_PLAN508_CASES = [
    pytest.param(_MOVE_XML, id="plan508_move"),
    pytest.param(_PARA_XML, id="plan508_parallelism"),
]


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestFabricFixtureParse:
    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_parses_without_error(self, plan_xml: str) -> None:
        """parse_showplan must not raise on a real Fabric plan."""
        operators = _parse(plan_xml)
        assert len(operators) >= 1

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_contains_compute_to_control_node(self, plan_xml: str) -> None:
        """The Fabric data-movement operator must appear in the parsed tree."""
        operators = _parse(plan_xml)
        found_ops: set[str] = set()
        for root in operators:
            found_ops |= _all_ops(root)
        assert "Compute To Control Node" in found_ops, (
            f"Expected 'Compute To Control Node' but got: {sorted(found_ops)}"
        )

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_contains_all_expected_operator_types(self, plan_xml: str) -> None:
        """All expected TPC-H operator types must be present."""
        operators = _parse(plan_xml)
        found_ops: set[str] = set()
        for root in operators:
            found_ops |= _all_ops(root)
        missing = _EXPECTED_OPS - found_ops
        assert not missing, f"Missing operator types: {sorted(missing)}"

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_root_is_compute_scalar_or_known_op(self, plan_xml: str) -> None:
        """Root operator must be a recognised plan operator, not 'Unknown'."""
        operators = _parse(plan_xml)
        assert operators[0].physical_op != "Unknown"

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_estimate_rows_positive(self, plan_xml: str) -> None:
        """Root operator must carry a positive row estimate."""
        operators = _parse(plan_xml)
        assert operators[0].estimate_rows > 0

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_stmt_text_contains_tpch_tables(self, plan_xml: str) -> None:
        """Statement text must reference TPC-H table names."""
        operators = _parse(plan_xml)
        stmt = operators[0].stmt_text or ""
        tpch_names = {
            "lineitem",
            "orders",
            "customer",
            "supplier",
            "nation",
            "region",
            "part",
            "partsupp",
        }
        assert any(t in stmt.lower() for t in tpch_names)

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_cost_pcts_sum_to_100(self, plan_xml: str) -> None:
        """Cost percentages across the full tree must sum to ≈ 100 %."""

        def _collect(node: PlanOperator) -> list[float]:
            return [node.cost_pct, *[p for child in node.children for p in _collect(child)]]

        for root in _parse(plan_xml):
            total = sum(_collect(root))
            assert total == pytest.approx(100.0, abs=1.0)


# ---------------------------------------------------------------------------
# Tree renderer tests
# ---------------------------------------------------------------------------


class TestFabricFixtureTreeRenderer:
    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_renders_without_error(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = _render_tree_str(operators)
        assert output  # non-empty

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_labels_compute_to_control_node_as_move(self, plan_xml: str) -> None:
        """The tree renderer must label 'Compute To Control Node' with '(Move)'."""
        operators = _parse(plan_xml)
        output = _render_tree_str(operators)
        assert "(Move)" in output
        assert "Compute To Control Node" in output

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_renders_hash_match(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = _render_tree_str(operators)
        assert "Hash Match" in output

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_renders_clustered_index_scan(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = _render_tree_str(operators)
        assert "Clustered Index Scan" in output

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_renders_statement_text(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = _render_tree_str(operators)
        assert "Statement 1" in output


# ---------------------------------------------------------------------------
# JSON renderer tests
# ---------------------------------------------------------------------------


class TestFabricFixtureJsonRenderer:
    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_operator_to_dict_is_json_serialisable(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        for root in operators:
            payload = json.dumps(operator_to_dict(root))
            parsed = json.loads(payload)
            assert "physicalOp" in parsed

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_json_contains_compute_to_control_node(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        # Serialise the entire forest to JSON and check the op name is present.
        all_json = json.dumps([operator_to_dict(r) for r in operators])
        assert "Compute To Control Node" in all_json

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_json_contains_hash_match(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        all_json = json.dumps([operator_to_dict(r) for r in operators])
        assert "Hash Match" in all_json


# ---------------------------------------------------------------------------
# Mermaid renderer tests
# ---------------------------------------------------------------------------


class TestFabricFixtureMermaidRenderer:
    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_renders_without_error(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = render_plan_mermaid(operators)
        assert output

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_output_starts_with_flowchart(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = render_plan_mermaid(operators)
        assert "flowchart" in output

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_contains_compute_to_control_node(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = render_plan_mermaid(operators)
        assert "Compute To Control Node" in output

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_contains_hash_match(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = render_plan_mermaid(operators)
        assert "Hash Match" in output


# ---------------------------------------------------------------------------
# DOT renderer tests
# ---------------------------------------------------------------------------


class TestFabricFixtureDotRenderer:
    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_renders_without_error(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = render_plan_dot(operators)
        assert output

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_output_is_valid_digraph(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = render_plan_dot(operators)
        assert "digraph" in output

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_contains_compute_to_control_node(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = render_plan_dot(operators)
        assert "Compute To Control Node" in output

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_contains_hash_match(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        output = render_plan_dot(operators)
        assert "Hash Match" in output


# ---------------------------------------------------------------------------
# SVG renderer tests (dot binary always mocked)
# ---------------------------------------------------------------------------


class TestFabricFixtureSvgRenderer:
    """SVG renderer: assert DOT text is generated; mock the dot binary so CI
    does not require Graphviz installed (matching the pattern in test_plan_svg.py)."""

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_dot_text_contains_compute_to_control_node(self, plan_xml: str) -> None:
        """The DOT text fed to the dot binary must include the Fabric operator."""
        operators = _parse(plan_xml)
        dot_text = render_plan_dot(operators)
        assert "Compute To Control Node" in dot_text

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_svg_renderer_called_with_dot_containing_operator(self, plan_xml: str) -> None:
        """render_plan_svg must pass DOT text with the operator to the dot subprocess."""
        operators = _parse(plan_xml)
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_mock_dot(),
            ) as mock_run,
        ):
            result = render_plan_svg(operators)

        assert result == _FAKE_SVG
        stdin_data: bytes = mock_run.call_args.kwargs["input"]
        assert b"Compute To Control Node" in stdin_data

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_svg_renderer_returns_bytes(self, plan_xml: str) -> None:
        operators = _parse(plan_xml)
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_mock_dot(),
            ),
        ):
            result = render_plan_svg(operators)

        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# HTML renderer tests
# ---------------------------------------------------------------------------


class TestFabricFixtureHtmlRenderer:
    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_renders_without_error(self, plan_xml: str) -> None:
        output = render_plan_html(plan_xml)
        assert output

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_output_is_html(self, plan_xml: str) -> None:
        output = render_plan_html(plan_xml)
        assert "<html" in output.lower()

    @pytest.mark.parametrize("plan_xml", _FIXTURE_CASES)
    def test_raw_xml_embedded_in_output(self, plan_xml: str) -> None:
        """The raw SHOWPLAN_XML must be embedded so the JS library can read it."""
        output = render_plan_html(plan_xml)
        # The HTML renderer embeds (a portion of) the XML; confirm the root element
        # and the Fabric operator so a regression where the full XML is omitted is caught.
        assert "ShowPlanXML" in output
        assert "Compute To Control Node" in output


# ---------------------------------------------------------------------------
# plan508_move: Compute To Control Node (Move) fixture
# ---------------------------------------------------------------------------


class TestPlan508MoveParse:
    """Parser tests specific to the plan508_move.sqlplan fixture.

    This plan exercises ``Compute To Control Node`` with LogicalOp ``Move``
    from a simple GROUP BY on a user table.
    """

    def test_parses_without_error(self) -> None:
        operators = _parse(_MOVE_XML)
        assert len(operators) >= 1

    def test_contains_compute_to_control_node(self) -> None:
        operators = _parse(_MOVE_XML)
        found: set[str] = set()
        for root in operators:
            found |= _all_ops(root)
        assert "Compute To Control Node" in found

    def test_move_logical_op_present(self) -> None:
        """LogicalOp 'Move' must appear alongside the Compute To Control Node."""

        def _all_logical_ops(root: PlanOperator) -> set[str]:
            result: set[str] = {root.logical_op}
            for child in root.children:
                result |= _all_logical_ops(child)
            return result

        operators = _parse(_MOVE_XML)
        logical_ops: set[str] = set()
        for root in operators:
            logical_ops |= _all_logical_ops(root)
        assert "Move" in logical_ops

    def test_root_is_not_unknown(self) -> None:
        operators = _parse(_MOVE_XML)
        assert operators[0].physical_op != "Unknown"

    def test_estimate_rows_positive(self) -> None:
        operators = _parse(_MOVE_XML)
        assert operators[0].estimate_rows > 0

    def test_cost_pcts_sum_to_100(self) -> None:
        def _collect(node: PlanOperator) -> list[float]:
            return [node.cost_pct, *[p for child in node.children for p in _collect(child)]]

        for root in _parse(_MOVE_XML):
            total = sum(_collect(root))
            assert total == pytest.approx(100.0, abs=1.0)


class TestPlan508MoveTreeRenderer:
    def test_renders_without_error(self) -> None:
        operators = _parse(_MOVE_XML)
        output = _render_tree_str(operators)
        assert output

    def test_labels_compute_to_control_node_as_move(self) -> None:
        operators = _parse(_MOVE_XML)
        output = _render_tree_str(operators)
        assert "(Move)" in output
        assert "Compute To Control Node" in output

    def test_renders_hash_match(self) -> None:
        operators = _parse(_MOVE_XML)
        output = _render_tree_str(operators)
        assert "Hash Match" in output


class TestPlan508MoveJsonRenderer:
    def test_is_json_serialisable(self) -> None:
        operators = _parse(_MOVE_XML)
        for root in operators:
            payload = json.dumps(operator_to_dict(root))
            parsed = json.loads(payload)
            assert "physicalOp" in parsed

    def test_json_contains_compute_to_control_node(self) -> None:
        operators = _parse(_MOVE_XML)
        all_json = json.dumps([operator_to_dict(r) for r in operators])
        assert "Compute To Control Node" in all_json


class TestPlan508MoveMermaidRenderer:
    def test_renders_without_error(self) -> None:
        operators = _parse(_MOVE_XML)
        output = render_plan_mermaid(operators)
        assert output

    def test_output_starts_with_flowchart(self) -> None:
        operators = _parse(_MOVE_XML)
        output = render_plan_mermaid(operators)
        assert "flowchart" in output

    def test_contains_compute_to_control_node(self) -> None:
        operators = _parse(_MOVE_XML)
        output = render_plan_mermaid(operators)
        assert "Compute To Control Node" in output


class TestPlan508MoveDotRenderer:
    def test_renders_without_error(self) -> None:
        operators = _parse(_MOVE_XML)
        output = render_plan_dot(operators)
        assert output

    def test_output_is_valid_digraph(self) -> None:
        operators = _parse(_MOVE_XML)
        output = render_plan_dot(operators)
        assert "digraph" in output

    def test_contains_compute_to_control_node(self) -> None:
        operators = _parse(_MOVE_XML)
        output = render_plan_dot(operators)
        assert "Compute To Control Node" in output


class TestPlan508MoveSvgRenderer:
    def test_dot_text_contains_compute_to_control_node(self) -> None:
        operators = _parse(_MOVE_XML)
        dot_text = render_plan_dot(operators)
        assert "Compute To Control Node" in dot_text

    def test_svg_renderer_returns_bytes(self) -> None:
        operators = _parse(_MOVE_XML)
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_mock_dot(),
            ),
        ):
            result = render_plan_svg(operators)
        assert isinstance(result, bytes)


class TestPlan508MoveHtmlRenderer:
    def test_renders_without_error(self) -> None:
        output = render_plan_html(_MOVE_XML)
        assert output

    def test_output_is_html(self) -> None:
        output = render_plan_html(_MOVE_XML)
        assert "<html" in output.lower()

    def test_raw_xml_embedded_in_output(self) -> None:
        output = render_plan_html(_MOVE_XML)
        assert "ShowPlanXML" in output
        assert "Compute To Control Node" in output


# ---------------------------------------------------------------------------
# plan508_parallelism: complex-surface fixture (Concatenation, Filter, etc.)
# ---------------------------------------------------------------------------


# Operator types that must appear in the plan508_parallelism fixture.
# This plan queries sys catalog tables and exercises a broader set of
# operators than the TPC-H plans.
_PARA_EXPECTED_OPS: frozenset[str] = frozenset(
    {
        "Concatenation",
        "Filter",
        "Sort",
        "Hash Match",
        "Clustered Index Scan",
    }
)


class TestPlan508ParallelismParse:
    """Parser tests for the plan508_parallelism.sqlplan fixture.

    This plan exercises Concatenation, Filter, Clustered Index Seek, Index
    Scan, and TopN Sort — operators not all covered by the TPC-H fixtures.
    """

    def test_parses_without_error(self) -> None:
        operators = _parse(_PARA_XML)
        assert len(operators) >= 1

    def test_contains_expected_operator_types(self) -> None:
        operators = _parse(_PARA_XML)
        found: set[str] = set()
        for root in operators:
            found |= _all_ops(root)
        missing = _PARA_EXPECTED_OPS - found
        assert not missing, f"Missing operator types: {sorted(missing)}"

    def test_contains_concatenation(self) -> None:
        operators = _parse(_PARA_XML)
        found: set[str] = set()
        for root in operators:
            found |= _all_ops(root)
        assert "Concatenation" in found

    def test_contains_filter(self) -> None:
        operators = _parse(_PARA_XML)
        found: set[str] = set()
        for root in operators:
            found |= _all_ops(root)
        assert "Filter" in found

    def test_root_is_not_unknown(self) -> None:
        operators = _parse(_PARA_XML)
        assert operators[0].physical_op != "Unknown"

    def test_estimate_rows_positive(self) -> None:
        operators = _parse(_PARA_XML)
        assert operators[0].estimate_rows > 0

    def test_cost_pcts_sum_to_100(self) -> None:
        def _collect(node: PlanOperator) -> list[float]:
            return [node.cost_pct, *[p for child in node.children for p in _collect(child)]]

        for root in _parse(_PARA_XML):
            total = sum(_collect(root))
            assert total == pytest.approx(100.0, abs=1.0)


class TestPlan508ParallelismTreeRenderer:
    def test_renders_without_error(self) -> None:
        operators = _parse(_PARA_XML)
        output = _render_tree_str(operators)
        assert output

    def test_renders_concatenation(self) -> None:
        operators = _parse(_PARA_XML)
        output = _render_tree_str(operators)
        assert "Concatenation" in output

    def test_renders_filter(self) -> None:
        operators = _parse(_PARA_XML)
        output = _render_tree_str(operators)
        assert "Filter" in output

    def test_renders_statement_text(self) -> None:
        operators = _parse(_PARA_XML)
        output = _render_tree_str(operators)
        assert "Statement 1" in output


class TestPlan508ParallelismJsonRenderer:
    def test_is_json_serialisable(self) -> None:
        operators = _parse(_PARA_XML)
        for root in operators:
            payload = json.dumps(operator_to_dict(root))
            parsed = json.loads(payload)
            assert "physicalOp" in parsed

    def test_json_contains_concatenation(self) -> None:
        operators = _parse(_PARA_XML)
        all_json = json.dumps([operator_to_dict(r) for r in operators])
        assert "Concatenation" in all_json


class TestPlan508ParallelismMermaidRenderer:
    def test_renders_without_error(self) -> None:
        operators = _parse(_PARA_XML)
        output = render_plan_mermaid(operators)
        assert output

    def test_output_starts_with_flowchart(self) -> None:
        operators = _parse(_PARA_XML)
        output = render_plan_mermaid(operators)
        assert "flowchart" in output

    def test_contains_concatenation(self) -> None:
        operators = _parse(_PARA_XML)
        output = render_plan_mermaid(operators)
        assert "Concatenation" in output


class TestPlan508ParallelismDotRenderer:
    def test_renders_without_error(self) -> None:
        operators = _parse(_PARA_XML)
        output = render_plan_dot(operators)
        assert output

    def test_output_is_valid_digraph(self) -> None:
        operators = _parse(_PARA_XML)
        output = render_plan_dot(operators)
        assert "digraph" in output

    def test_contains_concatenation(self) -> None:
        operators = _parse(_PARA_XML)
        output = render_plan_dot(operators)
        assert "Concatenation" in output


class TestPlan508ParallelismSvgRenderer:
    def test_dot_text_contains_filter(self) -> None:
        operators = _parse(_PARA_XML)
        dot_text = render_plan_dot(operators)
        assert "Filter" in dot_text

    def test_svg_renderer_returns_bytes(self) -> None:
        operators = _parse(_PARA_XML)
        with (
            patch("fabric_dw.cli._plan_svg.shutil.which", return_value="/usr/bin/dot"),
            patch(
                "fabric_dw.cli._plan_svg.subprocess.run",
                return_value=_mock_dot(),
            ),
        ):
            result = render_plan_svg(operators)
        assert isinstance(result, bytes)


class TestPlan508ParallelismHtmlRenderer:
    def test_renders_without_error(self) -> None:
        output = render_plan_html(_PARA_XML)
        assert output

    def test_output_is_html(self) -> None:
        output = render_plan_html(_PARA_XML)
        assert "<html" in output.lower()

    def test_raw_xml_embedded_in_output(self) -> None:
        output = render_plan_html(_PARA_XML)
        assert "ShowPlanXML" in output
