"""Unit tests for the SHOWPLAN XML parser (_plan_parse) and Rich renderer (_plan_render).

Fixture: a hand-crafted SHOWPLAN_XML document that exercises:
- Hash Match (Inner Join) over two Clustered Index Scans
- One operator marked Parallel="1"
- One operator with a <Warnings> child
- A second StmtSimple (multi-statement batch)
- Realistic EstimateRows / EstimatedTotalSubtreeCost values

The live Fabric API is NOT accessed — all tests run against the hand-built XML.
"""

from __future__ import annotations

import json

import pytest
from rich.console import Console

from fabric_dw.cli._plan_parse import (
    PlanOperator,
    _assign_cost_pct,
    humanise_rows,
    parse_showplan,
)
from fabric_dw.cli._plan_render import operator_to_dict, render_plan_tree

_NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan"

_STMT1_TEXT = "SELECT o.id, c.name FROM dbo.Orders o JOIN dbo.Customers c ON o.cust_id = c.id"
_STMT2_TEXT = "SELECT TOP 1 id FROM dbo.Orders"
_WARNINGS_EXPR = "CONVERT_IMPLICIT(int, [c].[id], 0)"

_FIXTURE_XML = (
    f'<?xml version="1.0" encoding="utf-16"?>'
    f'<ShowPlanXML xmlns="{_NS}" Version="1.6" Build="16.0.0.0">'
    f"<BatchSequence><Batch><Statements>"
    f'<StmtSimple StatementText="{_STMT1_TEXT}"'
    f' StatementId="1" StatementCompId="1" StatementType="SELECT">'
    f'<QueryPlan DegreeOfParallelism="4" MemoryGrant="2048">'
    f'<RelOp NodeId="0" PhysicalOp="Hash Match" LogicalOp="Inner Join"'
    f' EstimateRows="5000" EstimatedTotalSubtreeCost="1.5" Parallel="0">'
    f"<Hash>"
    f'<RelOp NodeId="1" PhysicalOp="Clustered Index Scan"'
    f' LogicalOp="Clustered Index Scan"'
    f' EstimateRows="10000" EstimatedTotalSubtreeCost="0.9" Parallel="1">'
    f'<IndexScan Ordered="false">'
    f"<Warnings>"
    f'<PlanAffectingConvert ConvertIssue="Cardinality Estimate"'
    f' Expression="{_WARNINGS_EXPR}"/>'
    f"</Warnings>"
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
    f'<StmtSimple StatementText="{_STMT2_TEXT}"'
    f' StatementId="2" StatementCompId="2" StatementType="SELECT">'
    f'<QueryPlan DegreeOfParallelism="1">'
    f'<RelOp NodeId="3" PhysicalOp="Clustered Index Scan"'
    f' LogicalOp="Clustered Index Scan"'
    f' EstimateRows="1" EstimatedTotalSubtreeCost="0.003" Parallel="0">'
    f'<IndexScan Ordered="true" ScanDirection="FORWARD"/>'
    f"</RelOp>"
    f"</QueryPlan>"
    f"</StmtSimple>"
    f"</Statements></Batch></BatchSequence>"
    f"</ShowPlanXML>"
)

_EMPTY_STMT_XML = (
    f'<ShowPlanXML xmlns="{_NS}">'
    f"<BatchSequence><Batch><Statements>"
    f'<StmtSimple StatementText="SET NOCOUNT ON"/>'
    f"</Statements></Batch></BatchSequence>"
    f"</ShowPlanXML>"
)

_UNKNOWN_OP_XML = (
    f'<ShowPlanXML xmlns="{_NS}">'
    f"<BatchSequence><Batch><Statements>"
    f'<StmtSimple StatementText="SELECT 1" StatementId="1">'
    f"<QueryPlan>"
    f'<RelOp NodeId="0" PhysicalOp="FabricDistributedShuffle"'
    f' EstimateRows="100" EstimatedTotalSubtreeCost="0.1">'
    f"<GenericOp/>"
    f"</RelOp>"
    f"</QueryPlan>"
    f"</StmtSimple>"
    f"</Statements></Batch></BatchSequence>"
    f"</ShowPlanXML>"
)

# SQL Server-style Parallelism operators (Distribute / Gather / Repartition
# Streams) do not appear in Fabric estimated plans, but may be produced by
# SQL Server or Synapse.  The parser must handle them without crashing.
_PARALLELISM_OPS_XML = (
    f'<ShowPlanXML xmlns="{_NS}">'
    f"<BatchSequence><Batch><Statements>"
    f'<StmtSimple StatementText="SELECT 1" StatementId="1">'
    f"<QueryPlan>"
    f'<RelOp NodeId="0" PhysicalOp="Parallelism" LogicalOp="Gather Streams"'
    f' EstimateRows="100" EstimatedTotalSubtreeCost="0.5" Parallel="0">'
    f"<Parallelism>"
    f'<RelOp NodeId="1" PhysicalOp="Parallelism" LogicalOp="Repartition Streams"'
    f' EstimateRows="100" EstimatedTotalSubtreeCost="0.4" Parallel="1">'
    f"<Parallelism>"
    f'<RelOp NodeId="2" PhysicalOp="Parallelism" LogicalOp="Distribute Streams"'
    f' EstimateRows="100" EstimatedTotalSubtreeCost="0.3" Parallel="1">'
    f"<Parallelism/>"
    f"</RelOp>"
    f"</Parallelism>"
    f"</RelOp>"
    f"</Parallelism>"
    f"</RelOp>"
    f"</QueryPlan>"
    f"</StmtSimple>"
    f"</Statements></Batch></BatchSequence>"
    f"</ShowPlanXML>"
)

_MISSING_ATTRS_XML = (
    f'<ShowPlanXML xmlns="{_NS}">'
    f"<BatchSequence><Batch><Statements>"
    f'<StmtSimple StatementId="1">'
    f"<QueryPlan>"
    f'<RelOp NodeId="0">'
    f"<GenericOp/>"
    f"</RelOp>"
    f"</QueryPlan>"
    f"</StmtSimple>"
    f"</Statements></Batch></BatchSequence>"
    f"</ShowPlanXML>"
)


class TestHumaniseRows:
    def test_billions(self) -> None:
        assert humanise_rows(2_500_000_000) == "2.5B"

    def test_millions(self) -> None:
        assert humanise_rows(1_234_567) == "1.2M"

    def test_thousands(self) -> None:
        assert humanise_rows(12_300) == "12.3K"

    def test_sub_thousand_whole(self) -> None:
        assert humanise_rows(5.0) == "5"

    def test_sub_thousand_fractional(self) -> None:
        assert humanise_rows(5.7) == "5.7"

    def test_zero(self) -> None:
        assert humanise_rows(0.0) == "0"

    def test_exactly_one_thousand(self) -> None:
        assert humanise_rows(1000.0) == "1.0K"

    def test_exactly_one_million(self) -> None:
        assert humanise_rows(1_000_000.0) == "1.0M"


class TestParseShowplan:
    def test_returns_two_statements(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        assert len(operators) == 2

    def test_first_stmt_is_hash_match(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.physical_op == "Hash Match"

    def test_first_stmt_logical_op(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.logical_op == "Inner Join"

    def test_first_stmt_node_id(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.node_id == 0

    def test_first_stmt_estimate_rows(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.estimate_rows == 5000.0

    def test_first_stmt_subtree_cost(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.estimated_total_subtree_cost == pytest.approx(1.5)

    def test_first_stmt_two_children(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert len(root.children) == 2

    def test_first_child_is_parallel(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.children[0].parallel is True

    def test_second_child_not_parallel(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.children[1].parallel is False

    def test_first_child_has_warnings(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.children[0].has_warnings is True

    def test_second_child_no_warnings(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.children[1].has_warnings is False

    def test_root_not_parallel(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.parallel is False

    def test_first_stmt_text(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.stmt_text is not None
        assert "Orders" in root.stmt_text

    def test_second_stmt_is_index_scan(self) -> None:
        second = parse_showplan(_FIXTURE_XML)[1]
        assert second.physical_op == "Clustered Index Scan"

    def test_second_stmt_no_children(self) -> None:
        second = parse_showplan(_FIXTURE_XML)[1]
        assert second.children == []

    def test_second_stmt_text(self) -> None:
        second = parse_showplan(_FIXTURE_XML)[1]
        assert second.stmt_text is not None
        assert "TOP 1" in second.stmt_text

    def test_empty_stmt_returns_empty_list(self) -> None:
        operators = parse_showplan(_EMPTY_STMT_XML)
        assert operators == []

    def test_unknown_physical_op_does_not_crash(self) -> None:
        operators = parse_showplan(_UNKNOWN_OP_XML)
        assert len(operators) == 1
        assert operators[0].physical_op == "FabricDistributedShuffle"

    def test_missing_attributes_use_defaults(self) -> None:
        operators = parse_showplan(_MISSING_ATTRS_XML)
        assert len(operators) == 1
        root = operators[0]
        assert root.physical_op == "Unknown"
        assert root.logical_op == "Unknown"
        assert root.estimate_rows == 0.0
        assert root.estimated_total_subtree_cost == 0.0
        assert root.parallel is False
        assert root.node_id == 0

    def test_missing_all_attributes_node_id_default(self) -> None:
        xml = (
            f'<ShowPlanXML xmlns="{_NS}">'
            f"<BatchSequence><Batch><Statements>"
            f'<StmtSimple StatementId="1">'
            f"<QueryPlan>"
            f'<RelOp PhysicalOp="GenericOp"><GenericOp/></RelOp>'
            f"</QueryPlan>"
            f"</StmtSimple>"
            f"</Statements></Batch></BatchSequence>"
            f"</ShowPlanXML>"
        )
        operators = parse_showplan(xml)
        assert operators[0].node_id == -1


class TestCostPct:
    def test_root_cost_pct_via_parse(self) -> None:
        # root own = 1.5 - (0.9 + 0.5) = 0.1  => 0.1/1.5*100 ≈ 6.67%
        root = parse_showplan(_FIXTURE_XML)[0]
        assert root.cost_pct == pytest.approx(100.0 * 0.1 / 1.5, rel=1e-3)

    def test_first_child_cost_pct(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        child = root.children[0]
        assert child.cost_pct == pytest.approx(60.0)

    def test_second_child_cost_pct(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        child = root.children[1]
        assert child.cost_pct == pytest.approx(33.33, rel=1e-2)

    def test_cost_pcts_sum_to_100(self) -> None:
        """All operator cost percentages in a plan must sum to ≈ 100%."""
        root = parse_showplan(_FIXTURE_XML)[0]
        all_nodes = [root, *root.children]
        total = sum(node.cost_pct for node in all_nodes)
        assert total == pytest.approx(100.0, abs=0.5)

    def test_zero_root_cost_guard(self) -> None:
        root = PlanOperator(
            physical_op="Op",
            estimated_total_subtree_cost=0.0,
        )
        _assign_cost_pct(root, 0.0)
        assert root.cost_pct == 0.0

    def test_leaf_cost_pct_equals_full_subtree_cost(self) -> None:
        second = parse_showplan(_FIXTURE_XML)[1]
        assert second.cost_pct == pytest.approx(100.0)

    def test_cost_pcts_do_not_exceed_100_per_node(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        all_nodes = [root, *root.children]
        for node in all_nodes:
            assert node.cost_pct <= 100.0 + 1e-6


class TestOperatorToDict:
    def test_root_keys_present(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        d = operator_to_dict(root)
        expected_keys = {
            "physicalOp",
            "logicalOp",
            "nodeId",
            "estimateRows",
            "estimatedTotalSubtreeCost",
            "parallel",
            "hasWarnings",
            "costPct",
            "stmtText",
            "children",
        }
        assert set(d.keys()) == expected_keys

    def test_children_serialised(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        d = operator_to_dict(root)
        assert len(d["children"]) == 2

    def test_json_serialisable(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        d = operator_to_dict(root)
        payload = json.dumps(d)
        parsed = json.loads(payload)
        assert parsed["physicalOp"] == "Hash Match"

    def test_parallel_flag_serialised(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        d = operator_to_dict(root)
        assert d["parallel"] is False
        assert d["children"][0]["parallel"] is True

    def test_has_warnings_serialised(self) -> None:
        root = parse_showplan(_FIXTURE_XML)[0]
        d = operator_to_dict(root)
        assert d["children"][0]["hasWarnings"] is True
        assert d["children"][1]["hasWarnings"] is False


def _render_to_str(operators: list[PlanOperator]) -> str:
    """Render *operators* to a string via a record-mode Console."""
    console = Console(record=True, highlight=False, width=120)
    render_plan_tree(operators, console=console)
    return console.export_text()


class TestRenderPlanTree:
    def test_renders_physical_op(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = _render_to_str(operators)
        assert "Hash Match" in output

    def test_renders_logical_op_when_different(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = _render_to_str(operators)
        assert "Inner Join" in output

    def test_renders_estimate_rows_humanised(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = _render_to_str(operators)
        assert "5.0K" in output

    def test_renders_cost_pct(self) -> None:
        # root own cost = 1.5 - (0.9 + 0.5) = 0.1 => 6.7%
        operators = parse_showplan(_FIXTURE_XML)
        output = _render_to_str(operators)
        assert "6.7%" in output

    def test_renders_parallel_badge(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = _render_to_str(operators)
        assert "[Parallel]" in output

    def test_renders_warnings_badge(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = _render_to_str(operators)
        assert "[!Warnings]" in output

    def test_renders_statement_text_header(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = _render_to_str(operators)
        assert "Orders" in output

    def test_renders_multiple_statements(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = _render_to_str(operators)
        assert "Statement 1" in output
        assert "Statement 2" in output

    def test_empty_operators_shows_message(self) -> None:
        output = _render_to_str([])
        assert "No plan operators" in output

    def test_unknown_op_renders_gracefully(self) -> None:
        operators = parse_showplan(_UNKNOWN_OP_XML)
        output = _render_to_str(operators)
        assert "FabricDistributedShuffle" in output

    def test_same_physical_and_logical_op_not_duplicated(self) -> None:
        second = parse_showplan(_FIXTURE_XML)[1]
        output = _render_to_str([second])
        assert "(Clustered Index Scan)" not in output

    def test_bracket_identifiers_not_stripped_from_stmt_text(self) -> None:
        """SQL bracket identifiers in stmt_text must survive Rich rendering."""
        bracket_xml = (
            f'<ShowPlanXML xmlns="{_NS}">'  # noqa: S608
            f"<BatchSequence><Batch><Statements>"
            f'<StmtSimple StatementText="SELECT [name] FROM [dbo].[Orders]"'
            f' StatementId="1">'
            f"<QueryPlan>"
            f'<RelOp NodeId="0" PhysicalOp="Clustered Index Scan"'
            f' LogicalOp="Clustered Index Scan"'
            f' EstimateRows="1" EstimatedTotalSubtreeCost="0.003" Parallel="0">'
            f'<IndexScan Ordered="true"/>'
            f"</RelOp>"
            f"</QueryPlan>"
            f"</StmtSimple>"
            f"</Statements></Batch></BatchSequence>"
            f"</ShowPlanXML>"
        )
        operators = parse_showplan(bracket_xml)
        output = _render_to_str(operators)
        assert "[name]" in output
        assert "[dbo]" in output
        assert "[Orders]" in output


class TestParseShowplanErrors:
    """Error-path tests for parse_showplan and humanise_rows."""

    def test_malformed_xml_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Malformed SHOWPLAN XML"):
            parse_showplan("not valid xml at all")

    def test_truncated_xml_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Malformed SHOWPLAN XML"):
            parse_showplan("<ShowPlanXML")

    def test_valid_xml_not_showplan_returns_empty(self) -> None:
        # Well-formed XML that is not a ShowPlanXML document — no StmtSimple nodes
        operators = parse_showplan("<root><child/></root>")
        assert operators == []


class TestHumaniseRowsEdgeCases:
    def test_nan_returns_string(self) -> None:
        result = humanise_rows(float("nan"))
        assert isinstance(result, str)
        # Must not raise

    def test_inf_returns_string(self) -> None:
        result = humanise_rows(float("inf"))
        assert isinstance(result, str)
        # Must not raise

    def test_neg_inf_returns_string(self) -> None:
        result = humanise_rows(float("-inf"))
        assert isinstance(result, str)
        # Must not raise


class TestParallelismOperatorGracefulDegradation:
    """Parser and renderer must handle SQL Server Parallelism operator subtypes
    (Distribute Streams / Gather Streams / Repartition Streams) without crashing.

    These operators appear in SQL Server and Synapse plans but not in Fabric
    estimated plans.  The parser is generic: it reads PhysicalOp/LogicalOp
    attributes and recurses into child RelOp nodes regardless of operator name,
    so all three subtypes must round-trip correctly.
    """

    def test_parses_gather_streams_without_error(self) -> None:
        operators = parse_showplan(_PARALLELISM_OPS_XML)
        assert len(operators) == 1

    def test_root_is_gather_streams(self) -> None:
        root = parse_showplan(_PARALLELISM_OPS_XML)[0]
        assert root.physical_op == "Parallelism"
        assert root.logical_op == "Gather Streams"

    def test_child_is_repartition_streams(self) -> None:
        root = parse_showplan(_PARALLELISM_OPS_XML)[0]
        assert len(root.children) == 1
        child = root.children[0]
        assert child.physical_op == "Parallelism"
        assert child.logical_op == "Repartition Streams"

    def test_grandchild_is_distribute_streams(self) -> None:
        root = parse_showplan(_PARALLELISM_OPS_XML)[0]
        grandchild = root.children[0].children[0]
        assert grandchild.physical_op == "Parallelism"
        assert grandchild.logical_op == "Distribute Streams"

    def test_parallel_flag_propagated(self) -> None:
        root = parse_showplan(_PARALLELISM_OPS_XML)[0]
        # Outer Gather Streams has Parallel="0", inner nodes have Parallel="1".
        assert root.parallel is False
        assert root.children[0].parallel is True

    def test_render_tree_does_not_crash(self) -> None:
        operators = parse_showplan(_PARALLELISM_OPS_XML)
        output = _render_to_str(operators)
        assert "Parallelism" in output

    def test_render_shows_all_three_subtypes(self) -> None:
        operators = parse_showplan(_PARALLELISM_OPS_XML)
        output = _render_to_str(operators)
        assert "Gather Streams" in output
        assert "Repartition Streams" in output
        assert "Distribute Streams" in output
