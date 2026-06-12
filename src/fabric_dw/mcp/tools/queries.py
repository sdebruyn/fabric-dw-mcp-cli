"""MCP tools for running query and connection management."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_workspace_allowed, assert_writes_allowed
from fabric_dw.mcp._helpers import fabric_err
from fabric_dw.services import queries
from fabric_dw.sql import SqlTarget

__all__ = ["register"]


def register(mcp: FastMCP) -> None:
    """Register query tools against *mcp*."""

    @mcp.tool(name="list_running_queries")
    async def list_running_queries(workspace: str, warehouse: str) -> list[dict[str, Any]]:
        """Return all currently-executing queries on a warehouse."""
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            item = await ctx.resolver.item(workspace, warehouse)
            if item.connection_string is None:
                msg = f"warehouse {warehouse!r} has no connection string; cannot query DMVs"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=item.display_name,
                connection_string=item.connection_string,
            )
            result = await queries.list_running(target, mode=ctx.auth_mode)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]

    @mcp.tool(name="list_connections")
    async def list_connections(workspace: str, warehouse: str) -> list[dict[str, Any]]:
        """Return all active SQL connections on a warehouse or SQL Analytics Endpoint."""
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            item = await ctx.resolver.item(workspace, warehouse)
            if item.connection_string is None:
                msg = f"warehouse {warehouse!r} has no connection string; cannot query DMVs"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=item.display_name,
                connection_string=item.connection_string,
            )
            result = await queries.list_connections(target, mode=ctx.auth_mode)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [c.model_dump(by_alias=True, mode="json") for c in result]

    @mcp.tool(name="kill_session")
    async def kill_session(workspace: str, warehouse: str, session_id: int) -> dict[str, Any]:
        """Terminate a session on a warehouse by session_id."""
        assert_writes_allowed("kill_session")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            item = await ctx.resolver.item(workspace, warehouse)
            if item.connection_string is None:
                msg = f"warehouse {warehouse!r} has no connection string; cannot kill sessions"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=item.display_name,
                connection_string=item.connection_string,
            )
            await queries.kill(target, session_id, mode=ctx.auth_mode)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"killed": True, "session_id": session_id}
