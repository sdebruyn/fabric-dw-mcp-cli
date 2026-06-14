"""Unit tests for the MCP views tool wrappers.

Target: src/fabric_dw/mcp/tools/views.py
Goal:   ≥95 % branch coverage.

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

from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import View
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


def _make_view(*, schema: str = "dbo", name: str = "vw_sales") -> View:
    now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    return View(
        schema_name=schema,
        name=name,
        qualified_name=f"{schema}.{name}",
        definition=f"CREATE VIEW {schema}.{name} AS SELECT 1 AS col",
        created=now,
        modified=now,
    )


# ---------------------------------------------------------------------------
# list_views — happy path
# ---------------------------------------------------------------------------


async def test_list_views_happy_path(mock_ctx, ctx_patch) -> None:
    """list_views resolves workspace + item, returns list of view dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    view = _make_view()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.views.list_views", new=AsyncMock(return_value=[view])),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_views",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "vw_sales"
    assert result[0]["schema_name"] == "dbo"
    assert result[0]["qualified_name"] == "dbo.vw_sales"


async def test_list_views_with_schema_filter(mock_ctx, ctx_patch) -> None:
    """list_views passes schema parameter to the service layer."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    view = _make_view(schema="sales")
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_svc = AsyncMock(return_value=[view])

    with (
        ctx_patch,
        patch("fabric_dw.services.views.list_views", new=mock_svc),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_views",
            {"workspace": WS_NAME, "item": WH_NAME, "schema": "sales"},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    _, kwargs = mock_svc.call_args
    assert kwargs.get("schema") == "sales"


async def test_list_views_empty_result(mock_ctx, ctx_patch) -> None:
    """list_views returns an empty list when no views exist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.views.list_views", new=AsyncMock(return_value=[])),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_views",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert result == []


# ---------------------------------------------------------------------------
# list_views — error paths
# ---------------------------------------------------------------------------


async def test_list_views_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """list_views converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.list_views",
            new=AsyncMock(side_effect=NotFoundError("view not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_views",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


async def test_list_views_no_connection_string_tool_error(mock_ctx, ctx_patch) -> None:
    """list_views raises ToolError when the item has no connection string."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry(connection_string=None))

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_views",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


async def test_list_views_workspace_not_in_allowlist(ctx_patch) -> None:
    """list_views raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_views",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert "allowlist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# list_views — SQL Endpoint support (read-only op — allowed)
# ---------------------------------------------------------------------------


async def test_list_views_sql_endpoint_happy_path(mock_ctx, ctx_patch) -> None:
    """list_views works on SQL Analytics Endpoints (read-only operation)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    view = _make_view()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.views.list_views", new=AsyncMock(return_value=[view])),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_views",
            {"workspace": WS_NAME, "item": "MySqlEndpoint"},
        )

    assert isinstance(result, list)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# read_view — happy path
# ---------------------------------------------------------------------------


async def test_read_view_happy_path(mock_ctx, ctx_patch) -> None:
    """read_view resolves workspace + item, returns columns + rows dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    columns = ["id", "name"]
    rows = [[1, "foo"], [2, "bar"]]

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.read_view",
            new=AsyncMock(return_value=(columns, rows)),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "read_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales"},
        )

    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [[1, "foo"], [2, "bar"]]


async def test_read_view_with_count(mock_ctx, ctx_patch) -> None:
    """read_view passes count to service layer."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_svc = AsyncMock(return_value=(["col"], [[42]]))

    with (
        ctx_patch,
        patch("fabric_dw.services.views.read_view", new=mock_svc),
    ):
        await mcp._tool_manager.call_tool(
            "read_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales", "count": 50},
        )

    _, kwargs = mock_svc.call_args
    assert kwargs.get("count") == 50


# ---------------------------------------------------------------------------
# read_view — error / guard paths
# ---------------------------------------------------------------------------


async def test_read_view_unqualified_name_raises_tool_error(ctx_patch) -> None:
    """read_view raises ToolError when qualified_name has no dot."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "read_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "vw_nodot"},
        )

    assert "schema" in str(exc_info.value).lower() or "view" in str(exc_info.value).lower()


async def test_read_view_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """read_view converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.read_view",
            new=AsyncMock(side_effect=FabricError("SQL error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "read_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales"},
        )


async def test_read_view_workspace_not_in_allowlist(ctx_patch) -> None:
    """read_view raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "read_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales"},
        )


# ---------------------------------------------------------------------------
# get_view — happy path
# ---------------------------------------------------------------------------


async def test_get_view_happy_path(mock_ctx, ctx_patch) -> None:
    """get_view returns the full view definition dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    view = _make_view()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.views.get_view", new=AsyncMock(return_value=view)),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales"},
        )

    assert isinstance(result, dict)
    assert result["name"] == "vw_sales"
    assert result["schema_name"] == "dbo"
    assert "definition" in result


# ---------------------------------------------------------------------------
# get_view — error / guard paths
# ---------------------------------------------------------------------------


async def test_get_view_unqualified_name_raises_tool_error(ctx_patch) -> None:
    """get_view raises ToolError when qualified_name has no dot."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "nodot"},
        )


async def test_get_view_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """get_view converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.get_view",
            new=AsyncMock(side_effect=NotFoundError("view not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales"},
        )


async def test_get_view_workspace_not_in_allowlist(ctx_patch) -> None:
    """get_view raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales"},
        )


# ---------------------------------------------------------------------------
# create_view — happy path
# ---------------------------------------------------------------------------


async def test_create_view_happy_path(mock_ctx, ctx_patch) -> None:
    """create_view resolves workspace + item, calls service, returns view dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    view = _make_view()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.views.create_view", new=AsyncMock(return_value=view)),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "select_body": "SELECT 1 AS col",
            },
        )

    assert isinstance(result, dict)
    assert result["name"] == "vw_sales"


async def test_create_view_clears_negative_cache(mock_ctx, ctx_patch) -> None:
    """create_view must call resolver.clear_negative_cache() after success."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    view = _make_view()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.views.create_view", new=AsyncMock(return_value=view)),
    ):
        await mcp._tool_manager.call_tool(
            "create_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "select_body": "SELECT 1 AS col",
            },
        )

    mock_ctx.resolver.clear_negative_cache.assert_called_once()


# ---------------------------------------------------------------------------
# create_view — error / guard paths
# ---------------------------------------------------------------------------


async def test_create_view_readonly_mode_blocked(ctx_patch) -> None:
    """create_view raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "select_body": "SELECT 1",
            },
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_create_view_unqualified_name_raises_tool_error(ctx_patch) -> None:
    """create_view raises ToolError when qualified_name has no dot."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "nodot",
                "select_body": "SELECT 1",
            },
        )


async def test_create_view_workspace_not_in_allowlist(ctx_patch) -> None:
    """create_view raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "select_body": "SELECT 1",
            },
        )


async def test_create_view_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """create_view converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.create_view",
            new=AsyncMock(side_effect=FabricError("SQL error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "select_body": "SELECT 1",
            },
        )


# ---------------------------------------------------------------------------
# update_view — happy path
# ---------------------------------------------------------------------------


async def test_update_view_happy_path(mock_ctx, ctx_patch) -> None:
    """update_view resolves workspace + item, calls service, returns updated view dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    view = _make_view()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.views.update_view", new=AsyncMock(return_value=view)),
    ):
        result = await mcp._tool_manager.call_tool(
            "update_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "select_body": "SELECT 2 AS col",
            },
        )

    assert isinstance(result, dict)
    assert result["name"] == "vw_sales"


# ---------------------------------------------------------------------------
# update_view — error / guard paths
# ---------------------------------------------------------------------------


async def test_update_view_readonly_mode_blocked(ctx_patch) -> None:
    """update_view raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "update_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "select_body": "SELECT 2",
            },
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_update_view_unqualified_name_raises_tool_error(ctx_patch) -> None:
    """update_view raises ToolError when qualified_name has no dot."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "update_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "nodot",
                "select_body": "SELECT 2",
            },
        )


async def test_update_view_workspace_not_in_allowlist(ctx_patch) -> None:
    """update_view raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "update_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "select_body": "SELECT 2",
            },
        )


async def test_update_view_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """update_view converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.update_view",
            new=AsyncMock(side_effect=FabricError("SQL error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "update_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "select_body": "SELECT 2",
            },
        )


# ---------------------------------------------------------------------------
# drop_view — happy path
# ---------------------------------------------------------------------------


async def test_drop_view_happy_path(mock_ctx, ctx_patch) -> None:
    """drop_view resolves workspace + item, calls service, returns dropped dict.

    Requires FABRIC_MCP_ALLOW_DESTRUCTIVE=1 because drop_view is a destructive operation.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch("fabric_dw.services.views.drop_view", new=AsyncMock(return_value=None)),
    ):
        result = await mcp._tool_manager.call_tool(
            "drop_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales"},
        )

    assert result == {"dropped": True}


# ---------------------------------------------------------------------------
# drop_view — error / guard paths
# ---------------------------------------------------------------------------


async def test_drop_view_readonly_mode_blocked(ctx_patch) -> None:
    """drop_view raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "drop_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales"},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_drop_view_unqualified_name_raises_tool_error(ctx_patch) -> None:
    """drop_view raises ToolError when qualified_name has no dot."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "drop_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "nodot"},
        )


async def test_drop_view_workspace_not_in_allowlist(ctx_patch) -> None:
    """drop_view raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(
            os.environ,
            {"FABRIC_MCP_WORKSPACES": "other-ws", "FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"},
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "drop_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales"},
        )


async def test_drop_view_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """drop_view converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.views.drop_view",
            new=AsyncMock(side_effect=FabricError("SQL error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "drop_view",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.vw_sales"},
        )


# ---------------------------------------------------------------------------
# rename_view — happy path
# ---------------------------------------------------------------------------


async def test_rename_view_happy_path(mock_ctx, ctx_patch) -> None:
    """rename_view resolves workspace + item, calls service, returns renamed view dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    renamed_view = _make_view(name="vw_revenue")
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch("fabric_dw.services.views.rename_view", new=AsyncMock(return_value=renamed_view)),
    ):
        result = await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "new_name": "vw_revenue",
            },
        )

    assert isinstance(result, dict)
    assert result["name"] == "vw_revenue"


# ---------------------------------------------------------------------------
# rename_view — error / guard paths
# ---------------------------------------------------------------------------


async def test_rename_view_readonly_mode_blocked(ctx_patch) -> None:
    """rename_view raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "new_name": "vw_revenue",
            },
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_rename_view_unqualified_name_raises_tool_error(ctx_patch) -> None:
    """rename_view raises ToolError when qualified_name has no dot."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "nodot",
                "new_name": "vw_revenue",
            },
        )


async def test_rename_view_workspace_not_in_allowlist(ctx_patch) -> None:
    """rename_view raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "new_name": "vw_revenue",
            },
        )


async def test_rename_view_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """rename_view converts FabricError to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.rename_view",
            new=AsyncMock(side_effect=FabricError("SQL error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "new_name": "vw_revenue",
            },
        )


async def test_rename_view_value_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """rename_view converts ValueError (e.g. validation error) to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.rename_view",
            new=AsyncMock(side_effect=ValueError("invalid name")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "new_name": "vw_revenue",
            },
        )
