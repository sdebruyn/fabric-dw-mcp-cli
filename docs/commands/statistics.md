---
title: Statistics
---

# Statistics

Manage user-defined statistics on Fabric Data Warehouses and read their details on SQL Analytics Endpoints.

**Targets:** Data Warehouse · SQL Analytics Endpoint

!!! note

    Only **single-column, histogram-based** statistics can be created or updated (Fabric limitation). Multi-column statistics are not supported.

## CLI

### statistics create

**Targets:** Data Warehouse only

Create a new single-column statistic.

```
fdw [-w WORKSPACE] statistics create [ITEM] --table schema.table --column COL --name NAME [OPTIONS]
```

| Option | Description | Default |
| --- | --- | --- |
| `--table schema.table` | Qualified table name (required). | - |
| `--column COL` | Column name to build the statistic on (required). Single column only. | - |
| `--name NAME` | Statistic name (required). | - |
| `--fullscan` | Use `WITH FULLSCAN` (default). Mutually exclusive with `--sample-percent`. | on |
| `--sample-percent N` | Sample `N`% of the table (1–100). Overrides `--fullscan`. | - |

### statistics delete

**Targets:** Data Warehouse only

Drop a statistic via `DROP STATISTICS`. Prompts for confirmation unless `--yes` is passed.

```
fdw [-w WORKSPACE] statistics delete [ITEM] QUALIFIED_TABLE STAT_NAME
```

### statistics list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List statistics on an item.

```
fdw [-w WORKSPACE] statistics list [ITEM] [OPTIONS]
```

| Option | Description | Default |
| --- | --- | --- |
| `--schema NAME` | Filter by schema name. | (all schemas) |
| `--table NAME` | Filter by table name (unqualified). | (all tables) |
| `--user-only` | Only show user-created statistics. | off |
| `--auto-only` | Only show auto-created statistics. | off |

### statistics show

**Targets:** Data Warehouse · SQL Analytics Endpoint

Show details of a named statistic using `DBCC SHOW_STATISTICS`. Returns the stat header, density vector, and histogram steps.

```
fdw [-w WORKSPACE] statistics show [ITEM] QUALIFIED_TABLE STAT_NAME [OPTIONS]
```

`QUALIFIED_TABLE` must be a dot-separated qualified name, e.g. `dbo.sales`.

| Option | Description | Default |
| --- | --- | --- |
| `--histogram` | Show only the histogram steps (skip header and density vector). | off |

### statistics update

**Targets:** Data Warehouse only

Update an existing statistic via `UPDATE STATISTICS`.

```
fdw [-w WORKSPACE] statistics update [ITEM] QUALIFIED_TABLE STAT_NAME [OPTIONS]
```

| Option | Description | Default |
| --- | --- | --- |
| `--fullscan` | Use `WITH FULLSCAN` (default). | on |
| `--sample-percent N` | Sample `N`% of the table (1–100). Overrides `--fullscan`. | - |

## MCP tools

Manage user-defined statistics on Fabric Data Warehouses and inspect them on SQL Analytics Endpoints.

!!! note

    Only **single-column, histogram-based** statistics can be created or updated (Fabric limitation). Multi-column statistics are not supported.

    Write tools (`create_statistics`, `update_statistics`, `delete_statistics`) are rejected on SQL Analytics Endpoints. Read tools (`list_statistics`, `show_statistics`) work on both item kinds.

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

**Returns:** `Statistic`: the newly-created statistic.

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

**Returns:** `{ "dropped": true }`: confirmation.

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

**Returns:** `list[dict]`: array of `Statistic` objects.

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

**Returns:** `StatisticDetails`: `{ stat_header, density_vector, histogram }`.

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

**Returns:** `{ "updated": true }`: confirmation.
