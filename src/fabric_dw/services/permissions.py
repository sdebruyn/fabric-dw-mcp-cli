"""Item access details and T-SQL granular permission services for Microsoft Fabric.

Item-level plane (REST API)
---------------------------
Wraps the admin endpoint ``GET /v1/admin/workspaces/{workspaceId}/items/{itemId}/users``
to return the list of principals (users, groups, service principals) that have
access to a given item, along with their effective permissions.

Caller must be a Fabric Administrator (Tenant.Read.All or Tenant.ReadWrite.All scope).

Reference:
    https://learn.microsoft.com/en-us/rest/api/fabric/admin/items/list-item-access-details

T-SQL granular plane (in-database)
-----------------------------------
Reads from ``sys.database_permissions`` / ``sys.database_principals`` and issues
``GRANT`` / ``DENY`` / ``REVOKE`` statements.  Applies to both Data Warehouses and
SQL Analytics Endpoints.

Statement-building safety
--------------------------
All statements are built from:
- Fixed permission allowlists (``OBJECT_PERMISSIONS``, ``SCHEMA_PERMISSIONS``,
  ``DATABASE_PERMISSIONS``).
- Validated, bracket-quoted identifiers via
  :func:`~fabric_dw.identifiers.validate_identifier` +
  :func:`~fabric_dw.identifiers.quote_identifier`.
- Validated, bracket-quoted principal names via
  :func:`~fabric_dw.identifiers.validate_principal_name` +
  :func:`~fabric_dw.identifiers.quote_principal`.

No SQL text is ever parsed or rewritten.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.identifiers import (
    parse_qualified_name,
    quote_identifier,
    quote_principal,
    validate_identifier,
    validate_principal_name,
)
from fabric_dw.models import DatabasePermission, DatabasePrincipal, ItemAccess
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "DATABASE_PERMISSIONS",
    "OBJECT_PERMISSIONS",
    "SCHEMA_PERMISSIONS",
    "deny_permission",
    "grant_permission",
    "list_database_principals",
    "list_item_access",
    "list_sql_permissions",
    "my_permissions",
    "revoke_permission",
]

# ---------------------------------------------------------------------------
# Item-level (REST) constants
# ---------------------------------------------------------------------------

_ADMIN_HINT = (
    "This endpoint requires Fabric Administrator role. "
    "See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin"
    "?WT.mc_id=MVP_310840 for how to request it."
)

# The admin items/users endpoint paginates under "accessDetails" instead of the
# default "value" key used by most Fabric list endpoints.  Named here so that a
# future schema change is caught at review time rather than silently yielding
# zero results.
_ACCESS_DETAILS_KEY = "accessDetails"

# ---------------------------------------------------------------------------
# T-SQL permission allowlists
# ---------------------------------------------------------------------------

#: Permissions valid on OBJECT-class securables (tables, views, functions, procedures).
OBJECT_PERMISSIONS: frozenset[str] = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "EXECUTE",
        "REFERENCES",
        "ALTER",
        "CONTROL",
        "VIEW DEFINITION",
        "TAKE OWNERSHIP",
    }
)

#: Permissions valid on SCHEMA-class securables.
SCHEMA_PERMISSIONS: frozenset[str] = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "EXECUTE",
        "REFERENCES",
        "ALTER",
        "CONTROL",
        "VIEW DEFINITION",
    }
)

#: Permissions valid on DATABASE-class securables.
DATABASE_PERMISSIONS: frozenset[str] = frozenset(
    {
        "CONNECT",
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "EXECUTE",
        "REFERENCES",
        "ALTER",
        "CONTROL",
        "VIEW DEFINITION",
        "CREATE TABLE",
        "CREATE VIEW",
        "CREATE PROCEDURE",
        "CREATE FUNCTION",
        "CREATE SCHEMA",
    }
)

# Map scope class name -> allowlist
_ALLOWLISTS: dict[str, frozenset[str]] = {
    "OBJECT": OBJECT_PERMISSIONS,
    "SCHEMA": SCHEMA_PERMISSIONS,
    "DATABASE": DATABASE_PERMISSIONS,
}

# ---------------------------------------------------------------------------
# SQL templates (reads)
# ---------------------------------------------------------------------------

_LIST_PERMISSIONS_SQL = """\
SELECT
    pr.name AS principal_name,
    pr.type_desc AS principal_type,
    pe.state_desc AS state,
    pe.permission_name,
    pe.class_desc AS securable_class,
    pe.major_id,
    pe.minor_id,
    COL_NAME(pe.major_id, pe.minor_id) AS column_name
FROM sys.database_principals AS pr
JOIN sys.database_permissions AS pe
    ON pe.grantee_principal_id = pr.principal_id
WHERE pe.class_desc IN ('DATABASE', 'SCHEMA', 'OBJECT_OR_COLUMN')
ORDER BY pr.name, pe.class_desc, pe.permission_name;
"""

_LIST_PRINCIPALS_SQL = """\
SELECT name, type_desc, authentication_type_desc
FROM sys.database_principals
ORDER BY name;
"""

_MY_PERMISSIONS_DATABASE_SQL = """\
SELECT entity_name, subentity_name, permission_name
FROM sys.fn_my_permissions(NULL, 'DATABASE')
ORDER BY permission_name;
"""

_MY_PERMISSIONS_SCHEMA_SQL = """\
SELECT entity_name, subentity_name, permission_name
FROM sys.fn_my_permissions({schema}, 'SCHEMA')
ORDER BY permission_name;
"""

_MY_PERMISSIONS_OBJECT_SQL = """\
SELECT entity_name, subentity_name, permission_name
FROM sys.fn_my_permissions({obj}, 'OBJECT')
ORDER BY permission_name;
"""

# Resolve object name from major_id (OBJECT class securables)
_RESOLVE_OBJECT_SQL = """\
SELECT
    pe.major_id,
    OBJECT_SCHEMA_NAME(pe.major_id) AS schema_name,
    OBJECT_NAME(pe.major_id) AS object_name
FROM sys.database_permissions AS pe
WHERE pe.class_desc = 'OBJECT_OR_COLUMN'
  AND pe.major_id > 0;
"""

# Resolve schema name from major_id (SCHEMA class securables)
_RESOLVE_SCHEMA_SQL = """\
SELECT s.schema_id, s.name AS schema_name
FROM sys.schemas AS s;
"""


# ---------------------------------------------------------------------------
# Item-level (REST) public API
# ---------------------------------------------------------------------------


async def list_item_access(
    http: FabricHttpClient,
    workspace_id: UUID,
    item_id: UUID,
) -> list[ItemAccess]:
    """Return the list of principals with access to *item_id* in *workspace_id*.

    Follows ``continuationUri`` pagination until all pages are consumed.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        item_id: GUID of the item (Warehouse or SQL Endpoint).

    Returns:
        A list of :class:`~fabric_dw.models.ItemAccess` objects, one per principal.

    Raises:
        PermissionDeniedError: If the caller is not a Fabric Administrator (HTTP 403).
        NotFoundError: If the workspace or item does not exist (HTTP 404).
    """
    # The optional ?type= query param is intentionally omitted: Warehouse and
    # SQLEndpoint items do not filter by type, and omitting it returns all principals.
    path = f"/admin/workspaces/{workspace_id}/items/{item_id}/users"

    try:
        return [
            ItemAccess.from_api(raw)
            async for raw in http.iter_paginated(HttpBase.FABRIC, path, key=_ACCESS_DETAILS_KEY)
        ]
    except PermissionDeniedError as exc:
        # Preserve the original HTTP context (status/request_id/body) and surface
        # the remediation text as a hint rather than replacing the message.
        raise PermissionDeniedError(
            str(exc.args[0]) if exc.args else "Permission denied",
            status=exc.status,
            request_id=exc.request_id,
            body=exc.body,
            hint=_ADMIN_HINT,
        ) from exc


# ---------------------------------------------------------------------------
# T-SQL reads
# ---------------------------------------------------------------------------


def _resolve_object_names(
    target: SqlTarget,
    mode: CredentialMode,
) -> dict[int, tuple[str | None, str | None]]:
    """Return a mapping from major_id -> (schema_name, object_name) for OBJECT class perms."""
    obj_cols, obj_rows = run_query(target, _RESOLVE_OBJECT_SQL, mode=mode)
    result: dict[int, tuple[str | None, str | None]] = {}
    for row in obj_rows:
        row_dict = dict(zip(obj_cols, row, strict=True))
        major_id = int(row_dict["major_id"])
        schema_name = row_dict.get("schema_name")
        object_name = row_dict.get("object_name")
        result[major_id] = (
            str(schema_name) if schema_name is not None else None,
            str(object_name) if object_name is not None else None,
        )
    return result


def _resolve_schema_names(
    target: SqlTarget,
    mode: CredentialMode,
) -> dict[int, str]:
    """Return a mapping from schema_id -> schema_name."""
    s_cols, s_rows = run_query(target, _RESOLVE_SCHEMA_SQL, mode=mode)
    result: dict[int, str] = {}
    for row in s_rows:
        d = dict(zip(s_cols, row, strict=True))
        result[int(d["schema_id"])] = str(d["schema_name"])
    return result


async def list_sql_permissions(
    target: SqlTarget,
    *,
    principal: str | None = None,
    schema: str | None = None,
    object_name: str | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[DatabasePermission]:
    """Return database permissions from ``sys.database_permissions``.

    Args:
        target: SQL connection target.
        principal: Filter by principal name (optional).
        schema: Filter by schema name (optional; for SCHEMA class only).
        object_name: Filter by qualified object name ``<schema>.<object>``
            (optional; for OBJECT class only).
        mode: Credential mode for Entra authentication.

    Returns:
        List of :class:`~fabric_dw.models.DatabasePermission` objects.
    """

    def _run() -> list[DatabasePermission]:
        cols, rows = run_query(target, _LIST_PERMISSIONS_SQL, mode=mode)
        obj_map = _resolve_object_names(target, mode)
        schema_map = _resolve_schema_names(target, mode)

        permissions: list[DatabasePermission] = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            principal_name = str(d["principal_name"])
            principal_type = str(d["principal_type"])
            state = str(d["state"])
            perm_name = str(d["permission_name"])
            class_desc = str(d["securable_class"])
            major_id = int(d["major_id"]) if d["major_id"] is not None else 0
            col_name_raw = d.get("column_name")
            col_name: str | None = str(col_name_raw) if col_name_raw is not None else None

            sec_class: str
            sec_schema: str | None = None
            sec_object: str | None = None

            if class_desc == "DATABASE":
                sec_class = "DATABASE"
            elif class_desc == "SCHEMA":
                sec_class = "SCHEMA"
                sec_schema = schema_map.get(major_id)
                col_name = None  # column grants only apply to OBJECT securables
            elif class_desc == "OBJECT_OR_COLUMN":
                sec_class = "OBJECT"
                schema_n, object_n = obj_map.get(major_id, (None, None))
                sec_schema = schema_n
                sec_object = object_n
            else:
                continue

            # Apply optional filters
            if principal is not None and principal_name.lower() != principal.lower():
                continue
            if schema is not None:
                if sec_class == "DATABASE":
                    continue
                if sec_schema is None or sec_schema.lower() != schema.lower():
                    continue
            if object_name is not None:
                if sec_class != "OBJECT":
                    continue
                # object_name is <schema>.<object>
                fq = f"{sec_schema}.{sec_object}" if sec_schema and sec_object else ""
                if fq.lower() != object_name.lower():
                    continue

            permissions.append(
                DatabasePermission(
                    principal_name=principal_name,
                    principal_type=principal_type,
                    state=state,
                    permission_name=perm_name,
                    securable_class=sec_class,
                    schema_name=sec_schema,
                    object_name=sec_object,
                    column_name=col_name,
                )
            )
        return permissions

    return await asyncio.to_thread(_run)


async def list_database_principals(
    target: SqlTarget,
    *,
    principal_type: str | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[DatabasePrincipal]:
    """Return principals from ``sys.database_principals``.

    Args:
        target: SQL connection target.
        principal_type: Filter: ``"user"`` for users, ``"role"`` for roles,
            ``"all"`` or ``None`` for no filter.
        mode: Credential mode for Entra authentication.

    Returns:
        List of :class:`~fabric_dw.models.DatabasePrincipal` objects.
    """

    def _run() -> list[DatabasePrincipal]:
        cols, rows = run_query(target, _LIST_PRINCIPALS_SQL, mode=mode)
        result: list[DatabasePrincipal] = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            name = str(d["name"])
            type_desc = str(d["type_desc"])
            auth_type = str(d["authentication_type_desc"])

            # Apply optional type filter
            if principal_type is not None and principal_type.lower() != "all":
                if principal_type.lower() == "user" and "USER" not in type_desc:
                    continue
                if principal_type.lower() == "role" and "ROLE" not in type_desc:
                    continue

            result.append(
                DatabasePrincipal(
                    name=name,
                    type=type_desc,
                    authentication_type=auth_type,
                )
            )
        return result

    return await asyncio.to_thread(_run)


async def my_permissions(
    target: SqlTarget,
    *,
    scope: str | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[dict[str, str]]:
    """Return permissions for the current connection via ``sys.fn_my_permissions``.

    Args:
        target: SQL connection target.
        scope: Securable scope -- ``None`` or ``"database"`` for database-level,
            ``"schema:<name>"`` for a schema, ``"object:<schema>.<object>"`` for
            an object.
        mode: Credential mode for Entra authentication.

    Returns:
        List of dicts with keys ``entity_name``, ``subentity_name``,
        ``permission_name``.
    """

    def _run() -> list[dict[str, str]]:
        sql: str
        if scope is None or scope.lower() == "database":
            sql = _MY_PERMISSIONS_DATABASE_SQL
        elif scope.lower().startswith("schema:"):
            schema_part = scope[len("schema:") :]
            validate_identifier(schema_part)
            # sys.fn_my_permissions expects an unbracketed, dot-qualified name
            # inside the string literal.  validate_identifier already restricts
            # the charset to [A-Za-z_][A-Za-z0-9_], so embedding it unbracketed
            # in a single-quoted literal is safe.
            sql = _MY_PERMISSIONS_SCHEMA_SQL.format(schema=f"'{schema_part}'")
        elif scope.lower().startswith("object:"):
            obj_part = scope[len("object:") :]
            schema_name, obj_name = parse_qualified_name(obj_part, "object")
            validate_identifier(schema_name)
            validate_identifier(obj_name)
            # Pass as 'schema.object' (unbracketed) -- sys.fn_my_permissions
            # cannot resolve bracket-quoted names in this context.
            sql = _MY_PERMISSIONS_OBJECT_SQL.format(obj=f"'{schema_name}.{obj_name}'")
        else:
            msg = (
                f"Invalid scope {scope!r}: expected 'database', "
                "'schema:<name>', or 'object:<schema>.<object>'"
            )
            raise ValueError(msg)

        cols, rows = run_query(target, sql, mode=mode)
        return [
            {k: str(v) if v is not None else "" for k, v in zip(cols, row, strict=True)}
            for row in rows
        ]

    return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Statement building helpers
# ---------------------------------------------------------------------------


def _validate_permissions(permissions_str: str, scope_class: str) -> list[str]:
    """Parse, upper-case, and validate a comma-separated permission string.

    Args:
        permissions_str: Comma-separated permission tokens (e.g. ``"SELECT,INSERT"``).
        scope_class: One of ``"DATABASE"``, ``"SCHEMA"``, ``"OBJECT"``.

    Returns:
        List of valid upper-cased permission tokens (input order preserved).

    Raises:
        ValueError: If the scope is unknown or any token is not in the allowlist.
    """
    allowlist = _ALLOWLISTS.get(scope_class.upper())
    if allowlist is None:
        msg = f"Unknown scope class {scope_class!r}: must be one of DATABASE, SCHEMA, OBJECT"
        raise ValueError(msg)

    tokens = [t.strip().upper() for t in permissions_str.split(",") if t.strip()]
    if not tokens:
        msg = "At least one permission must be specified"
        raise ValueError(msg)

    invalid = [t for t in tokens if t not in allowlist]
    if invalid:
        msg = (
            f"Invalid permission(s) for {scope_class}: {', '.join(sorted(invalid))}. "
            f"Allowed: {', '.join(sorted(allowlist))}"
        )
        raise ValueError(msg)

    return tokens


def _build_scope_clause(
    scope_class: str,
    *,
    schema: str | None = None,
    object_name: str | None = None,
) -> str:
    """Return the ON <class>::<securable> clause for a GRANT/DENY/REVOKE statement.

    Args:
        scope_class: ``"DATABASE"``, ``"SCHEMA"``, or ``"OBJECT"``.
        schema: Schema name (required when scope_class == "SCHEMA").
        object_name: Qualified object name ``<schema>.<obj>`` (required when
            scope_class == "OBJECT").

    Returns:
        The ON clause string, e.g. ``"ON OBJECT::[dbo].[sales]"``.

    Raises:
        ValueError: If required arguments are missing or identifiers are invalid.
    """
    if scope_class == "DATABASE":
        # DATABASE is the implicit scope in Fabric T-SQL; the ON clause is omitted
        # entirely.  ``GRANT SELECT TO [principal]`` is the correct form.
        return ""
    if scope_class == "SCHEMA":
        if not schema:
            msg = "--schema NAME is required for SCHEMA scope"
            raise ValueError(msg)
        validate_identifier(schema)
        return f"ON SCHEMA::{quote_identifier(schema)}"
    if scope_class == "OBJECT":
        if not object_name:
            msg = "--object SCHEMA.NAME is required for OBJECT scope"
            raise ValueError(msg)
        sch, obj = parse_qualified_name(object_name, "object")
        validate_identifier(sch)
        validate_identifier(obj)
        return f"ON OBJECT::{quote_identifier(sch)}.{quote_identifier(obj)}"
    msg = f"Unknown scope class {scope_class!r}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# T-SQL write operations
# ---------------------------------------------------------------------------


async def grant_permission(
    target: SqlTarget,
    permissions_str: str,
    principal_name: str,
    scope_class: str,
    *,
    schema: str | None = None,
    object_name: str | None = None,
    with_grant_option: bool = False,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Execute ``GRANT <permissions> ON <scope> TO <principal>``.

    Args:
        target: SQL connection target.
        permissions_str: Comma-separated permission tokens (e.g. ``"SELECT,INSERT"``).
        principal_name: The grantee principal name (Entra UPN, GUID, or role).
        scope_class: One of ``"DATABASE"``, ``"SCHEMA"``, ``"OBJECT"``.
        schema: Schema name (for SCHEMA scope).
        object_name: Qualified object name (for OBJECT scope).
        with_grant_option: When ``True``, adds ``WITH GRANT OPTION``.
        mode: Credential mode for Entra authentication.

    Raises:
        ValueError: If permissions or identifiers are invalid.
    """
    perms = _validate_permissions(permissions_str, scope_class)
    scope_clause = _build_scope_clause(scope_class, schema=schema, object_name=object_name)
    validate_principal_name(principal_name)
    quoted_principal = quote_principal(principal_name)
    perms_clause = ", ".join(perms)
    grant_option_clause = " WITH GRANT OPTION" if with_grant_option else ""
    on_part = f" {scope_clause}" if scope_clause else ""
    ddl = f"GRANT {perms_clause}{on_part} TO {quoted_principal}{grant_option_clause};"

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)


async def deny_permission(
    target: SqlTarget,
    permissions_str: str,
    principal_name: str,
    scope_class: str,
    *,
    schema: str | None = None,
    object_name: str | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Execute ``DENY <permissions> ON <scope> TO <principal>``.

    Args:
        target: SQL connection target.
        permissions_str: Comma-separated permission tokens.
        principal_name: The principal name to deny.
        scope_class: One of ``"DATABASE"``, ``"SCHEMA"``, ``"OBJECT"``.
        schema: Schema name (for SCHEMA scope).
        object_name: Qualified object name (for OBJECT scope).
        mode: Credential mode for Entra authentication.

    Raises:
        ValueError: If permissions or identifiers are invalid.
    """
    perms = _validate_permissions(permissions_str, scope_class)
    scope_clause = _build_scope_clause(scope_class, schema=schema, object_name=object_name)
    validate_principal_name(principal_name)
    quoted_principal = quote_principal(principal_name)
    perms_clause = ", ".join(perms)
    on_part = f" {scope_clause}" if scope_clause else ""
    ddl = f"DENY {perms_clause}{on_part} TO {quoted_principal};"

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)


async def revoke_permission(
    target: SqlTarget,
    permissions_str: str,
    principal_name: str,
    scope_class: str,
    *,
    schema: str | None = None,
    object_name: str | None = None,
    grant_option_only: bool = False,
    cascade: bool = False,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Execute ``REVOKE <permissions> ON <scope> FROM <principal>``.

    Args:
        target: SQL connection target.
        permissions_str: Comma-separated permission tokens.
        principal_name: The principal name to revoke from.
        scope_class: One of ``"DATABASE"``, ``"SCHEMA"``, ``"OBJECT"``.
        schema: Schema name (for SCHEMA scope).
        object_name: Qualified object name (for OBJECT scope).
        grant_option_only: When ``True``, only revokes the ``GRANT OPTION FOR``
            (leaves the base permission in place).
        cascade: When ``True``, adds ``CASCADE`` (required when the grantee has
            granted the permission to others).
        mode: Credential mode for Entra authentication.

    Raises:
        ValueError: If permissions or identifiers are invalid.
    """
    perms = _validate_permissions(permissions_str, scope_class)
    scope_clause = _build_scope_clause(scope_class, schema=schema, object_name=object_name)
    validate_principal_name(principal_name)
    quoted_principal = quote_principal(principal_name)
    perms_clause = ", ".join(perms)
    grant_option_prefix = "GRANT OPTION FOR " if grant_option_only else ""
    cascade_clause = " CASCADE" if cascade else ""
    on_part = f" {scope_clause}" if scope_clause else ""
    ddl = (
        f"REVOKE {grant_option_prefix}{perms_clause}{on_part} "
        f"FROM {quoted_principal}{cascade_clause};"
    )

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)
