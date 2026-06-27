"""Mermaid flowchart renderer for SHOWPLAN_XML execution plans.

Converts a list of :class:`~fabric_dw.plan._parse.PlanOperator` trees
(produced by :func:`~fabric_dw.plan._parse.parse_showplan`) into a
Mermaid ``flowchart TD`` diagram.  No third-party dependencies; Mermaid
output is plain text.

Public API
----------
- :func:`render_plan_mermaid`: render all statements to a Mermaid string.

Viewing the output
------------------
- Paste into `mermaid.live <https://mermaid.live>`_ for an interactive preview.
- GitHub Markdown renders Mermaid diagrams natively inside fenced code blocks::

      ```mermaid
      flowchart TD
      ...
      ```
"""

from __future__ import annotations

import re

from fabric_dw.plan._parse import PlanOperator, humanise_rows

__all__ = ["render_plan_mermaid"]

# Characters that need escaping inside Mermaid node labels (quoted strings).
# Mermaid uses double-quoted labels; we escape:
#   "   -> #quot;  (raw double-quote would break the label delimiter)
#   #   -> #35;    (Mermaid treats # as the start of an HTML entity)
#   |   -> #124;   (pipe breaks some older Mermaid renderers inside labels)
# Real newline/CR characters are stripped (they would break the node line).
_LABEL_UNSAFE = re.compile(r'["#|]')


def _escape_label(text: str) -> str:
    """Escape *text* for use inside a Mermaid double-quoted node label.

    Mermaid node labels are wrapped in ``"..."``.  Inside them:
    - ``"`` must become ``#quot;`` (Mermaid HTML-entity syntax)
    - ``#`` must become ``#35;`` (otherwise Mermaid treats it as an entity prefix)
    - ``|`` must become ``#124;`` (breaks some Mermaid renderers)
    - Real newlines/carriage-returns are stripped (would break the node line).

    Args:
        text: Raw label text.

    Returns:
        Escaped string safe for embedding in ``"..."``.
    """
    # Strip real newlines first (not the literal \n we use as line-break marker).
    text = text.replace("\n", " ").replace("\r", " ")

    def _replace(m: re.Match[str]) -> str:
        ch = m.group(0)
        if ch == '"':
            return "#quot;"
        if ch == "#":
            return "#35;"
        if ch == "|":
            return "#124;"
        return ch  # unreachable given pattern

    return _LABEL_UNSAFE.sub(_replace, text)


def _node_label(node: PlanOperator) -> str:
    """Compose the display label for a single operator node.

    Format: ``PhysicalOp [/ LogicalOp]\\nrows  XX.X%``
    LogicalOp is included only when it differs from PhysicalOp and is not
    the placeholder ``"Unknown"``.

    Args:
        node: The operator to label.

    Returns:
        A plain-text label (newlines use ``\\n`` Mermaid literal).
    """
    unknown = "Unknown"
    op = node.physical_op
    if node.logical_op not in {op, unknown, ""}:
        op = f"{op} / {node.logical_op}"

    rows = humanise_rows(node.estimate_rows)
    cost = f"{node.cost_pct:.1f}%"
    return f"{op}\\n{rows}  {cost}"


def _node_id(node: PlanOperator, stmt_idx: int) -> str:
    """Return a unique Mermaid node identifier for *node* in statement *stmt_idx*.

    Uses ``S<stmt_idx>N<node_id>`` when NodeId is available (>= 0), otherwise
    falls back to the object's :func:`id` to guarantee uniqueness.

    Args:
        node: The operator node.
        stmt_idx: Zero-based statement index (ensures cross-statement uniqueness).

    Returns:
        A Mermaid-safe alphanumeric identifier string.
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
    label = _escape_label(_node_label(node))
    lines.append(f'    {nid}["{label}"]')
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
        lines.append(f"    {parent_id} --> {child_id}")
        _emit_edges(child, stmt_idx, lines)


def _render_statement(root: PlanOperator, stmt_idx: int) -> str:
    """Render a single statement's operator tree as a ``flowchart TD`` block.

    Args:
        root: Root :class:`PlanOperator` for this statement.
        stmt_idx: Zero-based statement index (used for unique node IDs).

    Returns:
        A complete Mermaid ``flowchart TD`` block as a string.
    """
    lines: list[str] = ["flowchart TD"]
    _emit_nodes(root, stmt_idx, lines)
    _emit_edges(root, stmt_idx, lines)
    return "\n".join(lines)


def render_plan_mermaid(operators: list[PlanOperator]) -> str:
    """Render execution-plan operator trees as Mermaid ``flowchart TD`` diagrams.

    One ``flowchart TD`` block is emitted per :class:`PlanOperator` root
    (i.e. one per ``StmtSimple`` in the batch).  Multiple blocks are separated
    by a blank line.  When the list is empty, an empty string is returned.

    Args:
        operators: The list of root operator nodes returned by
            :func:`~fabric_dw.plan._parse.parse_showplan`.

    Returns:
        A Mermaid diagram string (UTF-8 text, no trailing newline).
        Paste the result into `mermaid.live <https://mermaid.live>`_ or a
        GitHub Markdown fenced code block (`` ```mermaid ``) to render.
    """
    if not operators:
        return "%% No plan operators found in the SHOWPLAN_XML."

    blocks: list[str] = []
    for idx, root in enumerate(operators):
        blocks.append(_render_statement(root, idx))

    return "\n\n".join(blocks)
