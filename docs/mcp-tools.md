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

## Cache

### clear_cache

Erase all cached workspace and item name-to-UUID mappings.

**Parameters:** None

**Returns:** `{ "cleared": true }` — confirmation.

---

!!! note "Name-or-GUID resolution"
    All `workspace`, `warehouse`, `endpoint`, and `snapshot` parameters accept either the item's display name or its GUID. The resolver translates names to GUIDs automatically and caches the mapping locally. Use [`clear_cache`](#clear_cache) to force a fresh lookup after renaming items outside this tool. See the [CLI reference](cli.md) for further details on name resolution and cache behaviour.
