"""MCP tools for SQL schema operations."""

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
from fabric_dw.mcp._helpers import fabric_err, require_warehouse
from fabric_dw.services import schemas as schemas_svc
from fabric_dw.sql import SqlTarget

__all__ = ["register"]


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register schema tools against *mcp*."""

    @mcp.tool(name="list_schemas")
    async def list_schemas(workspace: str, item: str) -> list[dict[str, Any]]:
        """List user-defined SQL schemas on a warehouse or SQL Analytics Endpoint.

        System schemas (``sys``, ``INFORMATION_SCHEMA``, ``db_*`` fixed-role
        schemas, ``guest``) are excluded.  ``dbo`` is included as it is
        user-writable.

        Listing schemas is a read-only operation and works on both Fabric Data
        Warehouses and SQL Analytics Endpoints.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot query schemas"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            result = await schemas_svc.list_schemas(target, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return [s.model_dump(mode="json") for s in result]

    @mcp.tool(name="create_schema")
    async def create_schema(workspace: str, item: str, name: str) -> dict[str, Any]:
        """Create a new SQL schema on a warehouse.

        Only Fabric Data Warehouses are supported; SQL Analytics Endpoints are
        rejected because they are read-only views over Lakehouse data.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.
            name: The schema name.  Must be a valid SQL identifier.
        """
        assert_writes_allowed("create_schema")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            require_warehouse(entry, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot create schemas"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            result = await schemas_svc.create_schema(target, name, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return result.model_dump(mode="json")

    @mcp.tool(name="delete_schema")
    async def delete_schema(
        workspace: str,
        item: str,
        name: str,
        cascade: bool = False,  # noqa: FBT001, FBT002
    ) -> dict[str, Any]:
        """Drop a SQL schema from a warehouse.

        CAUTION: This is a destructive, irreversible operation.  The schema will
        be permanently deleted.  If the schema still contains tables or views,
        the operation will fail unless *cascade* is ``True``.

        CAUTION: When *cascade* is ``True``, **all tables and views in the schema
        are permanently deleted along with their data**.  Confirm explicitly with
        the user before calling with ``cascade=True``.

        Only Fabric Data Warehouses are supported; SQL Analytics Endpoints are
        rejected.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.
            name: The schema name to drop.
            cascade: When ``True``, drop all tables and views in the schema first.
                Defaults to ``False``.
        """
        assert_writes_allowed("delete_schema")
        assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            entry = await ctx.resolver.item(workspace, item)
            require_warehouse(entry, item)
            if entry.connection_string is None:
                msg = f"item {item!r} has no connection string; cannot delete schemas"
                raise FabricError(msg)  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            await schemas_svc.delete_schema(target, name, cascade=cascade, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
        return {"deleted": True}
