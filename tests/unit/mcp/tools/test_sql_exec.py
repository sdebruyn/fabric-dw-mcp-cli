"""Unit tests for the MCP sql_exec tool wrappers — get_query_plan format param.

Target: src/fabric_dw/mcp/tools/sql_exec.py
Goal:   Cover all four format values, invalid-format error, and back-compat assertion.

Strategy
--------
- All calls routed via ``mcp._tool_manager.call_tool``.
- ``ServerContext`` injected by patching ``fabric_dw.mcp._context._SERVER_CTX``
  with the shared ``mock_ctx`` fixture.
- ``fabric_dw.services.sql_exec.get_plan`` is mocked to return a known SHOWPLAN_XML
  string (reused from tests/unit/cli/test_plan_parse.py fixture).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from tests.unit.mcp.conftest import (
    WH_NAME,
    WS_ID,
    WS_NAME,
    make_item_entry,
)

# ---------------------------------------------------------------------------
# SHOWPLAN_XML fixture — reused from test_plan_parse canonical fixture.
# ---------------------------------------------------------------------------

_NS = "http://schemas.microsoft.com/sqlserver/2004/07/showplan"

_STMT_TEXT = "SELECT o.id FROM dbo.Orders o JOIN dbo.Customers c ON o.cust_id = c.id"

_FIXTURE_XML = (
    f'<?xml version="1.0" encoding="utf-16"?>'
    f'<ShowPlanXML xmlns="{_NS}" Version="1.6" Build="16.0.0.0">'
    f"<BatchSequence><Batch><Statements>"
    f'<StmtSimple StatementText="{_STMT_TEXT}"'
    f' StatementId="1" StatementCompId="1" StatementType="SELECT">'
    f'<QueryPlan DegreeOfParallelism="4" MemoryGrant="2048">'
    f'<RelOp NodeId="0" PhysicalOp="Hash Match" LogicalOp="Inner Join"'
    f' EstimateRows="5000" EstimatedTotalSubtreeCost="1.5" Parallel="0">'
    f"<Hash>"
    f'<RelOp NodeId="1" PhysicalOp="Clustered Index Scan"'
    f' LogicalOp="Clustered Index Scan"'
    f' EstimateRows="10000" EstimatedTotalSubtreeCost="0.9" Parallel="1">'
    f'<IndexScan Ordered="false"/>'
    f"</RelOp>"
    f'<RelOp NodeId="2" PhysicalOp="Clustered Index Scan"'
    f' LogicalOp="Clustered Index Scan"'
    f' EstimateRows="3000" EstimatedTotalSubtreeCost="0.5" Parallel="0">'
    f'<IndexScan Ordered="false"/>'
    f"</RelOp>"
    f"</Hash>"
    f"</RelOp>"
    f"</QueryPlan>"
    f"</StmtSimple>"
    f"</Statements></Batch></BatchSequence>"
    f"</ShowPlanXML>"
)

# ---------------------------------------------------------------------------
# Module-level import of the mcp server (registers all tools)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _import_server() -> None:
    from fabric_dw.mcp.server import mcp  # noqa: F401, PLC0415


# ---------------------------------------------------------------------------
# Shared helper — call get_query_plan via mcp tool manager
# ---------------------------------------------------------------------------


async def _call_get_query_plan(mock_ctx, ctx_patch, **kwargs) -> dict:  # type: ignore[return]
    """Call get_query_plan with mocked service returning _FIXTURE_XML."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    params = {"workspace": WS_NAME, "item": WH_NAME, "query": "SELECT 1", **kwargs}

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_exec.get_plan",
            new=AsyncMock(return_value=_FIXTURE_XML),
        ),
    ):
        return await mcp._tool_manager.call_tool("get_query_plan", params)


# ---------------------------------------------------------------------------
# format="xml" (default) — back-compat assertions
# ---------------------------------------------------------------------------


async def test_get_query_plan_default_returns_plan_xml(mock_ctx, ctx_patch) -> None:
    """Omitting format returns plan_xml — back-compat shape unchanged."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch)

    assert "plan_xml" in result, "plan_xml key must be present for back-compat"
    assert result["plan_xml"] == _FIXTURE_XML


async def test_get_query_plan_xml_format_key_present(mock_ctx, ctx_patch) -> None:
    """format='xml' adds the format key without removing plan_xml."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="xml")

    assert result["format"] == "xml"
    assert "plan_xml" in result
    assert result["plan_xml"] == _FIXTURE_XML


async def test_get_query_plan_xml_plan_xml_unchanged(mock_ctx, ctx_patch) -> None:
    """format='xml' plan_xml value equals the raw XML from the service."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="xml")

    assert result["plan_xml"] == _FIXTURE_XML


# ---------------------------------------------------------------------------
# tree format
# ---------------------------------------------------------------------------


async def test_get_query_plan_tree_format_key(mock_ctx, ctx_patch) -> None:
    """format='tree' returns format='tree' and a 'plan' key."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="tree")

    assert result["format"] == "tree"
    assert "plan" in result


async def test_get_query_plan_tree_is_list(mock_ctx, ctx_patch) -> None:
    """format='tree' plan is a list (one entry per statement)."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="tree")

    assert isinstance(result["plan"], list)
    assert len(result["plan"]) == 1


async def test_get_query_plan_tree_root_physical_op(mock_ctx, ctx_patch) -> None:
    """format='tree' root operator physical op is Hash Match."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="tree")

    root = result["plan"][0]
    assert root["physicalOp"] == "Hash Match"


async def test_get_query_plan_tree_children_present(mock_ctx, ctx_patch) -> None:
    """format='tree' root operator has two children."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="tree")

    root = result["plan"][0]
    assert len(root["children"]) == 2


async def test_get_query_plan_tree_no_xml_key(mock_ctx, ctx_patch) -> None:
    """format='tree' must not contain plan_xml or plan_json."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="tree")

    assert "plan_xml" not in result
    assert "plan_json" not in result


# ---------------------------------------------------------------------------
# json format
# ---------------------------------------------------------------------------


async def test_get_query_plan_json_format_key(mock_ctx, ctx_patch) -> None:
    """format='json' returns format='json' and a 'plan_json' key."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="json")

    assert result["format"] == "json"
    assert "plan_json" in result


async def test_get_query_plan_json_is_string(mock_ctx, ctx_patch) -> None:
    """format='json' plan_json value is a str."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="json")

    assert isinstance(result["plan_json"], str)


async def test_get_query_plan_json_is_valid_json(mock_ctx, ctx_patch) -> None:
    """format='json' plan_json parses as valid JSON."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="json")

    parsed = json.loads(result["plan_json"])
    assert isinstance(parsed, list)
    assert len(parsed) == 1


async def test_get_query_plan_json_root_physical_op(mock_ctx, ctx_patch) -> None:
    """format='json' parsed tree root has correct physicalOp."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="json")

    parsed = json.loads(result["plan_json"])
    assert parsed[0]["physicalOp"] == "Hash Match"


async def test_get_query_plan_json_no_xml_key(mock_ctx, ctx_patch) -> None:
    """format='json' must not contain plan_xml."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="json")

    assert "plan_xml" not in result


# ---------------------------------------------------------------------------
# mermaid format
# ---------------------------------------------------------------------------


async def test_get_query_plan_mermaid_format_key(mock_ctx, ctx_patch) -> None:
    """format='mermaid' returns format='mermaid' and a 'mermaid' key."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="mermaid")

    assert result["format"] == "mermaid"
    assert "mermaid" in result


async def test_get_query_plan_mermaid_is_string(mock_ctx, ctx_patch) -> None:
    """format='mermaid' mermaid value is a str."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="mermaid")

    assert isinstance(result["mermaid"], str)


async def test_get_query_plan_mermaid_contains_flowchart(mock_ctx, ctx_patch) -> None:
    """format='mermaid' output contains Mermaid flowchart header."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="mermaid")

    assert "flowchart" in result["mermaid"]


async def test_get_query_plan_mermaid_contains_hash_match(mock_ctx, ctx_patch) -> None:
    """format='mermaid' output includes the root operator name."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="mermaid")

    assert "Hash Match" in result["mermaid"]


async def test_get_query_plan_mermaid_no_xml_key(mock_ctx, ctx_patch) -> None:
    """format='mermaid' must not contain plan_xml or plan_json."""
    result = await _call_get_query_plan(mock_ctx, ctx_patch, format="mermaid")

    assert "plan_xml" not in result
    assert "plan_json" not in result


# ---------------------------------------------------------------------------
# Invalid format — error path
# ---------------------------------------------------------------------------


async def test_get_query_plan_invalid_format_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """An unsupported format value raises ToolError (FastMCP validates Literal at schema level)."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_exec.get_plan",
            new=AsyncMock(return_value=_FIXTURE_XML),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_query_plan",
            {"workspace": WS_NAME, "item": WH_NAME, "query": "SELECT 1", "format": "svg"},
        )
