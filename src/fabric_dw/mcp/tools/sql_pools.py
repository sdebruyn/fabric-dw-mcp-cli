"""MCP tools for SQL Pools (beta) operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import AlreadyExists, FabricError, NotFound
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import (
    assert_destructive_allowed,
    assert_workspace_allowed,
    assert_writes_allowed,
)
from fabric_dw.mcp._helpers import fabric_err
from fabric_dw.models import SqlPool, SqlPoolClassifier
from fabric_dw.services import sql_pools as sql_pools_svc

__all__ = ["register"]


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
            config = await sql_pools_svc.get_configuration(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        pool = next((p for p in config.custom_sql_pools if p.name == pool_name), None)
        if pool is None:
            raise ToolError(f"pool {pool_name!r} not found")  # noqa: TRY003
        return pool.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="create_sql_pool")
    async def create_sql_pool(  # noqa: PLR0913
        workspace: str,
        name: str,
        max_percent: int,
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
        assert_writes_allowed("create_sql_pool")
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
        except Exception as exc:
            raise ToolError(f"Invalid pool: {exc}") from exc  # noqa: TRY003

        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            result = await sql_pools_svc.create_pool(ctx.http, ws_id, pool)
        except AlreadyExists as exc:
            raise ToolError(str(exc)) from exc
        except FabricError as exc:
            raise fabric_err(exc) from exc
        created = next(p for p in result.custom_sql_pools if p.name == name)
        return created.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="update_sql_pool")
    async def update_sql_pool(  # noqa: PLR0913
        workspace: str,
        name: str,
        max_percent: int | None = None,
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
        except NotFound as exc:
            raise ToolError(str(exc)) from exc
        except FabricError as exc:
            raise fabric_err(exc) from exc
        updated = next(p for p in result.custom_sql_pools if p.name == name)
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
            await sql_pools_svc.delete_pool(ctx.http, ws_id, pool_name)
        except NotFound as exc:
            raise ToolError(str(exc)) from exc
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"deleted": True, "pool_name": pool_name}

    @mcp.tool(name="reset_sql_pools")
    async def reset_sql_pools(workspace: str) -> dict[str, Any]:
        """Clear all SQL pools for a workspace, preserving the enabled/disabled state.

        Requires workspace admin role.  This tool targets a **beta / preview** API.

        CAUTION: this permanently removes ALL pool definitions in the workspace. The
        configuration's enabled-state is preserved, but every pool is wiped. Use only
        when the user explicitly requests a reset.
        """
        assert_writes_allowed("reset_sql_pools")
        assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id = await ctx.resolver.workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            result = await sql_pools_svc.reset_pools(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")  # ty: ignore[unresolved-attribute]

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
            result = await sql_pools_svc.disable(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")
