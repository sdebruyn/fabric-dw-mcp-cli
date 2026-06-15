"""MCP tools for SQL schema operations."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import (
    assert_workspace_allowed,
)
from fabric_dw.mcp._helpers import (
    make_sql_target,
    mutating_tool,
    resolve_item,
    tool_err,
)
from fabric_dw.services import schemas as schemas_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
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
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_schemas ws=%s item=%s", ws_id, entry.id)
            target = make_sql_target(ws_id, entry, item)
            result = await schemas_svc.list_schemas(target, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [s.model_dump(mode="json") for s in result]

    @mutating_tool(mcp, "create_schema")
    async def create_schema(workspace: str, item: str, name: str) -> dict[str, Any]:
        """Create a new SQL schema on a warehouse or SQL Analytics Endpoint.

        Both Fabric Data Warehouses and SQL Analytics Endpoints support
        ``CREATE SCHEMA`` per the Microsoft Fabric T-SQL reference.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            name: The schema name.  Must be a valid SQL identifier.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("create_schema ws=%s item=%s name=%r", ws_id, entry.id, name)
            target = make_sql_target(ws_id, entry, item)
            result = await schemas_svc.create_schema(target, name, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "delete_schema", destructive=True)
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

        Both Fabric Data Warehouses and SQL Analytics Endpoints support
        ``DROP SCHEMA`` per the Microsoft Fabric T-SQL reference.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            name: The schema name to drop.
            cascade: When ``True``, drop all tables and views in the schema first.
                Defaults to ``False``.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("delete_schema ws=%s item=%s name=%r", ws_id, entry.id, name)
            target = make_sql_target(ws_id, entry, item)
            await schemas_svc.delete_schema(
                target, name, cascade=cascade, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"deleted": True}
