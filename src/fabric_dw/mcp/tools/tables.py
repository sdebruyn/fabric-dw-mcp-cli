"""MCP tools for SQL table operations."""

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
from fabric_dw.mcp._helpers import fabric_err, parse_qualified_name
from fabric_dw.services import tables as tables_svc
from fabric_dw.sql import SqlTarget
from fabric_dw.sql_io import json_safe as _json_safe_value

__all__ = ["register"]


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register table tools against *mcp*."""

    @mcp.tool(name="list_tables")
    async def list_tables(
        workspace: str, item: str, schema: str | None = None
    ) -> list[dict[str, Any]]:
        """List SQL tables on a warehouse or SQL Analytics Endpoint.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            schema: When provided, only tables in this schema are returned.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot query tables"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            result = await tables_svc.list_tables(target, schema=schema, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return [t.model_dump(mode="json") for t in result]

    @mcp.tool(name="read_table")
    async def read_table(
        workspace: str, item: str, qualified_name: str, count: int = 10
    ) -> dict[str, Any]:
        """Return up to *count* rows from a table as JSON-serialisable columns + rows.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
            count: Maximum number of rows to return (default 10).
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot read tables"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            columns, rows = await tables_svc.read_table(
                target, schema, table_name, count=count, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return {
            "columns": columns,
            "rows": [[_json_safe_value(v) for v in row] for row in rows],
        }

    @mcp.tool(name="create_table")
    async def create_table(
        workspace: str, item: str, qualified_name: str, select_body: str
    ) -> dict[str, Any]:
        """Create a new SQL table via CTAS (CREATE TABLE AS SELECT).

        CAUTION: ``select_body`` is executed verbatim as DDL on the warehouse.
        Ensure the body matches the user's intent before calling this tool.
        The first non-comment keyword of ``select_body`` must be SELECT.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
            select_body: The SELECT statement that becomes the CTAS source.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        assert_writes_allowed("create_table")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot create tables"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            result = await tables_svc.create_table(
                target, schema, table_name, select_body, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return result.model_dump(mode="json")

    @mcp.tool(name="delete_table")
    async def delete_table(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
        """Drop a SQL table.

        CAUTION: This is a destructive, irreversible operation.  The table and all
        its data will be permanently deleted.  Confirm with the user before calling.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        assert_writes_allowed("delete_table")
        assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot delete tables"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            await tables_svc.delete_table(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return {"dropped": True}

    @mcp.tool(name="clear_table")
    async def clear_table(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
        """Truncate a SQL table (remove all rows, keep structure).

        CAUTION: This is a destructive, irreversible operation.  All rows will be
        permanently deleted.  The table structure and schema are preserved.
        Confirm with the user before calling.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        assert_writes_allowed("clear_table")
        assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot clear tables"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            await tables_svc.clear_table(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return {"truncated": True}
