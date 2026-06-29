"""MCP tools for permission operations.

Two planes are exposed:

``list_item_permissions``
    Fabric item-level permissions (REST admin API).  Relocated from
    ``get_warehouse_permissions`` and ``get_sql_endpoint_permissions``.

``list_sql_permissions``, ``list_database_principals``, ``my_permissions``
    T-SQL in-database permission reads (read-only tools).

``grant_permission``, ``deny_permission``, ``revoke_permission``
    T-SQL GRANT / DENY / REVOKE (mutating tools, blocked by FABRIC_MCP_READONLY,
    NOT destructive-gated).
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_workspace_allowed
from fabric_dw.mcp._helpers import (
    fabric_err,
    make_sql_target,
    mutating_tool,
    resolve_item,
    tool_err,
)
from fabric_dw.services import permissions as _permissions_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register permission tools against *mcp*."""

    # -------------------------------------------------------------------------
    # Item-level (REST admin API) - read-only
    # -------------------------------------------------------------------------

    @mcp.tool(name="list_item_permissions")
    async def list_item_permissions(workspace: str, item: str) -> list[dict[str, Any]]:
        """Return principals with access to a Warehouse or SQL Analytics Endpoint item.

        Uses the Fabric admin API.  Accepts both Data Warehouses and SQL Analytics
        Endpoints -- the item kind is resolved automatically from its GUID.

        Requires Fabric Administrator role (admin API).

        See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_item_permissions ws=%s item=%s", ws_id, entry.id)
            result = await _permissions_svc.list_item_access(ctx.http, ws_id, entry.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [a.model_dump(by_alias=True, mode="json") for a in result]

    # -------------------------------------------------------------------------
    # T-SQL reads - read-only
    # -------------------------------------------------------------------------

    @mcp.tool(name="list_sql_permissions")
    async def list_sql_permissions_tool(
        workspace: str,
        item: str,
        principal: str | None = None,
        schema: str | None = None,
        object_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """List T-SQL database permissions from sys.database_permissions.

        Reads from sys.database_permissions joined to sys.database_principals.
        Returns DATABASE, SCHEMA, and OBJECT class securables with readable names.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            principal: Filter by principal name (optional).
            schema: Filter by schema name -- returns SCHEMA class rows for this
                schema (optional).
            object_name: Filter by qualified object name ``<schema>.<object>``
                -- returns OBJECT class rows for this object (optional).
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_sql_permissions ws=%s item=%s", ws_id, entry.id)
            target = make_sql_target(ws_id, entry, item)
            result = await _permissions_svc.list_sql_permissions(
                target,
                principal=principal,
                schema=schema,
                object_name=object_name,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [p.model_dump(mode="json") for p in result]

    @mcp.tool(name="list_database_principals")
    async def list_database_principals_tool(
        workspace: str,
        item: str,
        principal_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List database principals from sys.database_principals.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            principal_type: Filter by type -- ``"user"`` for users, ``"role"`` for
                database roles, ``"all"`` or omit for no filter.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "list_database_principals ws=%s item=%s type=%r", ws_id, entry.id, principal_type
            )
            target = make_sql_target(ws_id, entry, item)
            result = await _permissions_svc.list_database_principals(
                target,
                principal_type=principal_type,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [p.model_dump(mode="json") for p in result]

    @mcp.tool(name="my_permissions")
    async def my_permissions_tool(
        workspace: str,
        item: str,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return permissions for the current connection via sys.fn_my_permissions.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            scope: Scope string -- ``"database"`` (default), ``"schema:<name>"``,
                or ``"object:<schema>.<object>"``.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("my_permissions ws=%s item=%s scope=%r", ws_id, entry.id, scope)
            target = make_sql_target(ws_id, entry, item)
            result = await _permissions_svc.my_permissions(
                target,
                scope=scope,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result

    # -------------------------------------------------------------------------
    # T-SQL writes - mutating (FABRIC_MCP_READONLY blocks), NOT destructive
    # -------------------------------------------------------------------------

    @mutating_tool(mcp, "grant_permission")
    async def grant_permission_tool(  # noqa: PLR0913
        workspace: str,
        item: str,
        permissions: str,
        principal: str,
        scope: str = "DATABASE",
        schema: str | None = None,
        object_name: str | None = None,
        with_grant_option: bool = False,  # noqa: FBT001, FBT002
    ) -> dict[str, Any]:
        """Grant permissions on a securable to a principal.

        Executes ``GRANT <permissions> ON <scope> TO <principal>``.
        Blocked by ``FABRIC_MCP_READONLY``.  Does NOT require
        ``FABRIC_MCP_ALLOW_DESTRUCTIVE``.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            permissions: Comma-separated permission tokens (e.g. ``"SELECT,INSERT"``).
            principal: Grantee principal name (Entra UPN, app GUID, or role name).
            scope: Securable class -- ``"DATABASE"`` (default), ``"SCHEMA"``, or
                ``"OBJECT"``.
            schema: Schema name (required when scope is ``"SCHEMA"``).
            object_name: Qualified object name ``<schema>.<object>`` (required when
                scope is ``"OBJECT"``).
            with_grant_option: When ``True``, allows the grantee to grant the
                permission to others (adds ``WITH GRANT OPTION``).
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "grant_permission ws=%s item=%s perms=%r principal=%r scope=%r",
                ws_id,
                entry.id,
                permissions,
                principal,
                scope,
            )
            target = make_sql_target(ws_id, entry, item)
            await _permissions_svc.grant_permission(
                target,
                permissions,
                principal,
                scope.upper(),
                schema=schema,
                object_name=object_name,
                with_grant_option=with_grant_option,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"granted": True, "permissions": permissions, "principal": principal, "scope": scope}

    @mutating_tool(mcp, "deny_permission")
    async def deny_permission_tool(  # noqa: PLR0913
        workspace: str,
        item: str,
        permissions: str,
        principal: str,
        scope: str = "DATABASE",
        schema: str | None = None,
        object_name: str | None = None,
    ) -> dict[str, Any]:
        """Deny permissions on a securable to a principal.

        Executes ``DENY <permissions> ON <scope> TO <principal>``.
        Blocked by ``FABRIC_MCP_READONLY``.  Does NOT require
        ``FABRIC_MCP_ALLOW_DESTRUCTIVE``.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            permissions: Comma-separated permission tokens (e.g. ``"SELECT"``).
            principal: Principal name to deny (Entra UPN, app GUID, or role name).
            scope: Securable class -- ``"DATABASE"`` (default), ``"SCHEMA"``, or
                ``"OBJECT"``.
            schema: Schema name (required when scope is ``"SCHEMA"``).
            object_name: Qualified object name ``<schema>.<object>`` (required when
                scope is ``"OBJECT"``).
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "deny_permission ws=%s item=%s perms=%r principal=%r scope=%r",
                ws_id,
                entry.id,
                permissions,
                principal,
                scope,
            )
            target = make_sql_target(ws_id, entry, item)
            await _permissions_svc.deny_permission(
                target,
                permissions,
                principal,
                scope.upper(),
                schema=schema,
                object_name=object_name,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"denied": True, "permissions": permissions, "principal": principal, "scope": scope}

    @mutating_tool(mcp, "revoke_permission")
    async def revoke_permission_tool(  # noqa: PLR0913
        workspace: str,
        item: str,
        permissions: str,
        principal: str,
        scope: str = "DATABASE",
        schema: str | None = None,
        object_name: str | None = None,
        grant_option_only: bool = False,  # noqa: FBT001, FBT002
        cascade: bool = False,  # noqa: FBT001, FBT002
    ) -> dict[str, Any]:
        """Revoke permissions on a securable from a principal.

        Executes ``REVOKE <permissions> ON <scope> FROM <principal>``.
        Blocked by ``FABRIC_MCP_READONLY``.  Does NOT require
        ``FABRIC_MCP_ALLOW_DESTRUCTIVE``.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            permissions: Comma-separated permission tokens (e.g. ``"SELECT,INSERT"``).
            principal: Principal name to revoke from (Entra UPN, app GUID, or role name).
            scope: Securable class -- ``"DATABASE"`` (default), ``"SCHEMA"``, or
                ``"OBJECT"``.
            schema: Schema name (required when scope is ``"SCHEMA"``).
            object_name: Qualified object name ``<schema>.<object>`` (required when
                scope is ``"OBJECT"``).
            grant_option_only: When ``True``, revokes only the grant option (adds
                ``GRANT OPTION FOR``), leaving the base permission in place.
            cascade: When ``True``, cascades the revocation to principals the
                grantee has granted the permission to (adds ``CASCADE``).
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "revoke_permission ws=%s item=%s perms=%r principal=%r scope=%r",
                ws_id,
                entry.id,
                permissions,
                principal,
                scope,
            )
            target = make_sql_target(ws_id, entry, item)
            await _permissions_svc.revoke_permission(
                target,
                permissions,
                principal,
                scope.upper(),
                schema=schema,
                object_name=object_name,
                grant_option_only=grant_option_only,
                cascade=cascade,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "revoked": True,
            "permissions": permissions,
            "principal": principal,
            "scope": scope,
        }
