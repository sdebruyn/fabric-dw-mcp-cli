---
title: Settings
---

# Settings

Manage **server-side** database settings on a Fabric Data Warehouse or SQL Analytics Endpoint.

!!! note

    `settings` manages server-side warehouse/database configuration. For client-side CLI defaults (workspace, warehouse) use [`fdw config`](../cli.md#defaults-fabric-dw-config) instead.

`show` and `result-set-caching` work on both Data Warehouses and SQL Analytics Endpoints. The `retention` command sets the time-travel retention period, which is primarily a Warehouse concept and may be a no-op on a SQL Analytics Endpoint.

The workspace is resolved from the global `-w/--workspace` option, the `FABRIC_DW_DEFAULT_WORKSPACE` environment variable, or the client-side config default. `ITEM` may be a display name or GUID.

**Targets:** Data Warehouse · SQL Analytics Endpoint

---

## CLI

### settings result-set-caching

**Targets:** Data Warehouse · SQL Analytics Endpoint

Enable or disable result-set caching.

Executes `ALTER DATABASE CURRENT SET RESULT_SET_CACHING { ON | OFF }` on the target.

```shell
fdw -w <workspace> settings result-set-caching [ITEM] (on|off)
```

`STATE` is case-insensitive (`on`, `off`, `ON`, `OFF`).

**Example:**

```shell
fdw -w MyWorkspace settings result-set-caching MyWarehouse on
fdw -w MyWorkspace settings result-set-caching MyWarehouse off
fdw -w MyWorkspace --json settings result-set-caching MyWarehouse on
```

---

### settings retention

**Targets:** Data Warehouse · SQL Analytics Endpoint

Set the time-travel retention period in days.

Executes `ALTER DATABASE CURRENT SET TIME_TRAVEL_RETENTION_PERIOD = <DAYS> DAYS` on the target.

```shell
fdw -w <workspace> settings retention [ITEM] --days DAYS
```

| Option | Description | Default |
| --- | --- | --- |
| `--days N` | Retention period in days (1-120, required). | — |

**Example:**

```shell
fdw -w MyWorkspace settings retention MyWarehouse --days 30
fdw -w MyWorkspace --json settings retention MyWarehouse --days 7
```

---

### settings show

**Targets:** Data Warehouse · SQL Analytics Endpoint

Display all server-side database settings for an item in a single table.

```shell
fdw -w <workspace> settings show [ITEM]
```

**Example:**

```shell
fdw -w MyWorkspace settings show MyWarehouse
fdw -w MyWorkspace --json settings show MyWarehouse
```

---

## MCP tools

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
