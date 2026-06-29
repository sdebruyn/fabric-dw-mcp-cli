---
title: SQL Analytics Endpoints
---

# SQL Analytics Endpoints

Manage Microsoft Fabric SQL Analytics Endpoints.

**Targets:** SQL Analytics Endpoint

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
| `--recreate-tables` | Drop and recreate all tables during the refresh. Use to resolve inconsistencies or force a clean rebuild. **Destructive**: use with caution. |

**Example**

```shell
# Standard refresh - shows a per-table Rich table
fdw -w MyWorkspace sql-endpoints refresh MyLakehouseEP

# Force a full table recreate
fdw -w MyWorkspace sql-endpoints refresh --recreate-tables MyLakehouseEP

# Emit raw JSON
fdw -w MyWorkspace --json sql-endpoints refresh MyLakehouseEP
```

## MCP tools

### get_sql_endpoint

**Targets:** SQL Analytics Endpoint

Return details for a single SQL analytics endpoint.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `endpoint` (`str`): endpoint name or GUID.

**Returns:** `Warehouse`: single SQL analytics endpoint object.

### list_sql_endpoints

**Targets:** SQL Analytics Endpoint

List all SQL analytics endpoints in a workspace, or across all visible workspaces.

**Parameters:**

- `workspace` (`str`): workspace name or GUID; required when `all_workspaces` is `false`; ignored when `true`.
- `all_workspaces` (`bool`, default `false`): when `true`, aggregate results across every workspace the caller can see.

**Returns:** `list[Warehouse]`: array of SQL analytics endpoint objects (same fields as Warehouse, `kind` is always `SQLEndpoint`).

### refresh_sql_endpoint_metadata

**Targets:** SQL Analytics Endpoint

Refresh metadata for a SQL analytics endpoint by syncing from the underlying Lakehouse delta tables. This is a long-running operation (LRO) polled to completion.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `endpoint` (`str`): endpoint name or GUID.
- `recreate_tables` (`bool`, default `False`): drop and recreate all tables during the refresh. Use to resolve inconsistencies or force a clean rebuild. DESTRUCTIVE - use with caution.

**Returns:** `list[TableSyncStatus]`: array of per-table sync results, each with `tableName`, `status`, `startDateTime`, `endDateTime`, `lastSuccessfulSyncDateTime`, and `error`.
