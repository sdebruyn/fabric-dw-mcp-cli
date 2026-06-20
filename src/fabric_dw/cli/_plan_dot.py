"""Graphviz DOT renderer for SHOWPLAN_XML execution plans.

Converts a list of :class:`~fabric_dw.cli._plan_parse.PlanOperator` trees
(produced by :func:`~fabric_dw.cli._plan_parse.parse_showplan`) into Graphviz
DOT text.  No third-party dependencies — DOT output is plain text.

Public API
----------
- :func:`render_plan_dot` — render all statements to a DOT string.

Viewing the output
------------------
- Pipe to ``dot -Tsvg -o plan.svg`` (requires Graphviz installed).
- Paste into `Graphviz Online <https://dreampuf.github.io/GraphvizOnline/>`_ for
  an interactive preview.
"""

from __future__ import annotations

from fabric_dw.cli._plan_parse import PlanOperator, humanise_rows

__all__ = ["render_plan_dot"]


def _escape_dot_label(text: str) -> str:
    """Escape *text* for use inside a DOT double-quoted label.

    DOT labels are wrapped in ``"…"``.  Inside them:
    - ``\\`` must become ``\\\\`` (backslash must be doubled first)
    - ``"`` must become ``\\"`` (would close the label delimiter)
    - Real newlines/carriage-returns become ``\\n`` (DOT line-break literal).

    Args:
        text: Raw label text.

    Returns:
        Escaped string safe for embedding in ``"…"``.
    """
    # Order matters: escape backslash first so later substitutions are not
    # double-processed.
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _node_label(node: PlanOperator) -> str:
    """Compose the display label for a single operator node.

    Format: ``PhysicalOp [/ LogicalOp]\\nrows  XX.X%``
    LogicalOp is included only when it differs from PhysicalOp and is not
    the placeholder ``"Unknown"``.

    Args:
        node: The operator to label.

    Returns:
        A plain-text label (newline uses ``\\n`` DOT literal).
    """
    unknown = "Unknown"
    op = node.physical_op
    if node.logical_op not in {op, unknown, ""}:
        op = f"{op} / {node.logical_op}"

    rows = humanise_rows(node.estimate_rows)
    cost = f"{node.cost_pct:.1f}%"
    return f"{op}\\n{rows}  {cost}"


def _node_id(node: PlanOperator, stmt_idx: int) -> str:
    """Return a unique DOT node identifier for *node* in statement *stmt_idx*.

    Uses ``S<stmt_idx>N<node_id>`` when NodeId is available (>= 0), otherwise
    falls back to the object's :func:`id` to guarantee uniqueness.

    Args:
        node: The operator node.
        stmt_idx: Zero-based statement index (ensures cross-statement uniqueness).

    Returns:
        A DOT-safe alphanumeric identifier string.
    """
    if node.node_id >= 0:
        return f"S{stmt_idx}N{node.node_id}"
    return f"S{stmt_idx}X{id(node)}"


def _emit_nodes(
    node: PlanOperator,
    stmt_idx: int,
    lines: list[str],
) -> None:
    """Recursively emit node definitions for *node* and its descendants.

    Args:
        node: The operator to emit.
        stmt_idx: Zero-based statement index for unique node IDs.
        lines: Accumulator list; lines are appended in-place.
    """
    nid = _node_id(node, stmt_idx)
    label = _escape_dot_label(_node_label(node))
    lines.append(f'    {nid} [label="{label}"];')
    for child in node.children:
        _emit_nodes(child, stmt_idx, lines)


def _emit_edges(
    node: PlanOperator,
    stmt_idx: int,
    lines: list[str],
) -> None:
    """Recursively emit edge definitions from *node* to each child.

    Args:
        node: The parent operator.
        stmt_idx: Zero-based statement index for unique node IDs.
        lines: Accumulator list; lines are appended in-place.
    """
    parent_id = _node_id(node, stmt_idx)
    for child in node.children:
        child_id = _node_id(child, stmt_idx)
        lines.append(f"    {parent_id} -> {child_id};")
        _emit_edges(child, stmt_idx, lines)


def _render_statement(root: PlanOperator, stmt_idx: int) -> str:
    """Render a single statement's operator tree as a ``digraph`` block.

    Args:
        root: Root :class:`PlanOperator` for this statement.
        stmt_idx: Zero-based statement index (used for unique node IDs and the
            graph name).

    Returns:
        A complete DOT ``digraph`` block as a string.
    """
    lines: list[str] = [f"digraph stmt{stmt_idx} {{"]
    _emit_nodes(root, stmt_idx, lines)
    _emit_edges(root, stmt_idx, lines)
    lines.append("}")
    return "\n".join(lines)


def render_plan_dot(operators: list[PlanOperator]) -> str:
    """Render execution-plan operator trees as Graphviz DOT digraphs.

    One ``digraph`` block is emitted per :class:`PlanOperator` root
    (i.e. one per ``StmtSimple`` in the batch).  Multiple blocks are separated
    by a blank line.  When the list is empty, a DOT comment is returned so the
    output is always non-empty and recognisably DOT text.

    Args:
        operators: The list of root operator nodes returned by
            :func:`~fabric_dw.cli._plan_parse.parse_showplan`.

    Returns:
        A DOT diagram string (UTF-8 text, no trailing newline).
        Pipe the result to ``dot -Tsvg`` (Graphviz) to render an image, or
        paste into `Graphviz Online <https://dreampuf.github.io/GraphvizOnline/>`_.
    """
    if not operators:
        return "// No plan operators found in the SHOWPLAN_XML."

    blocks: list[str] = []
    for idx, root in enumerate(operators):
        blocks.append(_render_statement(root, idx))

    return "\n\n".join(blocks)
