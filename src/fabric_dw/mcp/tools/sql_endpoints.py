"""MCP tools for SQL Analytics Endpoint operations."""

from __future__ import annotations

import logging
import os
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
from fabric_dw.mcp._helpers import fabric_err, resolve_item
from fabric_dw.services import permissions as _permissions_svc
from fabric_dw.services import sql_endpoints

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register SQL endpoint tools against *mcp*."""

    @mcp.tool(name="list_sql_endpoints")
    async def list_sql_endpoints(
        workspace: str,
        all_workspaces: bool = False,  # noqa: FBT001, FBT002
    ) -> list[dict[str, Any]]:
        """List all SQL analytics endpoints in a workspace.

        When *all_workspaces* is ``True``, ignore *workspace* and aggregate results
        across every workspace the caller can see.
        """
        _workspaces_allowlist = os.environ.get("FABRIC_MCP_WORKSPACES", "").strip()
        if all_workspaces and _workspaces_allowlist:
            raise ToolError(
                "all_workspaces=True is not permitted when FABRIC_MCP_WORKSPACES is configured; "
                "specify an individual workspace instead"
            )
        if not all_workspaces:
            assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            if all_workspaces:
                _log.debug("list_sql_endpoints all_workspaces=True")
                result = await sql_endpoints.list_all_workspaces(ctx.http)
            else:
                ws_id = await ctx.resolver.workspace_id(workspace)
                assert_workspace_allowed(workspace, str(ws_id))
                _log.debug("list_sql_endpoints ws=%s", ws_id)
                result = await sql_endpoints.list_endpoints(ctx.http, ws_id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [ep.model_dump(by_alias=True, mode="json") for ep in result]

    @mcp.tool(name="get_sql_endpoint")
    async def get_sql_endpoint(workspace: str, endpoint: str) -> dict[str, Any]:
        """Return details for a single SQL analytics endpoint (name or GUID)."""
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, endpoint)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("get_sql_endpoint ws=%s item=%s", ws_id, item.id)
            result = await sql_endpoints.get_endpoint(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="refresh_sql_endpoint_metadata")
    async def refresh_sql_endpoint_metadata(
        workspace: str,
        endpoint: str,
        recreate_tables: bool = False,  # noqa: FBT001, FBT002
    ) -> list[dict[str, Any]]:
        """Refresh metadata for a SQL analytics endpoint (sync from the underlying Lakehouse).

        This is a long-running operation (LRO) that is polled to completion.
        Returns a list of per-table sync results.

        Args:
            workspace: Workspace name or GUID.
            endpoint: SQL analytics endpoint name or GUID.
            recreate_tables: When ``True``, drop and recreate all tables during
                the refresh.  Use to resolve inconsistencies or force a clean
                rebuild.  **Destructive** — use with caution.
        """
        assert_writes_allowed("refresh_sql_endpoint_metadata")
        if recreate_tables:
            assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, endpoint)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "refresh_sql_endpoint_metadata ws=%s item=%s recreate=%s",
                ws_id,
                item.id,
                recreate_tables,
            )
            statuses = await sql_endpoints.refresh_metadata(
                ctx.http, ws_id, item.id, recreate_tables=recreate_tables
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [s.model_dump(by_alias=True, mode="json") for s in statuses]

    @mcp.tool(name="get_sql_endpoint_permissions")
    async def get_sql_endpoint_permissions(
        workspace: str, sql_endpoint: str
    ) -> list[dict[str, Any]]:
        """Return principals with access to a SQL Analytics Endpoint item.

        Requires Fabric Administrator role (admin API).

        See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
        """
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, sql_endpoint)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("get_sql_endpoint_permissions ws=%s item=%s", ws_id, item.id)
            result = await _permissions_svc.list_item_access(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [a.model_dump(by_alias=True, mode="json") for a in result]
