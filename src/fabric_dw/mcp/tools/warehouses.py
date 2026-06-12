"""MCP tools for warehouse operations."""

from __future__ import annotations

import logging
import os
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
from fabric_dw.mcp._helpers import fabric_err, resolve_item, tool_err
from fabric_dw.services import ownership as ownership_svc
from fabric_dw.services import permissions as _permissions_svc
from fabric_dw.services import warehouses

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register warehouse tools against *mcp*."""

    @mcp.tool(name="list_warehouses")
    async def list_warehouses(
        workspace: str,
        all_workspaces: bool = False,  # noqa: FBT001, FBT002
    ) -> list[dict[str, Any]]:
        """List all warehouses and SQL analytics endpoints in a workspace.

        When *all_workspaces* is ``True``, ignore *workspace* and aggregate results
        across every workspace the caller can see.
        """
        _workspaces_allowlist = os.environ.get("FABRIC_MCP_WORKSPACES", "").strip()
        if all_workspaces and _workspaces_allowlist:
            raise ToolError(
                "all_workspaces=True is not permitted when FABRIC_MCP_WORKSPACES is configured; "
                "specify an individual workspace instead"
            )
        if not all_workspaces:
            assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            if all_workspaces:
                _log.debug("list_warehouses all_workspaces=True")
                result = await warehouses.list_all_workspaces(ctx.http)
            else:
                ws_id = await ctx.resolver.workspace_id(workspace)
                assert_workspace_allowed(workspace, str(ws_id))
                _log.debug("list_warehouses ws=%s", ws_id)
                result = await warehouses.list_warehouses(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [wh.model_dump(by_alias=True, mode="json") for wh in result]

    @mcp.tool(name="get_warehouse")
    async def get_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
        """Return details for a single warehouse (name or GUID)."""
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("get_warehouse ws=%s item=%s", ws_id, item.id)
            result = await warehouses.get_warehouse(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="create_warehouse")
    async def create_warehouse(
        workspace: str,
        name: str,
        collation: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a new Warehouse in a workspace."""
        assert_writes_allowed("create_warehouse")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("create_warehouse ws=%s name=%r", ws_id, name)
            result = await warehouses.create(
                ctx.http, ws_id, name, collation=collation, description=description
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="rename_warehouse")
    async def rename_warehouse(
        workspace: str,
        warehouse: str,
        new_name: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Rename a Warehouse (and optionally update its description)."""
        assert_writes_allowed("rename_warehouse")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("rename_warehouse ws=%s item=%s new=%r", ws_id, item.id, new_name)
            result = await warehouses.rename(
                ctx.http, ws_id, item.id, new_name, description=description
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="delete_warehouse")
    async def delete_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
        """Delete a Warehouse."""
        assert_writes_allowed("delete_warehouse")
        assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("delete_warehouse ws=%s item=%s", ws_id, item.id)
            await warehouses.delete(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"deleted": True, "warehouse_id": str(item.id)}

    @mcp.tool(name="takeover_warehouse")
    async def takeover_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
        """Take ownership of a Warehouse."""
        assert_writes_allowed("takeover_warehouse")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("takeover_warehouse ws=%s item=%s", ws_id, item.id)
            await ownership_svc.takeover(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"taken_over": True, "warehouse_id": str(item.id)}

    @mcp.tool(name="get_warehouse_permissions")
    async def get_warehouse_permissions(workspace: str, warehouse: str) -> list[dict[str, Any]]:
        """Return principals with access to a Warehouse item.

        Requires Fabric Administrator role (admin API).

        See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("get_warehouse_permissions ws=%s item=%s", ws_id, item.id)
            result = await _permissions_svc.list_item_access(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [a.model_dump(by_alias=True, mode="json") for a in result]
