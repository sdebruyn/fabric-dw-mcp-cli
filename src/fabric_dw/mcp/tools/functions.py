"""MCP tools for T-SQL user-defined function operations.

Preview note: Scalar UDFs and inline TVFs are preview features on Microsoft Fabric DW
as of mid-2026.  Function DDL is supported on both Data Warehouses and SQL Analytics
Endpoints (no endpoint guard applies).
"""

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
from fabric_dw.services import functions as functions_svc
from fabric_dw.services.functions import validate_kind

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register T-SQL user-defined function tools against *mcp*."""

    @mcp.tool(name="list_functions")
    async def list_functions(
        workspace: str,
        item: str,
        schema: str | None = None,
        kind: str = "all",
    ) -> list[dict[str, Any]]:
        """List T-SQL user-defined functions on a warehouse or SQL Analytics Endpoint.

        Scalar UDFs (FN) and inline TVFs (IF) are preview features on Fabric DW as of
        mid-2026.  Function DDL is supported on both Data Warehouses and SQL Analytics
        Endpoints.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            schema: When provided, only functions in this schema are returned.
            kind: Filter by function kind — ``"scalar"`` (FN only),
                ``"inline-tvf"`` (IF only), or ``"all"`` (FN + IF + TF, the default).
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "list_functions ws=%s item=%s schema=%r kind=%r",
                ws_id,
                entry.id,
                schema,
                kind,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await functions_svc.list_functions(
                target,
                schema=schema,
                kind=validate_kind(kind),
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [f.model_dump(mode="json") for f in result]

    @mcp.tool(name="get_function")
    async def get_function(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
        """Fetch the full definition of a T-SQL user-defined function (schema.fn).

        Returns the function definition (from ``sys.sql_modules``) and its parameter list
        (from ``sys.parameters``).  Scalar UDFs and inline TVFs are supported on both
        Data Warehouses and SQL Analytics Endpoints.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            qualified_name: Dot-separated qualified function name, e.g. ``dbo.fn_clean_input``.
        """
        schema, fn_name = parse_qualified_name(qualified_name, kind="function")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("get_function ws=%s item=%s fn=%s.%s", ws_id, entry.id, schema, fn_name)
            target = make_sql_target(ws_id, entry, item)
            result = await functions_svc.get_function(target, schema, fn_name, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "create_function")
    async def create_function(
        workspace: str, item: str, qualified_name: str, body: str
    ) -> dict[str, Any]:
        """Create a new T-SQL user-defined function.

        Scalar UDFs and inline TVFs are preview features on Fabric DW as of mid-2026.
        Function DDL is supported on both Data Warehouses and SQL Analytics Endpoints.

        CAUTION: ``body`` is executed verbatim as DDL. Ensure the body matches the
        user's intent before calling this tool.

        The body should include the parameter list, RETURNS clause, and function body
        (everything that follows ``CREATE FUNCTION [schema].[name]``).

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            qualified_name: Dot-separated qualified function name, e.g. ``dbo.fn_clean_input``.
            body: The function body (parameter list, RETURNS clause, and implementation).
        """
        schema, fn_name = parse_qualified_name(qualified_name, kind="function")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("create_function ws=%s item=%s fn=%s.%s", ws_id, entry.id, schema, fn_name)
            target = make_sql_target(ws_id, entry, item)
            result = await functions_svc.create_function(
                target, schema, fn_name, body, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "update_function")
    async def update_function(
        workspace: str, item: str, qualified_name: str, body: str
    ) -> dict[str, Any]:
        """Redefine a T-SQL user-defined function via CREATE OR ALTER FUNCTION.

        Note: ALTER FUNCTION cannot change the function kind (e.g. scalar to inline TVF).
        The body must be compatible with the original function's kind.

        Scalar UDFs and inline TVFs are preview features on Fabric DW as of mid-2026.
        Function DDL is supported on both Data Warehouses and SQL Analytics Endpoints.

        CAUTION: ``body`` is executed verbatim as DDL. Ensure the body matches the
        user's intent before calling this tool.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            qualified_name: Dot-separated qualified function name, e.g. ``dbo.fn_clean_input``.
            body: The new function body (parameter list, RETURNS clause, and implementation).
        """
        schema, fn_name = parse_qualified_name(qualified_name, kind="function")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("update_function ws=%s item=%s fn=%s.%s", ws_id, entry.id, schema, fn_name)
            target = make_sql_target(ws_id, entry, item)
            result = await functions_svc.update_function(
                target, schema, fn_name, body, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "drop_function", destructive=True)
    async def drop_function(
        workspace: str,
        item: str,
        qualified_name: str,
        if_exists: bool = False,  # noqa: FBT001, FBT002
    ) -> dict[str, Any]:
        """Drop a T-SQL user-defined function.

        Function DDL is supported on both Data Warehouses and SQL Analytics Endpoints.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            qualified_name: Dot-separated qualified function name, e.g. ``dbo.fn_clean_input``.
            if_exists: When ``true``, a missing function is treated as a no-op and
                ``{"dropped": false}`` is returned instead of raising an error.
                Defaults to ``false``.
        """
        schema, fn_name = parse_qualified_name(qualified_name, kind="function")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("drop_function ws=%s item=%s fn=%s.%s", ws_id, entry.id, schema, fn_name)
            target = make_sql_target(ws_id, entry, item)
            dropped = await functions_svc.drop_function(
                target, schema, fn_name, if_exists=if_exists, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"dropped": dropped}

    @mutating_tool(mcp, "rename_function")
    async def rename_function(
        workspace: str, item: str, qualified_name: str, new_name: str
    ) -> dict[str, Any]:
        """Rename a T-SQL user-defined function via sp_rename.

        Works on both Data Warehouses and SQL Analytics Endpoints.

        The new name must be a bare (unqualified) identifier — ``sp_rename``
        cannot move a function across schemas.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            qualified_name: Current dot-separated qualified function name,
                e.g. ``dbo.fn_clean_input``.
            new_name: New bare function name (no schema prefix), e.g. ``fn_sanitize_input``.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        schema, old_fn_name = parse_qualified_name(qualified_name, kind="function")
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "rename_function ws=%s item=%s fn=%s.%s -> %s",
                ws_id,
                entry.id,
                schema,
                old_fn_name,
                new_name,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await functions_svc.rename_function(
                target, qualified_name, new_name, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        ctx.resolver.clear_negative_cache()
        return result.model_dump(mode="json")
