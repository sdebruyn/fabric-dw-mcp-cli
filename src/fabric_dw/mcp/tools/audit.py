"""MCP tools for SQL audit settings operations."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_workspace_allowed, assert_writes_allowed
from fabric_dw.mcp._helpers import resolve_item, tool_err
from fabric_dw.services import audit

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register audit tools against *mcp*."""

    @mcp.tool(name="get_audit_settings")
    async def get_audit_settings(workspace: str, warehouse: str) -> dict[str, Any]:
        """Fetch the current SQL audit settings for a warehouse or SQL analytics endpoint.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL analytics endpoint name or GUID.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("get_audit_settings ws=%s item=%s kind=%s", ws_id, item.id, item.kind)
            result = await audit.get_settings(ctx.http, ws_id, item.id, item.kind)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="enable_audit")
    async def enable_audit(
        workspace: str,
        warehouse: str,
        retention_days: Annotated[int, Field(ge=0, le=3650)] = 0,
    ) -> dict[str, Any]:
        """Enable SQL auditing on a warehouse or SQL analytics endpoint.

        CAUTION: Each audit write reads current settings via an eventually-consistent
        GET that may lag a recent PATCH by several minutes. Two audit writes issued
        within that window can cause the second to silently revert the first.
        Space audit writes at least a few minutes apart.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL analytics endpoint name or GUID.
            retention_days: Log retention in days (0-3650; 0 = unlimited). Default 0.
        """
        assert_writes_allowed("enable_audit")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "enable_audit ws=%s item=%s kind=%s retention=%d",
                ws_id,
                item.id,
                item.kind,
                retention_days,
            )
            result = await audit.enable(
                ctx.http, ws_id, item.id, item.kind, retention_days=retention_days
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="disable_audit")
    async def disable_audit(workspace: str, warehouse: str) -> dict[str, Any]:
        """Disable SQL auditing on a warehouse or SQL analytics endpoint.

        CAUTION: Each audit write reads current settings via an eventually-consistent
        GET that may lag a recent PATCH by several minutes. Two audit writes issued
        within that window can cause the second to silently revert the first.
        Space audit writes at least a few minutes apart.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL analytics endpoint name or GUID.
        """
        assert_writes_allowed("disable_audit")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("disable_audit ws=%s item=%s kind=%s", ws_id, item.id, item.kind)
            result = await audit.disable(ctx.http, ws_id, item.id, item.kind)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="set_audit_action_groups")
    async def set_audit_action_groups(
        workspace: str, warehouse: str, action_groups: list[str]
    ) -> dict[str, Any]:
        """Replace the audited action groups for a warehouse or SQL analytics endpoint.

        CAUTION: Each audit write reads current settings via an eventually-consistent
        GET that may lag a recent PATCH by several minutes. The retention period
        read from that GET is round-tripped; if retention was changed within the lag
        window, this call may silently revert it. Space audit writes at least a few
        minutes apart.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL analytics endpoint name or GUID.
            action_groups: List of audit action group names.
        """
        assert_writes_allowed("set_audit_action_groups")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "set_audit_action_groups ws=%s item=%s kind=%s groups=%s",
                ws_id,
                item.id,
                item.kind,
                action_groups,
            )
            result = await audit.set_action_groups(
                ctx.http, ws_id, item.id, action_groups, item.kind
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="add_audit_group")
    async def add_audit_group(workspace: str, warehouse: str, group: str) -> dict[str, Any]:
        """Add a single audit action group without overwriting the others.

        Idempotent -- if the group is already present the current settings are
        returned unchanged.  Auditing must already be enabled.

        CAUTION: changes take effect immediately on the live audit policy.

        CAUTION: Each audit write reads current settings via an eventually-consistent
        GET that may lag a recent PATCH by several minutes. Two audit writes issued
        within that window can cause the second to silently revert the first.
        Space audit writes at least a few minutes apart.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL analytics endpoint name or GUID.
            group: Action group name, e.g. ``BATCH_COMPLETED_GROUP``.
        """
        assert_writes_allowed("add_audit_group")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "add_audit_group ws=%s item=%s kind=%s group=%r", ws_id, item.id, item.kind, group
            )
            result = await audit.add_action_group(ctx.http, ws_id, item.id, group, item.kind)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="remove_audit_group")
    async def remove_audit_group(workspace: str, warehouse: str, group: str) -> dict[str, Any]:
        """Remove a single audit action group without overwriting the others.

        Idempotent -- if the group is not present the current settings are returned
        unchanged.  Auditing must already be enabled.

        CAUTION: changes take effect immediately on the live audit policy.

        CAUTION: Each audit write reads current settings via an eventually-consistent
        GET that may lag a recent PATCH by several minutes. Two audit writes issued
        within that window can cause the second to silently revert the first.
        Space audit writes at least a few minutes apart.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL analytics endpoint name or GUID.
            group: Action group name, e.g. ``BATCH_COMPLETED_GROUP``.
        """
        assert_writes_allowed("remove_audit_group")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "remove_audit_group ws=%s item=%s kind=%s group=%r",
                ws_id,
                item.id,
                item.kind,
                group,
            )
            result = await audit.remove_action_group(ctx.http, ws_id, item.id, group, item.kind)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="set_audit_retention")
    async def set_audit_retention(
        workspace: str,
        warehouse: str,
        days: Annotated[int, Field(ge=1, le=3650)],
    ) -> dict[str, Any]:
        """Update the audit log retention period without changing the audit enabled/disabled state.

        Audit must already be enabled; if disabled, enable it first with ``enable_audit``.

        CAUTION: Each audit write reads current settings via an eventually-consistent
        GET that may lag a recent PATCH by several minutes. Two audit writes issued
        within that window can cause the second to silently revert the first.
        Space audit writes at least a few minutes apart.

        Args:
            workspace: Workspace name or GUID.
            warehouse: Warehouse or SQL analytics endpoint name or GUID.
            days: Retention period in days (1-3650). The API enforces its own upper bound.
        """
        assert_writes_allowed("set_audit_retention")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "set_audit_retention ws=%s item=%s kind=%s days=%d",
                ws_id,
                item.id,
                item.kind,
                days,
            )
            result = await audit.set_retention(ctx.http, ws_id, item.id, item.kind, days=days)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(by_alias=True, mode="json")
