"""Plan-to-dict serialiser — shared by cli and mcp.

Provides :func:`operator_to_dict`, which converts a
:class:`~fabric_dw.plan._parse.PlanOperator` tree to a plain
JSON-serialisable dict.  No third-party dependencies.

The Rich terminal renderer (:func:`~fabric_dw.cli._plan_render.render_plan_tree`)
is CLI-presentation-only and lives in ``fabric_dw.cli._plan_render``.

Public API
----------
- :func:`operator_to_dict` — convert a :class:`PlanOperator` tree to a dict.
"""

from __future__ import annotations

from typing import Any

from fabric_dw.plan._parse import PlanOperator

__all__ = ["operator_to_dict"]


def operator_to_dict(node: PlanOperator) -> dict[str, Any]:
    """Convert a :class:`PlanOperator` tree to a plain JSON-serialisable dict.

    Used by both the ``--json`` output path of ``fdw sql plan`` and the MCP
    ``get_query_plan`` tool (``format="tree"`` / ``format="json"``).

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
