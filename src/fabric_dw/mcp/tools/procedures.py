"""MCP tools for stored procedure operations."""

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
    parse_qualified_name,
    resolve_item,
    tool_err,
)
from fabric_dw.services import procedures as procedures_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register stored procedure tools against *mcp*."""

    @mcp.tool(name="list_procedures")
    async def list_procedures(
        workspace: str, item: str, schema: str | None = None
    ) -> list[dict[str, Any]]:
        """List stored procedures on a warehouse or SQL Analytics Endpoint.

        Stored procedures are supported on both Fabric Data Warehouses and
        SQL Analytics Endpoints.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            schema: When provided, only procedures in this schema are returned.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_procedures ws=%s item=%s schema=%r", ws_id, entry.id, schema)
            target = make_sql_target(ws_id, entry, item)
            result = await procedures_svc.list_procedures(target, schema=schema, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [p.model_dump(mode="json") for p in result]

    @mcp.tool(name="get_procedure")
    async def get_procedure(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
        """Fetch the full definition of a stored procedure (schema.proc).

        Stored procedures are supported on both Fabric Data Warehouses and
        SQL Analytics Endpoints.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified procedure name, e.g. ``dbo.usp_load``.
        """
        schema, proc_name = parse_qualified_name(qualified_name, kind="procedure")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("get_procedure ws=%s item=%s proc=%s.%s", ws_id, entry.id, schema, proc_name)
            target = make_sql_target(ws_id, entry, item)
            result = await procedures_svc.get_procedure(
                target, schema, proc_name, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "create_procedure")
    async def create_procedure(
        workspace: str, item: str, qualified_name: str, body: str
    ) -> dict[str, Any]:
        """Create a new stored procedure.

        Stored procedures are supported on both Fabric Data Warehouses and
        SQL Analytics Endpoints.

        CAUTION: ``body`` is executed verbatim as DDL. Ensure the body
        matches the user's intent before calling this tool.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified procedure name, e.g. ``dbo.usp_load``.
            body: The procedure body (the AS … section).
        """
        schema, proc_name = parse_qualified_name(qualified_name, kind="procedure")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "create_procedure ws=%s item=%s proc=%s.%s", ws_id, entry.id, schema, proc_name
            )
            target = make_sql_target(ws_id, entry, item)
            result = await procedures_svc.create_procedure(
                target, schema, proc_name, body, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "update_procedure")
    async def update_procedure(
        workspace: str, item: str, qualified_name: str, body: str
    ) -> dict[str, Any]:
        """Redefine a stored procedure via CREATE OR ALTER PROCEDURE.

        Stored procedures are supported on both Fabric Data Warehouses and
        SQL Analytics Endpoints.

        CAUTION: ``body`` is executed verbatim as DDL. Ensure the body
        matches the user's intent before calling this tool.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified procedure name, e.g. ``dbo.usp_load``.
            body: The new procedure body (the AS … section).
        """
        schema, proc_name = parse_qualified_name(qualified_name, kind="procedure")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "update_procedure ws=%s item=%s proc=%s.%s", ws_id, entry.id, schema, proc_name
            )
            target = make_sql_target(ws_id, entry, item)
            result = await procedures_svc.update_procedure(
                target, schema, proc_name, body, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "drop_procedure", destructive=True)
    async def drop_procedure(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
        """Drop a stored procedure.

        Stored procedures are supported on both Fabric Data Warehouses and
        SQL Analytics Endpoints.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified procedure name, e.g. ``dbo.usp_load``.
        """
        schema, proc_name = parse_qualified_name(qualified_name, kind="procedure")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "drop_procedure ws=%s item=%s proc=%s.%s", ws_id, entry.id, schema, proc_name
            )
            target = make_sql_target(ws_id, entry, item)
            await procedures_svc.drop_procedure(target, schema, proc_name, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"dropped": True}

    @mutating_tool(mcp, "transfer_procedure")
    async def transfer_procedure(
        workspace: str, item: str, qualified_name: str, target_schema: str
    ) -> dict[str, Any]:
        """Move a stored procedure to another schema via ``ALTER SCHEMA ... TRANSFER OBJECT::...``.

        Stored procedures are supported on both Fabric Data Warehouses and
        SQL Analytics Endpoints; unlike ``transfer_table``, no endpoint guard
        is applied here.

        CAUTION: ``ALTER SCHEMA ... TRANSFER`` moves the procedure but does
        NOT rewrite the schema name inside its stored definition. After a
        transfer, ``get_procedure`` may still show the OLD schema name in the
        ``CREATE ... AS`` header even though the procedure now lives in the
        new schema.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Current dot-separated qualified procedure name,
                e.g. ``dbo.usp_load``.
            target_schema: Schema to move the procedure into, e.g. ``archive``.
        """
        parse_qualified_name(qualified_name, kind="procedure")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "transfer_procedure ws=%s item=%s qualified=%r target_schema=%r",
                ws_id,
                entry.id,
                qualified_name,
                target_schema,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await procedures_svc.transfer_procedure(
                target, qualified_name, target_schema, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")
