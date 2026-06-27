"""Neutral plan module: shared SHOWPLAN XML parsing and rendering logic.

Both the CLI (``fabric_dw.cli``) and the MCP server (``fabric_dw.mcp``)
import from here.  This module must NOT import from either cli or mcp.
"""

from __future__ import annotations

__all__: list[str] = []
