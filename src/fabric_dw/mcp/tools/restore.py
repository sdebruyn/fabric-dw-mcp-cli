"""MCP tools for warehouse restore point operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import (
    assert_destructive_allowed,
    assert_workspace_allowed,
    assert_writes_allowed,
)
from fabric_dw.mcp._helpers import fabric_err
from fabric_dw.services import restore as restore_svc

__all__ = ["register"]


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register restore point tools against *mcp*."""

    @mcp.tool(name="list_restore_points")
    async def list_restore_points(workspace: str, warehouse: str) -> list[dict[str, Any]]:
        """Return all restore points for a warehouse."""
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            item = await ctx.resolver.item(workspace, warehouse)
            result = await restore_svc.list_points(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [rp.model_dump(by_alias=True, mode="json") for rp in result]

    @mcp.tool(name="get_restore_point")
    async def get_restore_point(
        workspace: str, warehouse: str, restore_point_id: str
    ) -> dict[str, Any]:
        """Return a single restore point by ID.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse name or GUID.
            restore_point_id: The restore point ID string (e.g. ``"1726617378000"``).
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            item = await ctx.resolver.item(workspace, warehouse)
            result = await restore_svc.get_point(ctx.http, ws_id, item.id, restore_point_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="create_restore_point")
    async def create_restore_point(
        workspace: str,
        warehouse: str,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a restore point for a warehouse at the current timestamp.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse name or GUID.
            name: Optional display name (max 128 chars).
            description: Optional description (max 512 chars).
        """
        assert_writes_allowed("create_restore_point")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            item = await ctx.resolver.item(workspace, warehouse)
            result = await restore_svc.create_point(
                ctx.http, ws_id, item.id, name=name, description=description
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="update_restore_point")
    async def update_restore_point(
        workspace: str,
        warehouse: str,
        restore_point_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Rename and/or update the description of a restore point.

        At least one of *name* or *description* must be provided.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse name or GUID.
            restore_point_id: The restore point ID string.
            name: New display name (max 128 chars).
            description: New description (max 512 chars).
        """
        assert_writes_allowed("update_restore_point")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            item = await ctx.resolver.item(workspace, warehouse)
            result = await restore_svc.update_point(
                ctx.http,
                ws_id,
                item.id,
                restore_point_id,
                name=name,
                description=description,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="delete_restore_point")
    async def delete_restore_point(
        workspace: str, warehouse: str, restore_point_id: str
    ) -> dict[str, Any]:
        """Delete a user-defined restore point.

        System-created restore points cannot be deleted.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse name or GUID.
            restore_point_id: The restore point ID string.
        """
        assert_writes_allowed("delete_restore_point")
        assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            item = await ctx.resolver.item(workspace, warehouse)
            await restore_svc.delete_point(ctx.http, ws_id, item.id, restore_point_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"deleted": True, "restore_point_id": restore_point_id}

    @mcp.tool(name="restore_warehouse_in_place")
    async def restore_warehouse_in_place(
        workspace: str, warehouse: str, restore_point_id: str
    ) -> dict[str, Any]:
        """Restore a warehouse in-place to a restore point.

        WARNING: This is a destructive, long-running operation. The warehouse
        will be unavailable for approximately 10 minutes while the restore
        completes.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse name or GUID.
            restore_point_id: The restore point ID string to restore to.
        """
        assert_writes_allowed("restore_warehouse_in_place")
        assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            item = await ctx.resolver.item(workspace, warehouse)
            await restore_svc.restore_in_place(ctx.http, ws_id, item.id, restore_point_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"restored": True, "restore_point_id": restore_point_id}
