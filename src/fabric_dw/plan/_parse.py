"""Pure SHOWPLAN XML parser, zero Rich dependency, fully unit-testable.

Parses the ``ShowPlanXML`` format produced by ``SET SHOWPLAN_XML ON`` (or the
``sql plan`` command) into a small dataclass tree.  Uses stdlib
``xml.etree.ElementTree`` only; no third-party XML libraries required.

Public API
----------
- :class:`PlanOperator`: one node in the operator tree.
- :func:`parse_showplan`: top-level entry point; returns one
  :class:`PlanOperator` per ``StmtSimple`` in the batch.
- :func:`humanise_rows`: format a float row-count as ``1.2K`` / ``3.4M`` etc.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

__all__ = [
    "PlanOperator",
    "humanise_rows",
    "parse_showplan",
]

# ---------------------------------------------------------------------------
# ShowPlan XML namespace
# ---------------------------------------------------------------------------

_NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan"
_TAG = f"{{{_NS}}}"  # prefix for ElementTree qualified names

# ---------------------------------------------------------------------------
# Row-count humanisation thresholds
# ---------------------------------------------------------------------------

_BILLION = 1_000_000_000
_MILLION = 1_000_000
_THOUSAND = 1_000


def _tag(local: str) -> str:
    """Return the namespace-qualified tag name for *local*."""
    return f"{_TAG}{local}"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class PlanOperator:
    """A single operator node in a SHOWPLAN_XML execution plan.

    Attributes:
        physical_op: Value of the ``PhysicalOp`` XML attribute (e.g.
            ``"Hash Match"``, ``"Clustered Index Scan"``, etc.).
            ``"Unknown"`` when the attribute is absent.
        logical_op: Value of the ``LogicalOp`` XML attribute.
            ``"Unknown"`` when absent.
        node_id: Integer ``NodeId`` attribute; ``-1`` when absent.
        estimate_rows: Estimated row count from ``EstimateRows``; ``0.0``
            when absent or unparseable.
        estimated_total_subtree_cost: From ``EstimatedTotalSubtreeCost``;
            ``0.0`` when absent or unparseable.
        parallel: ``True`` when ``Parallel="1"`` on the ``RelOp`` element.
        has_warnings: ``True`` when the operator element contains a
            ``<Warnings>`` child element.
        cost_pct: Percentage of the total plan cost attributable to this
            node's *own* work (i.e. excluding child subtrees).  Populated
            by :func:`_assign_cost_pct` after the tree is built.
        children: Child :class:`PlanOperator` nodes.
        stmt_text: Statement text, only set on the root node of each
            ``StmtSimple`` sub-plan.
    """

    physical_op: str = "Unknown"
    logical_op: str = "Unknown"
    node_id: int = -1
    estimate_rows: float = 0.0
    estimated_total_subtree_cost: float = 0.0
    parallel: bool = False
    has_warnings: bool = False
    cost_pct: float = 0.0
    children: list[PlanOperator] = field(default_factory=list)
    stmt_text: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def humanise_rows(rows: float) -> str:
    """Return a compact human-readable representation of a row-count float.

    Args:
        rows: Estimated row count (may be fractional).

    Returns:
        A compact string:
        - ``"1.0B"`` for >= 1 000 000 000
        - ``"1.2M"`` for >= 1 000 000
        - ``"12.3K"`` for >= 1 000
        - ``"5"`` for < 1 000 (integer if whole-number, 1 dp otherwise)

    Examples:
        >>> humanise_rows(1_234_567)
        '1.2M'
        >>> humanise_rows(12_300)
        '12.3K'
        >>> humanise_rows(5.0)
        '5'
        >>> humanise_rows(5.7)
        '5.7'
    """
    if not math.isfinite(rows):
        return str(rows)
    if rows >= _BILLION:
        return f"{rows / _BILLION:.1f}B"
    if rows >= _MILLION:
        return f"{rows / _MILLION:.1f}M"
    if rows >= _THOUSAND:
        return f"{rows / _THOUSAND:.1f}K"
    # Sub-thousand: suppress trailing .0
    if rows == int(rows):
        return str(int(rows))
    return f"{rows:.1f}"


def _float_attr(element: ET.Element, attr: str) -> float:
    """Return the float value of *attr* on *element*, or ``0.0`` on failure."""
    raw = element.get(attr)
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _int_attr(element: ET.Element, attr: str, default: int = -1) -> int:
    """Return the int value of *attr* on *element*, or *default* on failure."""
    raw = element.get(attr)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Cost percentage assignment
# ---------------------------------------------------------------------------


def _assign_cost_pct(node: PlanOperator, root_total_cost: float) -> None:
    """Recursively set :attr:`PlanOperator.cost_pct` on *node* and its children.

    ``node_own_cost = node.estimated_total_subtree_cost
                      - sum(child.estimated_total_subtree_cost for each child)``

    A node's subtree cost already includes the sum of all children's subtree
    costs, so subtracting the sum gives the cost attributable solely to this
    node's own work.  The whole plan tree then sums to exactly 100 % (within
    floating-point precision).

    For leaf nodes (no children) the subtree cost is entirely the node's own.
    Guarded against zero or missing root cost (no division by zero).
    Clamped to >= 0 to absorb tiny float-noise artefacts.

    Args:
        node: The operator whose cost % is to be set.
        root_total_cost: Total cost of the root node (denominator).
    """
    if node.children:
        children_cost = sum(c.estimated_total_subtree_cost for c in node.children)
    else:
        children_cost = 0.0

    own_cost = max(0.0, node.estimated_total_subtree_cost - children_cost)

    if root_total_cost > 0.0:
        node.cost_pct = own_cost / root_total_cost * 100.0
    else:
        node.cost_pct = 0.0

    for child in node.children:
        _assign_cost_pct(child, root_total_cost)


# ---------------------------------------------------------------------------
# RelOp walker
# ---------------------------------------------------------------------------


def _parse_rel_op(rel_op_elem: ET.Element) -> PlanOperator:
    """Parse a ``<RelOp>`` element (and its descendants) into a :class:`PlanOperator`.

    The concrete operator child element (e.g. ``<HashMatch>``, ``<IndexScan>``)
    is the first child element of the ``<RelOp>``; child ``<RelOp>`` nodes live
    inside that concrete child.  Unknown/future operator elements are handled
    gracefully, and child ``<RelOp>`` nodes are still recursed into.

    Args:
        rel_op_elem: The ``<RelOp>`` XML element to parse.

    Returns:
        A fully-populated :class:`PlanOperator` (children included).
    """
    node = PlanOperator(
        physical_op=rel_op_elem.get("PhysicalOp", "Unknown"),
        logical_op=rel_op_elem.get("LogicalOp", "Unknown"),
        node_id=_int_attr(rel_op_elem, "NodeId"),
        estimate_rows=_float_attr(rel_op_elem, "EstimateRows"),
        estimated_total_subtree_cost=_float_attr(rel_op_elem, "EstimatedTotalSubtreeCost"),
        parallel=rel_op_elem.get("Parallel") == "1",
    )

    # The concrete operator element is the first (and only expected) child element.
    # Child RelOps live inside it.
    for concrete_child in rel_op_elem:
        # Check for a Warnings sibling at this level (Fabric plans may place it here)
        if concrete_child.tag == _tag("Warnings"):
            node.has_warnings = True
            continue

        # Recurse into child RelOps nested inside the concrete operator element.
        for grandchild in concrete_child:
            if grandchild.tag == _tag("Warnings"):
                node.has_warnings = True
            elif grandchild.tag == _tag("RelOp"):
                node.children.append(_parse_rel_op(grandchild))

    return node


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def parse_showplan(xml_text: str) -> list[PlanOperator]:
    """Parse a SHOWPLAN_XML document into a list of operator trees.

    One root :class:`PlanOperator` is returned per ``<StmtSimple>`` element
    in the plan (multi-statement batches produce multiple trees).
    Malformed / empty ``<StmtSimple>`` elements (no ``<QueryPlan>`` /
    ``<RelOp>``) are silently skipped.

    Args:
        xml_text: The raw SHOWPLAN_XML string.

    Returns:
        A (possibly empty) list of root :class:`PlanOperator` nodes, one per
        ``StmtSimple``, with :attr:`~PlanOperator.cost_pct` already set.

    Raises:
        ValueError: When *xml_text* is not valid XML or is not a recognised
            ShowPlan document.
    """
    try:
        root_elem = ET.fromstring(xml_text)  # noqa: S314  # nosec B314  (stdlib, trusted Fabric API XML)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed SHOWPLAN XML: {exc}") from exc

    results: list[PlanOperator] = []

    # Walk ShowPlanXML -> BatchSequence* / Batch -> Statements -> StmtSimple
    # The outermost element may be <ShowPlanXML> (with <BatchSequence>) or
    # <Batch> directly, depending on the Fabric driver response.  We handle both
    # by using .iter() to find all StmtSimple elements anywhere in the tree.
    for stmt in root_elem.iter(_tag("StmtSimple")):
        stmt_text = stmt.get("StatementText")

        # Each StmtSimple has exactly one QueryPlan child.
        query_plan = stmt.find(_tag("QueryPlan"))
        if query_plan is None:
            continue

        # The root RelOp is a direct child of QueryPlan.
        root_rel_op = query_plan.find(_tag("RelOp"))
        if root_rel_op is None:
            continue

        root_node = _parse_rel_op(root_rel_op)
        root_node.stmt_text = stmt_text

        # Assign cost percentages relative to this statement's total cost.
        _assign_cost_pct(root_node, root_node.estimated_total_subtree_cost)

        results.append(root_node)

    return results
