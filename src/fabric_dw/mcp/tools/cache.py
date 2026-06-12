"""MCP tools for cache management."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from fabric_dw.mcp._context import get_context

__all__ = ["register"]


def register(mcp: FastMCP) -> None:
    """Register cache tools against *mcp*."""

    @mcp.tool(name="clear_cache")
    async def clear_cache() -> dict[str, Any]:
        """Erase all cached workspace and item name to UUID mappings."""
        get_context().cache.clear()
        return {"cleared": True}
