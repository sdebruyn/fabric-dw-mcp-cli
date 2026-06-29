---
title: Permissions
---

# Permissions

Manage Fabric item-level and T-SQL in-database permissions.

Five distinct permission planes are exposed under the `permissions` top-level group:

- `permissions item` - Fabric item-level permissions (REST admin API). Covers which principals
  have access to a Warehouse or SQL Analytics Endpoint item.
- `permissions sql` - T-SQL granular in-database permissions. Reads from
  `sys.database_permissions` / `sys.database_principals` and issues `GRANT` / `DENY` / `REVOKE`
  statements.
- `permissions cls` - Column-level security. Applies `GRANT`, `DENY`, and `REVOKE` to named
  column lists rather than whole objects.
- `permissions rls` - Row-level security. Manages security policies with filter and block
  predicates that reference existing predicate functions.
- `permissions mask` - Dynamic data masking. Applies, inspects, and removes column-level masks.

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

Column-level security (CLS) restricts access to specific columns of a table. It uses the same
`GRANT`, `DENY`, and `REVOKE` T-SQL verbs as object-level security, but targets a named column
list rather than the table as a whole.

Permissions supported at column level: `SELECT`, `UPDATE`, `REFERENCES`.

Reference: [Column-level security in Microsoft Fabric](https://learn.microsoft.com/fabric/data-warehouse/column-level-security?WT.mc_id=MVP_310840)

### permissions cls grant

**Targets:** Data Warehouse / SQL Analytics Endpoint

Grant column-level permissions on an object to a principal. Executes
`GRANT <PERMISSIONS> ON OBJECT::<SCHEMA.TABLE> (<COLUMNS>) TO <PRINCIPAL>`.

**Synopsis**

```
fdw [-w WORKSPACE] permissions cls grant [ITEM] PERMISSIONS --object SCHEMA.TABLE --columns COL1,COL2 --to PRINCIPAL [OPTIONS]
```

`PERMISSIONS` is a comma-separated list of column-level permission tokens: `SELECT`, `UPDATE`,
or `REFERENCES`.

| Option | Description |
| --- | --- |
| `--object SCHEMA.TABLE` | Qualified table name. **Required.** |
| `--columns COL1,COL2` | Comma-separated list of column names. **Required.** |
| `--to PRINCIPAL` | Grantee principal: Entra UPN, application GUID, or role name. **Required.** |
| `--with-grant-option` | Allow the grantee to grant the column permission to others (`WITH GRANT OPTION`). |

**Example**

```shell
# Grant SELECT on specific columns to a user
fdw -w MyWorkspace permissions cls grant SalesWH SELECT --object dbo.sales --columns email,phone --to alice@contoso.com

# Grant with grant option
fdw -w MyWorkspace permissions cls grant SalesWH REFERENCES --object dbo.customers --columns ssn --to analysts --with-grant-option
```

### permissions cls deny

**Targets:** Data Warehouse / SQL Analytics Endpoint

Deny column-level permissions on an object to a principal. Executes
`DENY <PERMISSIONS> ON OBJECT::<SCHEMA.TABLE> (<COLUMNS>) TO <PRINCIPAL>`.

**Synopsis**

```
fdw [-w WORKSPACE] permissions cls deny [ITEM] PERMISSIONS --object SCHEMA.TABLE --columns COL1,COL2 --to PRINCIPAL
```

| Option | Description |
| --- | --- |
| `--object SCHEMA.TABLE` | Qualified table name. **Required.** |
| `--columns COL1,COL2` | Comma-separated list of column names. **Required.** |
| `--to PRINCIPAL` | Principal to deny. **Required.** |

**Example**

```shell
# Deny SELECT on sensitive columns to a user
fdw -w MyWorkspace permissions cls deny SalesWH SELECT --object dbo.sales --columns email,phone --to alice@contoso.com
```

### permissions cls revoke

**Targets:** Data Warehouse / SQL Analytics Endpoint

Revoke column-level permissions on an object from a principal. Executes
`REVOKE <PERMISSIONS> ON OBJECT::<SCHEMA.TABLE> (<COLUMNS>) FROM <PRINCIPAL>`.

This is a **destructive operation**: it removes an existing column permission. A confirmation
prompt is shown before executing. Pass `--yes` / `-y` to skip the prompt in scripts.

**Synopsis**

```
fdw [-w WORKSPACE] [-y] permissions cls revoke [ITEM] PERMISSIONS --object SCHEMA.TABLE --columns COL1,COL2 --from PRINCIPAL [OPTIONS]
```

| Option | Description |
| --- | --- |
| `--object SCHEMA.TABLE` | Qualified table name. **Required.** |
| `--columns COL1,COL2` | Comma-separated list of column names. **Required.** |
| `--from PRINCIPAL` | Principal to revoke from. **Required.** |
| `-y`, `--yes` | Skip the confirmation prompt (non-interactive / scripted use). |
| `--cascade` | Cascade the revocation to principals the grantee has granted the permission to. |

**Example**

```shell
# Revoke SELECT from a user (prompts for confirmation)
fdw -w MyWorkspace permissions cls revoke SalesWH SELECT --object dbo.sales --columns email,phone --from alice@contoso.com

# Revoke without prompt (scripted)
fdw -w MyWorkspace -y permissions cls revoke SalesWH SELECT --object dbo.sales --columns email,phone --from alice@contoso.com

# Revoke with cascade
fdw -w MyWorkspace -y permissions cls revoke SalesWH SELECT --object dbo.sales --columns ssn --from analysts --cascade
```

### permissions cls list

**Targets:** Data Warehouse / SQL Analytics Endpoint

List column-level permissions on an object. Queries `sys.database_permissions` and returns only
rows where `minor_id != 0` (column-level entries), joined to column names from `sys.columns`.

**Synopsis**

```
fdw [-w WORKSPACE] [--json] permissions cls list [ITEM] --object SCHEMA.TABLE
```

| Option | Description |
| --- | --- |
| `--object SCHEMA.TABLE` | Qualified table name. **Required.** |

**Example**

```shell
# Tabular output
fdw -w MyWorkspace permissions cls list SalesWH --object dbo.sales

# Raw JSON
fdw -w MyWorkspace --json permissions cls list SalesWH --object dbo.sales
```

Control row-level security (RLS) policies. Each policy contains one or more filter or block
predicates that reference existing predicate functions (table-valued functions). The CLI
never authors or modifies predicate function bodies.

### permissions rls list

**Targets:** Data Warehouse / SQL Analytics Endpoint

List all security policies and their predicates.

**Synopsis**

```
fdw [-w WORKSPACE] [--json] permissions rls list [ITEM]
```

**Example**

```shell
fdw -w MyWorkspace permissions rls list SalesWH
fdw -w MyWorkspace --json permissions rls list SalesWH
```

### permissions rls create

**Targets:** Data Warehouse / SQL Analytics Endpoint

Create a new row-level security policy with one filter or block predicate.

**Synopsis**

```
fdw [-w WORKSPACE] permissions rls create [ITEM] [POLICY_NAME]
    { --filter SCHEMA.FN(COL,...) | --block SCHEMA.FN(COL,...) }
    --on SCHEMA.TABLE
    [--operation AFTER-INSERT|AFTER-UPDATE|BEFORE-UPDATE|BEFORE-DELETE]
    [--state on|off]
```

| Option | Description |
| --- | --- |
| `--filter SCHEMA.FN(COL,...)` | Add a FILTER predicate (mutually exclusive with `--block`). |
| `--block SCHEMA.FN(COL,...)` | Add a BLOCK predicate (mutually exclusive with `--filter`). |
| `--on SCHEMA.TABLE` | Target table for the predicate (required). |
| `--operation OP` | Block operation: `after-insert`, `after-update`, `before-update`, `before-delete` (BLOCK only). |
| `--state on\|off` | Initial policy state (default: `on`). |

**Example**

```shell
# Filter predicate, enabled
fdw -w MyWorkspace permissions rls create SalesWH rls.SalesFilter \
    --filter rls.fn_filter(SalesRep) --on dbo.Sales

# Block predicate, disabled initially
fdw -w MyWorkspace permissions rls create SalesWH rls.SalesBlock \
    --block rls.fn_block(SalesRep) --on dbo.Sales \
    --operation after-insert --state off
```

### permissions rls add-predicate

**Targets:** Data Warehouse / SQL Analytics Endpoint

Add an additional predicate to an existing policy.

**Synopsis**

```
fdw [-w WORKSPACE] permissions rls add-predicate [ITEM] [POLICY_NAME]
    { --filter SCHEMA.FN(COL,...) | --block SCHEMA.FN(COL,...) }
    --on SCHEMA.TABLE
    [--operation AFTER-INSERT|AFTER-UPDATE|BEFORE-UPDATE|BEFORE-DELETE]
```

**Example**

```shell
fdw -w MyWorkspace permissions rls add-predicate SalesWH rls.SalesFilter \
    --filter rls.fn_filter(Region) --on dbo.Regions
```

### permissions rls drop-predicate

**Targets:** Data Warehouse / SQL Analytics Endpoint

Drop a predicate from an existing policy.

**Synopsis**

```
fdw [-w WORKSPACE] permissions rls drop-predicate [ITEM] [POLICY_NAME]
    { --filter | --block }
    --on SCHEMA.TABLE
```

| Option | Description |
| --- | --- |
| `--filter` | Target a FILTER predicate (flag, no value). |
| `--block` | Target a BLOCK predicate (flag, no value). |
| `--on SCHEMA.TABLE` | Table whose predicate is dropped. |

**Example**

```shell
fdw -w MyWorkspace permissions rls drop-predicate SalesWH rls.SalesFilter \
    --filter --on dbo.Sales
```

### permissions rls set-state

**Targets:** Data Warehouse / SQL Analytics Endpoint

Enable or disable an existing security policy (reversible, not destructive).

**Synopsis**

```
fdw [-w WORKSPACE] permissions rls set-state [ITEM] [POLICY_NAME]
    { --enable | --disable }
```

**Example**

```shell
fdw -w MyWorkspace permissions rls set-state SalesWH rls.SalesFilter --enable
fdw -w MyWorkspace permissions rls set-state SalesWH rls.SalesFilter --disable
```

### permissions rls drop

**Targets:** Data Warehouse / SQL Analytics Endpoint

**Destructive.** Drop an entire security policy (and all its predicates).

Requires confirmation (`--yes` / `-y`) or the `FABRIC_MCP_ALLOW_DESTRUCTIVE=1` environment
variable when called via MCP.

**Synopsis**

```
fdw [-w WORKSPACE] [-y] permissions rls drop [ITEM] [POLICY_NAME]
```

**Example**

```shell
fdw -w MyWorkspace -y permissions rls drop SalesWH rls.SalesFilter
```

Dynamic data masking (DDM) applies column-level masks that hide sensitive values from users who
lack the `UNMASK` permission. Masked data is stored unchanged; only the presentation is altered.
Use `permissions grant` / `permissions revoke` with the `UNMASK` permission token to control
which principals see unmasked values.

Reference: [Dynamic data masking in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/dynamic-data-masking?WT.mc_id=MVP_310840)

Supported mask functions:

| Type | Arguments | Effect |
| --- | --- | --- |
| `default` | none | Full mask per data type (e.g. `XXXX` for strings, `0` for numerics). |
| `email` | none | Shows first letter and `.com` suffix, e.g. `aXXX@XXXX.com`. |
| `random` | `--start LOW --end HIGH` | Replaces numeric values with a random number in `[LOW, HIGH]`. |
| `partial` | `--prefix N --padding STR --suffix M` | Shows `N` leading and `M` trailing characters; fills the rest with `STR`. |

### permissions mask list

**Targets:** Data Warehouse / SQL Analytics Endpoint

List all columns with dynamic data masking applied, optionally filtered to a single table.

**Synopsis**

```
fdw [-w WORKSPACE] [--json] permissions mask list [ITEM] [SCHEMA.TABLE]
```

| Option | Description |
| --- | --- |
| `ITEM` | Data Warehouse or SQL Analytics Endpoint name (optional; uses config default). |
| `SCHEMA.TABLE` | Restrict output to this table (optional). |

**Example**

```shell
# All masked columns in the warehouse
fdw -w MyWorkspace permissions mask list SalesWH

# Masked columns in a single table
fdw -w MyWorkspace permissions mask list SalesWH dbo.Customers
```

### permissions mask set

**Targets:** Data Warehouse / SQL Analytics Endpoint

Apply or replace a dynamic data mask on a single column. If the column already has a mask,
`ADD MASKED` replaces it without error.

**Synopsis**

```
fdw [-w WORKSPACE] permissions mask set [ITEM] SCHEMA.TABLE
    --column COL --function TYPE
    [--start LOW] [--end HIGH]
    [--prefix N] [--padding STR] [--suffix M]
```

| Option | Description |
| --- | --- |
| `ITEM` | Data Warehouse or SQL Analytics Endpoint name. |
| `SCHEMA.TABLE` | Schema-qualified table name (positional). |
| `--column COL` | Column to apply the mask to (required). |
| `--function TYPE` | Mask type: `default`, `email`, `random`, or `partial` (required). |
| `--start LOW` | Lower bound for `random` mask (required when `--function random`). |
| `--end HIGH` | Upper bound for `random` mask (required when `--function random`). |
| `--prefix N` | Leading characters to expose for `partial` mask (required when `--function partial`). |
| `--padding STR` | Replacement text for `partial` mask (required when `--function partial`). Cannot contain `"`, `)`, `;`, `--`, or control characters. |
| `--suffix M` | Trailing characters to expose for `partial` mask (required when `--function partial`). |

**Example**

```shell
# Email mask
fdw -w MyWorkspace permissions mask set SalesWH dbo.Customers \
    --column Email --function email

# Random numeric mask
fdw -w MyWorkspace permissions mask set SalesWH dbo.Employees \
    --column Salary --function random --start 1 --end 99999

# Partial mask showing the last 4 digits of a phone number
fdw -w MyWorkspace permissions mask set SalesWH dbo.Customers \
    --column Phone --function partial --prefix 0 --padding "XXX-XXX-" --suffix 4
```

### permissions mask drop

**Targets:** Data Warehouse / SQL Analytics Endpoint

Remove a masking function from a column. The column reverts to returning its actual value for
all users (subject to normal column permissions). This is a destructive operation.

Requires confirmation (`--yes` / `-y`) unless `FABRIC_MCP_ALLOW_DESTRUCTIVE=1` is set.

**Synopsis**

```
fdw [-w WORKSPACE] [-y] permissions mask drop [ITEM] SCHEMA.TABLE --column COL
```

| Option | Description |
| --- | --- |
| `ITEM` | Data Warehouse or SQL Analytics Endpoint name. |
| `SCHEMA.TABLE` | Schema-qualified table name (positional). |
| `--column COL` | Column whose mask to remove (required). |

**Example**

```shell
fdw -w MyWorkspace -y permissions mask drop SalesWH dbo.Customers --column Email
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
- `columns` (`list[str] | null`, optional): list of column names for column-level security
  (OBJECT scope only; permissions must be `SELECT`, `UPDATE`, or `REFERENCES`). Omit or pass
  `null` for no column restriction. Passing an empty list raises a `ToolError`.

**Returns:** `{ "granted": true, "permissions": str, "principal": str, "scope": str, "columns": list | null }`.

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
- `columns` (`list[str] | null`, optional): list of column names for column-level security
  (OBJECT scope only; permissions must be `SELECT`, `UPDATE`, or `REFERENCES`). Omit or pass
  `null` for no column restriction. Passing an empty list raises a `ToolError`.

**Returns:** `{ "denied": true, "permissions": str, "principal": str, "scope": str, "columns": list | null }`.

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
- `columns` (`list[str] | null`, optional): list of column names for column-level security
  (OBJECT scope only; permissions must be `SELECT`, `UPDATE`, or `REFERENCES`). Omit or pass
  `null` for no column restriction. Passing an empty list raises a `ToolError`.
- `grant_option_only` (`bool`, default `false`): when `true`, revokes only the grant option
  (`GRANT OPTION FOR`), leaving the base permission in place.
- `cascade` (`bool`, default `false`): when `true`, cascades the revocation to principals the
  grantee has granted the permission to (`CASCADE`).

**Returns:** `{ "revoked": true, "permissions": str, "principal": str, "scope": str, "columns": list | null }`.

### list_security_policies

**Targets:** Data Warehouse / SQL Analytics Endpoint

List all row-level security policies and their predicates from `sys.security_policies`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.

**Returns:** `list[SecurityPolicy]`: array of policy records, each with `policy_schema`, `policy_name`,
`is_enabled`, and `predicates` (array of objects with `predicate_type`, `operation`, `schema_name`,
`table_name`, and `predicate_definition`).

### create_security_policy

**Targets:** Data Warehouse / SQL Analytics Endpoint

Create a row-level security policy with one or more predicates. Executes `CREATE SECURITY POLICY`.

Blocked by `FABRIC_MCP_READONLY`. Does NOT require `FABRIC_MCP_ALLOW_DESTRUCTIVE`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `policy_name` (`str`): qualified policy name (`"schema.name"` or `"name"`).
- `predicates` (`list[dict]`): list of predicate definitions. Each entry must include:
  - `predicate_type` (`str`): `"FILTER"` or `"BLOCK"`.
  - `fn_schema` (`str`): schema of the predicate function.
  - `fn_name` (`str`): name of the predicate function.
  - `fn_args` (`list[str]`): column names to pass to the function.
  - `table_schema` (`str`): schema of the target table.
  - `table_name` (`str`): name of the target table.
  - `operation` (`str`, optional): block operation - `"AFTER_INSERT"`, `"AFTER_UPDATE"`,
    `"BEFORE_UPDATE"`, or `"BEFORE_DELETE"` (BLOCK predicates only).
- `state` (`bool`, default `true`): initial policy state - `true` to enable, `false` to disable.

**Returns:** `{ "created": true, "policy_name": str, "state": bool }`.

### add_security_predicate

**Targets:** Data Warehouse / SQL Analytics Endpoint

Add a predicate to an existing row-level security policy. Executes
`ALTER SECURITY POLICY ... ADD FILTER|BLOCK PREDICATE`.

Blocked by `FABRIC_MCP_READONLY`. Does NOT require `FABRIC_MCP_ALLOW_DESTRUCTIVE`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `policy_name` (`str`): qualified policy name (`"schema.name"` or `"name"`).
- `predicate_type` (`str`): `"FILTER"` or `"BLOCK"`.
- `fn_name` (`str`): name of the predicate function.
- `fn_args` (`list[str]`): column names to pass to the predicate function.
- `table_schema` (`str`): schema name of the target table.
- `table_name` (`str`): name of the target table.
- `fn_schema` (`str | null`, optional): schema of the predicate function. Omit when the function
  lives in the default schema.
- `operation` (`str | null`, optional): block operation - `"AFTER_INSERT"`, `"AFTER_UPDATE"`,
  `"BEFORE_UPDATE"`, or `"BEFORE_DELETE"` (BLOCK predicates only).

**Returns:** `{ "added": true, "policy_name": str, "predicate_type": str, "table": str }`.

### drop_security_predicate

**Targets:** Data Warehouse / SQL Analytics Endpoint

Drop a predicate from an existing row-level security policy. Executes
`ALTER SECURITY POLICY ... DROP FILTER|BLOCK PREDICATE ON`.

Blocked by `FABRIC_MCP_READONLY`. Does NOT require `FABRIC_MCP_ALLOW_DESTRUCTIVE`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `policy_name` (`str`): qualified policy name (`"schema.name"` or `"name"`).
- `predicate_type` (`str`): `"FILTER"` or `"BLOCK"`.
- `table_schema` (`str`): schema name of the target table.
- `table_name` (`str`): name of the target table.

**Returns:** `{ "dropped": true, "policy_name": str, "predicate_type": str, "table": str }`.

### set_security_policy_state

**Targets:** Data Warehouse / SQL Analytics Endpoint

Enable or disable a row-level security policy. Executes
`ALTER SECURITY POLICY ... WITH (STATE = ON|OFF)`.

Enabling or disabling a policy is reversible. Blocked by `FABRIC_MCP_READONLY`. Does NOT require
`FABRIC_MCP_ALLOW_DESTRUCTIVE`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `policy_name` (`str`): qualified policy name (`"schema.name"` or `"name"`).
- `enabled` (`bool`): `true` to enable the policy, `false` to disable it.

**Returns:** `{ "policy_name": str, "enabled": bool }`.

### drop_security_policy

**Targets:** Data Warehouse / SQL Analytics Endpoint

Drop a row-level security policy and all its predicates. Executes `DROP SECURITY POLICY`.

This is a **permanently destructive operation**: the policy and all its predicates are removed.
Blocked by `FABRIC_MCP_READONLY`. Also requires `FABRIC_MCP_ALLOW_DESTRUCTIVE=1`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `policy_name` (`str`): qualified policy name (`"schema.name"` or `"name"`).

**Returns:** `{ "dropped": true, "policy_name": str }`.

### list_masked_columns

**Targets:** Data Warehouse / SQL Analytics Endpoint

List columns with dynamic data masking applied, reading from `sys.masked_columns`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `table_schema` (`str | null`, optional): schema filter (case-insensitive). Omit for all schemas.
- `table_name` (`str | null`, optional): table name filter (case-insensitive). Omit for all tables.

**Returns:** `list[MaskedColumn]`: array of records with `schema_name`, `table_name`, `column_name`, and `masking_function`.

### set_column_mask

**Targets:** Data Warehouse / SQL Analytics Endpoint

Apply or replace a dynamic data mask on a column. Executes
`ALTER TABLE ... ALTER COLUMN ... ADD MASKED WITH (FUNCTION = '...')`.

Blocked by `FABRIC_MCP_READONLY`. Does NOT require `FABRIC_MCP_ALLOW_DESTRUCTIVE`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `table_schema` (`str`): schema name of the target table.
- `table_name` (`str`): name of the target table.
- `column_name` (`str`): name of the column to mask.
- `fn_type` (`str`): mask function type - `"default"`, `"email"`, `"random"`, or `"partial"` (case-insensitive).
- `start` (`int | null`, optional): lower bound for `random` mask (required when `fn_type` is `"random"`).
- `end` (`int | null`, optional): upper bound for `random` mask (required when `fn_type` is `"random"`). Must be >= `start`.
- `prefix` (`int | null`, optional): leading characters to expose for `partial` mask (required when `fn_type` is `"partial"`).
- `padding` (`str | null`, optional): replacement text for `partial` mask (required when `fn_type` is `"partial"`). Cannot contain `"`, `)`, `;`, `--`, control characters (including U+0085, U+2028, U+2029), and is capped at 128 characters.
- `suffix` (`int | null`, optional): trailing characters to expose for `partial` mask (required when `fn_type` is `"partial"`).

**Returns:** `{ "masked": true, "table_schema": str, "table_name": str, "column_name": str, "masking_function": str }`.

### drop_column_mask

**Targets:** Data Warehouse / SQL Analytics Endpoint

Remove a dynamic data mask from a column. Executes
`ALTER TABLE ... ALTER COLUMN ... DROP MASKED`.

Blocked by `FABRIC_MCP_READONLY`. Also requires `FABRIC_MCP_ALLOW_DESTRUCTIVE=1` because
removing a mask is destructive: unmasked data becomes visible to all users who can query the
column.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): Warehouse or SQL endpoint name or GUID.
- `table_schema` (`str`): schema name of the target table.
- `table_name` (`str`): name of the target table.
- `column_name` (`str`): name of the column whose mask to remove.

**Returns:** `{ "dropped": true, "table_schema": str, "table_name": str, "column_name": str }`.
