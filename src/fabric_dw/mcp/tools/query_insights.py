"""MCP tools for Query Insights DMV operations."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_workspace_allowed
from fabric_dw.mcp._helpers import fabric_err, make_sql_target, resolve_item
from fabric_dw.services import query_insights as _qi_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


def _parse_dt(value: str | None, param: str) -> datetime | None:
    """Parse an ISO-8601 string to datetime, raising ToolError on bad input."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"invalid {param} {value!r}: expected ISO-8601") from exc


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register query insights tools against *mcp*."""

    @mcp.tool(name="list_request_history")
    async def list_request_history(
        workspace: str,
        warehouse: str,
        limit: Annotated[int, Field(ge=1, le=10000)] = 100,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return completed SQL requests from queryinsights.exec_requests_history.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL Analytics Endpoint name or GUID.
            limit: Maximum rows to return (1-10000, default 100).
            since: Optional ISO-8601 lower bound on submit_time.
            until: Optional ISO-8601 upper bound on submit_time.
        """
        since_dt = _parse_dt(since, "since")
        until_dt = _parse_dt(until, "until")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_request_history ws=%s item=%s limit=%d", ws_id, item.id, limit)
            target = make_sql_target(ws_id, item, warehouse)
            result = await _qi_svc.list_request_history(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth_mode
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]

    @mcp.tool(name="list_session_history")
    async def list_session_history(
        workspace: str,
        warehouse: str,
        limit: Annotated[int, Field(ge=1, le=10000)] = 100,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return completed sessions from queryinsights.exec_sessions_history.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL Analytics Endpoint name or GUID.
            limit: Maximum rows to return (1-10000, default 100).
            since: Optional ISO-8601 lower bound on session_start_time.
            until: Optional ISO-8601 upper bound on session_start_time.
        """
        since_dt = _parse_dt(since, "since")
        until_dt = _parse_dt(until, "until")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_session_history ws=%s item=%s limit=%d", ws_id, item.id, limit)
            target = make_sql_target(ws_id, item, warehouse)
            result = await _qi_svc.list_session_history(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth_mode
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]

    @mcp.tool(name="list_frequent_queries")
    async def list_frequent_queries(
        workspace: str,
        warehouse: str,
        limit: Annotated[int, Field(ge=1, le=10000)] = 100,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return frequently-run queries from queryinsights.frequently_run_queries.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL Analytics Endpoint name or GUID.
            limit: Maximum rows to return (1-10000, default 100).
            since: Optional ISO-8601 lower bound on last_run_start_time.
            until: Optional ISO-8601 upper bound on last_run_start_time.
        """
        since_dt = _parse_dt(since, "since")
        until_dt = _parse_dt(until, "until")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_frequent_queries ws=%s item=%s limit=%d", ws_id, item.id, limit)
            target = make_sql_target(ws_id, item, warehouse)
            result = await _qi_svc.list_frequent_queries(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth_mode
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]

    @mcp.tool(name="list_long_running_queries")
    async def list_long_running_queries(
        workspace: str,
        warehouse: str,
        limit: Annotated[int, Field(ge=1, le=10000)] = 100,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return long-running queries from queryinsights.long_running_queries.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL Analytics Endpoint name or GUID.
            limit: Maximum rows to return (1-10000, default 100).
            since: Optional ISO-8601 lower bound on last_run_start_time.
            until: Optional ISO-8601 upper bound on last_run_start_time.
        """
        since_dt = _parse_dt(since, "since")
        until_dt = _parse_dt(until, "until")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_long_running_queries ws=%s item=%s limit=%d", ws_id, item.id, limit)
            target = make_sql_target(ws_id, item, warehouse)
            result = await _qi_svc.list_long_running_queries(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth_mode
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]

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
        since_dt = _parse_dt(since, "since")
        until_dt = _parse_dt(until, "until")
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
