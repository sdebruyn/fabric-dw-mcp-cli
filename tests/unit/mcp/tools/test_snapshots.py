"""Unit tests for the MCP snapshot tool wrappers.

Coverage targets
----------------
``src/fabric_dw/mcp/tools/snapshots.py``

Tool list
~~~~~~~~~
* ``list_snapshots``         — happy path, FabricError → ToolError, workspace guard
* ``create_snapshot``        — happy path, bad datetime, FabricError/ValueError → ToolError,
  readonly guard
* ``rename_snapshot``        — happy path, FabricError/ValueError → ToolError, readonly guard
* ``delete_snapshot``        — happy path, FabricError → ToolError, readonly + destructive guard
* ``roll_snapshot_timestamp`` — happy path, bad datetime, FabricError → ToolError, readonly guard
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import WarehouseSnapshot
from tests.unit.mcp.conftest import (
    SNAP_ID,
    WH_ID,
    WH_NAME,
    WS_ID,
    WS_NAME,
    make_item_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SNAP_NAME = "snap-1"
_SNAP_DT = "2026-01-01T00:00:00"


def _make_snapshot(
    snap_id: UUID = SNAP_ID,
    *,
    name: str = _SNAP_NAME,
    parent_wh_id: UUID = WH_ID,
) -> WarehouseSnapshot:
    return WarehouseSnapshot.model_validate(
        {
            "id": str(snap_id),
            "displayName": name,
            "parentWarehouseId": str(parent_wh_id),
            "snapshotDateTime": _SNAP_DT,
        }
    )


# ---------------------------------------------------------------------------
# list_snapshots
# ---------------------------------------------------------------------------


async def test_list_snapshots_happy_path(mock_ctx, ctx_patch) -> None:
    """list_snapshots returns a list of serialised WarehouseSnapshot dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    snap = _make_snapshot()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.list_snapshots",
            new=AsyncMock(return_value=[snap]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_snapshots",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == str(SNAP_ID)
    assert result[0]["displayName"] == _SNAP_NAME


async def test_list_snapshots_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """list_snapshots converts FabricError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.list_snapshots",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_snapshots",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )


async def test_list_snapshots_workspace_guard(ctx_patch) -> None:
    """list_snapshots raises ToolError when workspace is not in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_snapshots",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert "allowlist" in str(exc_info.value).lower()


async def test_list_snapshots_empty(mock_ctx, ctx_patch) -> None:
    """list_snapshots returns an empty list when no snapshots exist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.list_snapshots",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_snapshots",
            {"workspace": WS_NAME, "warehouse": WH_NAME},
        )

    assert result == []


# ---------------------------------------------------------------------------
# create_snapshot
# ---------------------------------------------------------------------------


async def test_create_snapshot_happy_path(mock_ctx, ctx_patch) -> None:
    """create_snapshot returns a serialised WarehouseSnapshot dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    snap = _make_snapshot()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.create",
            new=AsyncMock(return_value=snap),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_snapshot",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "name": _SNAP_NAME},
        )

    assert isinstance(result, dict)
    assert result["id"] == str(SNAP_ID)
    assert result["displayName"] == _SNAP_NAME


async def test_create_snapshot_with_datetime(mock_ctx, ctx_patch) -> None:
    """create_snapshot passes a parsed datetime to the service when snapshot_dt is supplied."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    snap = _make_snapshot()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_create = AsyncMock(return_value=snap)

    with (
        ctx_patch,
        patch("fabric_dw.services.snapshots.create", new=mock_create),
    ):
        await mcp._tool_manager.call_tool(
            "create_snapshot",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "name": _SNAP_NAME,
                "snapshot_dt": "2026-03-01T12:00:00",
            },
        )

    mock_create.assert_called_once()
    _, kwargs = mock_create.call_args
    assert kwargs.get("snapshot_dt") is not None
    assert isinstance(kwargs["snapshot_dt"], datetime)


async def test_create_snapshot_bad_datetime_becomes_tool_error(ctx_patch) -> None:
    """create_snapshot raises ToolError when snapshot_dt is not a valid ISO-8601 string."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_snapshot",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "name": _SNAP_NAME,
                "snapshot_dt": "not-a-date",
            },
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_create_snapshot_readonly_blocked(ctx_patch) -> None:
    """create_snapshot raises ToolError in FABRIC_MCP_READONLY=1 mode."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_snapshot",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "name": _SNAP_NAME},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_create_snapshot_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """create_snapshot converts FabricError to ToolError via tool_err."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.create",
            new=AsyncMock(side_effect=FabricError("create failed")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_snapshot",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "name": _SNAP_NAME},
        )


async def test_create_snapshot_value_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """create_snapshot converts ValueError to ToolError via tool_err."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.create",
            new=AsyncMock(side_effect=ValueError("name must be a non-empty string")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_snapshot",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "name": ""},
        )

    assert "non-empty" in str(exc_info.value).lower()


async def test_create_snapshot_workspace_guard(ctx_patch) -> None:
    """create_snapshot raises ToolError when workspace is not in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_snapshot",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "name": _SNAP_NAME},
        )

    assert "allowlist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# rename_snapshot
# ---------------------------------------------------------------------------


async def test_rename_snapshot_happy_path(mock_ctx, ctx_patch) -> None:
    """rename_snapshot returns a serialised WarehouseSnapshot dict with the new name."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    snap = _make_snapshot(name="snap-renamed")
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.rename",
            new=AsyncMock(return_value=snap),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "rename_snapshot",
            {"workspace": WS_NAME, "snapshot": _SNAP_NAME, "new_name": "snap-renamed"},
        )

    assert isinstance(result, dict)
    assert result["displayName"] == "snap-renamed"


async def test_rename_snapshot_readonly_blocked(ctx_patch) -> None:
    """rename_snapshot raises ToolError in FABRIC_MCP_READONLY=1 mode."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "rename_snapshot",
            {"workspace": WS_NAME, "snapshot": _SNAP_NAME, "new_name": "snap-renamed"},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_rename_snapshot_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """rename_snapshot converts FabricError to ToolError via tool_err."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.rename",
            new=AsyncMock(side_effect=NotFoundError("snapshot not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "rename_snapshot",
            {"workspace": WS_NAME, "snapshot": _SNAP_NAME, "new_name": "snap-renamed"},
        )


async def test_rename_snapshot_value_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """rename_snapshot converts ValueError to ToolError via tool_err."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.rename",
            new=AsyncMock(side_effect=ValueError("new_name must be a non-empty string")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "rename_snapshot",
            {"workspace": WS_NAME, "snapshot": _SNAP_NAME, "new_name": ""},
        )

    assert "non-empty" in str(exc_info.value).lower()


async def test_rename_snapshot_workspace_guard(ctx_patch) -> None:
    """rename_snapshot raises ToolError when workspace is not in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "rename_snapshot",
            {"workspace": WS_NAME, "snapshot": _SNAP_NAME, "new_name": "snap-renamed"},
        )

    assert "allowlist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# delete_snapshot
# ---------------------------------------------------------------------------


async def test_delete_snapshot_happy_path(mock_ctx, ctx_patch) -> None:
    """delete_snapshot returns a deletion-confirmed dict with the snapshot id."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry(item_id=SNAP_ID, display_name=_SNAP_NAME)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.snapshots.delete",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "delete_snapshot",
            {"workspace": WS_NAME, "snapshot": _SNAP_NAME},
        )

    assert result["deleted"] is True
    assert result["snapshot_id"] == str(SNAP_ID)


async def test_delete_snapshot_readonly_blocked(ctx_patch) -> None:
    """delete_snapshot raises ToolError in FABRIC_MCP_READONLY=1 mode."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1", "FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "delete_snapshot",
            {"workspace": WS_NAME, "snapshot": _SNAP_NAME},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_delete_snapshot_destructive_guard(ctx_patch) -> None:
    """delete_snapshot raises ToolError when FABRIC_MCP_ALLOW_DESTRUCTIVE is unset."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "delete_snapshot",
            {"workspace": WS_NAME, "snapshot": _SNAP_NAME},
        )

    assert "destructive" in str(exc_info.value).lower()


async def test_delete_snapshot_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """delete_snapshot converts FabricError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry(item_id=SNAP_ID, display_name=_SNAP_NAME)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.snapshots.delete",
            new=AsyncMock(side_effect=NotFoundError("snapshot not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "delete_snapshot",
            {"workspace": WS_NAME, "snapshot": _SNAP_NAME},
        )


async def test_delete_snapshot_workspace_guard(ctx_patch) -> None:
    """delete_snapshot raises ToolError when workspace is not in the allowlist."""
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
            "delete_snapshot",
            {"workspace": WS_NAME, "snapshot": _SNAP_NAME},
        )

    assert "allowlist" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# roll_snapshot_timestamp
# ---------------------------------------------------------------------------


async def test_roll_snapshot_timestamp_happy_path(mock_ctx, ctx_patch) -> None:
    """roll_snapshot_timestamp returns a roll-confirmed dict with applied_dt.

    When new_dt is omitted the service queries CURRENT_TIMESTAMP and returns
    the actual applied datetime.  The tool must surface that as applied_dt.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    _applied = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.roll_timestamp",
            new=AsyncMock(return_value=_applied),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "roll_snapshot_timestamp",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "snapshot_name": _SNAP_NAME},
        )

    assert result["rolled"] is True
    assert result["snapshot_name"] == _SNAP_NAME
    # applied_dt must be the ISO-8601 string of the server-side timestamp.
    assert result["applied_dt"] == _applied.isoformat()
    assert "new_dt" not in result


async def test_roll_snapshot_timestamp_with_datetime(mock_ctx, ctx_patch) -> None:
    """roll_snapshot_timestamp passes a parsed datetime to the service when new_dt is supplied.

    The service returns the applied datetime (equal to the supplied new_dt in
    this case); the tool must return it as applied_dt.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    _supplied_dt = datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC)
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_roll = AsyncMock(return_value=_supplied_dt)

    with (
        ctx_patch,
        patch("fabric_dw.services.snapshots.roll_timestamp", new=mock_roll),
    ):
        result = await mcp._tool_manager.call_tool(
            "roll_snapshot_timestamp",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "snapshot_name": _SNAP_NAME,
                "new_dt": "2026-06-01T09:00:00",
            },
        )

    mock_roll.assert_called_once()
    args, _kwargs = mock_roll.call_args
    # second positional arg is snapshot_name, third is new_dt
    assert args[1] == _SNAP_NAME
    assert isinstance(args[2], datetime)
    # applied_dt reflects the service's returned datetime, not the raw input string.
    assert result["applied_dt"] == _supplied_dt.isoformat()
    assert "new_dt" not in result


async def test_roll_snapshot_timestamp_bad_datetime_becomes_tool_error(ctx_patch) -> None:
    """roll_snapshot_timestamp raises ToolError when new_dt is not valid ISO-8601."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "roll_snapshot_timestamp",
            {
                "workspace": WS_NAME,
                "warehouse": WH_NAME,
                "snapshot_name": _SNAP_NAME,
                "new_dt": "not-a-date",
            },
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_roll_snapshot_timestamp_readonly_blocked(ctx_patch) -> None:
    """roll_snapshot_timestamp raises ToolError in FABRIC_MCP_READONLY=1 mode."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "roll_snapshot_timestamp",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "snapshot_name": _SNAP_NAME},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_roll_snapshot_timestamp_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """roll_snapshot_timestamp converts FabricError to ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.snapshots.roll_timestamp",
            new=AsyncMock(side_effect=FabricError("SQL execution failed")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "roll_snapshot_timestamp",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "snapshot_name": _SNAP_NAME},
        )


async def test_roll_snapshot_timestamp_workspace_guard(ctx_patch) -> None:
    """roll_snapshot_timestamp raises ToolError when workspace is not in the allowlist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "roll_snapshot_timestamp",
            {"workspace": WS_NAME, "warehouse": WH_NAME, "snapshot_name": _SNAP_NAME},
        )

    assert "allowlist" in str(exc_info.value).lower()
