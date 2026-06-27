"""Compatibility shim: SHOWPLAN XML parser moved to ``fabric_dw.plan._parse``.

The canonical location of this code is :mod:`fabric_dw.plan._parse`.
All symbols are re-exported here so existing CLI and test imports continue
to work without change.  New code should import directly from
``fabric_dw.plan._parse``.
"""

from __future__ import annotations

from fabric_dw.plan._parse import (  # noqa: F401
    PlanOperator,
    _assign_cost_pct,
    _float_attr,
    _int_attr,
    _parse_rel_op,
    _tag,
    humanise_rows,
    parse_showplan,
)

__all__ = [
    "PlanOperator",
    "humanise_rows",
    "parse_showplan",
]
