"""Unit tests for the MCP functions tool wrappers.

Target: src/fabric_dw/mcp/tools/functions.py
Goal:   ≥90% branch coverage.

Strategy
--------
- All calls routed via ``mcp._tool_manager.call_tool`` (same path FastMCP uses
  at runtime) so the ``@mcp.tool`` decorator, Pydantic validation, and guards
  are all exercised.
- ``ServerContext`` injected by patching ``fabric_dw.mcp._context._SERVER_CTX``
  with the shared ``mock_ctx`` fixture (defined in conftest).
- Service layer and SQL layer are fully mocked — no real network or SQL.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import FunctionDetails, FunctionKind
from tests.unit.mcp.conftest import (
    WH_NAME,
    WS_ID,
    WS_NAME,
    make_item_entry,
    make_sql_endpoint_entry,
)

# ---------------------------------------------------------------------------
# Module-level import of the mcp server (registers all tools)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _import_server() -> None:
    from fabric_dw.mcp.server import mcp  # noqa: F401, PLC0415


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_fn_details(*, schema: str = "dbo", name: str = "fn_clean") -> FunctionDetails:
    now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    return FunctionDetails(
        schema_name=schema,
        name=name,
        qualified_name=f"{schema}.{name}",
        kind=FunctionKind.SCALAR,
        is_inlineable=True,
        definition="(@x NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN @x END",
        parameters=[],
        created=now,
        modified=now,
    )


# ---------------------------------------------------------------------------
# list_functions — happy path
# ---------------------------------------------------------------------------


async def test_list_functions_happy_path(mock_ctx, ctx_patch) -> None:
    """list_functions resolves workspace + item, returns list of function dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    fn = _make_fn_details()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.functions.list_functions", new=AsyncMock(return_value=[fn])),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_functions",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "fn_clean"
    assert result[0]["schema_name"] == "dbo"
    assert result[0]["qualified_name"] == "dbo.fn_clean"
    assert result[0]["kind"] == "scalar"


async def test_list_functions_with_schema_filter(mock_ctx, ctx_patch) -> None:
    """list_functions passes schema parameter to the service layer."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    fn = _make_fn_details(schema="sales")
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_svc = AsyncMock(return_value=[fn])

    with (
        ctx_patch,
        patch("fabric_dw.services.functions.list_functions", new=mock_svc),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_functions",
            {"workspace": WS_NAME, "item": WH_NAME, "schema": "sales"},
        )

    assert isinstance(result, list)
    _, kwargs = mock_svc.call_args
    assert kwargs.get("schema") == "sales"


async def test_list_functions_empty_result(mock_ctx, ctx_patch) -> None:
    """list_functions returns an empty list when no functions exist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.functions.list_functions", new=AsyncMock(return_value=[])),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_functions",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert result == []


async def test_list_functions_on_sql_endpoint(mock_ctx, ctx_patch) -> None:
    """list_functions must work on SQL Analytics Endpoints — no endpoint guard."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.functions.list_functions", new=AsyncMock(return_value=[])),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_functions",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert result == []


async def test_list_functions_fabric_error_raises(mock_ctx, ctx_patch) -> None:
    """list_functions propagates FabricError as an MCP tool error."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.functions.list_functions",
            new=AsyncMock(side_effect=FabricError("service error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_functions",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


# ---------------------------------------------------------------------------
# get_function — happy path
# ---------------------------------------------------------------------------


async def test_get_function_happy_path(mock_ctx, ctx_patch) -> None:
    """get_function returns a function dict with definition and parameters."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    fn = _make_fn_details()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.functions.get_function", new=AsyncMock(return_value=fn)),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_function",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.fn_clean"},
        )

    assert isinstance(result, dict)
    assert result["name"] == "fn_clean"
    assert result["schema_name"] == "dbo"
    assert result["kind"] == "scalar"
    assert result["is_inlineable"] is True


async def test_get_function_not_found_raises(mock_ctx, ctx_patch) -> None:
    """get_function raises a tool error when the function does not exist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.functions.get_function",
            new=AsyncMock(side_effect=NotFoundError("not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_function",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.nonexistent",
            },
        )


async def test_get_function_invalid_qualified_name_raises(ctx_patch) -> None:
    """get_function raises when qualified_name has no dot."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_function",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "nodot"},
        )


# ---------------------------------------------------------------------------
# create_function — mutating tool
# ---------------------------------------------------------------------------


async def test_create_function_happy_path(mock_ctx, ctx_patch) -> None:
    """create_function creates a function and returns its details dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    fn = _make_fn_details()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.functions.create_function", new=AsyncMock(return_value=fn)),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_function",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.fn_clean",
                "body": "(@x NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN @x END",
            },
        )

    assert isinstance(result, dict)
    assert result["name"] == "fn_clean"


async def test_create_function_on_sql_endpoint_succeeds(mock_ctx, ctx_patch) -> None:
    """create_function on a SQL Analytics Endpoint must succeed — no endpoint guard."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    fn = _make_fn_details()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.functions.create_function", new=AsyncMock(return_value=fn)),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_function",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.fn_clean",
                "body": "(@x INT) RETURNS INT AS BEGIN RETURN @x END",
            },
        )

    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# update_function — mutating tool
# ---------------------------------------------------------------------------


async def test_update_function_happy_path(mock_ctx, ctx_patch) -> None:
    """update_function returns updated function details dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    fn = _make_fn_details()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.functions.update_function", new=AsyncMock(return_value=fn)),
    ):
        result = await mcp._tool_manager.call_tool(
            "update_function",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.fn_clean",
                "body": "(@x NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN UPPER(@x) END",
            },
        )

    assert isinstance(result, dict)
    assert result["name"] == "fn_clean"


# ---------------------------------------------------------------------------
# drop_function — destructive mutating tool
# ---------------------------------------------------------------------------


async def test_drop_function_happy_path(mock_ctx, ctx_patch) -> None:
    """drop_function returns {\"dropped\": True}.

    Requires FABRIC_MCP_ALLOW_DESTRUCTIVE=1 because drop_function is a destructive operation.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch("fabric_dw.services.functions.drop_function", new=AsyncMock(return_value=True)),
    ):
        result = await mcp._tool_manager.call_tool(
            "drop_function",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.fn_clean"},
        )

    assert result == {"dropped": True}


async def test_drop_function_not_found_raises(mock_ctx, ctx_patch) -> None:
    """drop_function propagates NotFoundError (when if_exists is false)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.functions.drop_function",
            new=AsyncMock(side_effect=NotFoundError("not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "drop_function",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.nonexistent"},
        )


async def test_drop_function_if_exists_missing_returns_not_dropped(mock_ctx, ctx_patch) -> None:
    """drop_function with if_exists=True on a missing function returns {"dropped": false}."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch("fabric_dw.services.functions.drop_function", new=AsyncMock(return_value=False)),
    ):
        result = await mcp._tool_manager.call_tool(
            "drop_function",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.fn_nope",
                "if_exists": True,
            },
        )

    assert result == {"dropped": False}


# ---------------------------------------------------------------------------
# list_functions — invalid kind rejected before reaching service
# ---------------------------------------------------------------------------


async def test_list_functions_invalid_kind_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """list_functions raises a ToolError when kind is not a valid value.

    The validation happens inside the tool (via validate_kind) before the
    service is called — so no mock of list_functions is needed.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_functions",
            {"workspace": WS_NAME, "item": WH_NAME, "kind": "multistatement-tvf"},
        )


@pytest.mark.parametrize("bad_kind", ["", "SCALAR", "Scalar", "fn", "tvf", "unknown"])
async def test_list_functions_various_invalid_kinds_raise(
    bad_kind: str, mock_ctx, ctx_patch
) -> None:
    """list_functions rejects all non-canonical kind values."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_functions",
            {"workspace": WS_NAME, "item": WH_NAME, "kind": bad_kind},
        )


@pytest.mark.parametrize("valid_kind", ["scalar", "inline-tvf", "all"])
async def test_list_functions_valid_kinds_accepted(valid_kind: str, mock_ctx, ctx_patch) -> None:
    """list_functions accepts all three valid kind values without error."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.functions.list_functions", new=AsyncMock(return_value=[])),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_functions",
            {"workspace": WS_NAME, "item": WH_NAME, "kind": valid_kind},
        )

    assert result == []
