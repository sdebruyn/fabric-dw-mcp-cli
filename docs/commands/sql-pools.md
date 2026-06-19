---
title: SQL Pools
---

# SQL Pools

!!! warning "Beta / preview feature"
    Manages workspace SQL Pools (currently in preview; the API may change before GA).  Callers must hold the **workspace admin role**.

Manage custom SQL Pools at the workspace level with sub-resource commands that mirror the Azure CLI style.

**Targets:** Workspace (not item-specific)

---

## CLI

### sql-pools create

**Targets:** Workspace (not item-specific)

Add a new SQL pool to a workspace.

**Synopsis**

```
fdw [-w WORKSPACE] sql-pools create [OPTIONS]
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Pool name. (required) |
| `--max-percent INTEGER` | Max resource percentage (1–100). (required) |
| `--default` / `--no-default` | Mark as default pool. Default: `--no-default`. |
| `--optimize-for-reads` / `--no-optimize-for-reads` | Enable read optimisation. Default: `--optimize-for-reads`. |
| `--classifier-type TEXT` | Classifier type (e.g. `Application Name`). |
| `--classifier-value TEXT` | Classifier value. Repeat for multiple values. |

**Example**

```shell
fdw -w MyWorkspace sql-pools create \
  --name ETL \
  --max-percent 30 \
  --no-optimize-for-reads \
  --classifier-type "Application Name" \
  --classifier-value "ETL" \
  --classifier-value "Load"
```

---

### sql-pools delete

**Targets:** Workspace (not item-specific)

Remove a SQL pool from a workspace. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] sql-pools delete [OPTIONS]
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Pool name to delete. (required) |
| `--yes` | Skip confirmation prompt. |

**Example**

```shell
fdw -w MyWorkspace --yes sql-pools delete --name ETL
```

---

### sql-pools disable

**Targets:** Workspace (not item-specific)

Disable custom SQL Pools for a workspace without deleting pool definitions. Re-enabling with `sql-pools enable` restores the previously saved configuration.

**Synopsis**

```
fdw [-w WORKSPACE] sql-pools disable
```

**Example**

```shell
fdw -w MyWorkspace sql-pools disable
```

---

### sql-pools enable

**Targets:** Workspace (not item-specific)

Enable custom SQL Pools for a workspace. Preserves the existing pool configuration.

**Synopsis**

```
fdw [-w WORKSPACE] sql-pools enable
```

**Example**

```shell
fdw -w MyWorkspace sql-pools enable
```

---

### sql-pools get

**Targets:** Workspace (not item-specific)

Fetch the full SQL Pools configuration (enabled flag + pool list) for a workspace.

**Synopsis**

```
fdw [-w WORKSPACE] sql-pools get
```

**Example**

```shell
fdw -w MyWorkspace sql-pools get
```

---

### sql-pools insights

**Targets:** Data Warehouse · SQL Analytics Endpoint

List SQL pool insight events from `queryinsights.sql_pool_insights`. Supports optional time-range filtering with `--since` and `--until` (ISO-8601 strings). The `--limit` option caps the number of rows returned (default: 100, max: 10 000).

**Synopsis**

```
fdw [-w WORKSPACE] sql-pools insights [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1–10 000). | `100` |
| `--since ISO8601` | Return rows with timestamp >= this value. | — |
| `--until ISO8601` | Return rows with timestamp <= this value. | — |

**Example**

```shell
fdw -w MyWorkspace sql-pools insights SalesWH
```

---

### sql-pools list

**Targets:** Workspace (not item-specific)

List all SQL pools in a workspace.

When no custom SQL pools are defined, Fabric Data Warehouse uses the default
(autonomous) workload management instead: the SQL analytics endpoint compute is
split evenly (50/50) into two isolated pools, `SELECT` (read/analytics queries)
and `NON-SELECT` (DML/DDL/ETL/ingestion statements). In that case this command
reports the default pools rather than printing an empty list. The default split
is documented in
[Workload management](https://learn.microsoft.com/fabric/data-warehouse/workload-management#compute-pool-isolation)
and [Custom SQL pools](https://learn.microsoft.com/fabric/data-warehouse/custom-sql-pools).

**Synopsis**

```
fdw [-w WORKSPACE] sql-pools list
```

**Example**

```shell
fdw -w MyWorkspace sql-pools list
```

**Output**

When custom pools exist, `--json` returns the array of custom pool objects (as
before). When none are defined, `--json` returns an object that stays honest
about there being no custom pools:

```json
{
  "customSQLPools": [],
  "default_workload_active": true,
  "default_pools": [
    {"name": "SELECT", "maxResourcePercentage": 50, "isDefault": true, "description": "Handles SELECT (read/analytics) queries."},
    {"name": "NON-SELECT", "maxResourcePercentage": 50, "isDefault": true, "description": "Handles non-SELECT (DML/DDL/ETL/ingestion) statements."}
  ]
}
```

---

### sql-pools show

**Targets:** Workspace (not item-specific)

Show details for a single SQL pool.

**Synopsis**

```
fdw [-w WORKSPACE] sql-pools show --name POOL
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Pool name to show. (required) |

**Example**

```shell
fdw -w MyWorkspace sql-pools show --name ETL
```

---

### sql-pools update

**Targets:** Workspace (not item-specific)

Update an existing SQL pool. Only the flags you provide are changed; all other fields are preserved.

**Synopsis**

```
fdw [-w WORKSPACE] sql-pools update [OPTIONS]
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Pool name to update. (required) |
| `--max-percent INTEGER` | New max resource percentage. |
| `--default` / `--no-default` | Set or clear the default flag. |
| `--optimize-for-reads` / `--no-optimize-for-reads` | Enable or disable read optimisation. |
| `--classifier-type TEXT` | New classifier type. |
| `--classifier-value TEXT` | New classifier value(s). Replaces all existing values. |

**Example**

```shell
fdw -w MyWorkspace sql-pools update --name ETL --max-percent 40
```

---

## MCP tools

!!! warning "Beta / preview feature"
    The SQL Pools tools manage the workspace SQL Pools configuration (currently in preview; the API may change before GA). All callers must hold the **workspace admin role**.

### create_sql_pool

**Targets:** Workspace (not item-specific)

Add a new SQL pool to a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `name` (`str`) — pool name (must be unique within the workspace).
- `max_percent` (`int`) — max resource percentage (1–100).
- `is_default` (`bool`, default `false`) — whether this is the default pool.
- `optimize_for_reads` (`bool`, default `true`) — enable read optimisation.
- `classifier_type` (`str | null`, optional) — classifier type (e.g. `"Application Name"`).
- `classifier_values` (`list[str] | null`, optional) — classifier value list.

**Returns:** `SqlPool` — the newly-created pool object.

---

### delete_sql_pool

**Targets:** Workspace (not item-specific)

Delete an SQL pool from a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `pool_name` (`str`) — name of the pool to delete.

**Returns:** `{ "deleted": true, "pool_name": str }` — confirmation.

---

### disable_sql_pools

**Targets:** Workspace (not item-specific)

Disable custom SQL Pools for a workspace, preserving the pool configuration. Re-enabling with `enable_sql_pools` restores the previously saved configuration.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `SqlPoolsConfiguration` — the updated configuration.

---

### enable_sql_pools

**Targets:** Workspace (not item-specific)

Enable custom SQL Pools for a workspace without modifying pool definitions.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `SqlPoolsConfiguration` — the updated configuration.

---

### get_sql_pool

**Targets:** Workspace (not item-specific)

Return details for a single SQL pool by name.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `pool_name` (`str`) — pool name.

**Returns:** `SqlPool` — single pool object (fields as above).

---

### get_sql_pools_configuration

**Targets:** Workspace (not item-specific)

Fetch the full SQL Pools configuration (enabled flag + pool list) for a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `SqlPoolsConfiguration` — object with `customSQLPoolsEnabled` (bool) and `customSQLPools` (list of pool objects, each with `name`, `maxResourcePercentage`, `isDefault`, `optimizeForReads`, and optional `classifier`).

---

### list_sql_pool_insights

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return SQL pool insight events from `queryinsights.sql_pool_insights`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `limit` (`int`, default `100`) — maximum rows to return (1–10 000).
- `since` (`str | null`, optional) — ISO-8601 lower bound on `timestamp`.
- `until` (`str | null`, optional) — ISO-8601 upper bound on `timestamp`.

**Returns:** `list[dict]` — array of SQL pool insight row objects.

---

### list_sql_pools

**Targets:** Workspace (not item-specific)

Return the list of SQL pools for a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `list[SqlPool]` — array of pool objects, each with `name`, `isDefault`, `maxResourcePercentage`, `optimizeForReads`, and optional `classifier`.

---

### update_sql_pool

**Targets:** Workspace (not item-specific)

Update an existing SQL pool.  Only the parameters you supply are changed; all other fields are preserved.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `name` (`str`) — name of the pool to update.
- `max_percent` (`int | null`, optional) — new max resource percentage.
- `is_default` (`bool | null`, optional) — set or clear the default flag.
- `optimize_for_reads` (`bool | null`, optional) — enable or disable read optimisation.
- `classifier_type` (`str | null`, optional) — new classifier type.
- `classifier_values` (`list[str] | null`, optional) — new classifier value list (replaces all existing values).

**Returns:** `SqlPool` — the updated pool object.
