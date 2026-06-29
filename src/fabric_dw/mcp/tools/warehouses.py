"""MCP tools for warehouse operations."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import (
    assert_workspace_allowed,
    workspace_allowlist_active,
)
from fabric_dw.mcp._helpers import fabric_err, mutating_tool, resolve_item, tool_err
from fabric_dw.services import ownership as ownership_svc
from fabric_dw.services import warehouses

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register warehouse tools against *mcp*."""

    @mcp.tool(name="list_warehouses")
    async def list_warehouses(
        workspace: str | None = None,
        all_workspaces: bool = False,  # noqa: FBT001, FBT002
    ) -> list[dict[str, Any]]:
        """List all warehouses and SQL analytics endpoints in a workspace.

        Args:
            workspace: Workspace name or GUID.  Optional when *all_workspaces*
                is ``True``; required otherwise.
            all_workspaces: When ``True``, ignore *workspace* and aggregate
                results across every workspace the caller can see.
        """
        ctx = get_context()
        if all_workspaces and workspace_allowlist_active(ctx.workspace_allowlist):
            raise ToolError(
                "all_workspaces=True is not permitted when a workspace allowlist is configured "
                "(env FABRIC_MCP_WORKSPACES or [mcp] workspace_allowlist in config.toml); "
                "specify an individual workspace instead"
            )
        if not all_workspaces:
            if not workspace or not workspace.strip():
                raise ToolError("workspace is required unless all_workspaces=True")
            assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            if all_workspaces:
                _log.debug("list_warehouses all_workspaces=True")
                result = await warehouses.list_all_workspaces(ctx.http)
            else:
                assert workspace is not None  # noqa: S101 — guard above raised ToolError otherwise
                ws_id = await ctx.resolver.workspace_id(workspace)
                assert_workspace_allowed(
                    workspace,
                    str(ws_id),
                    config_allowlist=ctx.workspace_allowlist,
                )
                _log.debug("list_warehouses ws=%s", ws_id)
                result = await warehouses.list_warehouses(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [wh.model_dump(by_alias=True, mode="json") for wh in result]

    @mcp.tool(name="get_warehouse")
    async def get_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
        """Return details for a single warehouse (name or GUID)."""
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("get_warehouse ws=%s item=%s", ws_id, item.id)
            result = await warehouses.get_warehouse(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mutating_tool(mcp, "create_warehouse")
    async def create_warehouse(
        workspace: str,
        name: str,
        collation: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a new Warehouse in a workspace.

        Args:
            workspace: Workspace name or GUID.
            name: Display name for the new warehouse.
            collation: Optional default collation for the new warehouse.
                Fabric Data Warehouse supports a fixed set of collations.
                Supported values include:

                - ``Latin1_General_100_BIN2_UTF8`` (recommended default)
                - ``Latin1_General_100_CI_AS_KS_WS_SC_UTF8``
                - ``Latin1_General_CI_AS``
                - ``SQL_Latin1_General_CP1_CI_AS``

                When omitted, the workspace default collation is used.
                Supplying an unsupported value will cause the Fabric API to
                return an error.  See the Fabric documentation for the full
                list of supported collations.
            description: Optional description for the new warehouse.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("create_warehouse ws=%s name=%r", ws_id, name)
            result = await warehouses.create(
                ctx.http, ws_id, name, collation=collation, description=description
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mutating_tool(mcp, "rename_warehouse")
    async def rename_warehouse(
        workspace: str,
        warehouse: str,
        new_name: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Rename a Warehouse (and optionally update its description)."""
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("rename_warehouse ws=%s item=%s new=%r", ws_id, item.id, new_name)
            result = await warehouses.rename(
                ctx.http, ws_id, item.id, new_name, description=description
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mutating_tool(mcp, "delete_warehouse", destructive=True)
    async def delete_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
        """Delete a Warehouse."""
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("delete_warehouse ws=%s item=%s", ws_id, item.id)
            await warehouses.delete(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"deleted": True, "warehouse_id": str(item.id)}

    @mutating_tool(mcp, "takeover_warehouse")
    async def takeover_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
        """Take ownership of a Warehouse."""
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("takeover_warehouse ws=%s item=%s", ws_id, item.id)
            await ownership_svc.takeover(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"taken_over": True, "warehouse_id": str(item.id)}
