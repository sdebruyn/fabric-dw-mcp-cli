"""MCP tools for permission operations.

Two planes are exposed:

``list_item_permissions``
    Fabric item-level permissions (REST admin API).  Relocated from
    ``get_warehouse_permissions`` and ``get_sql_endpoint_permissions``.

``list_sql_permissions``, ``list_database_principals``, ``my_permissions``
    T-SQL in-database permission reads (read-only tools).

``grant_permission``, ``deny_permission``
    T-SQL GRANT / DENY (mutating tools, blocked by FABRIC_MCP_READONLY,
    NOT destructive-gated).  Both accept an optional ``columns`` parameter
    for column-level security (OBJECT scope only).

``revoke_permission``
    T-SQL REVOKE (mutating tool, blocked by FABRIC_MCP_READONLY,
    ALSO blocked by missing FABRIC_MCP_ALLOW_DESTRUCTIVE -- destructive-gated
    because revoke removes an existing permission).  Also accepts an optional
    ``columns`` parameter for column-level security (OBJECT scope only).
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
from fabric_dw.services import mask as _mask_svc
from fabric_dw.services import permissions as _permissions_svc
from fabric_dw.services import rls as _rls_svc

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
    # T-SQL writes - grant/deny: mutating (FABRIC_MCP_READONLY blocks), NOT destructive
    # T-SQL writes - revoke: mutating AND destructive (also requires FABRIC_MCP_ALLOW_DESTRUCTIVE)
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
        columns: list[str] | None = None,
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
            columns: Optional list of column names for column-level security
                (OBJECT scope only; permissions must be SELECT, UPDATE, or
                REFERENCES). Pass ``None`` (omit) for no column restriction.
                Passing an empty list raises a ``ToolError``.
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
                columns=columns,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "granted": True,
            "permissions": permissions,
            "principal": principal,
            "scope": scope.upper(),
            "columns": columns,
        }

    @mutating_tool(mcp, "deny_permission")
    async def deny_permission_tool(  # noqa: PLR0913
        workspace: str,
        item: str,
        permissions: str,
        principal: str,
        scope: str = "DATABASE",
        schema: str | None = None,
        object_name: str | None = None,
        columns: list[str] | None = None,
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
            columns: Optional list of column names for column-level security
                (OBJECT scope only; permissions must be SELECT, UPDATE, or
                REFERENCES). Pass ``None`` (omit) for no column restriction.
                Passing an empty list raises a ``ToolError``.
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
                columns=columns,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "denied": True,
            "permissions": permissions,
            "principal": principal,
            "scope": scope.upper(),
            "columns": columns,
        }

    @mutating_tool(mcp, "revoke_permission", destructive=True)
    async def revoke_permission_tool(  # noqa: PLR0913
        workspace: str,
        item: str,
        permissions: str,
        principal: str,
        scope: str = "DATABASE",
        schema: str | None = None,
        object_name: str | None = None,
        columns: list[str] | None = None,
        grant_option_only: bool = False,  # noqa: FBT001, FBT002
        cascade: bool = False,  # noqa: FBT001, FBT002
    ) -> dict[str, Any]:
        """Revoke permissions on a securable from a principal.

        Executes ``REVOKE <permissions> ON <scope> FROM <principal>``.
        Blocked by ``FABRIC_MCP_READONLY``.  Requires
        ``FABRIC_MCP_ALLOW_DESTRUCTIVE=1`` because revoke removes an existing
        permission (destructive operation).

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
            columns: Optional list of column names for column-level security
                (OBJECT scope only; permissions must be SELECT, UPDATE, or
                REFERENCES). Pass ``None`` (omit) for no column restriction.
                Passing an empty list raises a ``ToolError``.
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
                columns=columns,
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
            "scope": scope.upper(),
            "columns": columns,
        }

    # -------------------------------------------------------------------------
    # Row-level security (RLS) tools
    # -------------------------------------------------------------------------

    @mcp.tool(name="list_security_policies")
    async def list_security_policies_tool(workspace: str, item: str) -> list[dict[str, Any]]:
        """List row-level security policies from sys.security_policies.

        Returns all security policies and their predicates for the target
        Data Warehouse or SQL Analytics Endpoint.

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
            _log.debug("list_security_policies ws=%s item=%s", ws_id, entry.id)
            target = make_sql_target(ws_id, entry, item)
            result = await _rls_svc.list_security_policies(target, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [p.model_dump(mode="json") for p in result]

    @mutating_tool(mcp, "create_security_policy")
    async def create_security_policy_tool(
        workspace: str,
        item: str,
        policy_name: str,
        predicates: list[dict[str, Any]],
        state: bool = True,  # noqa: FBT001, FBT002
    ) -> dict[str, Any]:
        """Create a row-level security policy.

        Executes ``CREATE SECURITY POLICY`` with one or more FILTER
        predicates. There is no predicate-type option (#966): Fabric Data
        Warehouse supports FILTER predicates only. Each entry in *predicates*
        must include:

        - ``fn_schema``: schema of the predicate function
        - ``fn_name``: name of the predicate function
        - ``fn_args``: list of column names to pass to the function
        - ``table_schema``: schema of the target table
        - ``table_name``: name of the target table

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            policy_name: Qualified policy name (``"schema.name"`` or ``"name"``).
            predicates: List of predicate definitions (see above).
            state: Initial policy state -- ``True`` to enable, ``False`` to disable
                (default: ``True``).
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "create_security_policy ws=%s item=%s policy=%r",
                ws_id,
                entry.id,
                policy_name,
            )
            target = make_sql_target(ws_id, entry, item)
            await _rls_svc.create_security_policy(
                target,
                policy_name,
                predicates,
                state=state,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"created": True, "policy_name": policy_name, "state": state}

    @mutating_tool(mcp, "add_security_predicate")
    async def add_security_predicate_tool(  # noqa: PLR0913
        workspace: str,
        item: str,
        policy_name: str,
        fn_name: str,
        fn_args: list[str],
        table_schema: str,
        table_name: str,
        fn_schema: str | None = None,
    ) -> dict[str, Any]:
        """Add a FILTER predicate to an existing row-level security policy.

        Executes ``ALTER SECURITY POLICY ... ADD FILTER PREDICATE``. There is
        no predicate-type or operation parameter (#966): Fabric Data
        Warehouse supports FILTER predicates only.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            policy_name: Qualified policy name (``"schema.name"`` or ``"name"``).
            fn_name: Name of the predicate function.
            fn_args: Column names to pass to the predicate function.
            table_schema: Schema name of the target table.
            table_name: Name of the target table.
            fn_schema: Schema name of the predicate function (optional -- omit
                when the function lives in the default schema).
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "add_security_predicate ws=%s item=%s policy=%r",
                ws_id,
                entry.id,
                policy_name,
            )
            target = make_sql_target(ws_id, entry, item)
            await _rls_svc.add_predicate(
                target,
                policy_name,
                fn_schema,
                fn_name,
                fn_args,
                table_schema,
                table_name,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "added": True,
            "policy_name": policy_name,
            "predicate_type": "FILTER",
            "table": f"{table_schema}.{table_name}",
        }

    @mutating_tool(mcp, "drop_security_predicate")
    async def drop_security_predicate_tool(
        workspace: str,
        item: str,
        policy_name: str,
        table_schema: str,
        table_name: str,
    ) -> dict[str, Any]:
        """Drop the FILTER predicate from an existing row-level security policy.

        Executes ``ALTER SECURITY POLICY ... DROP FILTER PREDICATE ON``. The
        T-SQL ``DROP PREDICATE ON`` syntax takes no operation qualifier.
        There is no predicate-type parameter (#966): Fabric Data Warehouse
        supports FILTER predicates only.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            policy_name: Qualified policy name (``"schema.name"`` or ``"name"``).
            table_schema: Schema name of the target table.
            table_name: Name of the target table.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "drop_security_predicate ws=%s item=%s policy=%r",
                ws_id,
                entry.id,
                policy_name,
            )
            target = make_sql_target(ws_id, entry, item)
            await _rls_svc.drop_predicate(
                target,
                policy_name,
                table_schema,
                table_name,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "dropped": True,
            "policy_name": policy_name,
            "predicate_type": "FILTER",
            "table": f"{table_schema}.{table_name}",
        }

    @mutating_tool(mcp, "set_security_policy_state")
    async def set_security_policy_state_tool(
        workspace: str,
        item: str,
        policy_name: str,
        enabled: bool,  # noqa: FBT001
    ) -> dict[str, Any]:
        """Enable or disable a row-level security policy.

        Executes ``ALTER SECURITY POLICY ... WITH (STATE = ON|OFF)``.
        Not destructive -- enabling or disabling a policy is reversible.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            policy_name: Qualified policy name (``"schema.name"`` or ``"name"``).
            enabled: ``True`` to enable the policy, ``False`` to disable it.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "set_security_policy_state ws=%s item=%s policy=%r enabled=%r",
                ws_id,
                entry.id,
                policy_name,
                enabled,
            )
            target = make_sql_target(ws_id, entry, item)
            await _rls_svc.set_policy_state(
                target, policy_name, enabled=enabled, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"policy_name": policy_name, "enabled": enabled}

    @mutating_tool(mcp, "drop_security_policy", destructive=True)
    async def drop_security_policy_tool(
        workspace: str,
        item: str,
        policy_name: str,
    ) -> dict[str, Any]:
        """Drop a row-level security policy.

        Executes ``DROP SECURITY POLICY``.  This is a permanently destructive
        operation -- the policy and all its predicates are removed.
        Requires ``FABRIC_MCP_ALLOW_DESTRUCTIVE=1``.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            policy_name: Qualified policy name (``"schema.name"`` or ``"name"``).
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "drop_security_policy ws=%s item=%s policy=%r",
                ws_id,
                entry.id,
                policy_name,
            )
            target = make_sql_target(ws_id, entry, item)
            await _rls_svc.drop_security_policy(target, policy_name, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"dropped": True, "policy_name": policy_name}

    # -------------------------------------------------------------------------
    # Dynamic data masking tools
    # -------------------------------------------------------------------------

    @mcp.tool(name="list_masked_columns")
    async def list_masked_columns_tool(
        workspace: str,
        item: str,
        table_schema: str | None = None,
        table_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """List columns with dynamic data masking from sys.masked_columns.

        Returns all masked columns on the target Data Warehouse or SQL Analytics
        Endpoint.  Filter by *table_schema* and/or *table_name* to narrow results.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            table_schema: Optional schema filter (case-insensitive). Pass ``None``
                to include all schemas.
            table_name: Optional table name filter (case-insensitive). Pass ``None``
                to include all tables.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_masked_columns ws=%s item=%s", ws_id, entry.id)
            target = make_sql_target(ws_id, entry, item)
            result = await _mask_svc.list_masked_columns(
                target,
                table_schema=table_schema,
                table_name=table_name,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [c.model_dump(mode="json") for c in result]

    @mutating_tool(mcp, "set_column_mask")
    async def set_column_mask_tool(  # noqa: PLR0913
        workspace: str,
        item: str,
        table_schema: str,
        table_name: str,
        column_name: str,
        fn_type: str,
        start: int | None = None,
        end: int | None = None,
        prefix: int | None = None,
        padding: str | None = None,
        suffix: int | None = None,
    ) -> dict[str, Any]:
        """Apply or replace a dynamic data mask on a column.

        Executes ``ALTER TABLE ... ALTER COLUMN ... ADD MASKED WITH (FUNCTION = '...')``.
        ``ADD MASKED`` replaces any existing mask on the column without error.

        Blocked by ``FABRIC_MCP_READONLY``.

        Supported mask function types:

        - ``"default"`` -- full masking; no extra args.
        - ``"email"`` -- email masking (exposes first char and ``".com"`` suffix); no extra args.
        - ``"random"`` -- numeric random mask; requires *start* and *end*.
        - ``"partial"`` -- custom string partial mask; requires *prefix*, *padding*, and *suffix*.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            table_schema: Schema name of the target table.
            table_name: Name of the target table.
            column_name: Name of the column to mask.
            fn_type: Mask function type -- ``"default"``, ``"email"``, ``"random"``,
                or ``"partial"`` (case-insensitive).
            start: Lower bound for ``random()`` masking (required when *fn_type* is
                ``"random"``). Must be <= *end*.
            end: Upper bound for ``random()`` masking (required when *fn_type* is
                ``"random"``).
            prefix: Leading characters to expose for ``partial()`` masking (required
                when *fn_type* is ``"partial"``).
            padding: Replacement padding string for ``partial()`` masking (required
                when *fn_type* is ``"partial"``). Must not contain ``"``, ``)``,
                ``;``, ``--``, control characters (including U+0085, U+2028,
                U+2029), and must not exceed 128 characters.
            suffix: Trailing characters to expose for ``partial()`` masking (required
                when *fn_type* is ``"partial"``).
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            target = make_sql_target(ws_id, entry, item)
            mask_fn_literal = await _mask_svc.set_column_mask(
                target,
                table_schema,
                table_name,
                column_name,
                fn_type,
                start=start,
                end=end,
                prefix=prefix,
                padding=padding,
                suffix=suffix,
                mode=ctx.auth_mode,
            )
            _log.debug(
                "set_column_mask ws=%s item=%s table=%s.%s col=%s fn=%r",
                ws_id,
                entry.id,
                table_schema,
                table_name,
                column_name,
                mask_fn_literal,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "masked": True,
            "table_schema": table_schema,
            "table_name": table_name,
            "column_name": column_name,
            "masking_function": mask_fn_literal,
        }

    @mutating_tool(mcp, "drop_column_mask", destructive=True)
    async def drop_column_mask_tool(
        workspace: str,
        item: str,
        table_schema: str,
        table_name: str,
        column_name: str,
    ) -> dict[str, Any]:
        """Remove a dynamic data mask from a column.

        Executes ``ALTER TABLE ... ALTER COLUMN ... DROP MASKED``.
        This is a permanently destructive operation -- the mask is removed from the
        column and unmasked values become visible to all users who query the column.
        Requires ``FABRIC_MCP_ALLOW_DESTRUCTIVE=1``.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            table_schema: Schema name of the target table.
            table_name: Name of the target table.
            column_name: Name of the column whose mask to remove.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "drop_column_mask ws=%s item=%s table=%s.%s col=%s",
                ws_id,
                entry.id,
                table_schema,
                table_name,
                column_name,
            )
            target = make_sql_target(ws_id, entry, item)
            await _mask_svc.drop_column_mask(
                target,
                table_schema,
                table_name,
                column_name,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "dropped": True,
            "table_schema": table_schema,
            "table_name": table_name,
            "column_name": column_name,
        }
