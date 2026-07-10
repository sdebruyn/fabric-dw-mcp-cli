"""Tests for the MCP server instructions block and execute_sql steering text.

Guards that prevent silent rot:

1. Every tool name mentioned in the server instructions string resolves against
   the live tool list, so a rename cannot silently break the instructions.
2. The character budget is asserted on the value the server actually ships
   (mcp.instructions), not just the source constant.
3. execute_sql's description opens with a steer toward dedicated alternatives,
   every tool name in that steer is real, and the existing DDL/DML warning
   is still present.
4. Every domain noun named in the 'Also:' line of the instructions corresponds
   to at least one live tool, so a domain cannot be silently removed or renamed
   without the instructions going red.
"""

from __future__ import annotations

import re

import pytest

from fabric_dw.mcp.server import _SERVER_INSTRUCTIONS, mcp

# ---------------------------------------------------------------------------
# Character budget for the server instructions block.
# The text is permanently resident in every client's context, so it must stay
# compact. The original 700-char budget (~175 tokens) covered 18 named tools
# across 5 domains. Adding the domain index for 15 uncovered domains (issue
# #992) brings the text to 783 chars. 900 chars (~225 tokens) provides ~117
# chars of headroom for future additions without requiring a new justification.
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
# underscores) so the snake_case tool-name guard above ignores them. This
# helper parses that line and the guard below checks each domain noun has at
# least one corresponding live tool.
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


def _has_matching_tool(domain_noun: str, live_tools: frozenset[str]) -> bool:
    """Return True when at least one live tool name corresponds to *domain_noun*.

    Normalises the domain noun to snake_case (spaces -> underscores), then
    checks whether the normalised key is a substring of any tool name.  Also
    tries two singular forms to handle English pluralisation:
    - strip a trailing 's' (e.g. 'column masks' -> 'column_mask')
    - replace a trailing 'ies' with 'y' (e.g. 'security policies' -> 'security_policy')
    """
    key = domain_noun.replace(" ", "_")
    return (
        any(key in name for name in live_tools)
        or (key.endswith("s") and any(key[:-1] in name for name in live_tools))
        or (key.endswith("ies") and any((key[:-3] + "y") in name for name in live_tools))
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
    """Every domain noun in the 'Also:' line must match at least one live tool.

    This is the domain-level analogue of test_instructions_tool_names_all_exist.
    When a domain's tools are removed or the domain is renamed, the instructions
    must go red rather than silently pointing at nothing.

    Derivation: _extract_domain_nouns parses the 'Also:' line; _has_matching_tool
    normalises each noun to snake_case and checks for a substring match in live
    tool names (with singular-form fallbacks for English plurals).
    """
    live_tools = frozenset(tool.name for tool in await mcp.list_tools())
    domain_nouns = _extract_domain_nouns(_SERVER_INSTRUCTIONS)
    assert domain_nouns, (
        "No domain nouns found in _SERVER_INSTRUCTIONS. "
        "Expected an 'Also:' line listing domain names."
    )
    missing = [d for d in domain_nouns if not _has_matching_tool(d, live_tools)]
    assert not missing, (
        f"Domain(s) in the 'Also:' line have no corresponding live tools: {missing}. "
        "Update _SERVER_INSTRUCTIONS in src/fabric_dw/mcp/server.py to remove or "
        "rename the affected domain(s)."
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
