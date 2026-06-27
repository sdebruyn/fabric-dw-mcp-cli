---
title: Views
---

# Views

Manage SQL views on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

**Targets:** Data Warehouse / SQL Analytics Endpoint

## CLI

### views columns

**Targets:** Data Warehouse / SQL Analytics Endpoint

List the columns of a view, including name, formatted data type, nullability, ordinal position, collation, identity, and computed flags.

**Synopsis**

```
fdw [-w WORKSPACE] views columns [WAREHOUSE] QUALIFIED_NAME
```

`QUALIFIED_NAME` must be a dot-separated `schema.view_name` string, e.g. `dbo.vw_sales`.

**Example**

```shell
fdw -w MyWorkspace views columns SalesWH dbo.vw_sales
```

```
 ordinal  name    data_type     nullable  is_identity  is_computed  collation_name
 -------  ------  ------------  --------  -----------  -----------  ----------------------------
 1        id      INT           False     False        False
 2        amount  DECIMAL(18,2) True      False        False
```

### views count

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return the total row count of a view using `SELECT COUNT_BIG(*)`.

**Synopsis**

```
fdw [-w WORKSPACE] views count [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fdw -w MyWorkspace --json views count SalesWH dbo.vw_sales
```

```json
{"schema": "dbo", "name": "vw_sales", "row_count": 12345}
```

### views create

**Targets:** Data Warehouse / SQL Analytics Endpoint

Create a new SQL view.

**Synopsis**

```
fdw [-w WORKSPACE] views create [OPTIONS] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--name SCHEMA.VIEW` | **Required.** Qualified view name (e.g. `dbo.vw_sales`). |
| `--select TEXT` | Inline SELECT statement for the view body. |
| `--from-file PATH` | Path to a `.sql` file containing the SELECT statement. |

Exactly one of `--select` or `--from-file` must be provided.

**Example**

```shell
fdw -w MyWorkspace views create SalesWH \
  --name dbo.vw_recent \
  --select "SELECT id, amount FROM dbo.sales WHERE sale_date >= '2026-01-01'"
```

### views drop

**Targets:** Data Warehouse / SQL Analytics Endpoint

Drop a SQL view. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] views drop [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fdw -w MyWorkspace --yes views drop SalesWH dbo.vw_recent
```

### views get

**Targets:** Data Warehouse / SQL Analytics Endpoint

Get the full definition of a single view.

**Synopsis**

```
fdw [-w WORKSPACE] views get [WAREHOUSE] QUALIFIED_NAME
```

`QUALIFIED_NAME` must be a dot-separated `schema.view_name` string, e.g. `dbo.vw_sales`.

**Example**

```shell
fdw -w MyWorkspace views get SalesWH dbo.vw_sales
```

```
schema_name    dbo
name           vw_sales
qualified_name dbo.vw_sales
created        2026-01-10T08:00:00Z
modified       2026-06-01T12:00:00Z
definition     SELECT id, amount FROM dbo.sales
```

### views list

**Targets:** Data Warehouse / SQL Analytics Endpoint

List all views on a warehouse or SQL Analytics Endpoint. Pass `--schema` to filter to a single schema.

**Synopsis**

```
fdw [-w WORKSPACE] views list [OPTIONS] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--schema TEXT` | Only list views in this schema. |

**Example**

```shell
fdw -w MyWorkspace views list SalesWH --schema dbo
```

```
 schema_name  name         created               modified
 ------------ ------------ --------------------- ---------------------
 dbo          vw_sales     2026-01-10T08:00:00Z  2026-06-01T12:00:00Z
 dbo          vw_monthly   2026-02-01T09:00:00Z  2026-05-15T14:00:00Z
```

### views read

**Targets:** Data Warehouse / SQL Analytics Endpoint

Read up to `--count` rows from a view and emit them as JSON (default), CSV, or Parquet.

CSV and Parquet formats require `--output`. JSON is emitted to stdout by default.

**Synopsis**

```
fdw [-w WORKSPACE] views read [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

| Option | Description | Default |
| --- | --- | --- |
| `--count N` | Maximum rows to return. | `10` |
| `--format {json\|csv\|parquet}` | Output format. | `json` |
| `--output PATH` | Write to file instead of stdout. Required for `csv` and `parquet`. | |

**Example**

```shell
fdw -w MyWorkspace views read SalesWH dbo.vw_sales --count 5
```

```json
[
  {"id": 1, "amount": 99.99, "customer_id": 42},
  ...
]
```

### views rename

**Targets:** Data Warehouse / SQL Analytics Endpoint

Rename a SQL view via `sp_rename`. The new name must be an unqualified (bare) identifier - `sp_rename` cannot move a view to a different schema.

**Synopsis**

```
fdw [-w WORKSPACE] views rename [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the current dot-separated `schema.view_name`.

| Option | Description |
| --- | --- |
| `--new-name TEXT` | **Required.** New bare view name (no schema prefix). |

**Example**

```shell
fdw -w MyWorkspace views rename SalesWH dbo.vw_recent --new-name vw_revenue
```

### views update

**Targets:** Data Warehouse / SQL Analytics Endpoint

Redefine an existing view using `CREATE OR ALTER VIEW`.

**Synopsis**

```
fdw [-w WORKSPACE] views update [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the dot-separated `schema.view_name` to update.

| Option | Description |
| --- | --- |
| `--select TEXT` | Inline SELECT statement for the new view body. |
| `--from-file PATH` | Path to a `.sql` file containing the new SELECT statement. |

Exactly one of `--select` or `--from-file` must be provided.

**Example**

```shell
fdw -w MyWorkspace views update SalesWH dbo.vw_recent \
  --select "SELECT id, amount, region FROM dbo.sales WHERE sale_date >= '2026-01-01'"
```

## MCP tools

### count_view_rows

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return the total row count of a view via `SELECT COUNT_BIG(*)`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated schema and view name, e.g. `dbo.vw_sales`.

**Returns:** `{ "schema": str, "name": str, "row_count": int }`: the schema name, view name, and total row count.

### create_view

**Targets:** Data Warehouse / SQL Analytics Endpoint

Create a new SQL view.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated schema and view name, e.g. `dbo.vw_sales`.
- `select_body` (`str`): the SELECT statement that forms the view body; executed verbatim as DDL.

**Returns:** `View`: the newly-created view object (fetched after DDL, includes `definition`).

### drop_view

**Targets:** Data Warehouse / SQL Analytics Endpoint

Drop a SQL view.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated schema and view name, e.g. `dbo.vw_sales`.

**Returns:** `{ "dropped": true }`: confirmation.

### get_view

**Targets:** Data Warehouse / SQL Analytics Endpoint

Fetch the full definition of a single SQL view.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated schema and view name, e.g. `dbo.vw_sales`.

**Returns:** `View`: single view object with `definition` populated from `sys.sql_modules`.

### get_view_columns

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return column metadata for a SQL view via `sys.columns`. Works on both Fabric Data Warehouses and SQL Analytics Endpoints.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated view name, e.g. `dbo.vw_sales`.

**Returns:** `list[dict]`: one dict per column, each containing:

- `ordinal` (`int`): 1-based column position (`column_id`).
- `name` (`str`): column name.
- `data_type` (`str`): formatted T-SQL type string, e.g. `INT`, `NVARCHAR(MAX)`, `DECIMAL(18,2)`.
- `nullable` (`bool`): whether the column allows `NULL`.
- `collation_name` (`str | null`): collation name, if applicable.
- `is_identity` (`bool`): whether the column is an identity column.
- `is_computed` (`bool`): whether the column is a computed column.

Results are ordered by ordinal position. Raises a `ToolError` if the view does not exist.

### list_views

**Targets:** Data Warehouse / SQL Analytics Endpoint

List SQL views on a warehouse or SQL Analytics Endpoint, optionally filtered to a single schema.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `schema` (`str | null`, optional): when provided, only views in this schema are returned; must be a valid SQL identifier.

**Returns:** `list[View]`: array of view objects, each with `schema_name`, `name`, `qualified_name`, `created`, `modified`, and `definition` (always `null` for list results).

### read_view

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return up to `count` rows from a view as JSON-serialisable columns and rows.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated schema and view name, e.g. `dbo.vw_sales`.
- `count` (`int`, default `10`): maximum rows to return.

**Returns:** `{ "columns": list[str], "rows": list[list] }`: column names and row arrays.

### rename_view

**Targets:** Data Warehouse / SQL Analytics Endpoint

Rename a SQL view via `sp_rename`. Works on both Data Warehouses and SQL Analytics Endpoints. The new name must be a bare (unqualified) identifier - `sp_rename` cannot move a view across schemas.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): current dot-separated qualified view name, e.g. `dbo.vw_sales`.
- `new_name` (`str`): new bare view name (no schema prefix), e.g. `vw_revenue`.

**Returns:** `View`: the updated view object (fetched after rename, includes `definition`).

### update_view

**Targets:** Data Warehouse / SQL Analytics Endpoint

Redefine an existing SQL view using `CREATE OR ALTER VIEW`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated schema and view name, e.g. `dbo.vw_sales`.
- `select_body` (`str`): the new SELECT statement; executed verbatim as DDL.

**Returns:** `View`: the updated view object (fetched after DDL, includes `definition`).
