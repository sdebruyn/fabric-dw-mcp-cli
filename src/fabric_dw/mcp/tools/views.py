"""MCP tools for SQL view operations."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import (
    assert_destructive_allowed,
    assert_workspace_allowed,
    assert_writes_allowed,
)
from fabric_dw.mcp._helpers import (
    make_sql_target,
    mutating_tool,
    parse_qualified_name,
    resolve_item,
    safe_rows,
    tool_err,
)
from fabric_dw.services import views as views_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


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
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_views ws=%s item=%s schema=%r", ws_id, entry.id, schema)
            target = make_sql_target(ws_id, entry, item)
            result = await views_svc.list_views(target, schema=schema, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [v.model_dump(mode="json") for v in result]

    @mcp.tool(name="read_view")
    async def read_view(
        workspace: str,
        item: str,
        qualified_name: str,
        count: Annotated[int, Field(ge=1, le=10000)] = 10,
    ) -> dict[str, Any]:
        """Return up to *count* rows from a view as JSON-serialisable columns + rows.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
            count: Maximum number of rows to return (1-10000, default 10).
        """
        schema, view_name = parse_qualified_name(qualified_name, kind="view")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "read_view ws=%s item=%s view=%s.%s count=%d",
                ws_id,
                entry.id,
                schema,
                view_name,
                count,
            )
            target = make_sql_target(ws_id, entry, item)
            columns, rows = await views_svc.read_view(
                target, schema, view_name, count=count, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "columns": columns,
            "rows": safe_rows(rows),
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
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("get_view ws=%s item=%s view=%s.%s", ws_id, entry.id, schema, view_name)
            target = make_sql_target(ws_id, entry, item)
            result = await views_svc.get_view(target, schema, view_name, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "create_view")
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
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("create_view ws=%s item=%s view=%s.%s", ws_id, entry.id, schema, view_name)
            target = make_sql_target(ws_id, entry, item)
            result = await views_svc.create_view(
                target, schema, view_name, select_body, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
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
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("update_view ws=%s item=%s view=%s.%s", ws_id, entry.id, schema, view_name)
            target = make_sql_target(ws_id, entry, item)
            result = await views_svc.update_view(
                target, schema, view_name, select_body, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
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
        assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("drop_view ws=%s item=%s view=%s.%s", ws_id, entry.id, schema, view_name)
            target = make_sql_target(ws_id, entry, item)
            await views_svc.drop_view(target, schema, view_name, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
        return {"dropped": True}

    @mcp.tool(name="rename_view")
    async def rename_view(
        workspace: str, item: str, qualified_name: str, new_name: str
    ) -> dict[str, Any]:
        """Rename a SQL view via sp_rename.

        Works on both Data Warehouses and SQL Analytics Endpoints.

        The new name must be a bare (unqualified) identifier — ``sp_rename``
        cannot move a view across schemas.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Current dot-separated qualified view name,
                e.g. ``dbo.vw_sales``.
            new_name: New bare view name (no schema prefix), e.g. ``vw_revenue``.
        """
        assert_writes_allowed("rename_view")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        schema, old_view_name = parse_qualified_name(qualified_name, kind="view")
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "rename_view ws=%s item=%s view=%s.%s -> %s",
                ws_id,
                entry.id,
                schema,
                old_view_name,
                new_name,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await views_svc.rename_view(
                target, qualified_name, new_name, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
        return result.model_dump(mode="json")
