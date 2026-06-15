"""MCP tools for warehouse restore point operations."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import (
    assert_workspace_allowed,
)
from fabric_dw.mcp._helpers import fabric_err, mutating_tool, resolve_item, tool_err
from fabric_dw.services import restore as restore_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register restore point tools against *mcp*."""

    @mcp.tool(name="list_restore_points")
    async def list_restore_points(workspace: str, warehouse: str) -> list[dict[str, Any]]:
        """Return all restore points for a warehouse."""
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_restore_points ws=%s item=%s", ws_id, item.id)
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
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("get_restore_point ws=%s item=%s rp=%r", ws_id, item.id, restore_point_id)
            result = await restore_svc.get_point(ctx.http, ws_id, item.id, restore_point_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mutating_tool(mcp, "create_restore_point")
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
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("create_restore_point ws=%s item=%s name=%r", ws_id, item.id, name)
            result = await restore_svc.create_point(
                ctx.http, ws_id, item.id, name=name, description=description
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mutating_tool(mcp, "update_restore_point")
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
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("update_restore_point ws=%s item=%s rp=%r", ws_id, item.id, restore_point_id)
            result = await restore_svc.update_point(
                ctx.http,
                ws_id,
                item.id,
                restore_point_id,
                name=name,
                description=description,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mutating_tool(mcp, "delete_restore_point", destructive=True)
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
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("delete_restore_point ws=%s item=%s rp=%r", ws_id, item.id, restore_point_id)
            await restore_svc.delete_point(ctx.http, ws_id, item.id, restore_point_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"deleted": True, "restore_point_id": restore_point_id}

    @mutating_tool(mcp, "restore_warehouse_in_place", destructive=True)
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
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "restore_warehouse_in_place ws=%s item=%s rp=%r",
                ws_id,
                item.id,
                restore_point_id,
            )
            await restore_svc.restore_in_place(ctx.http, ws_id, item.id, restore_point_id)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"restored": True, "restore_point_id": restore_point_id}
