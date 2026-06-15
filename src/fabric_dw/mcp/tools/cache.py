"""MCP tools for cache management."""

from __future__ import annotations

import logging
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from fabric_dw.mcp._context import get_context

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register cache tools against *mcp*."""

    @mcp.tool(name="clear_cache")
    async def clear_cache(
        scope: Literal["workspaces", "items", "all"] = "all",
    ) -> dict[str, Any]:
        """Erase cached workspace and item name-to-UUID mappings.

        Args:
            scope: Which portion of the cache to clear.

                - ``"workspaces"`` — clear only workspace name→UUID entries.
                - ``"items"`` — clear only item (warehouse/endpoint) entries.
                - ``"all"`` (default) — clear everything, including the
                  in-memory negative cache on the resolver.

        Returns:
            A dict with keys ``scope`` (the value used), ``workspaces_cleared``
            (number of workspace entries removed), ``items_cleared`` (number
            of item workspace buckets removed), and ``negative_cache_cleared``
            (``True`` when the resolver's negative cache was also wiped).
        """
        _log.info("clear_cache called with scope=%r", scope)
        ctx = get_context()
        cache = ctx.cache
        negative_cache_cleared = False

        # Read current counts via the public API before clearing.
        ws_count, items_count = cache.counts()

        if scope == "workspaces":
            cache.clear_scope("workspaces")
            items_count = 0  # items not touched
        elif scope == "items":
            cache.clear_scope("items")
            ws_count = 0  # workspaces not touched
        else:  # "all"
            cache.clear()
            ctx.resolver.clear_negative_cache()
            negative_cache_cleared = True

        _log.info(
            "clear_cache complete: scope=%r ws=%d items=%d neg=%s",
            scope,
            ws_count,
            items_count,
            negative_cache_cleared,
        )
        return {
            "scope": scope,
            "workspaces_cleared": ws_count,
            "items_cleared": items_count,
            "negative_cache_cleared": negative_cache_cleared,
        }
