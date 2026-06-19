---
title: MCP tools reference
---

# MCP tools reference

The MCP server exposes the following tools. Each takes the parameters listed and returns a JSON object whose shape matches the Pydantic model described in the Returns line. See [Authentication](authentication.md) for token setup and [MCP server setup](mcp.md) for installation.

---

## Item targets: Data Warehouse vs SQL Analytics Endpoint

Fabric has two SQL-surface item kinds:

- **Data Warehouse** — read-write, supports full DDL (CREATE/DROP/TRUNCATE TABLE, CREATE/DROP SCHEMA, CREATE/ALTER VIEW, etc.).
- **SQL Analytics Endpoint** — read-only SQL surface auto-generated over a Lakehouse. DDL and mutating operations are not supported; only read/query operations are allowed.

Each tool below is labelled with one of:

- **`Targets: Data Warehouse · SQL Analytics Endpoint`** — the tool works on both item kinds.
- **`Targets: Data Warehouse only`** — the tool is blocked on SQL Analytics Endpoints (either by an explicit guard in the source code, because it requires write/DDL capability that endpoints do not have, or because it calls warehouse-scoped REST API paths that are not available for SQL Analytics Endpoints).
- **`Targets: SQL Analytics Endpoint`** — the tool operates on SQL Analytics Endpoints specifically (not on Data Warehouses).
- **`Targets: Workspace (not item-specific)`** — the tool operates at the workspace level and does not target a specific DW or SQL Analytics Endpoint item.

---

## Workspaces

### list_workspaces

**Targets:** Workspace (not item-specific)

List all Fabric workspaces the authenticated principal has access to.

**Parameters:** None

**Returns:** `list[Workspace]` — array of workspace objects, each with `id`, `displayName`, `description`, `capacityId`, and `defaultDatasetStorageFormat`.

---

### get_workspace

**Targets:** Workspace (not item-specific)

Return details for a single workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `Workspace` — single workspace object (fields as above).

---

### set_workspace_collation

**Targets:** Workspace (not item-specific)

Set the default Data Warehouse collation for a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `collation` (`str`) — collation name (must be a supported Fabric collation).

**Returns:** `{ "workspace_id": str, "collation": str }` — the workspace GUID and the newly-set collation.

---

## Warehouses

### list_warehouses

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all warehouses and SQL analytics endpoints in a workspace, or across all visible workspaces.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID; required when `all_workspaces` is `false`; ignored when `true`.
- `all_workspaces` (`bool`, default `false`) — when `true`, aggregate results across every workspace the caller can see.

**Returns:** `list[Warehouse]` — array of warehouse objects, each with `id`, `displayName`, `description`, `workspaceId`, `kind` (`Warehouse` or `SQLEndpoint`), `connectionString`, `defaultCollation`, and `createdDate`.

---

### get_warehouse

**Targets:** Data Warehouse only

Return details for a single Data Warehouse. Uses the warehouse-scoped REST path (`GET /workspaces/{ws}/warehouses/{id}`); passing a SQL Analytics Endpoint will return a 404. Use `get_sql_endpoint` to retrieve endpoint details.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `Warehouse` — single warehouse object (fields as above).

---

### create_warehouse

**Targets:** Data Warehouse only

Create a new Warehouse in a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `name` (`str`) — display name for the new warehouse.
- `collation` (`str | null`, optional) — default collation for the new warehouse.
- `description` (`str | null`, optional) — description for the new warehouse.

**Returns:** `Warehouse` — the newly-created warehouse object.

---

### rename_warehouse

**Targets:** Data Warehouse only

Rename a Warehouse and optionally update its description.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `new_name` (`str`) — new display name.
- `description` (`str | null`, optional) — new description; omit to leave unchanged.

**Returns:** `Warehouse` — the updated warehouse object.

---

### delete_warehouse

**Targets:** Data Warehouse only

Delete a Warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `{ "deleted": true, "warehouse_id": str }` — confirmation with the warehouse GUID.

---

### takeover_warehouse

**Targets:** Data Warehouse only

Take ownership of a Warehouse. Not supported for SQL analytics endpoints.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `{ "taken_over": true, "warehouse_id": str }` — confirmation with the warehouse GUID.

---

### get_warehouse_permissions

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return all principals (users, groups, service principals) with access to a Warehouse, including their effective permissions.

!!! note

    Requires **Fabric Administrator** role (`Tenant.Read.All` or `Tenant.ReadWrite.All` scope). See [Microsoft Fabric admin documentation](https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin) for how to request the role.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `list[ItemAccess]` — array of access records, each with `principal` (containing `id`, `displayName`, `type`, and type-specific fields such as `userPrincipalName` or `aadAppId`) and `itemAccessDetails` (containing `type`, `permissions`, and `additionalPermissions`).

---

## SQL analytics endpoints

### list_sql_endpoints

**Targets:** SQL Analytics Endpoint

List all SQL analytics endpoints in a workspace, or across all visible workspaces.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID; required when `all_workspaces` is `false`; ignored when `true`.
- `all_workspaces` (`bool`, default `false`) — when `true`, aggregate results across every workspace the caller can see.

**Returns:** `list[Warehouse]` — array of SQL analytics endpoint objects (same fields as Warehouse, `kind` is always `SQLEndpoint`).

---

### get_sql_endpoint

**Targets:** SQL Analytics Endpoint

Return details for a single SQL analytics endpoint.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `endpoint` (`str`) — endpoint name or GUID.

**Returns:** `Warehouse` — single SQL analytics endpoint object.

---

### refresh_sql_endpoint_metadata

**Targets:** SQL Analytics Endpoint

Refresh metadata for a SQL analytics endpoint by syncing from the underlying Lakehouse delta tables. This is a long-running operation (LRO) polled to completion.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `endpoint` (`str`) — endpoint name or GUID.
- `recreate_tables` (`bool`, default `False`) — drop and recreate all tables during the refresh. Use to resolve inconsistencies or force a clean rebuild. DESTRUCTIVE — use with caution.

**Returns:** `list[TableSyncStatus]` — array of per-table sync results, each with `tableName`, `status`, `startDateTime`, `endDateTime`, `lastSuccessfulSyncDateTime`, and `error`.

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

## Audit

### get_audit_settings

**Targets:** Data Warehouse only

Fetch the current SQL audit settings for a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `AuditSettings` — object with `state` (`Enabled` or `Disabled`), `retentionDays`, and `auditActionsAndGroups`.

---

### enable_audit

**Targets:** Data Warehouse only

Enable SQL auditing on a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `retention_days` (`int`, default `0`) — audit log retention in days; `0` means unlimited.

**Returns:** `AuditSettings` — the updated audit settings.

---

### disable_audit

**Targets:** Data Warehouse only

Disable SQL auditing on a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `AuditSettings` — the updated audit settings.

---

### set_audit_action_groups

**Targets:** Data Warehouse only

Replace the audited action groups for a warehouse. This overwrites the existing list of groups.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `action_groups` (`list[str]`) — list of audit action group names (e.g. `["BATCH_COMPLETED_GROUP"]`).

**Returns:** `AuditSettings` — the updated audit settings.

---

### add_audit_group

**Targets:** Data Warehouse only

Add a single audit action group without overwriting the others. Idempotent — if the group is already present the current settings are returned unchanged. Auditing must already be enabled.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `group` (`str`) — action group name (e.g. `"BATCH_COMPLETED_GROUP"`).

**Returns:** `AuditSettings` — the updated audit settings.

---

### remove_audit_group

**Targets:** Data Warehouse only

Remove a single audit action group without overwriting the others. Idempotent — if the group is not present the current settings are returned unchanged. Auditing must already be enabled.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `group` (`str`) — action group name (e.g. `"BATCH_COMPLETED_GROUP"`).

**Returns:** `AuditSettings` — the updated audit settings.

---

### set_audit_retention

**Targets:** Data Warehouse only

Update the audit log retention period without changing the audit enabled/disabled state. Audit must already be enabled; if it is disabled, enable it first with `enable_audit`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `days` (`int`) — retention period in days (1–3653; 3653 ≈ 10 years).

**Returns:** `AuditSettings` — the updated audit settings.

---

## Queries

### list_running_queries

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return all currently-executing queries on a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `list[RunningQuery]` — array of query objects, each with `session_id`, `request_id`, `status`, `start_time`, `total_elapsed_time` (ms), `login_name`, `command`, and `query_text`.

---

### list_connections

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return all active SQL connections on a warehouse or SQL Analytics Endpoint. Queries `sys.dm_exec_connections`, which includes idle connections not visible via `list_running_queries`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `list[Connection]` — array of connection objects, each with `session_id`, `connect_time`, `client_net_address`, `auth_scheme`, `encrypt_option`, and `net_transport`.

---

### kill_session

**Targets:** Data Warehouse · SQL Analytics Endpoint

Terminate a session on a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `session_id` (`int`) — the session ID to terminate.

**Returns:** `{ "killed": true, "session_id": int }` — confirmation with the terminated session ID.

---

The following four tools query the `queryinsights` schema DMVs via TDS. They share the same parameter shape — `workspace`, `warehouse`, optional `limit`, optional `since`, and optional `until`.

### list_request_history

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return completed SQL requests from `queryinsights.exec_requests_history`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `limit` (`int`, default `100`) — maximum rows to return (1–10 000).
- `since` (`str | null`, optional) — ISO-8601 lower bound on `submit_time`.
- `until` (`str | null`, optional) — ISO-8601 upper bound on `submit_time`.

**Returns:** `list[dict]` — array of request-history row objects. Elapsed-time and CPU-time fields (e.g. `total_elapsed_time_ms`, `allocated_cpu_time_ms`) are JSON `number` (float) because Fabric returns fractional millisecond values.

---

### list_session_history

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return completed sessions from `queryinsights.exec_sessions_history`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `limit` (`int`, default `100`) — maximum rows to return (1–10 000).
- `since` (`str | null`, optional) — ISO-8601 lower bound on `session_start_time`.
- `until` (`str | null`, optional) — ISO-8601 upper bound on `session_start_time`.

**Returns:** `list[dict]` — array of session-history row objects. `total_query_elapsed_time_ms` is JSON `number` (float) because Fabric returns fractional millisecond values.

---

### list_frequent_queries

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return frequently-run queries from `queryinsights.frequently_run_queries`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `limit` (`int`, default `100`) — maximum rows to return (1–10 000).
- `since` (`str | null`, optional) — ISO-8601 lower bound on `last_run_start_time`.
- `until` (`str | null`, optional) — ISO-8601 upper bound on `last_run_start_time`.

**Returns:** `list[dict]` — array of frequently-run query row objects. Elapsed-time fields (e.g. `avg_total_elapsed_time_ms`, `min_run_total_elapsed_time_ms`, `max_run_total_elapsed_time_ms`, `last_run_total_elapsed_time_ms`) are JSON `number` (float); count fields remain `integer`.

---

### list_long_running_queries

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return long-running queries from `queryinsights.long_running_queries`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `limit` (`int`, default `100`) — maximum rows to return (1–10 000).
- `since` (`str | null`, optional) — ISO-8601 lower bound on `last_run_start_time`.
- `until` (`str | null`, optional) — ISO-8601 upper bound on `last_run_start_time`.

**Returns:** `list[dict]` — array of long-running query row objects. `median_total_elapsed_time_ms` and `last_run_total_elapsed_time_ms` are JSON `number` (float); `number_of_runs` remains `integer`.

---

## SQL

### execute_sql

**Targets:** Data Warehouse · SQL Analytics Endpoint

Execute an arbitrary SQL statement or batch against a warehouse or SQL Analytics Endpoint.

!!! warning

    This tool executes arbitrary SQL, including DDL (DROP, ALTER, TRUNCATE) and DML (DELETE, UPDATE). Use only when the user explicitly requests data modification. Default to SELECT when the user's intent is read-only investigation.

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

**Targets:** Data Warehouse only

Return all restore points for a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `list[RestorePoint]` — array of restore point objects.

---

### get_restore_point

**Targets:** Data Warehouse only

Return a single restore point by ID.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `restore_point_id` (`str`) — the restore point ID string (e.g. `"1726617378000"`).

**Returns:** `RestorePoint` — the restore point object.

---

### create_restore_point

**Targets:** Data Warehouse only

Create a restore point for a warehouse at the current timestamp.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `name` (`str | null`, optional) — display name (max 128 chars).
- `description` (`str | null`, optional) — description (max 512 chars).

**Returns:** `RestorePoint` — the newly-created restore point object.

---

### update_restore_point

**Targets:** Data Warehouse only

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

**Targets:** Data Warehouse only

Delete a user-defined restore point. System-created restore points cannot be deleted.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `restore_point_id` (`str`) — the restore point ID string.

**Returns:** `{ "deleted": true, "restore_point_id": str }` — confirmation.

---

### restore_warehouse_in_place

**Targets:** Data Warehouse only

Restore a warehouse in-place to a restore point. **This is a destructive, long-running operation** — the warehouse will be unavailable for approximately 10 minutes while the restore completes.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.
- `restore_point_id` (`str`) — the restore point ID string to restore to.

**Returns:** `{ "restored": true, "restore_point_id": str }` — confirmation.

---

## Snapshots

### list_snapshots

**Targets:** Data Warehouse only

Return all snapshots belonging to a warehouse.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `warehouse` (`str`) — warehouse name or GUID.

**Returns:** `list[WarehouseSnapshot]` — array of snapshot objects, each with `id`, `displayName`, `parentWarehouseId`, and `snapshotDateTime`.

---

### create_snapshot

**Targets:** Data Warehouse only

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

**Targets:** Data Warehouse only

Rename a warehouse snapshot and optionally update its description.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `snapshot` (`str`) — snapshot name or GUID.
- `new_name` (`str`) — new display name.
- `description` (`str | null`, optional) — new description; omit to leave unchanged.

**Returns:** `WarehouseSnapshot` — the updated snapshot object.

---

### delete_snapshot

**Targets:** Data Warehouse only

Delete a warehouse snapshot.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `snapshot` (`str`) — snapshot name or GUID.

**Returns:** `{ "deleted": true, "snapshot_id": str }` — confirmation with the snapshot GUID.

---

### roll_snapshot_timestamp

**Targets:** Data Warehouse only

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

**Targets:** Data Warehouse · SQL Analytics Endpoint

List SQL views on a warehouse or SQL Analytics Endpoint, optionally filtered to a single schema.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `schema` (`str | null`, optional) — when provided, only views in this schema are returned; must be a valid SQL identifier.

**Returns:** `list[View]` — array of view objects, each with `schema_name`, `name`, `qualified_name`, `created`, `modified`, and `definition` (always `null` for list results).

---

### get_view

**Targets:** Data Warehouse · SQL Analytics Endpoint

Fetch the full definition of a single SQL view.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated schema and view name, e.g. `dbo.vw_sales`.

**Returns:** `View` — single view object with `definition` populated from `sys.sql_modules`.

---

### create_view

**Targets:** Data Warehouse · SQL Analytics Endpoint

Create a new SQL view.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated schema and view name, e.g. `dbo.vw_sales`.
- `select_body` (`str`) — the SELECT statement that forms the view body; executed verbatim as DDL.

**Returns:** `View` — the newly-created view object (fetched after DDL, includes `definition`).

---

### update_view

**Targets:** Data Warehouse · SQL Analytics Endpoint

Redefine an existing SQL view using `CREATE OR ALTER VIEW`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated schema and view name, e.g. `dbo.vw_sales`.
- `select_body` (`str`) — the new SELECT statement; executed verbatim as DDL.

**Returns:** `View` — the updated view object (fetched after DDL, includes `definition`).

---

### drop_view

**Targets:** Data Warehouse · SQL Analytics Endpoint

Drop a SQL view.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated schema and view name, e.g. `dbo.vw_sales`.

**Returns:** `{ "dropped": true }` — confirmation.

---

### rename_view

**Targets:** Data Warehouse · SQL Analytics Endpoint

Rename a SQL view via `sp_rename`. Works on both Data Warehouses and SQL Analytics Endpoints. The new name must be a bare (unqualified) identifier — `sp_rename` cannot move a view across schemas.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — current dot-separated qualified view name, e.g. `dbo.vw_sales`.
- `new_name` (`str`) — new bare view name (no schema prefix), e.g. `vw_revenue`.

**Returns:** `View` — the updated view object (fetched after rename, includes `definition`).

---

### count_view_rows

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return the total row count of a view via `SELECT COUNT_BIG(*)`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated schema and view name, e.g. `dbo.vw_sales`.

**Returns:** `{ "schema": str, "name": str, "row_count": int }` — the schema name, view name, and total row count.

---

### read_view

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return up to `count` rows from a view as JSON-serialisable columns and rows.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated schema and view name, e.g. `dbo.vw_sales`.
- `count` (`int`, default `10`) — maximum rows to return.

**Returns:** `{ "columns": list[str], "rows": list[list] }` — column names and row arrays.

---

## Tables

!!! note "List source"

    No public REST API exists for enumerating warehouse tables. `list_tables` uses TDS `sys.tables JOIN sys.schemas`.

### list_tables

**Targets:** Data Warehouse · SQL Analytics Endpoint

List SQL tables on a warehouse or SQL Analytics Endpoint.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `schema` (`str | null`, optional) — when provided, only tables in this schema are returned.

**Returns:** `list[Table]` — each with `schema_name`, `name`, `qualified_name`, `created`, `modified`.

---

### count_table_rows

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return the total row count of a table via `SELECT COUNT_BIG(*)`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated table name, e.g. `dbo.sales`.

**Returns:** `{ "schema": str, "name": str, "row_count": int }` — the schema name, table name, and total row count.

---

### read_table

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return up to `count` rows from a table as JSON-serialisable columns and rows.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated table name, e.g. `dbo.sales`.
- `count` (`int`, default `10`) — maximum rows to return.

**Returns:** `{ "columns": list[str], "rows": list[list] }` — column names and row arrays.

---

### create_table

**Targets:** Data Warehouse only

Create a new SQL table via CTAS (`CREATE TABLE … AS SELECT`).

**CAUTION**: `select_body` is executed verbatim as DDL. Confirm intent before calling. The first non-comment keyword must be `SELECT`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated table name, e.g. `dbo.sales`.
- `select_body` (`str`) — the SELECT statement for the CTAS source.

**Returns:** `Table` — the newly-created table record.

---

### create_empty_table

**Targets:** Data Warehouse only

Create an empty SQL table from an explicit column specification (DDL only — no data is ever read or inserted). This scaffolds the table structure so that data can be loaded separately.

Server-side file access is unreliable in MCP deployments, so CSV/Parquet schema inference is not available via this tool. Use `fdw tables create --from-parquet` or `--from-csv` (CLI) for file-based schema inference.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse name or GUID. SQL Analytics Endpoints are rejected.
- `qualified_name` (`str`) — dot-separated table name, e.g. `dbo.sales`.
- `columns` (`list[object]`) — non-empty list of column definitions, each an object with:
  - `name` (`str`) — column identifier (must be a valid SQL identifier).
  - `sql_type` (`str`) — Fabric-DW-supported T-SQL type, e.g. `"INT"`, `"VARCHAR(255)"`, `"DECIMAL(18,2)"`.
  - `nullable` (`bool`, optional, default `true`) — whether the column allows `NULL`.

**Returns:** `Table` — the newly-created table record.

**Example call:**

```json
{
  "workspace": "MyWorkspace",
  "item": "SalesWarehouse",
  "qualified_name": "dbo.events",
  "columns": [
    {"name": "event_id", "sql_type": "BIGINT", "nullable": false},
    {"name": "event_type", "sql_type": "VARCHAR(100)", "nullable": true},
    {"name": "occurred_at", "sql_type": "DATETIME2(7)", "nullable": false}
  ]
}
```

---

### delete_table

**Targets:** Data Warehouse only

Drop a SQL table.

**CAUTION**: This is a destructive, irreversible operation. All data will be permanently deleted. Confirm with the user before calling.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated table name, e.g. `dbo.sales`.

**Returns:** `{ "dropped": true }` — confirmation.

---

### clear_table

**Targets:** Data Warehouse only

Truncate a SQL table (remove all rows, preserve structure).

**CAUTION**: This is a destructive, irreversible operation. All rows will be permanently deleted. The table structure is preserved. Confirm with the user before calling.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated table name, e.g. `dbo.sales`.

**Returns:** `{ "truncated": true }` — confirmation.

---

### clone_table

**Targets:** Data Warehouse only

Create a zero-copy clone of a table using `CREATE TABLE … AS CLONE OF …`. Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse name or GUID.
- `source` (`str`) — qualified source table name, e.g. `dbo.sales`.
- `new_table` (`str`) — qualified name for the new cloned table, e.g. `dbo.sales_clone`.
- `at` (`str | null`, optional) — ISO-8601 UTC timestamp for a point-in-time clone (e.g. `2024-05-20T14:00:00`). Must be within the data-retention window. When omitted, the clone reflects the current state of the source table.

**Returns:** `Table` — the newly-created cloned table record.

---

### rename_table

**Targets:** Data Warehouse only

Rename a SQL table via `sp_rename`. Only supported on Fabric Data Warehouses (SQL Analytics Endpoints are rejected). The new name must be a bare (unqualified) identifier — `sp_rename` cannot move a table to a different schema.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse name or GUID.
- `qualified_name` (`str`) — current dot-separated qualified table name, e.g. `dbo.sales`.
- `new_name` (`str`) — new bare table name (no schema prefix), e.g. `sales_v2`.

**Returns:** `Table` — the updated table record.

---

### load_table_from_url

**Targets:** Data Warehouse only

Load data into a Data Warehouse table via `COPY INTO` from a remote URL. For OneLake or same-tenant URLs, no credential is needed. For secured external URLs (Azure Blob Storage, ADLS Gen2), supply `credential_type` and the appropriate `secret`/`identity` values.

!!! warning "JSON not supported for remote URLs"

    If you need to load JSON, download the file locally and use the CLI `tables load --file` command instead (which converts JSON to Parquet client-side).

!!! note "Secret safety"

    The `secret` and `identity` parameters are accepted but are **never** logged or echoed in any server output.

!!! note "Table must exist"

    This tool does not create the target table. Use [`import_table_from_url`](#import_table_from_url) for a load-only flow with `if_exists` control over an existing table, or the CLI `tables load --file --create` for auto-create from a local file with schema inference.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse name or GUID. SQL Analytics Endpoints are rejected.
- `qualified_name` (`str`) — dot-separated qualified table name, e.g. `dbo.sales`.
- `url` (`str`) — source URL (OneLake DFS URL or external Azure Blob URL).
- `file_type` (`"CSV" | "PARQUET"`) — file type to load.
- `credential_type` (`"none" | "sas" | "managed-identity" | "service-principal" | "account-key"`, default `"none"`) — credential type for secured external URLs.
- `secret` (`str | null`, optional) — credential secret (SAS token, client secret, or account key). Never logged.
- `identity` (`str | null`, optional) — identity for `managed-identity` or `service-principal`.
- `delimiter` (`str | null`, optional) — CSV column delimiter (e.g. `,`, `\t`).
- `has_header` (`bool`, default `true`) — when `true`, the first CSV row is a header and is skipped.
- `encoding` (`str | null`, optional) — CSV encoding (e.g. `UTF8`, `UTF8BOM`).
- `field_quote` (`str | null`, optional) — CSV field-quote character.
- `row_terminator` (`str | null`, optional) — CSV row terminator (e.g. `\n`, `\r\n`).
- `max_errors` (`int | null`, optional) — maximum errors before aborting.
- `rejected_row_location` (`str | null`, optional) — URL to write rejected rows to.

**Returns:** `CopyIntoResult` — `{ "rows_loaded": int, "rows_rejected": int, "target": "schema.table" }`.

---

### import_table_from_url

**Targets:** Data Warehouse only

Load data from a remote URL into an existing Data Warehouse table with control over what happens when the table already has data. This tool extends [`load_table_from_url`](#load_table_from_url) with an `if_exists` policy.

!!! warning "Schema inference not supported for remote URLs"

    This tool does not auto-create the target table from the source schema (downloading the full file just for schema inference is not practical for remote sources). To auto-create a table from schema, use the CLI `tables load --file --create` with a local file. For `if_exists="replace"`, use the CLI instead.

!!! warning "Destructive operation"

    **`truncate` and `replace` are destructive** and require `FABRIC_MCP_ALLOW_DESTRUCTIVE=1`.

!!! note "Secret safety"

    The `secret` and `identity` parameters are accepted but are **never** logged or echoed in any server output.

**`if_exists` policy:**

| Value | Table exists | Table absent |
| --- | --- | --- |
| `"fail"` (default) | Error — table already exists | Load normally |
| `"append"` | Load into existing table | Load normally |
| `"truncate"` ⚠️ | `TRUNCATE` existing table, then load | Load normally |
| `"replace"` ⚠️ | Not supported for remote URLs — use CLI | Load normally |

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse name or GUID. SQL Analytics Endpoints are rejected.
- `qualified_name` (`str`) — dot-separated qualified table name, e.g. `dbo.sales`.
- `url` (`str`) — source URL (OneLake DFS URL or external Azure Blob URL).
- `file_type` (`"CSV" | "PARQUET"`) — file type to load. JSON is not supported for remote URLs.
- `if_exists` (`"fail" | "append" | "truncate" | "replace"`, default `"fail"`) — what to do when the target table already exists.
- `credential_type` (`"none" | "sas" | "managed-identity" | "service-principal" | "account-key"`, default `"none"`) — credential type for secured external URLs.
- `secret` (`str | null`, optional) — credential secret (SAS token, client secret, or account key). Never logged.
- `identity` (`str | null`, optional) — identity for `managed-identity` or `service-principal`.
- `delimiter` (`str | null`, optional) — CSV column delimiter (e.g. `,`, `\t`).
- `has_header` (`bool`, default `true`) — when `true`, the first CSV row is a header and is skipped.
- `encoding` (`str | null`, optional) — CSV encoding (e.g. `UTF8`, `UTF8BOM`).
- `field_quote` (`str | null`, optional) — CSV field-quote character.
- `row_terminator` (`str | null`, optional) — CSV row terminator (e.g. `\n`, `\r\n`).
- `max_errors` (`int | null`, optional) — maximum errors before aborting.
- `rejected_row_location` (`str | null`, optional) — URL to write rejected rows to.

**Returns:** `CopyIntoResult` — `{ "rows_loaded": int, "rows_rejected": int, "target": "schema.table" }`.

---

## Procedures

### list_procedures

**Targets:** Data Warehouse · SQL Analytics Endpoint

List stored procedures on a warehouse or SQL Analytics Endpoint, optionally filtered to a single schema.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `schema` (`str | null`, optional) — when provided, only procedures in this schema are returned.

**Returns:** `list[StoredProcedure]` — array of procedure objects, each with `schema_name`, `name`, `qualified_name`, `created`, and `modified`.

---

### get_procedure

**Targets:** Data Warehouse · SQL Analytics Endpoint

Fetch the full definition of a single stored procedure.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated qualified procedure name, e.g. `dbo.usp_load`.

**Returns:** `StoredProcedure` — single procedure object with `definition` populated from `sys.sql_modules`.

---

### create_procedure

**Targets:** Data Warehouse · SQL Analytics Endpoint

Create a new stored procedure.

!!! warning "Caution"

    `body` is executed verbatim as DDL. Ensure the body matches the user's intent before calling this tool.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated qualified procedure name, e.g. `dbo.usp_load`.
- `body` (`str`) — the procedure body (the `AS …` section).

**Returns:** `StoredProcedure` — the newly-created procedure object.

---

### update_procedure

**Targets:** Data Warehouse · SQL Analytics Endpoint

Redefine a stored procedure via `CREATE OR ALTER PROCEDURE`.

!!! warning "Caution"

    `body` is executed verbatim as DDL. Ensure the body matches the user's intent before calling this tool.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated qualified procedure name, e.g. `dbo.usp_load`.
- `body` (`str`) — the new procedure body (the `AS …` section).

**Returns:** `StoredProcedure` — the updated procedure object.

---

### drop_procedure

**Targets:** Data Warehouse · SQL Analytics Endpoint

Drop a stored procedure.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated qualified procedure name, e.g. `dbo.usp_load`.

**Returns:** `{ "dropped": true }` — confirmation.

---

## Functions

!!! warning "Preview"

    Scalar UDFs (`FN`) and inline TVFs (`IF`) are preview features on Fabric DW as of mid-2026. Function DDL is supported on both Data Warehouses and SQL Analytics Endpoints — no endpoint guard applies.

### list_functions

**Targets:** Data Warehouse · SQL Analytics Endpoint

List T-SQL user-defined functions on a warehouse or SQL Analytics Endpoint, optionally filtered by schema or kind.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `schema` (`str | null`, optional) — when provided, only functions in this schema are returned.
- `kind` (`str`, optional) — filter by function kind: `"scalar"` (FN only), `"inline-tvf"` (IF only), or `"all"` (FN + IF + TF, the default).

**Returns:** `list[Function]` — array of function objects, each with `schema_name`, `name`, `qualified_name`, `kind`, `is_inlineable`, `created`, and `modified`.

---

### get_function

**Targets:** Data Warehouse · SQL Analytics Endpoint

Fetch the full definition of a single T-SQL user-defined function, including its parameter list.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated qualified function name, e.g. `dbo.fn_clean_input`.

**Returns:** `FunctionDetails` — single function object with `definition` (from `sys.sql_modules`) and `parameters` (from `sys.parameters`).

---

### create_function

**Targets:** Data Warehouse · SQL Analytics Endpoint

Create a new T-SQL user-defined function. Scalar UDFs and inline TVFs are preview features.

!!! warning "Caution"

    `body` is executed verbatim as DDL. Ensure the body matches the user's intent before calling this tool.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated qualified function name, e.g. `dbo.fn_clean_input`.
- `body` (`str`) — the function body (parameter list, RETURNS clause, and implementation).

**Returns:** `FunctionDetails` — the newly-created function object.

---

### update_function

**Targets:** Data Warehouse · SQL Analytics Endpoint

Redefine a T-SQL user-defined function via `CREATE OR ALTER FUNCTION`.

!!! note

    `ALTER FUNCTION` cannot change the function kind (e.g. scalar to inline TVF). The body must be compatible with the original function's kind.

!!! warning "Caution"

    `body` is executed verbatim as DDL. Ensure the body matches the user's intent before calling this tool.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated qualified function name, e.g. `dbo.fn_clean_input`.
- `body` (`str`) — the new function body (parameter list, RETURNS clause, and implementation).

**Returns:** `FunctionDetails` — the updated function object.

---

### drop_function

**Targets:** Data Warehouse · SQL Analytics Endpoint

Drop a T-SQL user-defined function.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `qualified_name` (`str`) — dot-separated qualified function name, e.g. `dbo.fn_clean_input`.
- `if_exists` (`bool`, optional) — when `true`, emits `DROP FUNCTION IF EXISTS` (no-op when function does not exist). Defaults to `false`.

**Returns:** `{ "dropped": true }` — confirmation.

---

### rename_function

**Targets:** Data Warehouse · SQL Analytics Endpoint

Rename a T-SQL user-defined function via `sp_rename`. The new name must be a bare (unqualified) identifier — `sp_rename` cannot move a function across schemas.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `qualified_name` (`str`) — current dot-separated qualified function name, e.g. `dbo.fn_clean_input`.
- `new_name` (`str`) — new bare function name (no schema prefix), e.g. `fn_sanitize_input`.

**Returns:** `FunctionDetails` — the renamed function record.

---

## Schemas

!!! note "List source"

    No public REST API exists for enumerating warehouse schemas. `list_schemas` uses TDS `sys.schemas`, filtering out `sys`, `INFORMATION_SCHEMA`, `guest`, and `db_*` fixed-role schemas. `dbo` is always included because it is user-writable.

!!! note "SQL Analytics Endpoints"

    `list_schemas`, `create_schema`, and `delete_schema` all work on both Fabric Data Warehouses and SQL Analytics Endpoints. When `delete_schema` is called with `cascade=True` on a SQL Analytics Endpoint, views, stored procedures, and functions are dropped, but tables are **not** dropped (because `DROP TABLE` is a Warehouse-only operation on Fabric). If the schema still contains tables after the cascade pass, the subsequent `DROP SCHEMA` will be rejected by the engine; remove the tables manually before deleting the schema.

### list_schemas

**Targets:** Data Warehouse · SQL Analytics Endpoint

List user-defined SQL schemas on a warehouse or SQL Analytics Endpoint. System schemas are excluded automatically.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.

**Returns:** `list[Schema]` — array of schema objects, each with `name` and `principal_id`.

---

### create_schema

**Targets:** Data Warehouse · SQL Analytics Endpoint

Create a new SQL schema on a Fabric Data Warehouse or SQL Analytics Endpoint.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `name` (`str`) — the schema name; must be a valid SQL identifier.

**Returns:** `Schema` — the newly-created schema record with `name` and `principal_id`.

---

### delete_schema

**Targets:** Data Warehouse · SQL Analytics Endpoint

Drop a SQL schema from a Fabric Data Warehouse or SQL Analytics Endpoint.

**CAUTION**: This is a destructive, irreversible operation. The schema will be permanently deleted. If the schema still contains tables or views the operation will fail unless `cascade=True`.

**CAUTION**: When `cascade=True`, **all tables and views in the schema are permanently deleted along with their data**. Confirm explicitly with the user before calling with `cascade=True`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `name` (`str`) — the schema name to drop.
- `cascade` (`bool`, default `False`) — when `True`, drop all tables and views in the schema first.

**Returns:** `{ "deleted": true }` — confirmation.

---

## SQL Pools (beta)

!!! warning "Beta / preview feature"
    The SQL Pools tools manage the workspace SQL Pools configuration (currently in preview; the API may change before GA). All callers must hold the **workspace admin role**.

### get_sql_pools_configuration

**Targets:** Workspace (not item-specific)

Fetch the full SQL Pools configuration (enabled flag + pool list) for a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `SqlPoolsConfiguration` — object with `customSQLPoolsEnabled` (bool) and `customSQLPools` (list of pool objects, each with `name`, `maxResourcePercentage`, `isDefault`, `optimizeForReads`, and optional `classifier`).

---

### list_sql_pools

**Targets:** Workspace (not item-specific)

Return the list of SQL pools for a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `list[SqlPool]` — array of pool objects, each with `name`, `isDefault`, `maxResourcePercentage`, `optimizeForReads`, and optional `classifier`.

---

### get_sql_pool

**Targets:** Workspace (not item-specific)

Return details for a single SQL pool by name.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `pool_name` (`str`) — pool name.

**Returns:** `SqlPool` — single pool object (fields as above).

---

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

---

### delete_sql_pool

**Targets:** Workspace (not item-specific)

Delete an SQL pool from a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `pool_name` (`str`) — name of the pool to delete.

**Returns:** `{ "deleted": true, "pool_name": str }` — confirmation.

---

### enable_sql_pools

**Targets:** Workspace (not item-specific)

Enable custom SQL Pools for a workspace without modifying pool definitions.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `SqlPoolsConfiguration` — the updated configuration.

---

### disable_sql_pools

**Targets:** Workspace (not item-specific)

Disable custom SQL Pools for a workspace, preserving the pool configuration. Re-enabling with `enable_sql_pools` restores the previously saved configuration.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `SqlPoolsConfiguration` — the updated configuration.

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

## Statistics

Manage user-defined statistics on Fabric Data Warehouses and inspect them on SQL Analytics Endpoints.

!!! note

    Only **single-column, histogram-based** statistics can be created or updated (Fabric limitation). Multi-column statistics are not supported.

    Write tools (`create_statistics`, `update_statistics`, `delete_statistics`) are rejected on SQL Analytics Endpoints. Read tools (`list_statistics`, `show_statistics`) work on both item kinds.

### list_statistics

**Targets:** Data Warehouse · SQL Analytics Endpoint

**Guards:** `assert_workspace_allowed`

List statistics on a warehouse or SQL Analytics Endpoint.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse or SQL endpoint name or GUID. |
| `schema` | `str \| None` | Filter by schema name. |
| `table` | `str \| None` | Filter by table name (unqualified). |
| `user_only` | `bool` | Only return user-created statistics. |
| `auto_only` | `bool` | Only return auto-created statistics. |

**Returns:** `list[dict]` — array of `Statistic` objects.

---

### show_statistics

**Targets:** Data Warehouse · SQL Analytics Endpoint

**Guards:** `assert_workspace_allowed`

Show details of a statistic using `DBCC SHOW_STATISTICS`. Returns the stat header, density vector, and histogram steps.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse or SQL endpoint name or GUID. |
| `qualified_table` | `str` | Qualified table name, e.g. `dbo.sales`. |
| `stat_name` | `str` | Name of the statistic to show. |
| `histogram_only` | `bool` | When `true`, return only the histogram steps. |

**Returns:** `StatisticDetails` — `{ stat_header, density_vector, histogram }`.

---

### create_statistics

**Targets:** Data Warehouse only

**Guards:** `assert_writes_allowed`, `assert_workspace_allowed`

Create a single-column statistic on a table. Only single-column statistics are supported (Fabric limitation). SQL Analytics Endpoints are rejected.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse name or GUID. |
| `qualified_table` | `str` | Qualified table name, e.g. `dbo.sales`. |
| `column` | `str` | Column name. |
| `stat_name` | `str` | Name for the new statistic. |
| `fullscan` | `bool` | Use `WITH FULLSCAN` (default `true`). |
| `sample_percent` | `int \| None` | Sample percentage (1–100). Overrides `fullscan`. |

**Returns:** `Statistic` — the newly-created statistic.

---

### update_statistics

**Targets:** Data Warehouse only

**Guards:** `assert_writes_allowed`, `assert_workspace_allowed`

Update an existing statistic via `UPDATE STATISTICS`. SQL Analytics Endpoints are rejected.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse name or GUID. |
| `qualified_table` | `str` | Qualified table name, e.g. `dbo.sales`. |
| `stat_name` | `str` | Name of the statistic to update. |
| `fullscan` | `bool` | Use `WITH FULLSCAN` (default `true`). |
| `sample_percent` | `int \| None` | Sample percentage (1–100). Overrides `fullscan`. |

**Returns:** `{ "updated": true }` — confirmation.

---

### delete_statistics

**Targets:** Data Warehouse only

**Guards:** `assert_writes_allowed`, `assert_destructive_allowed`, `assert_workspace_allowed`

Drop a statistic via `DROP STATISTICS`. **Destructive and irreversible.** Requires `FABRIC_MCP_ALLOW_DESTRUCTIVE=1`. SQL Analytics Endpoints are rejected.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse name or GUID. |
| `qualified_table` | `str` | Qualified table name, e.g. `dbo.sales`. |
| `stat_name` | `str` | Name of the statistic to drop. |

**Returns:** `{ "dropped": true }` — confirmation.

---

## Settings

Read and modify server-side database settings on Fabric Data Warehouses and SQL Analytics Endpoints.

> **Note:** The read tool (`get_warehouse_settings`) works on both Data Warehouses and SQL Analytics Endpoints. The write tools (`set_result_set_caching`, `set_time_travel_retention`) execute `ALTER DATABASE CURRENT SET …` statements, which require autocommit and are primarily meaningful on Data Warehouses. Both setters return the effective `WarehouseSettings` read back after the change.

CLI equivalents: `fdw settings get`, `fdw settings set-result-set-caching`, `fdw settings set-time-travel-retention`.

### get_warehouse_settings

**Targets:** Data Warehouse · SQL Analytics Endpoint

**Guards:** `assert_workspace_allowed`

Return the current server-side database settings for a warehouse. Reads `result_set_caching`, `time_travel_retention_days`, and `time_travel_retention_cutoff_date` from `sys.databases`.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse or SQL Analytics Endpoint name or GUID. |

**Returns:** `WarehouseSettings` — `{ database, result_set_caching, time_travel_retention_days, time_travel_retention_cutoff_date }`.

---

### set_result_set_caching

**Targets:** Data Warehouse · SQL Analytics Endpoint

**Guards:** `assert_writes_allowed`, `assert_workspace_allowed`

Enable or disable result-set caching on a warehouse. Executes `ALTER DATABASE CURRENT SET RESULT_SET_CACHING { ON | OFF }` and returns the effective settings after the change.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse or SQL Analytics Endpoint name or GUID. |
| `enabled` | `bool` | `true` to enable result-set caching, `false` to disable it. |

**Returns:** `WarehouseSettings` — the effective settings after the change.

---

### set_time_travel_retention

**Targets:** Data Warehouse · SQL Analytics Endpoint

**Guards:** `assert_writes_allowed`, `assert_workspace_allowed`

Set the time-travel retention period on a warehouse. Executes `ALTER DATABASE CURRENT SET TIME_TRAVEL_RETENTION_PERIOD = <n> DAYS` and returns the effective settings after the change.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse or SQL Analytics Endpoint name or GUID. |
| `days` | `int` | Retention period in days. Must be in the range 1–120 (inclusive). |

**Returns:** `WarehouseSettings` — the effective settings after the change.

---

## dbt scaffold

Generate [dbt-fabric](https://docs.getdbt.com/docs/core/connect-data-platform/fabric-setup) project file contents pre-wired to a Microsoft Fabric Data Warehouse. Unlike the CLI `dbt init` command, these tools return file contents as text rather than writing files to disk, making them suitable for AI-assisted workflows where the AI agent writes the files.

!!! warning "Security"

    When `authentication="ServicePrincipal"` is used, the returned `profiles_yml` contains Jinja2 `env_var()` placeholders (`{{ env_var('AZURE_TENANT_ID') }}` etc.) rather than literal secrets. Never hard-code secrets into source-controlled files.

### generate_dbt_profile

**Targets:** Data Warehouse

Return the contents of all files needed to bootstrap a dbt project that connects to a Fabric Data Warehouse. No files are written; the caller is responsible for persisting the returned strings.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — Data Warehouse name or GUID.
- `project_name` (`str`, optional) — dbt project name. Defaults to the warehouse display name (sanitised to lowercase + underscores).
- `profile_name` (`str`, optional) — dbt profile name. Defaults to the project name.
- `schema` (`str`, default `"dbo"`) — default target schema for dbt models.
- `target` (`str`, default `"dev"`) — dbt target name.
- `threads` (`int`, default `4`) — number of dbt threads (1–64).
- `authentication` (`str`, optional) — authentication method (`"auto"`, `"CLI"`, `"ServicePrincipal"`). Defaults to the MCP server's active credential mode.
- `with_sources` (`bool`, default `False`) — when `True`, introspect the live warehouse and include all schemas/tables in the returned `sources_yml`.

**Returns:** object with:

- `profiles_yml` (`str`) — contents for `profiles.yml` (write next to `dbt_project.yml` or into `~/.dbt/profiles.yml`).
- `dbt_project_yml` (`str`) — contents for `dbt_project.yml`.
- `sources_yml` (`str`) — contents for `models/staging/_sources.yml` (placeholder or real entries when `with_sources=True`).
- `requirements_txt` (`str`) — pip requirements listing `dbt-core` and `dbt-fabric`.
- `gitignore` (`str`) — contents for `.gitignore`, pre-configured for dbt projects.

---

## Cache

### clear_cache

**Targets:** Workspace (not item-specific)

Erase all cached workspace and item name-to-UUID mappings.

**Parameters:** None

**Returns:** `{ "cleared": true }` — confirmation.

---

!!! note "Name-or-GUID resolution"
    All `workspace`, `warehouse`, `endpoint`, and `snapshot` parameters accept either the item's display name or its GUID. The resolver translates names to GUIDs automatically and caches the mapping locally. Use [`clear_cache`](#clear_cache) to force a fresh lookup after renaming items outside this tool. See the [CLI reference](cli.md) for further details on name resolution and cache behaviour.
