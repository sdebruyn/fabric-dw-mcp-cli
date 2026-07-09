"""Tests for the MCP server instructions block and execute_sql steering text.

Two guards that prevent silent rot:

1. Every tool name mentioned in the server instructions string resolves against
   the live tool list, so a rename cannot silently break the instructions.
2. The character budget is asserted so the instructions never silently balloon
   to the point where they become a significant context cost per request.
3. execute_sql's description opens with a steer toward dedicated alternatives
   and still contains the existing DDL/DML warning.
"""

from __future__ import annotations

import re

import pytest

from fabric_dw.mcp.server import _SERVER_INSTRUCTIONS, mcp

# ---------------------------------------------------------------------------
# Character budget for the server instructions block.
# The text is permanently resident in every client's context, so it must stay
# compact. 600 characters fits on a single screen and covers the full
# capability map plus the preference rule.
# ---------------------------------------------------------------------------

_INSTRUCTIONS_CHAR_BUDGET = 600

# ---------------------------------------------------------------------------
# Extract all tool names mentioned in the instructions.
# The instructions use bare snake_case identifiers (e.g. list_tables); we match
# word-boundary-anchored snake_case tokens so we do not accidentally capture
# prose words.
# ---------------------------------------------------------------------------

_TOOL_NAME_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")


def _tool_names_in_instructions(text: str) -> set[str]:
    """Return the set of snake_case tokens in *text* that look like tool names."""
    return set(_TOOL_NAME_RE.findall(text))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instructions_non_empty() -> None:
    """mcp.instructions must be a non-empty string."""
    assert mcp.instructions, "mcp.instructions must not be None or empty"
    assert isinstance(mcp.instructions, str)
    assert len(mcp.instructions.strip()) > 0


@pytest.mark.asyncio
async def test_instructions_within_character_budget() -> None:
    """Server instructions must stay within the documented character budget."""
    actual = len(_SERVER_INSTRUCTIONS)
    assert actual <= _INSTRUCTIONS_CHAR_BUDGET, (
        f"Server instructions are {actual} chars, which exceeds the budget of "
        f"{_INSTRUCTIONS_CHAR_BUDGET}. Trim the text or update the budget with "
        f"a justification comment."
    )


@pytest.mark.asyncio
async def test_instructions_tool_names_all_exist() -> None:
    """Every tool name mentioned in the instructions must exist in the live tool list.

    This is the drift guard: when a tool is renamed, the test fails immediately
    rather than silently leaving the instructions pointing at a non-existent tool.
    """
    live_tools = frozenset(tool.name for tool in await mcp.list_tools())
    mentioned = _tool_names_in_instructions(_SERVER_INSTRUCTIONS)
    missing = mentioned - live_tools
    assert not missing, (
        f"The server instructions reference tool(s) that do not exist: {sorted(missing)}. "
        "Update _SERVER_INSTRUCTIONS in src/fabric_dw/mcp/server.py to match "
        "the current tool names."
    )


@pytest.mark.asyncio
async def test_execute_sql_description_contains_steer() -> None:
    """execute_sql's description must open with a steer toward dedicated alternatives."""
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert "execute_sql" in tools, "execute_sql must be registered"
    description = tools["execute_sql"].description or ""
    # The steer must appear before the DDL/DML warning.
    steer_pos = description.find("Prefer dedicated tools")
    warning_pos = description.find("WARNING")
    assert steer_pos != -1, (
        "execute_sql description must contain a steer toward dedicated tools "
        "(expected text starting with 'Prefer dedicated tools')."
    )
    assert warning_pos != -1, "execute_sql description must still contain the DDL/DML WARNING text."
    assert steer_pos < warning_pos, (
        "The steer toward dedicated tools must appear before the DDL/DML WARNING "
        "in execute_sql's description."
    )


@pytest.mark.asyncio
async def test_execute_sql_description_names_three_alternatives() -> None:
    """execute_sql's description must name at least three dedicated alternative tools."""
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    description = tools["execute_sql"].description or ""
    live_tools = frozenset(tool.name for tool in tools.values())
    # Count how many real tool names are mentioned in the description
    # (excluding execute_sql itself).
    mentioned = _tool_names_in_instructions(description) - {"execute_sql"}
    alternatives = mentioned & live_tools
    assert len(alternatives) >= 3, (
        f"execute_sql description must name at least 3 dedicated alternative tools; "
        f"found: {sorted(alternatives)}."
    )


@pytest.mark.asyncio
async def test_execute_sql_ddl_warning_unchanged() -> None:
    """execute_sql description must still contain the original DDL/DML warning text."""
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    description = tools["execute_sql"].description or ""
    assert "DDL (DROP," in description, (
        "execute_sql description must retain the original DDL/DML warning text."
    )
    assert "FABRIC_MCP_READONLY" in description, (
        "execute_sql description must retain the FABRIC_MCP_READONLY mention."
    )
