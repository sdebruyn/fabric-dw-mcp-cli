"""Unit tests for fabric_dw.mcp.tools.workspaces — targeting uncovered branches.

Coverage targets (lines from coverage report):
  53-65  set_workspace_collation: happy path, ValueError → ToolError, FabricError → ToolError

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
from tests.unit.mcp.conftest import (
    WS_ID,
    WS_NAME,
)

# ---------------------------------------------------------------------------
# set_workspace_collation — happy path (lines 53-65)
# ---------------------------------------------------------------------------


async def test_set_workspace_collation_happy_path(mock_ctx, ctx_patch) -> None:
    """set_workspace_collation resolves workspace, sets collation, and returns dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.workspaces.set_collation",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_workspace_collation",
            {"workspace": WS_NAME, "collation": "Latin1_General_100_BIN2_UTF8"},
        )

    assert result["workspace_id"] == str(WS_ID)
    assert result["collation"] == "Latin1_General_100_BIN2_UTF8"
    mock_ctx.resolver.workspace_id.assert_called_once_with(WS_NAME)


async def test_set_workspace_collation_value_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """set_workspace_collation converts ValueError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.workspaces.set_collation",
            new=AsyncMock(side_effect=ValueError("unsupported collation")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "set_workspace_collation",
            {"workspace": WS_NAME, "collation": "bad_collation"},
        )

    assert "unsupported collation" in str(exc_info.value)


async def test_set_workspace_collation_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """set_workspace_collation wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.workspaces.set_collation",
            new=AsyncMock(side_effect=NotFoundError("workspace not found")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "set_workspace_collation",
            {"workspace": WS_NAME, "collation": "Latin1_General_100_BIN2_UTF8"},
        )

    assert "NotFoundError" in str(exc_info.value) or "not found" in str(exc_info.value).lower()


async def test_set_workspace_collation_readonly_blocks(ctx_patch) -> None:
    """set_workspace_collation raises ToolError when FABRIC_MCP_READONLY is set."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "set_workspace_collation",
            {"workspace": WS_NAME, "collation": "Latin1_General_100_BIN2_UTF8"},
        )


async def test_set_workspace_collation_workspace_allowlist_blocks(ctx_patch) -> None:
    """set_workspace_collation raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "set_workspace_collation",
            {"workspace": WS_NAME, "collation": "Latin1_General_100_BIN2_UTF8"},
        )


async def test_set_workspace_collation_resolved_id_allowlist_check(mock_ctx, ctx_patch) -> None:
    """set_workspace_collation passes when the workspace name is in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        # Use the workspace name (not GUID) so the first guard passes too.
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": WS_NAME}),
        patch(
            "fabric_dw.services.workspaces.set_collation",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_workspace_collation",
            {"workspace": WS_NAME, "collation": "Latin1_General_100_BIN2_UTF8"},
        )

    assert result["workspace_id"] == str(WS_ID)


# ---------------------------------------------------------------------------
# list_capacities
# ---------------------------------------------------------------------------

_CAP_ID = "aaaaaaaa-0000-0000-0000-000000000001"

_CAPACITY_PAYLOAD = {
    "id": _CAP_ID,
    "displayName": "F64Capacity",
    "sku": "F64",
    "region": "West Europe",
    "state": "Active",
}


async def test_list_capacities_happy_path(ctx_patch) -> None:
    """list_capacities returns capacity dicts from the service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import Capacity  # noqa: PLC0415

    cap = Capacity.model_validate(_CAPACITY_PAYLOAD)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.capacities.list_all",
            new=AsyncMock(return_value=[cap]),
        ),
    ):
        result = await mcp._tool_manager.call_tool("list_capacities", {})

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == _CAP_ID
    assert result[0]["displayName"] == "F64Capacity"
    assert result[0]["sku"] == "F64"
    assert result[0]["region"] == "West Europe"
    assert result[0]["state"] == "Active"


async def test_list_capacities_fabric_error_becomes_tool_error(ctx_patch) -> None:
    """list_capacities wraps FabricError into ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.exceptions import PermissionDeniedError  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.capacities.list_all",
            new=AsyncMock(side_effect=PermissionDeniedError("Forbidden")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool("list_capacities", {})


# ---------------------------------------------------------------------------
# assign_workspace_to_capacity
# ---------------------------------------------------------------------------

_VALID_CAP_UUID = "deadbeef-dead-beef-dead-beef00000001"


async def test_assign_workspace_to_capacity_happy_path(mock_ctx, ctx_patch) -> None:
    """assign_workspace_to_capacity resolves workspace, calls service, returns dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.workspaces.assign_to_capacity",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "assign_workspace_to_capacity",
            {"workspace": WS_NAME, "capacity_id": _VALID_CAP_UUID},
        )

    assert result["workspace_id"] == str(WS_ID)
    assert result["capacity_id"] == _VALID_CAP_UUID


async def test_assign_workspace_to_capacity_invalid_uuid_raises_tool_error(
    ctx_patch,
) -> None:
    """assign_workspace_to_capacity raises ToolError when capacity_id is not a UUID."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError, match="UUID"),
    ):
        await mcp._tool_manager.call_tool(
            "assign_workspace_to_capacity",
            {"workspace": WS_NAME, "capacity_id": "not-a-uuid"},
        )


async def test_assign_workspace_to_capacity_readonly_blocks(ctx_patch) -> None:
    """assign_workspace_to_capacity raises ToolError when FABRIC_MCP_READONLY is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "assign_workspace_to_capacity",
            {"workspace": WS_NAME, "capacity_id": _VALID_CAP_UUID},
        )


async def test_assign_workspace_to_capacity_allowlist_blocks(ctx_patch) -> None:
    """assign_workspace_to_capacity raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "assign_workspace_to_capacity",
            {"workspace": WS_NAME, "capacity_id": _VALID_CAP_UUID},
        )


async def test_assign_workspace_to_capacity_fabric_error_becomes_tool_error(
    mock_ctx, ctx_patch
) -> None:
    """assign_workspace_to_capacity wraps FabricError into ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.exceptions import NotFoundError  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.workspaces.assign_to_capacity",
            new=AsyncMock(side_effect=NotFoundError("workspace not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "assign_workspace_to_capacity",
            {"workspace": WS_NAME, "capacity_id": _VALID_CAP_UUID},
        )
