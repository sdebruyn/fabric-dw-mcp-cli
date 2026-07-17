"""MCP tools for running query and connection management."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_workspace_allowed, assert_writes_allowed
from fabric_dw.mcp._helpers import (
    make_sql_target,
    parse_iso8601,
    resolve_item,
    tool_err,
)
from fabric_dw.services import queries
from fabric_dw.services import query_insights as _qi_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register query tools against *mcp*."""

    @mcp.tool(name="list_running_queries")
    async def list_running_queries(workspace: str, item: str) -> list[dict[str, Any]]:
        """Return all currently-executing queries on a warehouse or SQL Analytics Endpoint.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_running_queries ws=%s item=%s", ws_id, entry.id)
            target = make_sql_target(ws_id, entry, item)
            result = await queries.list_running(target, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            _log.debug(
                "list_running_queries: unhandled driver exception (suppressed)", exc_info=True
            )
            raise ToolError(
                "Listing running queries failed due to a driver or network error."
            ) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]

    @mcp.tool(name="list_connections")
    async def list_connections(workspace: str, item: str) -> list[dict[str, Any]]:
        """Return all active SQL connections on a warehouse or SQL Analytics Endpoint.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_connections ws=%s item=%s", ws_id, entry.id)
            target = make_sql_target(ws_id, entry, item)
            result = await queries.list_connections(target, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            _log.debug("list_connections: unhandled driver exception (suppressed)", exc_info=True)
            raise ToolError("Listing connections failed due to a driver or network error.") from exc
        return [c.model_dump(by_alias=True, mode="json") for c in result]

    @mcp.tool(name="kill_session")
    async def kill_session(
        workspace: str,
        item: str,
        session_id: Annotated[int, Field(ge=1)],
    ) -> dict[str, Any]:
        """Terminate a session on a warehouse by session_id.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            session_id: Session ID to terminate (must be a positive integer).
        """
        assert_writes_allowed("kill_session")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("kill_session ws=%s item=%s session=%s", ws_id, entry.id, session_id)
            target = make_sql_target(ws_id, entry, item)
            await queries.kill(target, session_id, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            _log.debug("kill_session: unhandled driver exception (suppressed)", exc_info=True)
            raise ToolError("Session kill failed due to a driver or network error.") from exc
        return {"killed": True, "session_id": session_id}

    @mcp.tool(name="list_request_history")
    async def list_request_history(
        workspace: str,
        item: str,
        limit: Annotated[int, Field(ge=1, le=10000)] = 100,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return completed SQL requests from queryinsights.exec_requests_history.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            limit: Maximum rows to return (1-10000, default 100).
            since: Optional ISO-8601 lower bound on submit_time.
            until: Optional ISO-8601 upper bound on submit_time.
        """
        since_dt = parse_iso8601(since, "since")
        until_dt = parse_iso8601(until, "until")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_request_history ws=%s item=%s limit=%d", ws_id, entry.id, limit)
            target = make_sql_target(ws_id, entry, item)
            result = await _qi_svc.list_request_history(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            _log.debug(
                "list_request_history: unhandled driver exception (suppressed)", exc_info=True
            )
            raise ToolError(
                "Request history query failed due to a driver or network error."
            ) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]

    @mcp.tool(name="list_session_history")
    async def list_session_history(
        workspace: str,
        item: str,
        limit: Annotated[int, Field(ge=1, le=10000)] = 100,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return completed sessions from queryinsights.exec_sessions_history.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            limit: Maximum rows to return (1-10000, default 100).
            since: Optional ISO-8601 lower bound on session_start_time.
            until: Optional ISO-8601 upper bound on session_start_time.
        """
        since_dt = parse_iso8601(since, "since")
        until_dt = parse_iso8601(until, "until")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_session_history ws=%s item=%s limit=%d", ws_id, entry.id, limit)
            target = make_sql_target(ws_id, entry, item)
            result = await _qi_svc.list_session_history(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            _log.debug(
                "list_session_history: unhandled driver exception (suppressed)", exc_info=True
            )
            raise ToolError(
                "Session history query failed due to a driver or network error."
            ) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]

    @mcp.tool(name="list_frequent_queries")
    async def list_frequent_queries(
        workspace: str,
        item: str,
        limit: Annotated[int, Field(ge=1, le=10000)] = 100,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return frequently-run queries from queryinsights.frequently_run_queries.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            limit: Maximum rows to return (1-10000, default 100).
            since: Optional ISO-8601 lower bound on last_run_start_time.
            until: Optional ISO-8601 upper bound on last_run_start_time.
        """
        since_dt = parse_iso8601(since, "since")
        until_dt = parse_iso8601(until, "until")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_frequent_queries ws=%s item=%s limit=%d", ws_id, entry.id, limit)
            target = make_sql_target(ws_id, entry, item)
            result = await _qi_svc.list_frequent_queries(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            _log.debug(
                "list_frequent_queries: unhandled driver exception (suppressed)", exc_info=True
            )
            raise ToolError(
                "Frequent queries lookup failed due to a driver or network error."
            ) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]

    @mcp.tool(name="list_locks")
    async def list_locks(  # noqa: PLR0913
        workspace: str,
        item: str,
        limit: Annotated[int, Field(ge=1, le=10000)] = 100,
        waiting_only: bool = False,  # noqa: FBT001, FBT002
        blocked_only: bool = False,  # noqa: FBT001, FBT002
        include_database: bool = False,  # noqa: FBT001, FBT002
    ) -> list[dict[str, Any]]:
        """Return active lock rows from sys.dm_tran_locks joined with sys.dm_exec_requests.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            limit: Maximum rows to return (1-10000, default 100).
            waiting_only: When True, restrict to locks with request_status WAIT or CONVERT.
            blocked_only: When True, show only blocked sessions (victims).
                The blocker's session_id appears in blocking_session_id.
            include_database: When True, include DATABASE-scoped lock rows (excluded by default).
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_locks ws=%s item=%s limit=%d", ws_id, entry.id, limit)
            target = make_sql_target(ws_id, entry, item)
            result = await queries.list_locks(
                target,
                limit=limit,
                waiting_only=waiting_only,
                blocked_only=blocked_only,
                include_database=include_database,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            _log.debug("list_locks: unhandled driver exception (suppressed)", exc_info=True)
            raise ToolError("Listing locks failed due to a driver or network error.") from exc
        return [lock.model_dump(by_alias=True, mode="json") for lock in result]

    @mcp.tool(name="get_request_detail")
    async def get_request_detail(
        workspace: str,
        item: str,
        dist_statement_id: str,
    ) -> dict[str, Any] | None:
        """Look up a completed query from queryinsights.exec_requests_history.

        Uses distributed_statement_id to retrieve full query text and execution
        metrics after the query completes.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            dist_statement_id: The GUID identifying the distributed statement to look up.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "get_request_detail ws=%s item=%s dist_statement_id=%s",
                ws_id,
                entry.id,
                dist_statement_id,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await _qi_svc.get_request_detail(target, dist_statement_id, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            _log.debug("get_request_detail: unhandled driver exception (suppressed)", exc_info=True)
            raise ToolError(
                "Request detail lookup failed due to a driver or network error."
            ) from exc
        if result is None:
            return None
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="list_long_running_queries")
    async def list_long_running_queries(
        workspace: str,
        item: str,
        limit: Annotated[int, Field(ge=1, le=10000)] = 100,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return long-running queries from queryinsights.long_running_queries.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            limit: Maximum rows to return (1-10000, default 100).
            since: Optional ISO-8601 lower bound on last_run_start_time.
            until: Optional ISO-8601 upper bound on last_run_start_time.
        """
        since_dt = parse_iso8601(since, "since")
        until_dt = parse_iso8601(until, "until")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_long_running_queries ws=%s item=%s limit=%d", ws_id, entry.id, limit)
            target = make_sql_target(ws_id, entry, item)
            result = await _qi_svc.list_long_running_queries(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            _log.debug(
                "list_long_running_queries: unhandled driver exception (suppressed)", exc_info=True
            )
            raise ToolError(
                "Long-running queries lookup failed due to a driver or network error."
            ) from exc
        return [q.model_dump(by_alias=True, mode="json") for q in result]
