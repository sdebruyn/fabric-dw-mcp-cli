"""Shared live MCP tool introspection helper for unit tests.

Single source of truth: build a fresh MCP server via the production
registration path (``MCPServer`` + ``register_all``) and enumerate all
registered tools.  Used by:

- ``tests/unit/mcp/test_contract.py`` — contract / invariant checks
- ``tests/unit/mcp/test_server.py`` — registration property checks
"""

from __future__ import annotations

import asyncio
import re

# snake_case naming convention enforced for every MCP tool name.
SNAKE_CASE_RE: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")

# Minimum tool count, guarding against a catastrophic registration drop.
# Deliberately well below the real count (121 at the time of writing) so that
# adding a tool never requires a bump, while a whole domain going missing is
# still caught.  The exact number is never asserted; only this floor is.
MIN_TOOL_COUNT = 90


def collect_live_mcp_tool_names() -> frozenset[str]:
    """Register all MCP tools against a fresh MCPServer; return tool names.

    Uses ``register_all()`` against a throwaway server so that any tool added
    to the production server automatically appears here.  Tool names are
    enumerated via the public ``asyncio.run(mcp.list_tools())`` API to avoid
    relying on private internals.
    """
    from mcp.server.mcpserver import MCPServer  # noqa: PLC0415

    from fabric_dw.mcp.tools import register_all  # noqa: PLC0415

    mcp: MCPServer[None] = MCPServer("coverage-check")
    register_all(mcp)
    return frozenset(tool.name for tool in asyncio.run(mcp.list_tools()))
