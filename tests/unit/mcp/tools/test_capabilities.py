"""Unit tests for fabric_dw.mcp.tools.capabilities — list_capabilities tool."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_capabilities_returns_grouped_dict() -> None:
    """list_capabilities returns a dict[str, list[str]]."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    result = await mcp._tool_manager.call_tool("list_capabilities", {})

    assert isinstance(result, dict)
    for domain, tools in result.items():
        assert isinstance(domain, str)
        assert isinstance(tools, list)
        for tool_name in tools:
            assert isinstance(tool_name, str)


@pytest.mark.asyncio
async def test_list_capabilities_server_domain_contains_itself() -> None:
    """result['server'] must contain 'list_capabilities'."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    result = await mcp._tool_manager.call_tool("list_capabilities", {})

    assert "server" in result
    assert "list_capabilities" in result["server"]


@pytest.mark.asyncio
async def test_list_capabilities_all_registered_tools_are_present() -> None:
    """Every tool from mcp.list_tools() must appear somewhere in the result."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    live_tools = {tool.name for tool in await mcp.list_tools()}
    result = await mcp._tool_manager.call_tool("list_capabilities", {})
    found_tools = {name for tools in result.values() for name in tools}

    assert live_tools == found_tools


@pytest.mark.asyncio
async def test_list_capabilities_values_are_sorted() -> None:
    """Each domain's tool list must be in sorted order."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    result = await mcp._tool_manager.call_tool("list_capabilities", {})

    for domain, tools in result.items():
        assert tools == sorted(tools), f"Tools in domain '{domain}' are not sorted"
