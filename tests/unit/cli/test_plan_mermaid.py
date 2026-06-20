"""Unit tests for the Mermaid renderer (_plan_mermaid).

Uses the same fixture XML established in test_plan_parse.py — all tests are
offline, no Fabric API calls.
"""

from __future__ import annotations

from fabric_dw.cli._plan_mermaid import (
    _escape_label,
    _node_id,
    _node_label,
    render_plan_mermaid,
)
from fabric_dw.cli._plan_parse import PlanOperator, parse_showplan

_NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan"

_STMT1_TEXT = "SELECT o.id, c.name FROM dbo.Orders o JOIN dbo.Customers c ON o.cust_id = c.id"
_STMT2_TEXT = "SELECT TOP 1 id FROM dbo.Orders"

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
    f'<IndexScan Ordered="false"><Warnings/></IndexScan>'
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


class TestEscapeLabel:
    def test_plain_text_unchanged(self) -> None:
        assert _escape_label("Hash Match") == "Hash Match"

    def test_double_quote_escaped(self) -> None:
        assert _escape_label('say "hello"') == "say #quot;hello#quot;"

    def test_hash_escaped(self) -> None:
        assert _escape_label("cost #1") == "cost #35;1"

    def test_pipe_escaped(self) -> None:
        assert _escape_label("a | b") == "a #124; b"

    def test_real_newline_stripped(self) -> None:
        # Real \n in an op name would break the node line — must become a space.
        assert _escape_label("line1\nline2") == "line1 line2"

    def test_real_cr_stripped(self) -> None:
        assert _escape_label("line1\rline2") == "line1 line2"

    def test_backslash_n_literal_unchanged(self) -> None:
        # The literal two-character sequence \n (used as Mermaid line-break) must survive.
        assert _escape_label("a\\nb") == "a\\nb"

    def test_empty_string(self) -> None:
        assert _escape_label("") == ""


class TestNodeLabel:
    def test_same_physical_and_logical_shows_only_physical(self) -> None:
        node = PlanOperator(
            physical_op="Clustered Index Scan",
            logical_op="Clustered Index Scan",
            estimate_rows=1000.0,
            cost_pct=50.0,
        )
        label = _node_label(node)
        assert "Clustered Index Scan" in label
        assert "/ Clustered Index Scan" not in label

    def test_different_logical_op_shown(self) -> None:
        node = PlanOperator(
            physical_op="Hash Match",
            logical_op="Inner Join",
            estimate_rows=5000.0,
            cost_pct=6.7,
        )
        label = _node_label(node)
        assert "Hash Match / Inner Join" in label

    def test_unknown_logical_op_not_shown(self) -> None:
        node = PlanOperator(
            physical_op="Sort",
            logical_op="Unknown",
            estimate_rows=100.0,
            cost_pct=10.0,
        )
        label = _node_label(node)
        assert "Unknown" not in label

    def test_humanised_rows_in_label(self) -> None:
        node = PlanOperator(
            physical_op="Sort",
            logical_op="Sort",
            estimate_rows=5000.0,
            cost_pct=10.0,
        )
        label = _node_label(node)
        assert "5.0K" in label

    def test_cost_pct_in_label(self) -> None:
        node = PlanOperator(
            physical_op="Sort",
            logical_op="Sort",
            estimate_rows=100.0,
            cost_pct=33.333,
        )
        label = _node_label(node)
        assert "33.3%" in label

    def test_newline_separator_present(self) -> None:
        node = PlanOperator(
            physical_op="Sort",
            logical_op="Sort",
            estimate_rows=1.0,
            cost_pct=100.0,
        )
        label = _node_label(node)
        assert "\\n" in label


class TestNodeId:
    def test_positive_node_id_uses_stmt_and_node(self) -> None:
        node = PlanOperator(node_id=5)
        assert _node_id(node, 0) == "S0N5"

    def test_node_id_includes_stmt_index(self) -> None:
        node = PlanOperator(node_id=3)
        assert _node_id(node, 2) == "S2N3"

    def test_negative_node_id_uses_object_id(self) -> None:
        node = PlanOperator(node_id=-1)
        result = _node_id(node, 0)
        assert result.startswith("S0X")
        assert str(id(node)) in result

    def test_different_nodes_different_ids(self) -> None:
        a = PlanOperator(node_id=-1)
        b = PlanOperator(node_id=-1)
        assert _node_id(a, 0) != _node_id(b, 0)


class TestRenderPlanMermaid:
    def test_empty_operators_returns_diagnostic_comment(self) -> None:
        output = render_plan_mermaid([])
        assert output.startswith("%%")
        assert "No plan operators" in output

    def test_starts_with_flowchart_td(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        assert output.startswith("flowchart TD")

    def test_two_statements_produce_two_flowchart_blocks(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        assert output.count("flowchart TD") == 2

    def test_blocks_separated_by_blank_line(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        assert "\n\n" in output

    def test_root_node_present(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        assert "Hash Match" in output

    def test_child_nodes_present(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        assert "Clustered Index Scan" in output

    def test_logical_op_shown_when_different(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        assert "Inner Join" in output

    def test_edges_connect_parent_to_children(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        # Root node S0N0 should have arrows to S0N1 and S0N2
        assert "S0N0 --> S0N1" in output
        assert "S0N0 --> S0N2" in output

    def test_no_self_edges(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        for line in output.splitlines():
            if "-->" in line:
                parts = line.strip().split(" --> ")
                assert parts[0] != parts[1], f"Self-edge found: {line}"

    def test_humanised_rows_in_output(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        # Root has EstimateRows=5000 -> "5.0K"
        assert "5.0K" in output

    def test_cost_pct_in_output(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        # root own cost = 1.5-(0.9+0.5)=0.1, pct = 0.1/1.5*100 ≈ 6.7%
        assert "6.7%" in output

    def test_unknown_operator_renders_gracefully(self) -> None:
        operators = parse_showplan(_UNKNOWN_OP_XML)
        output = render_plan_mermaid(operators)
        assert "flowchart TD" in output
        assert "FabricDistributedShuffle" in output

    def test_node_definition_uses_double_quoted_label(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        # Every node definition line should have the pattern: ID["..."]
        node_lines = [
            line
            for line in output.splitlines()
            if line.strip() and "-->" not in line and line.strip() != "flowchart TD"
        ]
        for line in node_lines:
            stripped = line.strip()
            assert "[" in stripped, f"Unexpected node line (missing [): {line!r}"
            assert '"]' in stripped, f'Unexpected node line (missing "]): {line!r}'

    def test_second_statement_node_ids_prefixed_with_s1(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        # Second statement has NodeId=3, so should produce S1N3
        assert "S1N3" in output

    def test_single_statement_returns_single_flowchart(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        single = [operators[0]]
        output = render_plan_mermaid(single)
        assert output.count("flowchart TD") == 1
        assert "\n\n" not in output

    def test_leaf_node_has_no_outgoing_edges(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        # S0N1 and S0N2 are leaves — they should not appear on the left of -->
        lines = output.splitlines()
        edge_sources = {line.strip().split(" --> ")[0] for line in lines if "-->" in line}
        assert "S0N1" not in edge_sources
        assert "S0N2" not in edge_sources

    def test_no_trailing_newline(self) -> None:
        operators = parse_showplan(_FIXTURE_XML)
        output = render_plan_mermaid(operators)
        assert not output.endswith("\n")

    def test_special_chars_in_op_name_escaped(self) -> None:
        node = PlanOperator(
            physical_op='Op "X"',
            logical_op='Op "X"',
            estimate_rows=1.0,
            cost_pct=100.0,
        )
        output = render_plan_mermaid([node])
        assert '"Op "X""' not in output  # raw unescaped double-quote must not appear inside label
        assert "#quot;" in output

    def test_pipe_in_op_name_escaped(self) -> None:
        node = PlanOperator(
            physical_op="Left | Right",
            logical_op="Left | Right",
            estimate_rows=1.0,
            cost_pct=100.0,
        )
        output = render_plan_mermaid([node])
        assert "#124;" in output
        # Raw pipe must not appear inside the node label (between [ and ])
        for line in output.splitlines():
            if '["' in line:
                label_part = line.split('["', 1)[1].rstrip('"]')
                assert "|" not in label_part, f"Raw pipe found in label: {line!r}"

    def test_real_newline_in_op_name_does_not_break_output(self) -> None:
        node = PlanOperator(
            physical_op="Op\nWith\nNewlines",
            logical_op="Op\nWith\nNewlines",
            estimate_rows=1.0,
            cost_pct=100.0,
        )
        output = render_plan_mermaid([node])
        # Every line in the output should be well-formed (node lines stay single-line).
        for line in output.splitlines():
            if '["' in line:
                assert line.count('["') == 1, f"Malformed node line: {line!r}"
