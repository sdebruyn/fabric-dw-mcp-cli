"""MCP tools for warehouse settings operations."""

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
    resolve_item,
    tool_err,
)
from fabric_dw.services import settings as settings_svc
from fabric_dw.services.settings import RETENTION_MAX, RETENTION_MIN

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register settings tools against *mcp*."""

    @mcp.tool(name="get_warehouse_settings")
    async def get_warehouse_settings(
        workspace: str,
        item: str,
    ) -> dict[str, Any]:
        """Return the current server-side database settings for a warehouse.

        Reads ``result_set_caching``, ``time_travel_retention_days``, and
        ``time_travel_retention_cutoff_date`` from ``sys.databases``.

        Both Data Warehouses and SQL Analytics Endpoints are supported.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "get_warehouse_settings ws=%s item=%s",
                ws_id,
                entry.id,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await settings_svc.get_settings(
                target,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "set_result_set_caching")
    async def set_result_set_caching(
        workspace: str,
        item: str,
        enabled: bool,  # noqa: FBT001
    ) -> dict[str, Any]:
        """Enable or disable result-set caching on a warehouse.

        Executes ``ALTER DATABASE CURRENT SET RESULT_SET_CACHING { ON | OFF }``
        and returns the effective settings read back after the change.

        Both Data Warehouses and SQL Analytics Endpoints accept the statement,
        though the practical effect on SQL Analytics Endpoints is not guaranteed.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            enabled: ``True`` to enable result-set caching, ``False`` to disable it.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "set_result_set_caching ws=%s item=%s enabled=%s",
                ws_id,
                entry.id,
                enabled,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await settings_svc.set_result_set_caching(
                target,
                enabled=enabled,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "set_time_travel_retention")
    async def set_time_travel_retention(
        workspace: str,
        item: str,
        days: Annotated[int, Field(ge=RETENTION_MIN, le=RETENTION_MAX)],
    ) -> dict[str, Any]:
        """Set the time-travel retention period on a warehouse.

        Executes ``ALTER DATABASE CURRENT SET TIME_TRAVEL_RETENTION_PERIOD = <n> DAYS``
        and returns the effective settings read back after the change.

        The time-travel retention feature is primarily a Data Warehouse concept.
        Running this on a SQL Analytics Endpoint is allowed but may be a no-op.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            days: Retention period in days. Must be in the range 1-120 (inclusive).
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "set_time_travel_retention ws=%s item=%s days=%s",
                ws_id,
                entry.id,
                days,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await settings_svc.set_time_travel_retention(
                target,
                days,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")
