"""Unit tests for fabric_dw.mcp.tools.warehouses — targeting uncovered branches.

Coverage targets (lines from coverage report):
  60-61  list_warehouses: all_workspaces + FABRIC_MCP_WORKSPACES set → ToolError
  67-76  get_warehouse happy path + FabricError funnel
  96-97  create_warehouse: FabricError / ValueError → tool_err
  109-122 rename_warehouse happy path
  129-138 delete_warehouse happy path (needs FABRIC_MCP_ALLOW_DESTRUCTIVE=1)
  143-153 takeover_warehouse happy path + FabricError funnel

Testing strategy mirrors tests/unit/mcp/test_server.py: tools are invoked
via ``mcp._tool_manager.call_tool(name, args)`` with the ServerContext
patched via ``ctx_patch``.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import NotFoundError
from fabric_dw.models import ItemAccess, Warehouse, WarehouseKind
from tests.unit.mcp.conftest import (
    WH_ID,
    WH_NAME,
    WS_ID,
    WS_NAME,
    make_item_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_warehouse() -> Warehouse:
    return Warehouse.model_validate(
        {
            "id": str(WH_ID),
            "displayName": WH_NAME,
            "workspaceId": str(WS_ID),
            "kind": WarehouseKind.WAREHOUSE,
            "connectionString": "wh.fabric.microsoft.com",
        }
    )


def _make_item_access() -> ItemAccess:
    return ItemAccess.model_validate(
        {
            "principal": {
                "id": str(WH_ID),
                "displayName": "Alice Example",
                "type": "User",
                "userDetails": {"userPrincipalName": "alice@example.com"},
            },
            "itemAccessDetails": {
                "type": "Warehouse",
                "permissions": ["Read"],
                "additionalPermissions": [],
            },
        }
    )


# ---------------------------------------------------------------------------
# list_warehouses — all_workspaces=True with FABRIC_MCP_WORKSPACES set (lines 60-61)
# ---------------------------------------------------------------------------


async def test_list_warehouses_all_workspaces_with_allowlist_raises(ctx_patch) -> None:
    """all_workspaces=True must raise ToolError when FABRIC_MCP_WORKSPACES is configured."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "my-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_warehouses",
            {"all_workspaces": True},
        )

    assert "FABRIC_MCP_WORKSPACES" in str(exc_info.value)


async def test_list_warehouses_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """list_warehouses wraps FabricError into ToolError (lines 60-61)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(side_effect=NotFoundError("workspace not found")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_warehouses",
            {"workspace": WS_NAME},
        )

    assert "NotFoundError" in str(exc_info.value) or "not found" in str(exc_info.value).lower()


async def test_list_warehouses_all_workspaces_true_no_workspace_succeeds(
    ctx_patch,
) -> None:
    """all_workspaces=True with no workspace arg must aggregate without error."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    wh = _make_warehouse()
    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.list_all_workspaces",
            new=AsyncMock(return_value=[wh]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_warehouses",
            {"all_workspaces": True},
        )

    assert isinstance(result, list)
    assert result[0]["id"] == str(WH_ID)


async def test_list_warehouses_no_workspace_no_all_workspaces_raises_clear_error(
    ctx_patch,
) -> None:
    """No workspace and all_workspaces=False must raise a clear ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_warehouses",
            {},
        )

    assert "workspace is required" in str(exc_info.value).lower()
    assert "all_workspaces" in str(exc_info.value).lower()


async def test_list_warehouses_workspace_provided_all_workspaces_false_succeeds(
    mock_ctx, ctx_patch
) -> None:
    """Existing behaviour: list_warehouses with workspace and all_workspaces=False works."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    wh = _make_warehouse()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(return_value=[wh]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_warehouses",
            {"workspace": WS_NAME},
        )

    assert isinstance(result, list)
    assert result[0]["id"] == str(WH_ID)


# ---------------------------------------------------------------------------
# get_warehouse — happy path (lines 67-76)
# ---------------------------------------------------------------------------


async def test_get_warehouse_happy_path(mock_ctx, ctx_patch) -> None:
    """get_warehouse resolves workspace + warehouse and returns a dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    wh = _make_warehouse()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.get_warehouse",
            new=AsyncMock(return_value=wh),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert isinstance(result, dict)
    assert result["id"] == str(WH_ID)
    assert result["displayName"] == WH_NAME
    mock_ctx.resolver.item.assert_called_once()


async def test_get_warehouse_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """get_warehouse wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.get_warehouse",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "get_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert "NotFoundError" in str(exc_info.value) or "not found" in str(exc_info.value).lower()


async def test_get_warehouse_workspace_allowlist_blocks(ctx_patch) -> None:
    """get_warehouse raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "get_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert "allowlist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# create_warehouse — ValueError / FabricError funnel (lines 96-97)
# ---------------------------------------------------------------------------


async def test_create_warehouse_happy_path(mock_ctx, ctx_patch) -> None:
    """create_warehouse resolves workspace and returns a warehouse dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    wh = _make_warehouse()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.create",
            new=AsyncMock(return_value=wh),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_warehouse",
            {"workspace": WS_NAME, "name": WH_NAME},
        )

    assert isinstance(result, dict)
    assert result["id"] == str(WH_ID)


async def test_create_warehouse_value_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """create_warehouse converts ValueError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.create",
            new=AsyncMock(side_effect=ValueError("invalid collation")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_warehouse",
            {"workspace": WS_NAME, "name": WH_NAME, "collation": "bad_collation"},
        )

    assert "invalid collation" in str(exc_info.value)


async def test_create_warehouse_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """create_warehouse wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.create",
            new=AsyncMock(side_effect=NotFoundError("workspace not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_warehouse",
            {"workspace": WS_NAME, "name": WH_NAME},
        )


async def test_create_warehouse_readonly_blocks(ctx_patch) -> None:
    """create_warehouse raises ToolError when FABRIC_MCP_READONLY is set."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "create_warehouse",
            {"workspace": WS_NAME, "name": WH_NAME},
        )


# ---------------------------------------------------------------------------
# rename_warehouse — happy path + error funnel (lines 109-122)
# ---------------------------------------------------------------------------


async def test_rename_warehouse_happy_path(mock_ctx, ctx_patch) -> None:
    """rename_warehouse resolves workspace + warehouse and returns updated dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    renamed = _make_warehouse()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.rename",
            new=AsyncMock(return_value=renamed),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "rename_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "new_name": "wh-v2"},
        )

    assert isinstance(result, dict)
    assert result["id"] == str(WH_ID)


async def test_rename_warehouse_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """rename_warehouse wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.rename",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "rename_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "new_name": "wh-v2"},
        )


async def test_rename_warehouse_readonly_blocks(ctx_patch) -> None:
    """rename_warehouse raises ToolError when FABRIC_MCP_READONLY is set."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "rename_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "new_name": "wh-v2"},
        )


async def test_rename_warehouse_workspace_allowlist_blocks(ctx_patch) -> None:
    """rename_warehouse raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "rename_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "new_name": "wh-v2"},
        )


# ---------------------------------------------------------------------------
# delete_warehouse — happy path + error funnel (lines 129-138)
# ---------------------------------------------------------------------------


async def test_delete_warehouse_happy_path(mock_ctx, ctx_patch) -> None:
    """delete_warehouse resolves warehouse, deletes it, and returns {deleted: True}."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.warehouses.delete",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "delete_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert result["deleted"] is True
    assert result["warehouse_id"] == str(WH_ID)


async def test_delete_warehouse_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """delete_warehouse wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.warehouses.delete",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "delete_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


async def test_delete_warehouse_destructive_guard_blocks(ctx_patch) -> None:
    """delete_warehouse raises ToolError unless FABRIC_MCP_ALLOW_DESTRUCTIVE=1."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    # Build an env dict without FABRIC_MCP_ALLOW_DESTRUCTIVE so the guard fires.
    env_without = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_DESTRUCTIVE"}
    with (
        ctx_patch,
        patch.dict(os.environ, env_without, clear=True),
        pytest.raises(ToolError, match="FABRIC_MCP_ALLOW_DESTRUCTIVE"),
    ):
        await mcp._tool_manager.call_tool(
            "delete_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


async def test_delete_warehouse_readonly_blocks(ctx_patch) -> None:
    """delete_warehouse raises ToolError when FABRIC_MCP_READONLY is set."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "delete_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


# ---------------------------------------------------------------------------
# takeover_warehouse — happy path + error funnel (lines 143-153)
# ---------------------------------------------------------------------------


async def test_takeover_warehouse_happy_path(mock_ctx, ctx_patch) -> None:
    """takeover_warehouse resolves warehouse, calls ownership service, returns dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.ownership.takeover",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "takeover_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert result["taken_over"] is True
    assert result["warehouse_id"] == str(WH_ID)


async def test_takeover_warehouse_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """takeover_warehouse wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.ownership.takeover",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "takeover_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


async def test_takeover_warehouse_readonly_blocks(ctx_patch) -> None:
    """takeover_warehouse raises ToolError when FABRIC_MCP_READONLY is set."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "takeover_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


async def test_takeover_warehouse_workspace_allowlist_blocks(ctx_patch) -> None:
    """takeover_warehouse raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "takeover_warehouse",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )
