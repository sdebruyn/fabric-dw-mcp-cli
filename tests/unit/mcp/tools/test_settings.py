"""Unit tests for fabric_dw.mcp.tools.settings."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import NotFoundError
from fabric_dw.models import WarehouseSettings
from fabric_dw.services.settings import RETENTION_MAX, RETENTION_MIN
from tests.unit.mcp.conftest import (
    WH_NAME,
    WS_NAME,
    make_item_entry,
)

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_settings(
    *,
    result_set_caching: bool = True,
    time_travel_retention_days: int | None = 7,
) -> WarehouseSettings:
    return WarehouseSettings(
        database="my-warehouse",
        result_set_caching=result_set_caching,
        time_travel_retention_days=time_travel_retention_days,
        time_travel_retention_cutoff_date=_NOW,
    )


# ---------------------------------------------------------------------------
# get_warehouse_settings — happy path + error funnel
# ---------------------------------------------------------------------------


async def test_get_warehouse_settings_happy_path(mock_ctx, ctx_patch) -> None:
    """get_warehouse_settings resolves workspace + item and returns a settings dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_settings()
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.settings.get_settings",
            new=AsyncMock(return_value=settings),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_warehouse_settings",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert isinstance(result, dict)
    assert result["database"] == "my-warehouse"
    assert result["result_set_caching"] is True
    assert result["time_travel_retention_days"] == 7


async def test_get_warehouse_settings_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """get_warehouse_settings wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    with (
        ctx_patch,
        patch(
            "fabric_dw.services.settings.get_settings",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_warehouse_settings",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


async def test_get_warehouse_settings_workspace_allowlist_blocks(ctx_patch) -> None:
    """get_warehouse_settings raises ToolError when workspace is not in allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "get_warehouse_settings",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


# ---------------------------------------------------------------------------
# set_result_set_caching — happy path + guards
# ---------------------------------------------------------------------------


async def test_set_result_set_caching_enable(mock_ctx, ctx_patch) -> None:
    """set_result_set_caching calls service with enabled=True and returns settings."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_settings(result_set_caching=True)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_set = AsyncMock(return_value=settings)

    with (
        ctx_patch,
        patch("fabric_dw.services.settings.set_result_set_caching", new=mock_set),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_result_set_caching",
            {"workspace": WS_NAME, "item": WH_NAME, "enabled": True},
        )

    assert isinstance(result, dict)
    assert result["result_set_caching"] is True
    _, kwargs = mock_set.call_args
    assert kwargs.get("enabled") is True
    # The tool must forward the server's active credential mode to the service.
    assert kwargs.get("mode") is mock_ctx.auth_mode


async def test_set_result_set_caching_disable(mock_ctx, ctx_patch) -> None:
    """set_result_set_caching calls service with enabled=False and returns settings."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_settings(result_set_caching=False)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_set = AsyncMock(return_value=settings)

    with (
        ctx_patch,
        patch("fabric_dw.services.settings.set_result_set_caching", new=mock_set),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_result_set_caching",
            {"workspace": WS_NAME, "item": WH_NAME, "enabled": False},
        )

    assert result["result_set_caching"] is False
    _, kwargs = mock_set.call_args
    assert kwargs.get("enabled") is False


async def test_set_result_set_caching_readonly_blocked(ctx_patch) -> None:
    """set_result_set_caching is blocked by FABRIC_MCP_READONLY."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "set_result_set_caching",
            {"workspace": WS_NAME, "item": WH_NAME, "enabled": True},
        )


async def test_set_result_set_caching_workspace_allowlist_blocks(ctx_patch) -> None:
    """set_result_set_caching raises ToolError when workspace is not in allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "set_result_set_caching",
            {"workspace": WS_NAME, "item": WH_NAME, "enabled": True},
        )


async def test_set_result_set_caching_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """set_result_set_caching wraps FabricError from the service into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    with (
        ctx_patch,
        patch(
            "fabric_dw.services.settings.set_result_set_caching",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "set_result_set_caching",
            {"workspace": WS_NAME, "item": WH_NAME, "enabled": True},
        )


# ---------------------------------------------------------------------------
# set_time_travel_retention — happy path + bounds validation + guards
# ---------------------------------------------------------------------------


async def test_set_time_travel_retention_happy_path(mock_ctx, ctx_patch) -> None:
    """set_time_travel_retention calls service with the given days and returns settings."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_settings(time_travel_retention_days=30)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_set = AsyncMock(return_value=settings)

    with (
        ctx_patch,
        patch("fabric_dw.services.settings.set_time_travel_retention", new=mock_set),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_time_travel_retention",
            {"workspace": WS_NAME, "item": WH_NAME, "days": 30},
        )

    assert isinstance(result, dict)
    assert result["time_travel_retention_days"] == 30
    # days may be passed positionally (after target) or by keyword; accept either
    # so the assertion does not break if the signature changes.
    args, kwargs = mock_set.call_args
    passed_days = kwargs.get("days", args[1] if len(args) > 1 else None)
    assert passed_days == 30
    # The tool must forward the server's active credential mode to the service.
    assert kwargs.get("mode") is mock_ctx.auth_mode


async def test_set_time_travel_retention_min_bound(mock_ctx, ctx_patch) -> None:
    """set_time_travel_retention accepts days=RETENTION_MIN (lower bound)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_settings(time_travel_retention_days=RETENTION_MIN)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.settings.set_time_travel_retention",
            new=AsyncMock(return_value=settings),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_time_travel_retention",
            {"workspace": WS_NAME, "item": WH_NAME, "days": RETENTION_MIN},
        )

    assert result["time_travel_retention_days"] == RETENTION_MIN


async def test_set_time_travel_retention_max_bound(mock_ctx, ctx_patch) -> None:
    """set_time_travel_retention accepts days=RETENTION_MAX (upper bound)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_settings(time_travel_retention_days=RETENTION_MAX)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.settings.set_time_travel_retention",
            new=AsyncMock(return_value=settings),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_time_travel_retention",
            {"workspace": WS_NAME, "item": WH_NAME, "days": RETENTION_MAX},
        )

    assert result["time_travel_retention_days"] == RETENTION_MAX


async def test_set_time_travel_retention_below_min_raises_tool_error(ctx_patch) -> None:
    """set_time_travel_retention raises ToolError when days < RETENTION_MIN."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "set_time_travel_retention",
            {"workspace": WS_NAME, "item": WH_NAME, "days": RETENTION_MIN - 1},
        )


async def test_set_time_travel_retention_above_max_raises_tool_error(ctx_patch) -> None:
    """set_time_travel_retention raises ToolError when days > RETENTION_MAX."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "set_time_travel_retention",
            {"workspace": WS_NAME, "item": WH_NAME, "days": RETENTION_MAX + 1},
        )


async def test_set_time_travel_retention_fabric_error_becomes_tool_error(
    mock_ctx, ctx_patch
) -> None:
    """set_time_travel_retention wraps FabricError from the service into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    with (
        ctx_patch,
        patch(
            "fabric_dw.services.settings.set_time_travel_retention",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "set_time_travel_retention",
            {"workspace": WS_NAME, "item": WH_NAME, "days": 30},
        )


async def test_set_time_travel_retention_readonly_blocked(ctx_patch) -> None:
    """set_time_travel_retention is blocked by FABRIC_MCP_READONLY."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "set_time_travel_retention",
            {"workspace": WS_NAME, "item": WH_NAME, "days": 30},
        )


async def test_set_time_travel_retention_workspace_allowlist_blocks(ctx_patch) -> None:
    """set_time_travel_retention raises ToolError when workspace is not in allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "set_time_travel_retention",
            {"workspace": WS_NAME, "item": WH_NAME, "days": 30},
        )


# ---------------------------------------------------------------------------
# Registration check — all three tools exist in the server
# ---------------------------------------------------------------------------


def test_settings_tools_are_registered() -> None:
    """All three settings tools are registered in the MCP server."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "get_warehouse_settings" in tool_names
    assert "set_result_set_caching" in tool_names
    assert "set_time_travel_retention" in tool_names
