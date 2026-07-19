"""Tests for the MCP server instructions block and execute_sql steering text.

Guards that prevent silent rot:

1. Every tool name mentioned in the server instructions string resolves against
   the live tool list, so a rename cannot silently break the instructions.
2. The character budget is asserted on the value the server actually ships
   (mcp.instructions), not just the source constant.
3. execute_sql's description opens with a steer toward dedicated alternatives,
   every tool name in that steer is real, and the existing DDL/DML warning
   is still present.
4. Domain guard (existence): every domain noun in the 'Also:' line resolves to
   a known domain via resolve_domain(), so a domain emptied of tools is caught
   even when other domains' tool names happen to share the same text.
5. Domain guard (completeness): every live tool domain is either named in the
   'Also:' line or in the intentional allow-list, so a new domain cannot be
   added silently without the instructions being updated.
"""

from __future__ import annotations

import re

import pytest

from fabric_dw.mcp.server import _SERVER_INSTRUCTIONS, mcp
from fabric_dw.telemetry_commands import resolve_domain

# ---------------------------------------------------------------------------
# Character budget for the server instructions block.
# The text is permanently resident in every client's context, so it must stay
# compact. The original 700-char budget (~175 tokens) covered 18 named tools
# across 5 domains. The domain index added in issue #992 brings the text to
# 795 chars. Adding list_capabilities + server domain (#1018) brings it to
# 822 chars. 900 chars (~225 tokens) provides ~78 chars of headroom for
# future additions without requiring a new justification.
# The budget exists to stop the block growing into a manual.
# ---------------------------------------------------------------------------

_INSTRUCTIONS_CHAR_BUDGET = 900

# ---------------------------------------------------------------------------
# Extract all tool names mentioned in a string.
# The instructions use bare snake_case identifiers (e.g. list_tables); we match
# word-boundary-anchored snake_case tokens so we do not accidentally capture
# prose words.
# ---------------------------------------------------------------------------

_TOOL_NAME_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")


def _tool_names_in_text(text: str) -> set[str]:
    """Return the set of snake_case tokens in *text* that look like tool names."""
    return set(_TOOL_NAME_RE.findall(text))


# ---------------------------------------------------------------------------
# Domain index helpers.
# The 'Also:' line in _SERVER_INSTRUCTIONS lists domain nouns with spaces (no
# underscores) so the snake_case tool-name guard above ignores them entirely.
# Two guards below check the domain index: one asserts every named domain is
# live (existence), the other asserts every live domain is named (completeness).
# Both use resolve_domain() from telemetry_commands - the authoritative mapping.
# ---------------------------------------------------------------------------


def _extract_domain_nouns(text: str) -> list[str]:
    """Return the domain nouns listed in the 'Also:' line of the instructions.

    The line has the form ``Also: noun one, noun two, ...``.  Items are
    stripped of leading/trailing whitespace and trailing punctuation.
    Returns an empty list when no 'Also:' line is present.
    """
    for line in text.splitlines():
        if line.startswith("Also:"):
            _, _, rest = line.partition(":")
            return [item.strip().rstrip(".") for item in rest.split(",") if item.strip()]
    return []


# Domains intentionally absent from the 'Also:' line, with the reason.
# When a new tool domain is added and test_domain_index_completeness fails,
# either add the domain to the 'Also:' line or add it here with an explanation.
_DOMAINS_NAMED_BY_TOOLS: frozenset[str] = frozenset(
    {
        "schemas",  # list_schemas in Discover; delete_schema in Mutate
        "tables",  # list_tables, read_table, etc. in Discover/Read/Inspect/Mutate
        "views",  # list_views, read_view, etc. in Discover/Read/Inspect
        "functions",  # list_functions in Discover
        "procedures",  # list_procedures in Discover
        "sql",  # execute_sql and get_query_plan; the block steers AWAY from this domain
    }
)

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
    """Server instructions must stay within the documented character budget.

    The assertion binds to mcp.instructions - the value actually shipped to
    clients - rather than _SERVER_INSTRUCTIONS, so any transformation applied
    by the constructor is also covered.
    """
    shipped = mcp.instructions or ""
    actual = len(shipped)
    assert actual <= _INSTRUCTIONS_CHAR_BUDGET, (
        f"mcp.instructions is {actual} chars, which exceeds the budget of "
        f"{_INSTRUCTIONS_CHAR_BUDGET}. Trim the text or update the budget with "
        f"a justification comment."
    )
    # Also assert on the source constant so the test catches mismatches
    # between the constant and what the constructor receives.
    const_len = len(_SERVER_INSTRUCTIONS)
    assert const_len <= _INSTRUCTIONS_CHAR_BUDGET, (
        f"_SERVER_INSTRUCTIONS is {const_len} chars, which exceeds the budget of "
        f"{_INSTRUCTIONS_CHAR_BUDGET}."
    )


@pytest.mark.asyncio
async def test_instructions_tool_names_all_exist() -> None:
    """Every tool name mentioned in the instructions must exist in the live tool list.

    This is the drift guard: when a tool is renamed, the test fails immediately
    rather than silently leaving the instructions pointing at a non-existent tool.
    """
    live_tools = frozenset(tool.name for tool in await mcp.list_tools())
    mentioned = _tool_names_in_text(_SERVER_INSTRUCTIONS)
    missing = mentioned - live_tools
    assert not missing, (
        f"The server instructions reference tool(s) that do not exist: {sorted(missing)}. "
        "Update _SERVER_INSTRUCTIONS in src/fabric_dw/mcp/server.py to match "
        "the current tool names."
    )


@pytest.mark.asyncio
async def test_domain_index_all_have_tools() -> None:
    """Every domain noun in the 'Also:' line must resolve to a domain with live tools.

    Uses resolve_domain() from telemetry_commands - the authoritative mapping -
    rather than substring heuristics. This means a domain emptied of all its tools
    causes this test to fail even when other domains' tool names happen to contain
    the same text (e.g. 'warehouses' would not be rescued by restore_warehouse_in_place
    or get_warehouse_settings, which belong to different domains).
    """
    live_domains = frozenset(resolve_domain(t.name) for t in await mcp.list_tools())
    domain_nouns = _extract_domain_nouns(_SERVER_INSTRUCTIONS)
    assert domain_nouns, (
        "No domain nouns found in _SERVER_INSTRUCTIONS. "
        "Expected an 'Also:' line listing domain names."
    )
    missing = [d for d in domain_nouns if d.replace(" ", "_") not in live_domains]
    assert not missing, (
        f"Domain(s) in the 'Also:' line have no corresponding live tools: {missing}. "
        "Update _SERVER_INSTRUCTIONS in src/fabric_dw/mcp/server.py to remove or "
        "rename the affected domain(s)."
    )


@pytest.mark.asyncio
async def test_domain_index_completeness() -> None:
    """Every live tool domain must be named in the instructions or in the allow-list.

    Guards against a new domain of tools being added without updating the
    instructions - the failure mode that issue #992 was opened to fix - so it
    cannot recur silently.

    The allow-list (_DOMAINS_NAMED_BY_TOOLS) covers domains that are already
    represented by concrete tool names in Discover/Read/Inspect/Mutate, or that
    the block deliberately steers away from (the sql domain).  Any new domain
    that does not belong in either category must be added to the 'Also:' line.
    """
    live_domains = frozenset(resolve_domain(t.name) for t in await mcp.list_tools())
    named_in_also = frozenset(
        d.replace(" ", "_") for d in _extract_domain_nouns(_SERVER_INSTRUCTIONS)
    )
    unnamed = live_domains - named_in_also - _DOMAINS_NAMED_BY_TOOLS
    assert not unnamed, (
        f"Tool domain(s) are not represented in the server instructions: {sorted(unnamed)}. "
        "Add them to the 'Also:' line in _SERVER_INSTRUCTIONS, or to "
        "_DOMAINS_NAMED_BY_TOOLS if they are already covered by named tools."
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
async def test_execute_sql_steer_tool_names_all_exist() -> None:
    """Every tool name in execute_sql's steer paragraph must exist in the live list.

    Strict guard: a rename cannot silently leave the steer pointing at a phantom
    tool. The steer paragraph is the text before the WARNING line.
    """
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    description = tools["execute_sql"].description or ""
    live_tools = frozenset(tools.keys())
    # Extract only the steer paragraph (before WARNING) to avoid false positives
    # from the rest of the docstring (e.g. DDL keyword fragments).
    warning_pos = description.find("WARNING")
    steer_text = description[:warning_pos] if warning_pos != -1 else description
    mentioned = _tool_names_in_text(steer_text) - {"execute_sql"}
    missing = mentioned - live_tools
    assert not missing, (
        f"execute_sql steer references tool(s) that do not exist: {sorted(missing)}. "
        "Update the steer paragraph in src/fabric_dw/mcp/tools/sql_exec.py."
    )


@pytest.mark.asyncio
async def test_execute_sql_description_names_three_alternatives() -> None:
    """execute_sql's description must name at least three dedicated alternative tools.

    This weaker bound guards the minimum useful signal: even if the list shrinks
    via legitimate refactoring, at least three concrete names must remain.
    """
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    description = tools["execute_sql"].description or ""
    live_tools = frozenset(tools.keys())
    warning_pos = description.find("WARNING")
    steer_text = description[:warning_pos] if warning_pos != -1 else description
    mentioned = _tool_names_in_text(steer_text) - {"execute_sql"}
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
