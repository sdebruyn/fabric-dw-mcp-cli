"""MCP tools for SQL statistics operations."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

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
from fabric_dw.services import statistics as statistics_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register statistics tools against *mcp*."""

    @mcp.tool(name="list_statistics")
    async def list_statistics(  # noqa: PLR0913
        workspace: str,
        item: str,
        schema: str | None = None,
        table: str | None = None,
        *,
        user_only: bool = False,
        auto_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List statistics on a warehouse or SQL Analytics Endpoint.

        Both Data Warehouses and SQL Analytics Endpoints are supported.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            schema: When provided, only statistics on tables in this schema are returned.
            table: When provided, only statistics on this table (unqualified name) are returned.
            user_only: When True, only user-created statistics are returned.
            auto_only: When True, only auto-created statistics are returned.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "list_statistics ws=%s item=%s schema=%r table=%r user_only=%s auto_only=%s",
                ws_id,
                entry.id,
                schema,
                table,
                user_only,
                auto_only,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await statistics_svc.list_statistics(
                target,
                schema=schema,
                table=table,
                user_only=user_only,
                auto_only=auto_only,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [s.model_dump(mode="json") for s in result]

    @mcp.tool(name="show_statistics")
    async def show_statistics(
        workspace: str,
        item: str,
        qualified_table: str,
        stat_name: str,
        *,
        histogram_only: bool = False,
    ) -> dict[str, Any]:
        """Show details of a statistic using DBCC SHOW_STATISTICS.

        Returns the stat header, density vector, and histogram steps.
        Both Data Warehouses and SQL Analytics Endpoints are supported.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_table: Qualified table name, e.g. ``dbo.sales``.
            stat_name: The name of the statistic to show.
            histogram_only: When True, return only the histogram steps.
        """
        parse_qualified_name(qualified_table, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "show_statistics ws=%s item=%s table=%r stat=%r",
                ws_id,
                entry.id,
                qualified_table,
                stat_name,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await statistics_svc.show_statistics(
                target,
                qualified_table,
                stat_name,
                histogram_only=histogram_only,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "create_statistics")
    async def create_statistics(  # noqa: PLR0913
        workspace: str,
        item: str,
        qualified_table: str,
        column: str,
        stat_name: str,
        *,
        fullscan: bool = True,
        sample_percent: Annotated[int, Field(ge=1, le=100)] | None = None,
    ) -> dict[str, Any]:
        """Create a single-column statistic on a table.

        Only supported on Data Warehouses (SQL Analytics Endpoints are read-only).
        Only single-column statistics are supported (Fabric limitation).

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID. SQL Analytics Endpoints are rejected.
            qualified_table: Qualified table name, e.g. ``dbo.sales``.
            column: Column name to build the statistic on.
            stat_name: Name for the new statistic.
            fullscan: When True (default), use WITH FULLSCAN.
                Ignored when sample_percent is provided.
            sample_percent: Sample percentage (1-100). When provided, overrides fullscan
                and uses WITH SAMPLE n PERCENT.
        """
        parse_qualified_name(qualified_table, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "create_statistics ws=%s item=%s table=%r col=%r name=%r",
                ws_id,
                entry.id,
                qualified_table,
                column,
                stat_name,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await statistics_svc.create_statistics(
                target,
                qualified_table,
                column,
                name=stat_name,
                fullscan=fullscan,
                sample_percent=sample_percent,
                kind=entry.kind,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "update_statistics")
    async def update_statistics(  # noqa: PLR0913
        workspace: str,
        item: str,
        qualified_table: str,
        stat_name: str,
        *,
        fullscan: bool = True,
        sample_percent: Annotated[int, Field(ge=1, le=100)] | None = None,
    ) -> dict[str, Any]:
        """Update an existing statistic via UPDATE STATISTICS.

        Only supported on Data Warehouses (SQL Analytics Endpoints are read-only).

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID. SQL Analytics Endpoints are rejected.
            qualified_table: Qualified table name, e.g. ``dbo.sales``.
            stat_name: Name of the statistic to update.
            fullscan: When True (default), use WITH FULLSCAN.
                Ignored when sample_percent is provided.
            sample_percent: Sample percentage (1-100). When provided, overrides fullscan
                and uses WITH SAMPLE n PERCENT.
        """
        parse_qualified_name(qualified_table, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "update_statistics ws=%s item=%s table=%r stat=%r",
                ws_id,
                entry.id,
                qualified_table,
                stat_name,
            )
            target = make_sql_target(ws_id, entry, item)
            await statistics_svc.update_statistics(
                target,
                qualified_table,
                stat_name,
                fullscan=fullscan,
                sample_percent=sample_percent,
                kind=entry.kind,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"updated": True}

    @mutating_tool(mcp, "delete_statistics", destructive=True)
    async def delete_statistics(
        workspace: str,
        item: str,
        qualified_table: str,
        stat_name: str,
    ) -> dict[str, Any]:
        """Drop a statistic via DROP STATISTICS.

        CAUTION: This is a destructive, irreversible operation.
        Only supported on Data Warehouses (SQL Analytics Endpoints are read-only).

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID. SQL Analytics Endpoints are rejected.
            qualified_table: Qualified table name, e.g. ``dbo.sales``.
            stat_name: Name of the statistic to drop.
        """
        parse_qualified_name(qualified_table, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "delete_statistics ws=%s item=%s table=%r stat=%r",
                ws_id,
                entry.id,
                qualified_table,
                stat_name,
            )
            target = make_sql_target(ws_id, entry, item)
            await statistics_svc.drop_statistics(
                target,
                qualified_table,
                stat_name,
                kind=entry.kind,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"dropped": True}
