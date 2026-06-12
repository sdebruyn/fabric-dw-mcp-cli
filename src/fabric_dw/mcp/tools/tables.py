"""MCP tools for SQL table operations."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
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
    parse_qualified_name,
    resolve_item,
    safe_rows,
    tool_err,
)
from fabric_dw.services import tables as tables_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


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
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_tables ws=%s item=%s schema=%r", ws_id, entry.id, schema)
            target = make_sql_target(ws_id, entry, item)
            result = await tables_svc.list_tables(target, schema=schema, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [t.model_dump(mode="json") for t in result]

    @mcp.tool(name="read_table")
    async def read_table(
        workspace: str,
        item: str,
        qualified_name: str,
        count: Annotated[int, Field(ge=1, le=10000)] = 10,
    ) -> dict[str, Any]:
        """Return up to *count* rows from a table as JSON-serialisable columns + rows.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
            count: Maximum number of rows to return (1-10000, default 10).
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "read_table ws=%s item=%s table=%s.%s count=%d",
                ws_id,
                entry.id,
                schema,
                table_name,
                count,
            )
            target = make_sql_target(ws_id, entry, item)
            columns, rows = await tables_svc.read_table(
                target, schema, table_name, count=count, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "columns": columns,
            "rows": safe_rows(rows),
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
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "create_table ws=%s item=%s table=%s.%s", ws_id, entry.id, schema, table_name
            )
            target = make_sql_target(ws_id, entry, item)
            result = await tables_svc.create_table(
                target, schema, table_name, select_body, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
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
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "delete_table ws=%s item=%s table=%s.%s", ws_id, entry.id, schema, table_name
            )
            target = make_sql_target(ws_id, entry, item)
            await tables_svc.delete_table(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
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
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("clear_table ws=%s item=%s table=%s.%s", ws_id, entry.id, schema, table_name)
            target = make_sql_target(ws_id, entry, item)
            await tables_svc.clear_table(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"truncated": True}

    @mcp.tool(name="clone_table")
    async def clone_table(
        workspace: str,
        item: str,
        source: str,
        new_table: str,
        at: str | None = None,
    ) -> dict[str, Any]:
        """Create a zero-copy clone of a table using ``CREATE TABLE … AS CLONE OF …``.

        Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.
            source: Qualified source table name, e.g. ``dbo.sales``.
            new_table: Qualified name for the new cloned table, e.g. ``dbo.sales_clone``.
            at: Optional ISO-8601 UTC timestamp for a point-in-time clone,
                e.g. ``2024-05-20T14:00:00``.  Must be within the data-retention
                window (30 days by default).  When omitted, the clone reflects the
                current state of the source table.
        """
        parse_qualified_name(source, kind="table")
        parse_qualified_name(new_table, kind="table")
        assert_writes_allowed("clone_table")
        assert_workspace_allowed(workspace)

        at_dt: datetime | None = None
        if at is not None:
            try:
                at_dt = datetime.fromisoformat(at)
            except ValueError as exc:
                from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

                raise ToolError(
                    f"invalid --at timestamp {at!r}: expected ISO-8601 (e.g. 2024-01-01T00:00:00)"
                ) from exc
            at_dt = at_dt.replace(tzinfo=UTC) if at_dt.tzinfo is None else at_dt.astimezone(UTC)

        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "clone_table ws=%s item=%s source=%s new_table=%s at=%s",
                ws_id,
                entry.id,
                source,
                new_table,
                at,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await tables_svc.clone_table(
                target,
                source,
                new_table,
                at=at_dt,
                kind=entry.kind,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
        return result.model_dump(mode="json")
