---
title: Permissions
---

# Permissions

Manage Fabric item-level and T-SQL in-database permissions.

Two distinct permission planes are exposed under the `permissions` top-level group:

- `permissions item` - Fabric item-level permissions (REST admin API). Covers which principals
  have access to a Warehouse or SQL Analytics Endpoint item.
- `permissions sql` - T-SQL granular in-database permissions. Reads from
  `sys.database_permissions` / `sys.database_principals` and issues `GRANT` / `DENY` / `REVOKE`
  statements.

**Targets:** Data Warehouse / SQL Analytics Endpoint

## CLI

### permissions item list

**Targets:** Data Warehouse / SQL Analytics Endpoint

List all principals (users, groups, service principals) with access to an item, including their
effective permissions. Requires **Fabric Administrator** role.

**Synopsis**

```
fdw [-w WORKSPACE] [--json] permissions item list [ITEM]
```

| Option | Description |
| --- | --- |
| `--json` | Emit raw JSON instead of a Rich table. Pass on the root command. |

**Example**

```shell
# Tabular output
fdw -w MyWorkspace permissions item list SalesWH

# Raw JSON
fdw -w MyWorkspace --json permissions item list MyLakehouseEP
```

```
 Display Name    UPN / App ID             Type    Permissions    Additional Permissions
 --------------- ------------------------ ------- -------------- ----------------------
 Alice           alice@contoso.com        User    Read, Write
 DataPipeline    00000000-0000-...        ServicePrincipal  Read
```

### permissions sql list

**Targets:** Data Warehouse / SQL Analytics Endpoint

List T-SQL database permissions from `sys.database_permissions` joined to
`sys.database_principals`. Returns `DATABASE`, `SCHEMA`, and `OBJECT` class securables with
readable names.

**Synopsis**

```
fdw [-w WORKSPACE] [--json] permissions sql list [ITEM] [OPTIONS]
```

| Option | Description |
| --- | --- |
| `--principal NAME` | Filter by principal name. |
| `--schema NAME` | Filter by schema name (SCHEMA class only). |
| `--object SCHEMA.NAME` | Filter by qualified object name (OBJECT class only). |

**Example**

```shell
# All permissions
fdw -w MyWorkspace permissions sql list SalesWH

# Filter by principal
fdw -w MyWorkspace permissions sql list SalesWH --principal alice@contoso.com

# Filter by schema
fdw -w MyWorkspace permissions sql list SalesWH --schema dbo
```

### permissions sql principals

**Targets:** Data Warehouse / SQL Analytics Endpoint

List database principals from `sys.database_principals`.

**Synopsis**

```
fdw [-w WORKSPACE] [--json] permissions sql principals [ITEM] [OPTIONS]
```

| Option | Description |
| --- | --- |
| `--type [user\|role\|all]` | Filter principal type (default: `all`). |

**Example**

```shell
# All principals
fdw -w MyWorkspace permissions sql principals SalesWH

# Only users
fdw -w MyWorkspace permissions sql principals SalesWH --type user

# Only roles
fdw -w MyWorkspace permissions sql principals SalesWH --type role
```

### permissions sql mine

**Targets:** Data Warehouse / SQL Analytics Endpoint

Show permissions for the current connection via `sys.fn_my_permissions`.

**Synopsis**

```
fdw [-w WORKSPACE] [--json] permissions sql mine [ITEM] [SCOPE]
```

`SCOPE` is optional and accepts:

- `database` (default) - database-level permissions.
- `schema:<name>` - permissions on a specific schema.
- `object:<schema>.<object>` - permissions on a specific object.

**Example**

```shell
# Database-level
fdw -w MyWorkspace permissions sql mine SalesWH

# Schema-level
fdw -w MyWorkspace permissions sql mine SalesWH schema:dbo

# Object-level
fdw -w MyWorkspace permissions sql mine SalesWH object:dbo.sales
```

### permissions sql grant

**Targets:** Data Warehouse / SQL Analytics Endpoint

Grant permissions on a securable to a principal. Executes
`GRANT <PERMISSIONS> ON <SCOPE> TO <PRINCIPAL>`.

**Synopsis**

```
fdw [-w WORKSPACE] permissions sql grant [ITEM] PERMISSIONS --to PRINCIPAL [OPTIONS]
```

`PERMISSIONS` is a comma-separated list of T-SQL permission tokens (e.g. `SELECT,INSERT`).

| Option | Description |
| --- | --- |
| `--to PRINCIPAL` | Grantee principal: Entra UPN, application GUID, or role name. **Required.** |
| `--with-grant-option` | Allow the grantee to grant the permission to others (`WITH GRANT OPTION`). |
| `--database` | Target the DATABASE scope (default when no scope option is given). |
| `--schema NAME` | Target a SCHEMA scope (provide schema name). |
| `--object SCHEMA.NAME` | Target an OBJECT scope (provide qualified name, e.g. `dbo.sales`). |

`--database`, `--schema`, and `--object` are mutually exclusive.

**Allowed permissions by scope**

| Scope | Allowed permissions |
| --- | --- |
| `DATABASE` | `CONNECT`, `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `EXECUTE`, `REFERENCES`, `ALTER`, `CONTROL`, `VIEW DEFINITION`, `CREATE TABLE`, `CREATE VIEW`, `CREATE PROCEDURE`, `CREATE FUNCTION`, `CREATE SCHEMA` |
| `SCHEMA` | `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `EXECUTE`, `REFERENCES`, `ALTER`, `CONTROL`, `VIEW DEFINITION` |
| `OBJECT` | `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `EXECUTE`, `REFERENCES`, `ALTER`, `CONTROL`, `VIEW DEFINITION`, `TAKE OWNERSHIP` |

**Example**

```shell
# Grant SELECT on the database to a user
fdw -w MyWorkspace permissions sql grant SalesWH SELECT --to alice@contoso.com

# Grant SELECT and INSERT on a specific object
fdw -w MyWorkspace permissions sql grant SalesWH SELECT,INSERT --to analysts --object dbo.sales

# Grant EXECUTE on a schema with grant option
fdw -w MyWorkspace permissions sql grant SalesWH EXECUTE --to analysts --schema dbo --with-grant-option
```

### permissions sql deny

**Targets:** Data Warehouse / SQL Analytics Endpoint

Deny permissions on a securable to a principal. Executes
`DENY <PERMISSIONS> ON <SCOPE> TO <PRINCIPAL>`.

**Synopsis**

```
fdw [-w WORKSPACE] permissions sql deny [ITEM] PERMISSIONS --to PRINCIPAL [OPTIONS]
```

| Option | Description |
| --- | --- |
| `--to PRINCIPAL` | Principal to deny. **Required.** |
| `--database` | Target the DATABASE scope (default). |
| `--schema NAME` | Target a SCHEMA scope. |
| `--object SCHEMA.NAME` | Target an OBJECT scope. |

**Example**

```shell
# Deny SELECT on the database to a user
fdw -w MyWorkspace permissions sql deny SalesWH SELECT --to alice@contoso.com

# Deny EXECUTE on a schema
fdw -w MyWorkspace permissions sql deny SalesWH EXECUTE --to analysts --schema dbo
```

### permissions sql revoke

**Targets:** Data Warehouse / SQL Analytics Endpoint

Revoke permissions on a securable from a principal. Executes
`REVOKE <PERMISSIONS> ON <SCOPE> FROM <PRINCIPAL>`.

This is a **destructive operation**: it removes an existing permission. A confirmation prompt
is shown before executing. Pass `--yes` / `-y` to skip the prompt in scripts.

**Synopsis**

```
fdw [-w WORKSPACE] [-y] permissions sql revoke [ITEM] PERMISSIONS --from PRINCIPAL [OPTIONS]
```

| Option | Description |
| --- | --- |
| `--from PRINCIPAL` | Principal to revoke from. **Required.** |
| `-y`, `--yes` | Skip the confirmation prompt (non-interactive / scripted use). |
| `--grant-option-only` | Revoke only the `GRANT OPTION`, leaving the base permission in place. |
| `--cascade` | Cascade the revocation to principals the grantee has granted to. |
| `--database` | Target the DATABASE scope (default). |
| `--schema NAME` | Target a SCHEMA scope. |
| `--object SCHEMA.NAME` | Target an OBJECT scope. |

**Example**

```shell
# Revoke SELECT from a user at database scope (prompts for confirmation)
fdw -w MyWorkspace permissions sql revoke SalesWH SELECT --from alice@contoso.com

# Revoke without prompt (scripted)
fdw -w MyWorkspace -y permissions sql revoke SalesWH SELECT --from alice@contoso.com

# Revoke only the grant option
fdw -w MyWorkspace -y permissions sql revoke SalesWH SELECT --from alice@contoso.com --grant-option-only

# Revoke with cascade
fdw -w MyWorkspace -y permissions sql revoke SalesWH SELECT --from analysts --schema dbo --cascade
```

## MCP tools

### list_item_permissions

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return principals with access to a Warehouse or SQL Analytics Endpoint item.

!!! note

    Requires **Fabric Administrator** role (`Tenant.Read.All` or `Tenant.ReadWrite.All` scope). See [Microsoft Fabric admin documentation](https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin?WT.mc_id=MVP_310840) for how to request the role.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.

**Returns:** `list[ItemAccess]`: array of access records, each with `principal` (containing `id`,
`displayName`, `type`, and type-specific fields such as `userPrincipalName` or `aadAppId`) and
`itemAccessDetails` (containing `type`, `permissions`, and `additionalPermissions`).

### list_sql_permissions

**Targets:** Data Warehouse / SQL Analytics Endpoint

List T-SQL database permissions from `sys.database_permissions`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `principal` (`str | null`, optional): filter by principal name.
- `schema` (`str | null`, optional): filter by schema name - returns SCHEMA class rows.
- `object_name` (`str | null`, optional): filter by qualified object name `<schema>.<object>` -
  returns OBJECT class rows.

**Returns:** `list[DatabasePermission]`: array of permission records with `principal_name`,
`principal_type`, `state` (`GRANT`, `DENY`), `permission_name`, `securable_class` (`DATABASE`,
`SCHEMA`, `OBJECT`), `schema_name`, and `object_name`.

### list_database_principals

**Targets:** Data Warehouse / SQL Analytics Endpoint

List database principals from `sys.database_principals`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `principal_type` (`str | null`, optional): filter by type - `"user"` for users, `"role"` for
  database roles, `"all"` or omit for no filter.

**Returns:** `list[DatabasePrincipal]`: array of principal records with `name`, `type`, and
`authentication_type`.

### my_permissions

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return permissions for the current connection via `sys.fn_my_permissions`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `scope` (`str | null`, optional): securable scope - `"database"` (default),
  `"schema:<name>"`, or `"object:<schema>.<object>"`.

**Returns:** `list[dict]`: array of permission records with `entity_name`, `subentity_name`, and
`permission_name`.

### grant_permission

**Targets:** Data Warehouse / SQL Analytics Endpoint

Grant permissions on a securable to a principal. Executes
`GRANT <permissions> ON <scope> TO <principal>`.

Blocked by `FABRIC_MCP_READONLY`. Does NOT require `FABRIC_MCP_ALLOW_DESTRUCTIVE`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `permissions` (`str`): comma-separated permission tokens (e.g. `"SELECT,INSERT"`).
- `principal` (`str`): grantee principal name (Entra UPN, application GUID, or role name).
- `scope` (`str`, default `"DATABASE"`): securable class - `"DATABASE"`, `"SCHEMA"`, or
  `"OBJECT"`.
- `schema` (`str | null`, optional): schema name (required when scope is `"SCHEMA"`).
- `object_name` (`str | null`, optional): qualified object name `<schema>.<object>` (required
  when scope is `"OBJECT"`).
- `with_grant_option` (`bool`, default `false`): when `true`, allows the grantee to grant the
  permission to others (`WITH GRANT OPTION`).

**Returns:** `{ "granted": true, "permissions": str, "principal": str, "scope": str }`.

### deny_permission

**Targets:** Data Warehouse / SQL Analytics Endpoint

Deny permissions on a securable to a principal. Executes
`DENY <permissions> ON <scope> TO <principal>`.

Blocked by `FABRIC_MCP_READONLY`. Does NOT require `FABRIC_MCP_ALLOW_DESTRUCTIVE`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `permissions` (`str`): comma-separated permission tokens.
- `principal` (`str`): principal name to deny.
- `scope` (`str`, default `"DATABASE"`): securable class - `"DATABASE"`, `"SCHEMA"`, or
  `"OBJECT"`.
- `schema` (`str | null`, optional): schema name (required when scope is `"SCHEMA"`).
- `object_name` (`str | null`, optional): qualified object name (required when scope is
  `"OBJECT"`).

**Returns:** `{ "denied": true, "permissions": str, "principal": str, "scope": str }`.

### revoke_permission

**Targets:** Data Warehouse / SQL Analytics Endpoint

Revoke permissions on a securable from a principal. Executes
`REVOKE <permissions> ON <scope> FROM <principal>`.

Blocked by `FABRIC_MCP_READONLY`. Also requires `FABRIC_MCP_ALLOW_DESTRUCTIVE=1`
because revoke removes an existing permission (destructive operation).
`grant_permission` and `deny_permission` do NOT require `FABRIC_MCP_ALLOW_DESTRUCTIVE`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `permissions` (`str`): comma-separated permission tokens.
- `principal` (`str`): principal name to revoke from.
- `scope` (`str`, default `"DATABASE"`): securable class - `"DATABASE"`, `"SCHEMA"`, or
  `"OBJECT"`.
- `schema` (`str | null`, optional): schema name (required when scope is `"SCHEMA"`).
- `object_name` (`str | null`, optional): qualified object name (required when scope is
  `"OBJECT"`).
- `grant_option_only` (`bool`, default `false`): when `true`, revokes only the grant option
  (`GRANT OPTION FOR`), leaving the base permission in place.
- `cascade` (`bool`, default `false`): when `true`, cascades the revocation to principals the
  grantee has granted the permission to (`CASCADE`).

**Returns:** `{ "revoked": true, "permissions": str, "principal": str, "scope": str }`.
