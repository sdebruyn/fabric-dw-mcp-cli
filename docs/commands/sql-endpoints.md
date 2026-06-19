---
title: SQL Analytics Endpoints
---

# SQL Analytics Endpoints

Manage Microsoft Fabric SQL Analytics Endpoints.

**Targets:** SQL Analytics Endpoint

---

## CLI

### sql-endpoints get

**Targets:** SQL Analytics Endpoint

Get details for a specific SQL Analytics Endpoint.

**Synopsis**

```
fdw [-w WORKSPACE] sql-endpoints get ENDPOINT
```

**Example**

```shell
fdw -w MyWorkspace sql-endpoints get MyLakehouseEP
```

---

### sql-endpoints list

**Targets:** SQL Analytics Endpoint

List all SQL Analytics Endpoints in a workspace. Supports `-A` / `--all-workspaces` to scan every visible workspace. `-w` / `--workspace` and `--all-workspaces` are mutually exclusive.

**Synopsis**

```
fdw [-w WORKSPACE] sql-endpoints list [-A]
```

| Option | Description |
| --- | --- |
| `-A` / `--all-workspaces` | Scan all visible workspaces and aggregate results. Mutually exclusive with `-w`. |

**Example**

```shell
# List endpoints in the default (or configured) workspace
fdw sql-endpoints list

# List endpoints in a specific workspace
fdw -w MyWorkspace sql-endpoints list

# Aggregate across all visible workspaces
fdw sql-endpoints list --all-workspaces
```

```
 displayName        id
 ------------------ ------------------------------------
 MyLakehouseEP      f9e1...
```

---

### sql-endpoints permissions

**Targets:** SQL Analytics Endpoint

List all principals (users, groups, service principals) with access to a SQL Analytics Endpoint, including their effective permissions. Requires **Fabric Administrator** role.

**Synopsis**

```
fdw [-w WORKSPACE] [--json] sql-endpoints permissions ENDPOINT
```

| Option | Description |
| --- | --- |
| `--json` | Emit raw JSON instead of a Rich table. Pass on the root command. |

**Example**

```shell
# Tabular output
fdw -w MyWorkspace sql-endpoints permissions MyLakehouseEP

# Raw JSON
fdw -w MyWorkspace --json sql-endpoints permissions MyLakehouseEP
```

```
 Display Name    UPN / App ID             Type    Permissions    Additional Permissions
 --------------- ------------------------ ------- -------------- ----------------------
 Alice           alice@contoso.com        User    Read, Write
 DataPipeline    00000000-0000-...        ServicePrincipal  Read
```

---

### sql-endpoints refresh

**Targets:** SQL Analytics Endpoint

Refresh metadata for a SQL Analytics Endpoint by triggering a sync from the underlying Lakehouse delta tables. This is a long-running operation (LRO) that is polled to completion.

Results are shown as a Rich table (Table, Status, End Time, Error). Pass `--json` on the root command to emit raw JSON instead.

**Synopsis**

```
fdw [-w WORKSPACE] sql-endpoints refresh [--recreate-tables] ENDPOINT
```

**Options**

| Flag | Description |
|------|-------------|
| `--recreate-tables` | Drop and recreate all tables during the refresh. Use to resolve inconsistencies or force a clean rebuild. **Destructive** — use with caution. |

**Example**

```shell
# Standard refresh — shows a per-table Rich table
fdw -w MyWorkspace sql-endpoints refresh MyLakehouseEP

# Force a full table recreate
fdw -w MyWorkspace sql-endpoints refresh --recreate-tables MyLakehouseEP

# Emit raw JSON
fdw -w MyWorkspace --json sql-endpoints refresh MyLakehouseEP
```

---

## MCP tools

### get_sql_endpoint

**Targets:** SQL Analytics Endpoint

Return details for a single SQL analytics endpoint.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `endpoint` (`str`) — endpoint name or GUID.

**Returns:** `Warehouse` — single SQL analytics endpoint object.

---

### get_sql_endpoint_permissions

**Targets:** SQL Analytics Endpoint

Return all principals (users, groups, service principals) with access to a SQL Analytics Endpoint, including their effective permissions.

!!! note

    Requires **Fabric Administrator** role (`Tenant.Read.All` or `Tenant.ReadWrite.All` scope). See [Microsoft Fabric admin documentation](https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin) for how to request the role.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `sql_endpoint` (`str`) — SQL analytics endpoint name or GUID.

**Returns:** `list[ItemAccess]` — array of access records, each with `principal` (containing `id`, `displayName`, `type`, and type-specific fields such as `userPrincipalName` or `aadAppId`) and `itemAccessDetails` (containing `type`, `permissions`, and `additionalPermissions`).

---

### list_sql_endpoints

**Targets:** SQL Analytics Endpoint

List all SQL analytics endpoints in a workspace, or across all visible workspaces.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID; required when `all_workspaces` is `false`; ignored when `true`.
- `all_workspaces` (`bool`, default `false`) — when `true`, aggregate results across every workspace the caller can see.

**Returns:** `list[Warehouse]` — array of SQL analytics endpoint objects (same fields as Warehouse, `kind` is always `SQLEndpoint`).

---

### refresh_sql_endpoint_metadata

**Targets:** SQL Analytics Endpoint

Refresh metadata for a SQL analytics endpoint by syncing from the underlying Lakehouse delta tables. This is a long-running operation (LRO) polled to completion.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `endpoint` (`str`) — endpoint name or GUID.
- `recreate_tables` (`bool`, default `False`) — drop and recreate all tables during the refresh. Use to resolve inconsistencies or force a clean rebuild. DESTRUCTIVE — use with caution.

**Returns:** `list[TableSyncStatus]` — array of per-table sync results, each with `tableName`, `status`, `startDateTime`, `endDateTime`, `lastSuccessfulSyncDateTime`, and `error`.
