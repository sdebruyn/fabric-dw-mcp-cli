"""MCP tool for listing available tools grouped by domain."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from fabric_dw.telemetry_commands import resolve_domain

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register capabilities tools against *mcp*."""

    @mcp.tool(name="list_capabilities")
    async def list_capabilities() -> dict[str, list[str]]:
        """List all available MCP tools grouped by domain.

        Call this tool first to discover what dedicated tools are available
        before falling back to ``execute_sql``.  Dedicated tools return typed,
        structured results and avoid SQL dialect pitfalls.

        Returns:
            A dict mapping domain name to a sorted list of tool names in that
            domain.  The dict itself is sorted by domain key.
        """
        _log.info("list_capabilities called")
        tools = await mcp.list_tools()
        grouped: dict[str, list[str]] = {}
        for tool in tools:
            domain = resolve_domain(tool.name)
            grouped.setdefault(domain, []).append(tool.name)
        for domain, names in grouped.items():
            grouped[domain] = sorted(names)
        return dict(sorted(grouped.items()))
