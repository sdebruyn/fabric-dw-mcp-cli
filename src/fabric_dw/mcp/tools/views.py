"""MCP tools for SQL view operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_workspace_allowed, assert_writes_allowed
from fabric_dw.mcp._helpers import fabric_err, parse_qualified_name
from fabric_dw.services import views as views_svc
from fabric_dw.sql import SqlTarget
from fabric_dw.sql_io import json_safe as _json_safe_value

__all__ = ["register"]


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register view tools against *mcp*."""

    @mcp.tool(name="list_views")
    async def list_views(
        workspace: str, item: str, schema: str | None = None
    ) -> list[dict[str, Any]]:
        """List SQL views on a warehouse or SQL Analytics Endpoint.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            schema: When provided, only views in this schema are returned.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot query views"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            result = await views_svc.list_views(target, schema=schema, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return [v.model_dump(mode="json") for v in result]

    @mcp.tool(name="read_view")
    async def read_view(
        workspace: str, item: str, qualified_name: str, count: int = 10
    ) -> dict[str, Any]:
        """Return up to *count* rows from a view as JSON-serialisable columns + rows.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
            count: Maximum number of rows to return (default 10).
        """
        schema, view_name = parse_qualified_name(qualified_name, kind="view")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot read views"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            columns, rows = await views_svc.read_view(
                target, schema, view_name, count=count, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return {
            "columns": columns,
            "rows": [[_json_safe_value(v) for v in row] for row in rows],
        }

    @mcp.tool(name="get_view")
    async def get_view(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
        """Fetch the full definition of a view (schema.view).

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
        """
        schema, view_name = parse_qualified_name(qualified_name, kind="view")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot query views"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            result = await views_svc.get_view(target, schema, view_name, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return result.model_dump(mode="json")

    @mcp.tool(name="create_view")
    async def create_view(
        workspace: str, item: str, qualified_name: str, select_body: str
    ) -> dict[str, Any]:
        """Create a new SQL view.

        CAUTION: ``select_body`` is executed verbatim as DDL. Ensure the body
        matches the user's intent before calling this tool.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
            select_body: The SELECT statement that forms the view body.
        """
        schema, view_name = parse_qualified_name(qualified_name, kind="view")
        assert_writes_allowed("create_view")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot create views"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            result = await views_svc.create_view(
                target, schema, view_name, select_body, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return result.model_dump(mode="json")

    @mcp.tool(name="update_view")
    async def update_view(
        workspace: str, item: str, qualified_name: str, select_body: str
    ) -> dict[str, Any]:
        """Redefine a SQL view via CREATE OR ALTER VIEW.

        CAUTION: ``select_body`` is executed verbatim as DDL. Ensure the body
        matches the user's intent before calling this tool.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
            select_body: The new SELECT statement.
        """
        schema, view_name = parse_qualified_name(qualified_name, kind="view")
        assert_writes_allowed("update_view")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot update views"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            result = await views_svc.update_view(
                target, schema, view_name, select_body, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return result.model_dump(mode="json")

    @mcp.tool(name="drop_view")
    async def drop_view(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
        """Drop a SQL view.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
        """
        schema, view_name = parse_qualified_name(qualified_name, kind="view")
        assert_writes_allowed("drop_view")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot drop views"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            await views_svc.drop_view(target, schema, view_name, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return {"dropped": True}
