"""Unit tests for fabric_dw.mcp.tools.statistics."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import ItemKindError, NotFoundError
from fabric_dw.models import Statistic, StatisticDetails
from tests.unit.mcp.conftest import (
    WH_NAME,
    WS_NAME,
    make_item_entry,
    make_sql_endpoint_entry,
)

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_statistic() -> Statistic:
    return Statistic(
        name="stat_sales_id",
        qualified_table="dbo.sales",
        column="id",
        auto_created=False,
        user_created=True,
        last_updated=_NOW,
        generation_method=None,
    )


def _make_details() -> StatisticDetails:
    return StatisticDetails(
        stat_header=None,
        density_vector=[],
        histogram=[],
    )


# ---------------------------------------------------------------------------
# list_statistics — happy path + error funnel
# ---------------------------------------------------------------------------


async def test_list_statistics_happy_path(mock_ctx, ctx_patch) -> None:
    """list_statistics resolves workspace + item and returns list of dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    stat = _make_statistic()
    item = make_item_entry()
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.statistics.list_statistics",
            new=AsyncMock(return_value=[stat]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_statistics",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "stat_sales_id"


async def test_list_statistics_with_filters(mock_ctx, ctx_patch) -> None:
    """list_statistics passes schema/table/user_only filters to service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    stat = _make_statistic()
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_list = AsyncMock(return_value=[stat])

    with (
        ctx_patch,
        patch("fabric_dw.services.statistics.list_statistics", new=mock_list),
    ):
        await mcp._tool_manager.call_tool(
            "list_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "schema": "dbo",
                "table": "sales",
                "user_only": True,
            },
        )

    _, kwargs = mock_list.call_args
    assert kwargs.get("schema") == "dbo"
    assert kwargs.get("table") == "sales"
    assert kwargs.get("user_only") is True


async def test_list_statistics_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """list_statistics wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    with (
        ctx_patch,
        patch(
            "fabric_dw.services.statistics.list_statistics",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_statistics",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


async def test_list_statistics_workspace_allowlist_blocks(ctx_patch) -> None:
    """list_statistics raises ToolError when workspace is not in allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "list_statistics",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


# ---------------------------------------------------------------------------
# show_statistics — happy path + error funnel
# ---------------------------------------------------------------------------


async def test_show_statistics_happy_path(mock_ctx, ctx_patch) -> None:
    """show_statistics resolves item and returns StatisticDetails dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    details = _make_details()
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.statistics.show_statistics",
            new=AsyncMock(return_value=details),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "show_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "stat_name": "stat_sales_id",
            },
        )

    assert isinstance(result, dict)
    assert "histogram" in result


async def test_show_statistics_histogram_only(mock_ctx, ctx_patch) -> None:
    """show_statistics passes histogram_only=True to service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_show = AsyncMock(return_value=_make_details())

    with (
        ctx_patch,
        patch("fabric_dw.services.statistics.show_statistics", new=mock_show),
    ):
        await mcp._tool_manager.call_tool(
            "show_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "stat_name": "stat_sales_id",
                "histogram_only": True,
            },
        )

    _, kwargs = mock_show.call_args
    assert kwargs.get("histogram_only") is True


async def test_show_statistics_bad_qualified_table_raises_tool_error(ctx_patch) -> None:
    """show_statistics raises ToolError for a non-qualified table name."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "show_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "nodot",
                "stat_name": "stat",
            },
        )


# ---------------------------------------------------------------------------
# create_statistics — guard checks and happy path
# ---------------------------------------------------------------------------


async def test_create_statistics_happy_path(mock_ctx, ctx_patch) -> None:
    """create_statistics calls service and returns Statistic dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    stat = _make_statistic()
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.statistics.create_statistics",
            new=AsyncMock(return_value=stat),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "column": "id",
                "stat_name": "stat_sales_id",
            },
        )

    assert result["name"] == "stat_sales_id"


async def test_create_statistics_readonly_blocked(ctx_patch) -> None:
    """create_statistics is blocked by FABRIC_MCP_READONLY."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "create_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "column": "id",
                "stat_name": "s",
            },
        )


async def test_create_statistics_sql_endpoint_rejected(mock_ctx, ctx_patch) -> None:
    """create_statistics rejects SQL Endpoint items via the service-layer guard."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.statistics.create_statistics",
            new=AsyncMock(side_effect=ItemKindError("read-only")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "column": "id",
                "stat_name": "s",
            },
        )


# ---------------------------------------------------------------------------
# update_statistics — guard checks and happy path
# ---------------------------------------------------------------------------


async def test_update_statistics_happy_path(mock_ctx, ctx_patch) -> None:
    """update_statistics calls service and returns {"updated": True}."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.statistics.update_statistics",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "update_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "stat_name": "s",
            },
        )

    assert result == {"updated": True}


async def test_update_statistics_readonly_blocked(ctx_patch) -> None:
    """update_statistics is blocked by FABRIC_MCP_READONLY."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "update_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "stat_name": "s",
            },
        )


async def test_update_statistics_sql_endpoint_rejected(mock_ctx, ctx_patch) -> None:
    """update_statistics rejects SQL Endpoint items via the service-layer guard."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.statistics.update_statistics",
            new=AsyncMock(side_effect=ItemKindError("read-only")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "update_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "stat_name": "s",
            },
        )


# ---------------------------------------------------------------------------
# delete_statistics — destructive guard + happy path + endpoint guard
# ---------------------------------------------------------------------------


async def test_delete_statistics_happy_path(mock_ctx, ctx_patch) -> None:
    """delete_statistics calls service and returns {"dropped": True}."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.statistics.drop_statistics",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "delete_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "stat_name": "s",
            },
        )

    assert result == {"dropped": True}


async def test_delete_statistics_destructive_guard_blocks(ctx_patch) -> None:
    """delete_statistics is blocked without FABRIC_MCP_ALLOW_DESTRUCTIVE."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {}, clear=True),  # ensure ALLOW_DESTRUCTIVE is absent
        pytest.raises(ToolError, match="destructive"),
    ):
        await mcp._tool_manager.call_tool(
            "delete_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "stat_name": "s",
            },
        )


async def test_delete_statistics_readonly_blocked(ctx_patch) -> None:
    """delete_statistics is blocked by FABRIC_MCP_READONLY (write guard fires first)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "delete_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "stat_name": "s",
            },
        )


async def test_delete_statistics_sql_endpoint_rejected(mock_ctx, ctx_patch) -> None:
    """delete_statistics rejects SQL Endpoint items via the service-layer guard."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.statistics.drop_statistics",
            new=AsyncMock(side_effect=ItemKindError("read-only")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "delete_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "dbo.sales",
                "stat_name": "s",
            },
        )


async def test_delete_statistics_bad_qualified_table_raises_tool_error(ctx_patch) -> None:
    """delete_statistics raises ToolError for a non-qualified table name."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "delete_statistics",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_table": "nodot",
                "stat_name": "s",
            },
        )
