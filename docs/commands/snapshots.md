---
title: Snapshots
---

# Snapshots

Manage Microsoft Fabric Data Warehouse snapshots.

**Targets:** Data Warehouse only

---

## CLI

### snapshots create

**Targets:** Data Warehouse only

Create a new snapshot for a warehouse. Optionally pin it to a specific point in time.

**Synopsis**

```
fdw [-w WORKSPACE] snapshots create [OPTIONS] [WAREHOUSE] NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional description. |
| `--snapshot-dt TEXT` | Optional snapshot datetime (ISO 8601, UTC). Defaults to the current timestamp. |

**Example**

```shell
fdw -w MyWorkspace snapshots create SalesWH snap-2026-06-08 \
  --snapshot-dt 2026-06-08T00:00:00Z
```

---

### snapshots delete

**Targets:** Data Warehouse only

Delete a snapshot. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] snapshots delete SNAPSHOT
```

**Example**

```shell
fdw -w MyWorkspace --yes snapshots delete snap-old
```

---

### snapshots list

**Targets:** Data Warehouse only

List all snapshots for a warehouse.

**Synopsis**

```
fdw [-w WORKSPACE] snapshots list [WAREHOUSE]
```

**Example**

```shell
fdw -w MyWorkspace snapshots list SalesWH
```

```
 displayName      id           createdTime
 ---------------- ------------ ---------------------
 snap-2026-06-01  d1e2...      2026-06-01T00:00:00Z
```

---

### snapshots rename

**Targets:** Data Warehouse only

Rename a snapshot and optionally update its description.

**Synopsis**

```
fdw [-w WORKSPACE] snapshots rename [OPTIONS] SNAPSHOT NEW_NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional new description. |

**Example**

```shell
fdw -w MyWorkspace snapshots rename snap-2026-06-01 snap-june-2026
```

---

### snapshots roll

**Targets:** Data Warehouse only

Roll a snapshot on a warehouse to a new timestamp. `SNAPSHOT_NAME` must be the display name of the snapshot database. The warehouse and workspace are resolved via the usual precedence rules.

**Synopsis**

```
fdw [-w WORKSPACE] snapshots roll [OPTIONS] [WAREHOUSE] SNAPSHOT_NAME
```

| Option | Description |
| --- | --- |
| `--at TEXT` | Target datetime (ISO 8601, UTC). Defaults to `CURRENT_TIMESTAMP`. |

**Example**

```shell
fdw -w MyWorkspace snapshots roll SalesWH snap-june-2026 \
  --at 2026-06-08T12:00:00Z
```

---

## MCP tools

### create_snapshot

**Targets:** Data Warehouse only

Create a new warehouse snapshot.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.
- `name` (`str`): display name for the new snapshot.
- `description` (`str | null`, optional): optional description.
- `snapshot_dt` (`str | null`, optional): ISO-8601 datetime string for the snapshot point-in-time; defaults to the current timestamp when omitted.

**Returns:** `WarehouseSnapshot`: the newly-created snapshot object.

---

### delete_snapshot

**Targets:** Data Warehouse only

Delete a warehouse snapshot.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `snapshot` (`str`): snapshot name or GUID.

**Returns:** `{ "deleted": true, "snapshot_id": str }`: confirmation with the snapshot GUID.

---

### list_snapshots

**Targets:** Data Warehouse only

Return all snapshots belonging to a warehouse.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.

**Returns:** `list[WarehouseSnapshot]`: array of snapshot objects, each with `id`, `displayName`, `parentWarehouseId`, and `snapshotDateTime`.

---

### rename_snapshot

**Targets:** Data Warehouse only

Rename a warehouse snapshot and optionally update its description.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `snapshot` (`str`): snapshot name or GUID.
- `new_name` (`str`): new display name.
- `description` (`str | null`, optional): new description; omit to leave unchanged.

**Returns:** `WarehouseSnapshot`: the updated snapshot object.

---

### roll_snapshot_timestamp

**Targets:** Data Warehouse only

Roll a snapshot's timestamp forward, or reset it to the current time.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): parent warehouse name or GUID (used for the SQL connection).
- `snapshot_name` (`str`): the snapshot database name to roll.
- `new_dt` (`str | null`, optional): target ISO-8601 datetime string; defaults to `CURRENT_TIMESTAMP` when omitted.

**Returns:** `{ "rolled": true, "snapshot_name": str, "new_dt": str | null }`: confirmation with the snapshot name and the target datetime.
