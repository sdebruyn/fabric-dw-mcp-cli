---
title: Settings
---

# Settings

Manage **server-side** database settings on a Fabric Data Warehouse or SQL Analytics Endpoint.

!!! note

    `settings` manages server-side warehouse/database configuration. For client-side CLI defaults (workspace, warehouse) use [`fdw config`](config.md) instead.

`show` works on both Data Warehouses and SQL Analytics Endpoints. `result-set-caching`, `retention`, and `data-lake-log-publishing` issue `ALTER DATABASE CURRENT SET …`, which is **not supported on SQL Analytics Endpoints**: running any of them against an endpoint returns a clean error.

The workspace is resolved from the global `-w/--workspace` option, the `FABRIC_DW_DEFAULT_WORKSPACE` environment variable, or the client-side config default. `ITEM` may be a display name or GUID.

**Targets:** Data Warehouse

## CLI

### settings data-lake-log-publishing

**Targets:** Data Warehouse

Enable or disable Delta Lake log publishing.

Executes `ALTER DATABASE CURRENT SET DATA_LAKE_LOG_PUBLISHING = { AUTO | PAUSED }` on the target.

```shell
fdw -w <workspace> settings data-lake-log-publishing [ITEM] (on|off)
```

`STATE` is case-insensitive (`on`, `off`, `ON`, `OFF`). `on` maps to `= AUTO`; `off` maps to `= PAUSED`.

**Example:**

```shell
fdw -w MyWorkspace settings data-lake-log-publishing MyWarehouse on
fdw -w MyWorkspace settings data-lake-log-publishing MyWarehouse off
fdw -w MyWorkspace --json settings data-lake-log-publishing MyWarehouse on
```

### settings result-set-caching

**Targets:** Data Warehouse

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

### settings retention

**Targets:** Data Warehouse

Set the time-travel retention period in days.

Executes `ALTER DATABASE CURRENT SET TIME_TRAVEL_RETENTION_PERIOD = <DAYS> DAYS` on the target.

```shell
fdw -w <workspace> settings retention [ITEM] --days DAYS
```

| Option | Description | Default |
| --- | --- | --- |
| `--days N` | Retention period in days (1-120, required). | - |

**Example:**

```shell
fdw -w MyWorkspace settings retention MyWarehouse --days 30
fdw -w MyWorkspace --json settings retention MyWarehouse --days 7
```

### settings show

**Targets:** Data Warehouse / SQL Analytics Endpoint

Display all server-side database settings for an item in a single table.

```shell
fdw -w <workspace> settings show [ITEM]
```

**Example:**

```shell
fdw -w MyWorkspace settings show MyWarehouse
fdw -w MyWorkspace --json settings show MyWarehouse
```

## MCP tools

### get_warehouse_settings

**Targets:** Data Warehouse / SQL Analytics Endpoint

**Guards:** `assert_workspace_allowed`

Return the current server-side database settings for a warehouse. Reads `result_set_caching`, `time_travel_retention_days`, `time_travel_retention_cutoff_date`, and `data_lake_log_publishing` from `sys.databases`.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse or SQL Analytics Endpoint name or GUID. |

**Returns:** `WarehouseSettings`: `{ database, result_set_caching, time_travel_retention_days, time_travel_retention_cutoff_date, data_lake_log_publishing }`.

### set_data_lake_log_publishing

**Targets:** Data Warehouse

**Guards:** `assert_writes_allowed`, `assert_workspace_allowed`

Enable or disable Delta Lake log publishing on a warehouse. Executes `ALTER DATABASE CURRENT SET DATA_LAKE_LOG_PUBLISHING = { AUTO | PAUSED }` and returns the effective settings after the change.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse name or GUID. SQL Analytics Endpoints are rejected. |
| `enabled` | `bool` | `true` to enable Delta Lake log publishing (`= AUTO`), `false` to disable it (`= PAUSED`). |

**Returns:** `WarehouseSettings`: the effective settings after the change.

### set_result_set_caching

**Targets:** Data Warehouse

**Guards:** `assert_writes_allowed`, `assert_workspace_allowed`

Enable or disable result-set caching on a warehouse. Executes `ALTER DATABASE CURRENT SET RESULT_SET_CACHING { ON | OFF }` and returns the effective settings after the change.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse or SQL Analytics Endpoint name or GUID. |
| `enabled` | `bool` | `true` to enable result-set caching, `false` to disable it. |

**Returns:** `WarehouseSettings`: the effective settings after the change.

### set_time_travel_retention

**Targets:** Data Warehouse

**Guards:** `assert_writes_allowed`, `assert_workspace_allowed`

Set the time-travel retention period on a warehouse. Executes `ALTER DATABASE CURRENT SET TIME_TRAVEL_RETENTION_PERIOD = <n> DAYS` and returns the effective settings after the change.

**Parameters:**

| Parameter | Type | Description |
| --- | --- | --- |
| `workspace` | `str` | Workspace name or GUID. |
| `item` | `str` | Warehouse or SQL Analytics Endpoint name or GUID. |
| `days` | `int` | Retention period in days. Must be in the range 1-120 (inclusive). |

**Returns:** `WarehouseSettings`: the effective settings after the change.
