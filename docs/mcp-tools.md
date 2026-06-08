---
title: MCP tools reference
---

# MCP tools reference

The MCP server exposes the following tools. Each takes the parameters listed and returns a JSON object whose shape matches the Pydantic model described in the Returns line. See [Authentication](authentication.md) for token setup and [MCP server setup](mcp.md) for installation.

---

## Workspaces

### list_workspaces

List all Fabric workspaces the authenticated principal has access to.

**Parameters:** None

**Returns:** `list[Workspace]` — array of workspace objects, each with `id`, `displayName`, `description`, `capacityId`, and `defaultDatasetStorageFormat`.

---

### get_workspace

Return details for a single workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `Workspace` — single workspace object (fields as above).

---

### set_workspace_collation

Set the default Data Warehouse collation for a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `collation` (`str`) — collation name (must be a supported Fabric collation).

**Returns:** `{ "workspace_id": str, "collation": str }` — the workspace GUID and the newly-set collation.

---

## Warehouses

### list_warehouses

List all warehouses and SQL analytics endpoints in a workspace, or across all visible workspaces.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID; required when `all_workspaces` is `false`; ignored when `true`.
- `all_workspaces` (`bool`, default `false`) — when `true`, aggregate results across every workspace the caller can see.

**Returns:** `list[Warehouse]` — array of warehouse objects, each with `id`, `displayName`, `description`, `workspaceId`, `kind` (`Warehouse` or `SQLEndpoint`), `connectionString`, `defaultCollation`, and `createdDate`.

---

### get_warehouse

Return details for a single warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `Warehouse` — single warehouse object (fields as above).

---

### create_warehouse

Create a new Warehouse in a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `name` (`str`) — display name for the new warehouse.
- `collation` (`str | null`, optional) — default collation for the new warehouse.
- `description` (`str | null`, optional) — description for the new warehouse.

**Returns:** `Warehouse` — the newly-created warehouse object.

---

### rename_warehouse

Rename a Warehouse and optionally update its description.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `new_name` (`str`) — new display name.
- `description` (`str | null`, optional) — new description; omit to leave unchanged.

**Returns:** `Warehouse` — the updated warehouse object.

---

### delete_warehouse

Delete a Warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `{ "deleted": true, "warehouse_id": str }` — confirmation with the warehouse GUID.

---

### takeover_warehouse

Take ownership of a Warehouse. Not supported for SQL analytics endpoints.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `{ "taken_over": true, "warehouse_id": str }` — confirmation with the warehouse GUID.

---

## SQL analytics endpoints

### list_sql_endpoints

List all SQL analytics endpoints in a workspace, or across all visible workspaces.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID; required when `all_workspaces` is `false`; ignored when `true`.
- `all_workspaces` (`bool`, default `false`) — when `true`, aggregate results across every workspace the caller can see.

**Returns:** `list[Warehouse]` — array of SQL analytics endpoint objects (same fields as Warehouse, `kind` is always `SQLEndpoint`).

---

### get_sql_endpoint

Return details for a single SQL analytics endpoint.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `endpoint` (`str`) — endpoint name or GUID.

**Returns:** `Warehouse` — single SQL analytics endpoint object.

---

### refresh_sql_endpoint_metadata

Refresh metadata for a SQL analytics endpoint by syncing from the underlying Lakehouse delta tables. This is a long-running operation (LRO) polled to completion.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `endpoint` (`str`) — endpoint name or GUID.

**Returns:** `dict` — the raw LRO completion payload from the Fabric API.

---

## Audit

### get_audit_settings

Fetch the current SQL audit settings for a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `AuditSettings` — object with `state` (`Enabled` or `Disabled`), `retentionDays`, and `auditActionsAndGroups`.

---

### enable_audit

Enable SQL auditing on a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `retention_days` (`int`, default `0`) — audit log retention in days; `0` means unlimited.

**Returns:** `AuditSettings` — the updated audit settings.

---

### disable_audit

Disable SQL auditing on a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `AuditSettings` — the updated audit settings.

---

### set_audit_action_groups

Replace the audited action groups for a warehouse. This overwrites the existing list of groups.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `action_groups` (`list[str]`) — list of audit action group names (e.g. `["BATCH_COMPLETED_GROUP"]`).

**Returns:** `AuditSettings` — the updated audit settings.

---

### add_audit_group

Add a single audit action group without overwriting the others. Idempotent — if the group is already present the current settings are returned unchanged. Auditing must already be enabled.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `group` (`str`) — action group name (e.g. `"BATCH_COMPLETED_GROUP"`).

**Returns:** `AuditSettings` — the updated audit settings.

---

### remove_audit_group

Remove a single audit action group without overwriting the others. Idempotent — if the group is not present the current settings are returned unchanged. Auditing must already be enabled.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `group` (`str`) — action group name (e.g. `"BATCH_COMPLETED_GROUP"`).

**Returns:** `AuditSettings` — the updated audit settings.

---

### set_audit_retention

Update the audit log retention period without changing the audit enabled/disabled state. Audit must already be enabled; if it is disabled, enable it first with `enable_audit`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `days` (`int`) — retention period in days (1–3653; 3653 ≈ 10 years).

**Returns:** `AuditSettings` — the updated audit settings.

---

## Queries

### list_running_queries

Return all currently-executing queries on a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `list[RunningQuery]` — array of query objects, each with `session_id`, `request_id`, `status`, `start_time`, `total_elapsed_time` (ms), `login_name`, `command`, and `query_text`.

---

### list_connections

Return all active SQL connections on a warehouse or SQL Analytics Endpoint. Queries `sys.dm_exec_connections`, which includes idle connections not visible via `list_running_queries`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `list[Connection]` — array of connection objects, each with `session_id`, `connect_time`, `client_net_address`, `auth_scheme`, `encrypt_option`, and `net_transport`.

---

### kill_session

Terminate a session on a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `session_id` (`int`) — the session ID to terminate.

**Returns:** `{ "killed": true, "session_id": int }` — confirmation with the terminated session ID.

---

## SQL

### execute_sql

Execute an arbitrary SQL statement or batch against a warehouse or SQL Analytics Endpoint.

> **Warning:** This tool executes arbitrary SQL, including DDL (DROP, ALTER, TRUNCATE) and DML (DELETE, UPDATE). Use only when the user explicitly requests data modification. Default to SELECT when the user's intent is read-only investigation.

Multi-statement batches are supported; only the **last** result set is returned. DDL/DML statements that produce no result set return `columns=[]` and `rows=[]`.

`datetime` and `Decimal` column values are pre-serialised to strings. `bytes`/varbinary columns are base64-encoded and their column names are suffixed with `__base64`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `query` (`str`) — SQL statement or batch to execute.

**Returns:** `{ "columns": list[str], "rows": list[list[Any]], "rowcount": int }` — `rowcount` is `-1` when the driver does not report a count.

---

## Restore Points

Restore point IDs are timestamp-based strings (e.g. `"1726617378000"`), not GUIDs. Each `RestorePoint` object has `id`, `displayName`, `description`, `creationMode` (`"UserDefined"` or `"SystemCreated"`), and `eventDateTime`.

### list_restore_points

Return all restore points for a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `list[RestorePoint]` — array of restore point objects.

---

### get_restore_point

Return a single restore point by ID.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `restore_point_id` (`str`) — the restore point ID string (e.g. `"1726617378000"`).

**Returns:** `RestorePoint` — the restore point object.

---

### create_restore_point

Create a restore point for a warehouse at the current timestamp.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `name` (`str | null`, optional) — display name (max 128 chars).
- `description` (`str | null`, optional) — description (max 512 chars).

**Returns:** `RestorePoint` — the newly-created restore point object.

---

### update_restore_point

Rename and/or update the description of a restore point. At least one of `name` or `description` must be supplied.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `restore_point_id` (`str`) — the restore point ID string.
- `name` (`str | null`, optional) — new display name (max 128 chars).
- `description` (`str | null`, optional) — new description (max 512 chars).

**Returns:** `RestorePoint` — the updated restore point object.

---

### delete_restore_point

Delete a user-defined restore point. System-created restore points cannot be deleted.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `restore_point_id` (`str`) — the restore point ID string.

**Returns:** `{ "deleted": true, "restore_point_id": str }` — confirmation.

---

### restore_warehouse_in_place

Restore a warehouse in-place to a restore point. **This is a destructive, long-running operation** — the warehouse will be unavailable for approximately 10 minutes while the restore completes.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `restore_point_id` (`str`) — the restore point ID string to restore to.

**Returns:** `{ "restored": true, "restore_point_id": str }` — confirmation.

---

## Snapshots

### list_snapshots

Return all snapshots belonging to a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `list[WarehouseSnapshot]` — array of snapshot objects, each with `id`, `displayName`, `parentWarehouseId`, and `snapshotDateTime`.

---

### create_snapshot

Create a new warehouse snapshot.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `name` (`str`) — display name for the new snapshot.
- `description` (`str | null`, optional) — optional description.
- `snapshot_dt` (`str | null`, optional) — ISO-8601 datetime string for the snapshot point-in-time; defaults to the current timestamp when omitted.

**Returns:** `WarehouseSnapshot` — the newly-created snapshot object.

---

### rename_snapshot

Rename a warehouse snapshot and optionally update its description.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `snapshot` (`str`) — snapshot name or GUID.
- `new_name` (`str`) — new display name.
- `description` (`str | null`, optional) — new description; omit to leave unchanged.

**Returns:** `WarehouseSnapshot` — the updated snapshot object.

---

### delete_snapshot

Delete a warehouse snapshot.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `snapshot` (`str`) — snapshot name or GUID.

**Returns:** `{ "deleted": true, "snapshot_id": str }` — confirmation with the snapshot GUID.

---

### roll_snapshot_timestamp

Roll a snapshot's timestamp forward, or reset it to the current time.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — parent warehouse name or GUID (used for the SQL connection).
- `snapshot_name` (`str`) — the snapshot database name to roll.
- `new_dt` (`str | null`, optional) — target ISO-8601 datetime string; defaults to `CURRENT_TIMESTAMP` when omitted.

**Returns:** `{ "rolled": true, "snapshot_name": str, "new_dt": str | null }` — confirmation with the snapshot name and the target datetime.

---

## SQL Views

### list_views

List SQL views on a warehouse or SQL Analytics Endpoint, optionally filtered to a single schema.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `schema` (`str | null`, optional) — when provided, only views in this schema are returned; must be a valid SQL identifier.

**Returns:** `list[View]` — array of view objects, each with `schema_name`, `name`, `qualified_name`, `created`, `modified`, and `definition` (always `null` for list results).

---

### get_view

Fetch the full definition of a single SQL view.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated schema and view name, e.g. `dbo.vw_sales`.

**Returns:** `View` — single view object with `definition` populated from `sys.sql_modules`.

---

### create_view

Create a new SQL view.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated schema and view name, e.g. `dbo.vw_sales`.
- `select_body` (`str`) — the SELECT statement that forms the view body; executed verbatim as DDL.

**Returns:** `View` — the newly-created view object (fetched after DDL, includes `definition`).

---

### update_view

Redefine an existing SQL view using `CREATE OR ALTER VIEW`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated schema and view name, e.g. `dbo.vw_sales`.
- `select_body` (`str`) — the new SELECT statement; executed verbatim as DDL.

**Returns:** `View` — the updated view object (fetched after DDL, includes `definition`).

---

### drop_view

Drop a SQL view.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated schema and view name, e.g. `dbo.vw_sales`.

**Returns:** `{ "dropped": true }` — confirmation.

---

## Tables

> **List-source note** — no public REST API exists for enumerating warehouse tables. `list_tables` uses TDS `sys.tables JOIN sys.schemas`.

### list_tables

List SQL tables on a warehouse or SQL Analytics Endpoint.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `schema` (`str | null`, optional) — when provided, only tables in this schema are returned.

**Returns:** `list[Table]` — each with `schema_name`, `name`, `qualified_name`, `created`, `modified`.

---

### read_table

Return up to `count` rows from a table as JSON-serialisable columns and rows.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated table name, e.g. `dbo.sales`.
- `count` (`int`, default `10`) — maximum rows to return.

**Returns:** `{ "columns": list[str], "rows": list[list] }` — column names and row arrays.

---

### create_table

Create a new SQL table via CTAS (`CREATE TABLE … AS SELECT`).

**CAUTION**: `select_body` is executed verbatim as DDL. Confirm intent before calling. The first non-comment keyword must be `SELECT`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated table name, e.g. `dbo.sales`.
- `select_body` (`str`) — the SELECT statement for the CTAS source.

**Returns:** `Table` — the newly-created table record.

---

### delete_table

Drop a SQL table.

**CAUTION**: This is a destructive, irreversible operation. All data will be permanently deleted. Confirm with the user before calling.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated table name, e.g. `dbo.sales`.

**Returns:** `{ "dropped": true }` — confirmation.

---

### clear_table

Truncate a SQL table (remove all rows, preserve structure).

**CAUTION**: This is a destructive, irreversible operation. All rows will be permanently deleted. The table structure is preserved. Confirm with the user before calling.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated table name, e.g. `dbo.sales`.

**Returns:** `{ "truncated": true }` — confirmation.

---

## SQL Pools (beta)

!!! warning "Beta / preview feature"
    The SQL Pools tools manage the workspace SQL Pools configuration (currently in preview; the API may change before GA). All callers must hold the **workspace admin role**.

### get_sql_pools_configuration

Fetch the full SQL Pools configuration (enabled flag + pool list) for a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `SqlPoolsConfiguration` — object with `customSQLPoolsEnabled` (bool) and `customSQLPools` (list of pool objects, each with `name`, `maxResourcePercentage`, `isDefault`, `optimizeForReads`, and optional `classifier`).

---

### list_sql_pools

Return the list of SQL pools for a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `list[SqlPool]` — array of pool objects, each with `name`, `isDefault`, `maxResourcePercentage`, `optimizeForReads`, and optional `classifier`.

---

### get_sql_pool

Return details for a single SQL pool by name.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `pool_name` (`str`) — pool name.

**Returns:** `SqlPool` — single pool object (fields as above).

---

### create_sql_pool

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

### update_sql_pool

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

---

### delete_sql_pool

Delete an SQL pool from a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `pool_name` (`str`) — name of the pool to delete.

**Returns:** `{ "deleted": true, "pool_name": str }` — confirmation.

---

### reset_sql_pools

Clear all SQL pools for a workspace.  The `customSQLPoolsEnabled` flag is preserved.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `SqlPoolsConfiguration` — the updated configuration (with an empty pool list).

---

### enable_sql_pools

Enable custom SQL Pools for a workspace without modifying pool definitions.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `SqlPoolsConfiguration` — the updated configuration.

---

### disable_sql_pools

Disable custom SQL Pools for a workspace, preserving the pool configuration. Re-enabling with `enable_sql_pools` restores the previously saved configuration.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `SqlPoolsConfiguration` — the updated configuration.

---

## Cache

### clear_cache

Erase all cached workspace and item name-to-UUID mappings.

**Parameters:** None

**Returns:** `{ "cleared": true }` — confirmation.

---

!!! note "Name-or-GUID resolution"
    All `workspace`, `warehouse`, `endpoint`, and `snapshot` parameters accept either the item's display name or its GUID. The resolver translates names to GUIDs automatically and caches the mapping locally. Use [`clear_cache`](#clear_cache) to force a fresh lookup after renaming items outside this tool. See the [CLI reference](cli.md) for further details on name resolution and cache behaviour.
