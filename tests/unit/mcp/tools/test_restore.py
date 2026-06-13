"""Unit tests for the MCP restore-point tool wrappers.

Coverage targets
----------------
``src/fabric_dw/mcp/tools/restore.py``

Tool list
~~~~~~~~~
* ``list_restore_points``    — happy path, FabricError → ToolError, workspace guard
* ``get_restore_point``      — happy path, FabricError → ToolError, workspace guard
* ``create_restore_point``   — happy path, FabricError → ToolError, readonly guard
* ``update_restore_point``   — happy path, ValueError → ToolError, FabricError → ToolError,
  readonly guard
* ``delete_restore_point``   — happy path, FabricError → ToolError, readonly + destructive guard
* ``restore_warehouse_in_place`` — happy path, FabricError → ToolError, readonly + destructive
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import RestorePoint
from tests.unit.mcp.conftest import (
    WH_NAME,
    WS_ID,
    WS_NAME,
    make_item_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RP_ID = "1726617378000"
_RP_ID_2 = "1726617490000"


def _make_restore_point(
    rp_id: str = _RP_ID,
    *,
    name: str | None = "rp-1",
    creation_mode: str | None = "UserDefined",
) -> RestorePoint:
    return RestorePoint.model_validate(
        {
            "id": rp_id,
            "displayName": name,
            "creationMode": creation_mode,
            "eventDateTime": "2026-01-01T00:00:00",
        }
    )


# ---------------------------------------------------------------------------
# list_restore_points
# ---------------------------------------------------------------------------


async def test_list_restore_points_happy_path(mock_ctx, ctx_patch) -> None:
    """list_restore_points returns a list of serialised RestorePoint dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    rp = _make_restore_point()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.restore.list_points",
            new=AsyncMock(return_value=[rp]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_restore_points",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == _RP_ID
    assert result[0]["displayName"] == "rp-1"


async def test_list_restore_points_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """list_restore_points converts FabricError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.restore.list_points",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_restore_points",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


async def test_list_restore_points_workspace_guard(ctx_patch) -> None:
    """list_restore_points raises ToolError when workspace is not in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_restore_points",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert "allowlist" in str(exc_info.value).lower()


async def test_list_restore_points_multiple(mock_ctx, ctx_patch) -> None:
    """list_restore_points serialises every RestorePoint in the result list."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    rp1 = _make_restore_point(_RP_ID)
    rp2 = _make_restore_point(_RP_ID_2, name=None, creation_mode="SystemCreated")
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.restore.list_points",
            new=AsyncMock(return_value=[rp1, rp2]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_restore_points",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert len(result) == 2
    assert result[1]["id"] == _RP_ID_2
    assert result[1]["creationMode"] == "SystemCreated"


# ---------------------------------------------------------------------------
# get_restore_point
# ---------------------------------------------------------------------------


async def test_get_restore_point_happy_path(mock_ctx, ctx_patch) -> None:
    """get_restore_point returns a single serialised RestorePoint dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    rp = _make_restore_point()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.restore.get_point",
            new=AsyncMock(return_value=rp),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert isinstance(result, dict)
    assert result["id"] == _RP_ID
    assert result["displayName"] == "rp-1"


async def test_get_restore_point_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """get_restore_point converts FabricError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.restore.get_point",
            new=AsyncMock(side_effect=NotFoundError("restore point not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )


async def test_get_restore_point_workspace_guard(ctx_patch) -> None:
    """get_restore_point raises ToolError when workspace is not in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "get_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert "allowlist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# create_restore_point
# ---------------------------------------------------------------------------


async def test_create_restore_point_happy_path(mock_ctx, ctx_patch) -> None:
    """create_restore_point returns a serialised RestorePoint dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    rp = _make_restore_point()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.restore.create_point",
            new=AsyncMock(return_value=rp),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert isinstance(result, dict)
    assert result["id"] == _RP_ID


async def test_create_restore_point_with_name_and_description(mock_ctx, ctx_patch) -> None:
    """create_restore_point forwards name and description to the service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    rp = _make_restore_point(name="my-rp")
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_create = AsyncMock(return_value=rp)

    with (
        ctx_patch,
        patch("fabric_dw.services.restore.create_point", new=mock_create),
    ):
        await mcp._tool_manager.call_tool(
            "create_restore_point",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "name": "my-rp",
                "description": "test description",
            },
        )

    mock_create.assert_called_once()
    _, kwargs = mock_create.call_args
    assert kwargs.get("name") == "my-rp"
    assert kwargs.get("description") == "test description"


async def test_create_restore_point_readonly_blocked(ctx_patch) -> None:
    """create_restore_point raises ToolError in FABRIC_MCP_READONLY=1 mode."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_create_restore_point_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """create_restore_point converts FabricError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.restore.create_point",
            new=AsyncMock(side_effect=FabricError("service failure")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


async def test_create_restore_point_workspace_guard(ctx_patch) -> None:
    """create_restore_point raises ToolError when workspace is not in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert "allowlist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# update_restore_point
# ---------------------------------------------------------------------------


async def test_update_restore_point_happy_path(mock_ctx, ctx_patch) -> None:
    """update_restore_point returns a serialised RestorePoint dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    rp = _make_restore_point(name="updated-rp")
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.restore.update_point",
            new=AsyncMock(return_value=rp),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "update_restore_point",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "restore_point_id": _RP_ID,
                "name": "updated-rp",
            },
        )

    assert isinstance(result, dict)
    assert result["id"] == _RP_ID
    assert result["displayName"] == "updated-rp"


async def test_update_restore_point_value_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """update_restore_point wraps ValueError (no name/description) into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.restore.update_point",
            new=AsyncMock(
                side_effect=ValueError("At least one of name or description must be provided")
            ),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "update_restore_point",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "restore_point_id": _RP_ID,
            },
        )

    assert "name or description" in str(exc_info.value).lower()


async def test_update_restore_point_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """update_restore_point converts FabricError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.restore.update_point",
            new=AsyncMock(side_effect=NotFoundError("restore point not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "update_restore_point",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "restore_point_id": _RP_ID,
                "name": "new-name",
            },
        )


async def test_update_restore_point_readonly_blocked(ctx_patch) -> None:
    """update_restore_point raises ToolError in FABRIC_MCP_READONLY=1 mode."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "update_restore_point",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "restore_point_id": _RP_ID,
                "name": "new-name",
            },
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_update_restore_point_workspace_guard(ctx_patch) -> None:
    """update_restore_point raises ToolError when workspace is not in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "update_restore_point",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "restore_point_id": _RP_ID,
                "name": "new-name",
            },
        )

    assert "allowlist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# delete_restore_point
# ---------------------------------------------------------------------------


async def test_delete_restore_point_happy_path(mock_ctx, ctx_patch) -> None:
    """delete_restore_point returns a deletion-confirmed dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.restore.delete_point",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "delete_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert result == {"deleted": True, "restore_point_id": _RP_ID}


async def test_delete_restore_point_readonly_blocked(ctx_patch) -> None:
    """delete_restore_point raises ToolError in FABRIC_MCP_READONLY=1 mode."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1", "FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "delete_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_delete_restore_point_destructive_guard(ctx_patch) -> None:
    """delete_restore_point raises ToolError when FABRIC_MCP_ALLOW_DESTRUCTIVE is unset."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {}, clear=False),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "delete_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert "destructive" in str(exc_info.value).lower()


async def test_delete_restore_point_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """delete_restore_point converts FabricError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.restore.delete_point",
            new=AsyncMock(side_effect=NotFoundError("restore point not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "delete_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )


async def test_delete_restore_point_workspace_guard(ctx_patch) -> None:
    """delete_restore_point raises ToolError when workspace is not in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(
            os.environ,
            {"FABRIC_MCP_WORKSPACES": "other-workspace", "FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"},
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "delete_restore_point",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert "allowlist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# restore_warehouse_in_place
# ---------------------------------------------------------------------------


async def test_restore_warehouse_in_place_happy_path(mock_ctx, ctx_patch) -> None:
    """restore_warehouse_in_place returns a restoration-confirmed dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.restore.restore_in_place",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "restore_warehouse_in_place",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert result == {"restored": True, "restore_point_id": _RP_ID}


async def test_restore_warehouse_in_place_readonly_blocked(ctx_patch) -> None:
    """restore_warehouse_in_place raises ToolError in FABRIC_MCP_READONLY=1 mode."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1", "FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "restore_warehouse_in_place",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_restore_warehouse_in_place_destructive_guard(ctx_patch) -> None:
    """restore_warehouse_in_place raises ToolError when destructive tools are disabled."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "restore_warehouse_in_place",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert "destructive" in str(exc_info.value).lower()


async def test_restore_warehouse_in_place_fabric_error_becomes_tool_error(
    mock_ctx, ctx_patch
) -> None:
    """restore_warehouse_in_place converts FabricError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.restore.restore_in_place",
            new=AsyncMock(side_effect=FabricError("restore failed")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "restore_warehouse_in_place",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )


async def test_restore_warehouse_in_place_value_error_becomes_tool_error(
    mock_ctx, ctx_patch
) -> None:
    """restore_warehouse_in_place wraps ValueError into ToolError via tool_err."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.restore.restore_in_place",
            new=AsyncMock(side_effect=ValueError("cannot restore: operation already in flight")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "restore_warehouse_in_place",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert "operation already in flight" in str(exc_info.value).lower()


async def test_restore_warehouse_in_place_workspace_guard(ctx_patch) -> None:
    """restore_warehouse_in_place raises ToolError when workspace is not in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(
            os.environ,
            {"FABRIC_MCP_WORKSPACES": "other-workspace", "FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"},
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "restore_warehouse_in_place",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "restore_point_id": _RP_ID},
        )

    assert "allowlist" in str(exc_info.value).lower()
