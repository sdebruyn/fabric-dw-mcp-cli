"""Rich terminal tree renderer for SHOWPLAN_XML execution plans.

Converts a list of :class:`~fabric_dw.plan._parse.PlanOperator` trees
(produced by :func:`~fabric_dw.plan._parse.parse_showplan`) into Rich
``Tree`` objects and prints them to a console.

Public API
----------
- :func:`render_plan_tree` — build and print Rich trees for all statements.
- :func:`operator_to_dict` — re-exported from :mod:`fabric_dw.plan._render`
  for backward compatibility; new code should import from there directly.
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.tree import Tree

from fabric_dw.plan._parse import PlanOperator, humanise_rows
from fabric_dw.plan._render import operator_to_dict  # re-export

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

    # Operator names (escaped so bracket characters in op names render literally)
    parts.append(f"[bold]{escape(node.physical_op)}[/bold]")
    unknown = "Unknown"
    if node.logical_op not in {node.physical_op, unknown, ""}:
        parts.append(f"({escape(node.logical_op)})")

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
            :func:`~fabric_dw.plan._parse.parse_showplan`.
        console: Optional Rich ``Console`` to print to; defaults to stdout.
    """
    con = console if console is not None else Console()

    if not operators:
        con.print("[dim]No plan operators found in the SHOWPLAN_XML.[/dim]")
        return

    for idx, root in enumerate(operators):
        # Statement header (stmt_text escaped so SQL bracket identifiers render literally)
        if root.stmt_text:
            stmt = escape(root.stmt_text.strip())
            con.print(f"\n[bold dim]Statement {idx + 1}:[/bold dim] {stmt}")
        elif len(operators) > 1:
            con.print(f"\n[bold dim]Statement {idx + 1}[/bold dim]")

        tree = _build_rich_tree(root)
        con.print(tree)
