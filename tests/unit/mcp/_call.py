"""Shared MCP tool-call helper for tests/unit/mcp/ and its subpackages.

Every test in this suite invokes an MCP tool through
``mcp._tool_manager.call_tool(name, arguments)`` rather than the public
``FastMCP.call_tool()`` / ``MCPServer.call_tool()`` API. That is deliberate:
the public API always runs with ``convert_result=True`` and wraps the return
value in a ``CallToolResult``, which would force every assertion in the
suite to be rewritten against ``.structured_content`` / ``.content[0].text``
instead of the raw Python value a tool function returns. Going through the
private tool manager (with its ``convert_result`` default of ``False``)
keeps assertions written against plain dicts, lists, and primitives.

Centralising the call here removes ~580 direct references to the private
SDK internal, and gives the suite a single place to update instead of one
per call site. In MCP SDK v2, ``ToolManager.call_tool()`` gains a required
``context`` positional argument; that is the reason this wrapper exists,
so the v2 migration only has to change the body of :func:`call_tool` rather
than every test module.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP


async def call_tool(mcp: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    """Call the MCP tool *name* with *arguments* and return its raw return value.

    Thin wrapper around ``mcp._tool_manager.call_tool(name, arguments)``. See
    the module docstring for why this goes through the private tool manager
    instead of the public, result-wrapping ``call_tool()`` API.
    """
    return await mcp._tool_manager.call_tool(name, arguments)
