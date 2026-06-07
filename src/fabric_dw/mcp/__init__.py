"""MCP surface for fabric-dw.

Exports
-------
run : Entry point for the ``fabric-dw-mcp`` console script (re-exported
      from :mod:`fabric_dw.mcp.server` for convenience).
"""

from __future__ import annotations

from fabric_dw.mcp.server import run

__all__ = ["run"]
