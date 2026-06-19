"""Rich terminal tree renderer for SHOWPLAN_XML execution plans.

Converts a list of :class:`~fabric_dw.cli._plan_parse.PlanOperator` trees
(produced by :func:`~fabric_dw.cli._plan_parse.parse_showplan`) into Rich
``Tree`` objects and prints them to a console.

Public API
----------
- :func:`render_plan_tree` — build and print Rich trees for all statements.
- :func:`operator_to_dict` — convert a :class:`PlanOperator` to a plain dict
  for JSON output.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.tree import Tree

from fabric_dw.cli._plan_parse import PlanOperator, humanise_rows

__all__ = [
    "operator_to_dict",
    "render_plan_tree",
]

# ---------------------------------------------------------------------------
# Cost colour thresholds
# ---------------------------------------------------------------------------

_GREEN_THRESHOLD = 10.0  # < 10%  => green
_YELLOW_THRESHOLD = 30.0  # < 30%  => yellow; >= 30% => red


def _cost_colour(pct: float) -> str:
    """Return the Rich colour name for a given cost percentage."""
    if pct < _GREEN_THRESHOLD:
        return "green"
    if pct < _YELLOW_THRESHOLD:
        return "yellow"
    return "red"


# ---------------------------------------------------------------------------
# Label builder
# ---------------------------------------------------------------------------


def _node_label(node: PlanOperator) -> str:
    """Compose the Rich-markup label for a single operator node.

    Format (elements separated by spaces, absent parts omitted):
    ``[bold]PhysicalOp[/bold] (LogicalOp)  rows  [colour]XX.X%[/colour]
    [Parallel]  [!Warnings]``

    Args:
        node: The operator to label.

    Returns:
        A Rich markup string.
    """
    parts: list[str] = []

    # Operator names
    parts.append(f"[bold]{node.physical_op}[/bold]")
    unknown = "Unknown"
    if node.logical_op not in {node.physical_op, unknown, ""}:
        parts.append(f"({node.logical_op})")

    # Estimated rows
    parts.append(humanise_rows(node.estimate_rows))

    # Cost percentage
    colour = _cost_colour(node.cost_pct)
    parts.append(f"[{colour}]{node.cost_pct:.1f}%[/{colour}]")

    # Badges
    if node.parallel:
        parts.append("[cyan][Parallel][/cyan]")
    if node.has_warnings:
        parts.append("[yellow bold][!Warnings][/yellow bold]")

    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Tree builder
# ---------------------------------------------------------------------------


def _build_rich_tree(node: PlanOperator, parent: Tree | None = None) -> Tree:
    """Recursively build a ``rich.tree.Tree`` from a :class:`PlanOperator` tree.

    Args:
        node: The operator node to render.
        parent: The parent Rich ``Tree`` node to attach to; when *None* a new
            root ``Tree`` is created.

    Returns:
        The Rich ``Tree`` node for *node*.
    """
    label = _node_label(node)
    if parent is None:
        rich_node: Tree = Tree(label)
    else:
        rich_node = parent.add(label)

    for child in node.children:
        _build_rich_tree(child, rich_node)

    return rich_node


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_plan_tree(
    operators: list[PlanOperator],
    *,
    console: Console | None = None,
) -> None:
    """Print Rich terminal trees for each statement plan to *console*.

    One tree per :class:`PlanOperator` root (i.e. one per ``StmtSimple``).
    When the list is empty, a plain message is printed instead.

    Args:
        operators: The list of root operator nodes returned by
            :func:`~fabric_dw.cli._plan_parse.parse_showplan`.
        console: Optional Rich ``Console`` to print to; defaults to stdout.
    """
    con = console if console is not None else Console()

    if not operators:
        con.print("[dim]No plan operators found in the SHOWPLAN_XML.[/dim]")
        return

    for idx, root in enumerate(operators):
        # Statement header
        if root.stmt_text:
            con.print(f"\n[bold dim]Statement {idx + 1}:[/bold dim] {root.stmt_text.strip()}")
        elif len(operators) > 1:
            con.print(f"\n[bold dim]Statement {idx + 1}[/bold dim]")

        tree = _build_rich_tree(root)
        con.print(tree)


def operator_to_dict(node: PlanOperator) -> dict[str, Any]:
    """Convert a :class:`PlanOperator` tree to a plain JSON-serialisable dict.

    Used by the ``--json`` output path of ``sql plan``.

    Args:
        node: The operator node to serialise.

    Returns:
        A nested dict with keys matching the :class:`PlanOperator` attributes.
        The ``children`` key contains a list of similarly-structured dicts.
    """
    return {
        "physicalOp": node.physical_op,
        "logicalOp": node.logical_op,
        "nodeId": node.node_id,
        "estimateRows": node.estimate_rows,
        "estimatedTotalSubtreeCost": node.estimated_total_subtree_cost,
        "parallel": node.parallel,
        "hasWarnings": node.has_warnings,
        "costPct": round(node.cost_pct, 2),
        "stmtText": node.stmt_text,
        "children": [operator_to_dict(c) for c in node.children],
    }
