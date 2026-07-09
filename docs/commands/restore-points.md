---
title: Restore Points
---

# Restore Points

Manage Microsoft Fabric Warehouse restore points.

A restore point captures the state of a warehouse at a point in time. User-defined restore points can be created, renamed, and deleted. System-created restore points are managed automatically by Fabric and cannot be deleted. Restore point IDs are timestamp-based strings (e.g. `"1726617378000"`), not GUIDs.

**Targets:** Data Warehouse only

## CLI

### restore-points create

**Targets:** Data Warehouse only

Create a new restore point for a warehouse at the current timestamp.

**Synopsis**

```
fdw [-w WORKSPACE] restore-points create [OPTIONS] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Optional display name (max 128 chars). |
| `--description TEXT` | Optional description (max 512 chars). |

**Example**

```shell
fdw -w MyWorkspace restore-points create SalesWH \
  --name "Before migration" \
  --description "Pre-migration checkpoint"
```

### restore-points delete

**Targets:** Data Warehouse only

Delete a user-defined restore point. System-created restore points cannot be deleted. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] restore-points delete [WAREHOUSE] RESTORE_POINT_ID
```

**Example**

```shell
fdw -w MyWorkspace --yes restore-points delete SalesWH 1726617378000
```

### restore-points get

**Targets:** Data Warehouse only

Get details for a single restore point by ID.

**Synopsis**

```
fdw [-w WORKSPACE] restore-points get [WAREHOUSE] RESTORE_POINT_ID
```

**Example**

```shell
fdw -w MyWorkspace restore-points get SalesWH 1726617378000
```

### restore-points list

**Targets:** Data Warehouse only

List all restore points for a warehouse.

**Synopsis**

```
fdw [-w WORKSPACE] restore-points list [WAREHOUSE]
```

**Example**

```shell
fdw -w MyWorkspace restore-points list SalesWH
```

```
 id              displayName        creationMode   eventDateTime
 --------------- ------------------ -------------- ---------------------
 1726617378000   Before migration   UserDefined    2024-10-18T22:17:09Z
```

### restore-points rename

**Targets:** Data Warehouse only

Rename a restore point and optionally update its description.

**Synopsis**

```
fdw [-w WORKSPACE] restore-points rename [OPTIONS] [WAREHOUSE] RESTORE_POINT_ID NEW_NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional new description. |

**Example**

```shell
fdw -w MyWorkspace restore-points rename SalesWH 1726617378000 "Post-migration backup"
```

### restore-points restore

**Targets:** Data Warehouse only

Restore a warehouse in-place to a restore point. **This is a destructive operation**: the warehouse will be unavailable for approximately 10 minutes. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] restore-points restore [WAREHOUSE] RESTORE_POINT_ID
```

**Example**

```shell
fdw -w MyWorkspace --yes restore-points restore SalesWH 1726617378000
```

## MCP tools

### create_restore_point

**Targets:** Data Warehouse only

Create a restore point for a warehouse at the current timestamp.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.
- `name` (`str | null`, optional): display name (max 128 chars).
- `description` (`str | null`, optional): description (max 512 chars).

**Returns:** `RestorePoint`: the newly-created restore point object.

### delete_restore_point

**Targets:** Data Warehouse only

Delete a user-defined restore point. System-created restore points cannot be deleted.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.
- `restore_point_id` (`str`): the restore point ID string.

**Returns:** `{ "deleted": true, "restore_point_id": str }`: confirmation.

### get_restore_point

**Targets:** Data Warehouse only

Return a single restore point by ID.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.
- `restore_point_id` (`str`): the restore point ID string (e.g. `"1726617378000"`).

**Returns:** `RestorePoint`: the restore point object.

### list_restore_points

**Targets:** Data Warehouse only

Return all restore points for a warehouse.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.

**Returns:** `list[RestorePoint]`: array of restore point objects.

### restore_warehouse_in_place

**Targets:** Data Warehouse only

Restore a warehouse in-place to a restore point. **This is a destructive, long-running operation**: the warehouse will be unavailable for approximately 10 minutes while the restore completes.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.
- `restore_point_id` (`str`): the restore point ID string to restore to.

**Returns:** `{ "restored": true, "restore_point_id": str }`: confirmation.

### update_restore_point

**Targets:** Data Warehouse only

Rename and/or update the description of a restore point. At least one of `name` or `description` must be supplied.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.
- `restore_point_id` (`str`): the restore point ID string.
- `name` (`str | null`, optional): new display name (max 128 chars).
- `description` (`str | null`, optional): new description (max 512 chars).

**Returns:** `RestorePoint`: the updated restore point object.
