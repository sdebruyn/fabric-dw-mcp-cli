"""Shared live MCP tool introspection helper for unit tests.

Single source of truth: build a fresh MCP server via the production
registration path (``InstrumentedFastMCP`` + ``register_all``) and
enumerate all registered tools.  Used by:

- ``tests/unit/test_telemetry_commands.py`` — domain-coverage checks
- ``tests/unit/mcp/test_contract.py`` — contract / invariant checks
- ``tests/unit/mcp/test_server.py`` — registration property checks
"""

from __future__ import annotations

import asyncio
import re

# snake_case naming convention enforced for every MCP tool name.
SNAKE_CASE_RE: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


def collect_live_mcp_tool_names() -> frozenset[str]:
    """Register all MCP tools against a fresh InstrumentedFastMCP; return tool names.

    Uses ``InstrumentedFastMCP`` (the same class the production MCP server
    instantiates) and ``register_all()`` so that any tool added to the server
    automatically appears here.  Tool names are enumerated via the public
    ``asyncio.run(mcp.list_tools())`` API to avoid relying on private internals.
    """
    from fabric_dw.mcp._helpers import InstrumentedFastMCP  # noqa: PLC0415
    from fabric_dw.mcp.tools import register_all  # noqa: PLC0415

    mcp = InstrumentedFastMCP("coverage-check")
    register_all(mcp)
    return frozenset(tool.name for tool in asyncio.run(mcp.list_tools()))
