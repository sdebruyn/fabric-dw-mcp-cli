"""Compatibility shim: Mermaid renderer moved to ``fabric_dw.plan._mermaid``.

The canonical location of this code is :mod:`fabric_dw.plan._mermaid`.
The public API and the private helpers used by tests are re-exported here
(``render_plan_mermaid``, ``_escape_label``, ``_node_id``, ``_node_label``)
so existing CLI and test imports continue to work without change.
New code should import directly from ``fabric_dw.plan._mermaid``.
"""

from __future__ import annotations

from fabric_dw.plan._mermaid import (  # noqa: F401
    _escape_label,
    _node_id,
    _node_label,
    render_plan_mermaid,
)

__all__ = ["render_plan_mermaid"]
