"""MCP tools for SQL Pools (beta) operations."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field, ValidationError

from fabric_dw.exceptions import AlreadyExistsError, FabricError, NotFoundError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import (
    assert_destructive_allowed,
    assert_workspace_allowed,
    assert_writes_allowed,
)
from fabric_dw.mcp._helpers import (
    fabric_err,
    make_sql_target,
    mutating_tool,
    parse_iso8601,
    resolve_item,
)
from fabric_dw.models import SqlPool, SqlPoolClassifier
from fabric_dw.services import query_insights as _qi_svc
from fabric_dw.services import sql_pools as sql_pools_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register SQL Pools tools against *mcp*."""

    @mcp.tool(name="get_sql_pools_configuration")
    async def get_sql_pools_configuration(workspace: str) -> dict[str, Any]:
        """Fetch the full SQL Pools configuration (enabled flag + pool list) for a workspace.

        Requires workspace admin role.  This tool targets a **beta / preview** API
        endpoint that may change before general availability.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("get_sql_pools_configuration ws=%s", ws_id)
            result = await sql_pools_svc.get_configuration(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="list_sql_pools")
    async def list_sql_pools(workspace: str) -> list[dict[str, Any]]:
        """Return the list of custom SQL pools for a workspace.

        Requires workspace admin role.  This tool targets a **beta / preview** API.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_sql_pools ws=%s", ws_id)
            config = await sql_pools_svc.get_configuration(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [p.model_dump(by_alias=True, mode="json") for p in config.custom_sql_pools]

    @mcp.tool(name="get_sql_pool")
    async def get_sql_pool(workspace: str, pool_name: str) -> dict[str, Any]:
        """Return details for a single SQL pool by name.

        Args:
            workspace: Workspace name or GUID.
            pool_name: The pool name.

        Requires workspace admin role.  This tool targets a **beta / preview** API.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("get_sql_pool ws=%s pool=%r", ws_id, pool_name)
            config = await sql_pools_svc.get_configuration(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        pool = next((p for p in config.custom_sql_pools if p.name == pool_name), None)
        if pool is None:
            raise ToolError(f"pool {pool_name!r} not found")
        return pool.model_dump(by_alias=True, mode="json")

    @mutating_tool(mcp, "create_sql_pool")
    async def create_sql_pool(  # noqa: PLR0913
        workspace: str,
        name: str,
        max_percent: Annotated[int, Field(ge=1, le=100)],
        is_default: bool = False,  # noqa: FBT001, FBT002
        optimize_for_reads: bool = True,  # noqa: FBT001, FBT002
        classifier_type: str | None = None,
        classifier_values: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add a new custom SQL pool to a workspace.

        Args:
            workspace: Workspace name or GUID.
            name: Pool name (must be unique within the workspace).
            max_percent: Max resource percentage (1-100).
            is_default: Whether this pool is the default pool. Defaults to false.
            optimize_for_reads: Enable read optimisation. Defaults to true.
            classifier_type: Classifier type (e.g. ``"Application Name"``).
            classifier_values: List of classifier values (e.g. application names).

        Requires workspace admin role.  This tool targets a **beta / preview** API.
        """
        assert_workspace_allowed(workspace)
        classifier: SqlPoolClassifier | None = None
        if classifier_type is not None:
            classifier = SqlPoolClassifier.model_validate(
                {"type": classifier_type, "value": classifier_values or []}
            )

        try:
            pool = SqlPool.model_validate(
                {
                    "name": name,
                    "isDefault": is_default,
                    "maxResourcePercentage": max_percent,
                    "optimizeForReads": optimize_for_reads,
                    "classifier": (
                        classifier.model_dump(by_alias=True, mode="json") if classifier else None
                    ),
                }
            )
        except ValidationError as exc:
            raise ToolError(f"Invalid pool: {exc}") from exc

        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("create_sql_pool ws=%s name=%r max_percent=%d", ws_id, name, max_percent)
            result = await sql_pools_svc.create_pool(ctx.http, ws_id, pool)
        except AlreadyExistsError as exc:
            raise ToolError(str(exc)) from exc
        except FabricError as exc:
            raise fabric_err(exc) from exc
        created = next((p for p in result.custom_sql_pools if p.name == name), None)
        if created is None:
            raise ToolError(
                f"pool {name!r} was not found in the API response after creation; "
                "the pool may have been created but is not yet visible (eventual consistency)"
            )
        ctx.resolver.clear_negative_cache()
        return created.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="update_sql_pool")
    async def update_sql_pool(  # noqa: PLR0913
        workspace: str,
        name: str,
        max_percent: Annotated[int, Field(ge=1, le=100)] | None = None,
        is_default: bool | None = None,  # noqa: FBT001
        optimize_for_reads: bool | None = None,  # noqa: FBT001
        classifier_type: str | None = None,
        classifier_values: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update an existing SQL pool.  Only the parameters you supply are changed.

        Args:
            workspace: Workspace name or GUID.
            name: Name of the pool to update.
            max_percent: New max resource percentage (1-100), or omit to keep current.
            is_default: Set or clear the default flag, or omit to keep current.
            optimize_for_reads: Enable/disable read optimisation, or omit to keep current.
            classifier_type: New classifier type, or omit to keep current.
            classifier_values: New classifier value list, or omit to keep current.

        Requires workspace admin role.  This tool targets a **beta / preview** API.
        """
        assert_writes_allowed("update_sql_pool")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("update_sql_pool ws=%s name=%r", ws_id, name)
            result = await sql_pools_svc.update_pool(
                ctx.http,
                ws_id,
                name,
                max_resource_percentage=max_percent,
                is_default=is_default,
                optimize_for_reads=optimize_for_reads,
                classifier_type=classifier_type,
                classifier_values=classifier_values,
            )
        except NotFoundError as exc:
            raise ToolError(str(exc)) from exc
        except FabricError as exc:
            raise fabric_err(exc) from exc
        updated = next((p for p in result.custom_sql_pools if p.name == name), None)
        if updated is None:
            raise ToolError(
                f"pool {name!r} was not found in the API response after update; "
                "the pool may have been updated but is not yet visible (eventual consistency)"
            )
        return updated.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="delete_sql_pool")
    async def delete_sql_pool(workspace: str, pool_name: str) -> dict[str, Any]:
        """Delete an SQL pool from a workspace.

        Args:
            workspace: Workspace name or GUID.
            pool_name: Name of the pool to delete.

        Requires workspace admin role.  This tool targets a **beta / preview** API.
        """
        assert_writes_allowed("delete_sql_pool")
        assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("delete_sql_pool ws=%s pool=%r", ws_id, pool_name)
            await sql_pools_svc.delete_pool(ctx.http, ws_id, pool_name)
        except NotFoundError as exc:
            raise ToolError(str(exc)) from exc
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"deleted": True, "pool_name": pool_name}

    @mcp.tool(name="enable_sql_pools")
    async def enable_sql_pools(workspace: str) -> dict[str, Any]:
        """Enable custom SQL Pools for a workspace without modifying pool definitions.

        Requires workspace admin role.  This tool targets a **beta / preview** API.
        """
        assert_writes_allowed("enable_sql_pools")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("enable_sql_pools ws=%s", ws_id)
            result = await sql_pools_svc.enable(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="disable_sql_pools")
    async def disable_sql_pools(workspace: str) -> dict[str, Any]:
        """Disable custom SQL Pools for a workspace, preserving pool configuration.

        Re-enabling with enable_sql_pools restores the previously saved configuration.

        Requires workspace admin role.  This tool targets a **beta / preview** API.
        """
        assert_writes_allowed("disable_sql_pools")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("disable_sql_pools ws=%s", ws_id)
            result = await sql_pools_svc.disable(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="list_sql_pool_insights")
    async def list_sql_pool_insights(
        workspace: str,
        warehouse: str,
        limit: Annotated[int, Field(ge=1, le=10000)] = 100,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return SQL pool insight events from queryinsights.sql_pool_insights.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL Analytics Endpoint name or GUID.
            limit: Maximum rows to return (1-10000, default 100).
            since: Optional ISO-8601 lower bound on timestamp.
            until: Optional ISO-8601 upper bound on timestamp.
        """
        since_dt = parse_iso8601(since, "since")
        until_dt = parse_iso8601(until, "until")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_sql_pool_insights ws=%s item=%s limit=%d", ws_id, item.id, limit)
            target = make_sql_target(ws_id, item, warehouse)
            result = await _qi_svc.list_sql_pool_insights(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth_mode
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]
