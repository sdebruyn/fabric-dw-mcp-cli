"""MCP tools for workspace operations."""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_workspace_allowed, assert_writes_allowed
from fabric_dw.mcp._helpers import fabric_err
from fabric_dw.models import Workspace
from fabric_dw.services import workspaces

__all__ = ["register"]

_log = logging.getLogger(__name__)


def _workspace_in_allowlist(ws: Workspace, allowed: frozenset[str]) -> bool:
    """Return True when *ws* matches any entry in *allowed* (name or GUID)."""
    return ws.name.strip().lower() in allowed or str(ws.id).strip().lower() in allowed


def register(mcp: FastMCP) -> None:
    """Register workspace tools against *mcp*."""

    @mcp.tool(name="list_workspaces")
    async def list_workspaces() -> list[dict[str, Any]]:
        """List all Fabric workspaces the caller has access to.

        When ``FABRIC_MCP_WORKSPACES`` is configured only the workspaces that
        match the allowlist (by name or GUID) are returned.
        """
        _log.debug("list_workspaces called")
        ctx = get_context()
        try:
            result = await workspaces.list_all(ctx.http)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        raw_allowlist = os.environ.get("FABRIC_MCP_WORKSPACES", "").strip()
        if raw_allowlist:
            allowed: frozenset[str] = frozenset(
                entry.strip().lower() for entry in raw_allowlist.split(",") if entry.strip()
            )
            if allowed:
                result = [ws for ws in result if _workspace_in_allowlist(ws, allowed)]
        return [ws.model_dump(by_alias=True, mode="json") for ws in result]

    @mcp.tool(name="get_workspace")
    async def get_workspace(workspace: str) -> dict[str, Any]:
        """Return details for a single workspace (name or GUID)."""
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("get_workspace ws=%s", ws_id)
            result = await workspaces.get(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="set_workspace_collation")
    async def set_workspace_collation(workspace: str, collation: str) -> dict[str, Any]:
        """Set the default Data Warehouse collation for a workspace."""
        assert_writes_allowed("set_workspace_collation")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("set_workspace_collation ws=%s collation=%r", ws_id, collation)
            await workspaces.set_collation(ctx.http, ws_id, collation)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"workspace_id": str(ws_id), "collation": collation}
