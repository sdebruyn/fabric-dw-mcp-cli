---
title: Tables & views
---

# Creating & managing tables and views

This guide walks through building out a schema model on a Microsoft Fabric Data Warehouse with `fabric-dw`: from an empty schema, through populated tables, to reporting views and the statistics that keep the optimizer honest. Every step shows a runnable CLI example (`fdw …`) and names the equivalent MCP tool, so the same workflow applies whether you drive it from a terminal or from an AI assistant wired to the [MCP server](../install.md#mcp).

The flat per-command references stay the source of truth for every flag and parameter: [Schemas](../commands/schemas.md), [Tables](../commands/tables.md), [Views](../commands/views.md), and [Statistics](../commands/statistics.md). This guide ties them together as a single narrative.

## What you'll build

A small star-schema-style model in one warehouse:

- a custom `sales` schema,
- a dimension-style table (`sales.customer`) created from explicit columns,
- a fact-style table (`sales.orders`) loaded from a file,
- a reporting view (`sales.vw_orders_by_month`) kept under version control as a `.sql` file,
- single-column statistics so the optimizer can plan joins and filters.

You'll then maintain and iterate on the model - clone, rename, re-cluster, clear, and finally tear it down.

### CLI vs MCP - when to use which

- **CLI** (`fdw …`) is the full surface. File-based schema inference (`--from-parquet` / `--from-csv` / `--from-json`), `tables load`, and `if-exists replace` are **CLI-only** because they need reliable local file access.
- **MCP tools** mirror the CLI for everything that doesn't depend on server-side file access, so an AI assistant can author and inspect the same objects. Where an MCP equivalent exists it's named in each step; where it doesn't (notably `tables load`), the gap is called out.

## Prerequisites

- `fabric-dw` [installed](../install.md) and [authenticated](../authentication.md) (the Azure credential chain - Azure CLI, managed identity, service principal, and more).
- A target warehouse you can write to. The examples use a workspace `MyWorkspace` and a warehouse `SalesWH`; substitute your own (both are resolvable by **name or GUID**, and both default from your [configuration](../commands/config.md), so `-w`/the item argument can be omitted once defaults are set).
- For MCP usage, the [MCP server](../install.md#mcp) registered with your assistant. Mutating and destructive tools have their own opt-in guards (see [Destructive operations](#destructive-operations-and-the-yes-flag) below).

!!! warning "Data Warehouse vs SQL Analytics Endpoint"

    A **SQL Analytics Endpoint** (the read query surface over a Lakehouse) cannot create, alter, or drop **tables**: that's a Warehouse-only capability. On a SQL Analytics Endpoint you can still create **views**, list/read/inspect everything, and manage schemas. The capability split is spelled out under [The SQL Analytics Endpoint read-only guard](#the-sql-analytics-endpoint-read-only-guard); each step below marks its **Targets**.

## Set your defaults

Store the workspace and warehouse once so you do not repeat them on every command:

```shell
fdw config set workspace MyWorkspace
fdw config set warehouse SalesWH
```

The rest of this guide assumes these defaults are set, so the examples omit `-w MyWorkspace` and drop the warehouse positional where it is optional. Any command still accepts an explicit `-w`/`--workspace` or a positional `[WAREHOUSE]`/`[ITEM]` to override them. Commands that take a trailing required argument (such as `tables clear … QUALIFIED_NAME` or `schemas create … NAME`) keep the warehouse positional so the remaining arguments stay unambiguous. See [Configuration & defaults](../commands/config.md).

## Step 1 - Create a schema

Schemas are the namespace for your tables and views. `dbo` always exists; create custom schemas to group related objects.

**Targets:** Data Warehouse / SQL Analytics Endpoint

```shell
# Create the schema (warehouse positional kept - schema NAME follows)
fdw schemas create SalesWH sales

# Confirm it exists (system schemas like sys / INFORMATION_SCHEMA are excluded; dbo is shown)
fdw schemas list
```

**MCP:** `create_schema`, `list_schemas`.

!!! note "Schema names are case-sensitive"

    Fabric warehouses use a fixed, case-sensitive default collation (`Latin1_General_100_BIN2_UTF8`), and **schema names are case-sensitive**. `Sales` and `sales` are different schemas. See [Limitations & gotchas](#fixed-case-sensitive-collation) before you settle on a naming convention - collation can't be changed after the warehouse is created.

## Step 2 - Create tables

`tables create` is a single command with mutually-exclusive source modes (validated client-side before any DDL runs). All of them are **Data Warehouse only**: tables can't be created on a SQL Analytics Endpoint.

**Targets:** Data Warehouse only / **MCP:** `create_table` (CTAS), `create_empty_table` (explicit columns)

### Empty table from explicit columns

The most direct way to scaffold a dimension table. `--column NAME:TYPE[:null|notnull]` is repeatable; columns are nullable unless you append `:notnull`.

```shell
fdw tables create \
  --name sales.customer \
  --column "customer_id:BIGINT:notnull" \
  --column "name:VARCHAR(200):notnull" \
  --column "region:VARCHAR(100)" \
  --column "signed_up:DATE"
```

**MCP** (`create_empty_table`) takes an explicit `columns` list - each entry is `{name, sql_type, nullable?}`:

```json
{
  "workspace": "MyWorkspace",
  "item": "SalesWH",
  "qualified_name": "sales.customer",
  "columns": [
    {"name": "customer_id", "sql_type": "BIGINT", "nullable": false},
    {"name": "name", "sql_type": "VARCHAR(200)", "nullable": false},
    {"name": "region", "sql_type": "VARCHAR(100)"},
    {"name": "signed_up", "sql_type": "DATE"}
  ]
}
```

### Infer a table from a data file (Parquet, CSV, or JSON)

All three file-based flags - `--from-parquet`, `--from-csv`, and `--from-json`: do the same thing conceptually: read only the schema from a data file and scaffold an **empty** table (no rows are read or inserted). How much of the file each flag reads differs by format:

- **Parquet**: reads only the Parquet footer, which encodes exact column names and types; no row-group data is touched.
- **CSV**: reads the header row plus a bounded sample of rows (controlled by `--sample-rows`) to infer types; accepts `--delimiter` and `--encoding` for non-default files.
- **JSON**: reads a bounded sample of records from a JSONL file (one JSON object per line) or a JSON file containing an array of objects.

**Shared options** (available on all three paths unless noted):

| Option | Applies to | Effect |
| --- | --- | --- |
| `--all-varchar` | CSV, JSON | Force every inferred column to `VARCHAR` |
| `--varchar-length N` | CSV, JSON, Parquet (default length for string cols) | Default string column length |
| `--sample-rows N` | CSV, JSON | Cap the number of rows sampled for inference |
| `--delimiter CHAR` | CSV only | Field delimiter (default `,`) |
| `--encoding ENC` | CSV only | File encoding (default `utf-8`) |

**Mutual exclusivity:** `--from-parquet`, `--from-csv`, `--from-json`, `--column`, and the CTAS flags `--select`/`--from-file` are all mutually exclusive - pick exactly one column source per `tables create` invocation.

```shell
# From a Parquet footer (exact types, no sampling)
fdw tables create \
  --name sales.orders \
  --from-parquet ./exports/orders.parquet

# From a CSV header with type inference
fdw tables create \
  --name staging.raw_products \
  --from-csv ./data/products.csv --varchar-length 500

# From a JSONL file (one JSON object per line)
# data/audit_log.jsonl: {"id": 1, "action": "login", "occurred_at": "2026-01-01T00:00:00"}
fdw tables create \
  --name sales.audit_log \
  --from-json ./data/audit_log.jsonl --varchar-length 500
```

Once the table is created, load data into it with `tables load --format <parquet|csv|json>`.

!!! note "File inference is CLI-only"

    The MCP `create_empty_table` tool deliberately takes an explicit `columns` list and does **not** do Parquet/CSV/JSON inference - server-side file access is unreliable in MCP deployments. Use the CLI for file-based inference, or pass the resolved columns to `create_empty_table`.

### CTAS - create a table from a query

`CREATE TABLE … AS SELECT` materialises a query result into a new table. Supply the SELECT inline with `--select`, or keep the body under version control and pass `--from-file body.sql`. The body is rejected client-side if its first non-comment keyword isn't `SELECT`.

```shell
# Inline SELECT
fdw tables create \
  --name sales.orders_2026 \
  --select "SELECT * FROM sales.orders WHERE YEAR(sale_date) = 2026"

# Body from a versioned .sql file, with CLUSTER BY
fdw tables create \
  --name sales.orders_2026 \
  --from-file ./sql/orders_2026.sql \
  --cluster-by customer_id --cluster-by sale_date
```

**MCP:** `create_table` (pass the SELECT as `select_body`, and optionally `cluster_by`).

`--cluster-by COL` is repeatable (up to 4) on **any** create mode and emits `WITH (CLUSTER BY (…))`. On the empty-DDL paths each clustering column must exist in the schema; on the CTAS path existence isn't validated, because the result columns come from the SELECT.

### Inspect what you built

**Targets:** Data Warehouse / SQL Analytics Endpoint / **MCP:** `get_table_columns`, `count_table_rows`, `list_tables`

```shell
# Column metadata (name, type, nullability, ordinal, collation, identity/computed)
fdw tables columns SalesWH sales.customer

# Row count via COUNT_BIG(*)
fdw --json tables count SalesWH sales.orders

# List tables in a schema
fdw tables list --schema sales
```

## Step 3 - Populate the tables

Loading data is its own topic. The bridge from "empty table" to "populated table" is **`tables load`**, which issues `COPY INTO` from a local file or a remote URL (and can auto-create the table from the source schema with `--create`):

```shell
fdw tables load SalesWH sales.orders --file ./data/orders.parquet
```

**Targets:** Data Warehouse only. There is **no MCP `load` tool for local files**; the MCP server exposes `load_table_from_url` / `import_table_from_url` for remote URLs only (local staging needs reliable file access). For the full loading surface - `--create`, `--if-exists`, credentials for secured external URLs, CSV options - see the [`tables load`](../commands/tables.md#tables-load) reference rather than this guide.

## Step 4 - Create views

Views give consumers a stable, named query. Unlike tables, **views are creatable on both a Data Warehouse and a SQL Analytics Endpoint**: there's no Warehouse-only guard.

**Targets:** Data Warehouse / SQL Analytics Endpoint / **MCP:** `create_view`, `update_view`, `get_view`, `get_view_columns`, `rename_view`, `list_views`

### Create from a versioned `.sql` file

Keeping the view body in a `.sql` file (rather than inlining SQL) makes it reviewable and diff-able. The body is rejected client-side if it isn't a `SELECT`.

```shell
# sql/vw_orders_by_month.sql:
# SELECT region,
#        DATETRUNC(month, sale_date) AS month,
#        COUNT_BIG(*)               AS order_count,
#        SUM(amount)                AS total_amount
# FROM sales.orders
# GROUP BY region, DATETRUNC(month, sale_date)

fdw views create \
  --name sales.vw_orders_by_month \
  --from-file ./sql/vw_orders_by_month.sql
```

You can also create inline with `--select "<SELECT>"`. **MCP:** `create_view` (pass the SELECT as `select_body`).

### Inspect and update a view

```shell
# Full definition (from sys.sql_modules) - warehouse positional kept, view name follows
fdw views get SalesWH sales.vw_orders_by_month

# Column metadata
fdw views columns SalesWH sales.vw_orders_by_month

# Redefine in place via CREATE OR ALTER VIEW (prompts for confirmation)
fdw views update SalesWH sales.vw_orders_by_month \
  --from-file ./sql/vw_orders_by_month.sql
```

**MCP:** `get_view`, `get_view_columns`, `update_view` (which runs `CREATE OR ALTER VIEW`).

!!! note "Related object kinds on a SQL Analytics Endpoint"

    Views, stored procedures, and table-valued/scalar functions are the object kinds you *can* create on a SQL Analytics Endpoint. `fabric-dw` ships read-only [`procedures`](../commands/procedures.md) (`list_procedures`, `get_procedure`) and [`functions`](../commands/functions.md) (`list_functions`, `get_function`) groups for inspecting the procs and functions that sit alongside your views.

## Step 5 - Help the optimizer

After you load data, give the query optimizer the statistics it needs. `fabric-dw` manages **single-column** statistics (a Fabric limitation - multi-column statistics aren't supported), and you must pass an explicit `--name` (Fabric requires an explicit statistic name - there is no auto-generated default).

**Targets:** Data Warehouse only (create/update/delete) / Data Warehouse / SQL Analytics Endpoint (list/show) / **MCP:** `create_statistics`, `update_statistics`, `list_statistics`, `show_statistics`

```shell
# Create a single-column statistic
fdw statistics create \
  --table sales.orders --column region --name stat_orders_region

# Refresh it after a load (warehouse positional kept - table/stat names follow)
fdw statistics update SalesWH sales.orders stat_orders_region

# List / inspect
fdw statistics list --table orders
fdw statistics show SalesWH sales.orders stat_orders_region
```

**MCP:** `create_statistics`, `update_statistics`, `list_statistics`, `show_statistics`. The mutating statistics tools are DW-only; `list_statistics` / `show_statistics` also work on a SQL Analytics Endpoint.

### Layout tuning with clustering

Beyond statistics, `CLUSTER BY` controls physical data layout. You set clustering at create time (Step 2) or change it later on an existing table:

```shell
# Re-cluster an existing table (transactional CTAS-swap; copies the whole table)
fdw --yes tables cluster-by SalesWH sales.orders \
  --cluster-by customer_id --cluster-by sale_date

# Remove clustering (omit --cluster-by entirely)
fdw --yes tables cluster-by SalesWH sales.orders

# Inspect current clustering columns
fdw tables cluster-columns SalesWH sales.orders
```

**Targets:** Data Warehouse only / **MCP:** `set_cluster_columns` (destructive - copies the full table), `get_cluster_columns`.

!!! tip "Diagnosing slow queries"

    The [Agent Skills](../skills.md) page documents `/query-optimizer` (clustering and missing/stale statistics for a single query) and `/warehouse-performance` (warehouse-wide statistics health). They pick up where this authoring workflow leaves off.

## Step 6 - Maintain & iterate

**Clone** a table (zero-copy, near-instant, independent of the source). Add `--at` for a point-in-time clone within the warehouse's data-retention window:

```shell
# Current-state clone
fdw tables clone --source sales.orders --name sales.orders_backup

# Point-in-time clone (UTC, within retention)
fdw tables clone \
  --source sales.orders --name sales.orders_may_snapshot \
  --at 2026-05-20T14:00:00
```

**Rename** a table or view (`sp_rename`; the new name must be **unqualified** - `sp_rename` itself can't move an object across schemas, it only renames in place):

```shell
fdw tables rename SalesWH sales.orders_2025 --new-name orders_archive_2025
fdw views rename SalesWH sales.vw_recent --new-name vw_revenue
```

**Move** a table or view to a different schema with `transfer` (`ALTER SCHEMA ... TRANSFER OBJECT::...`; a separate operation from rename, and the way to actually relocate an object):

```shell
fdw tables transfer SalesWH sales.orders_2025 --target-schema archive
fdw views transfer SalesWH sales.vw_recent --target-schema archive
```

The same operation is available for functions and procedures. See [Tables](../commands/tables.md#tables-transfer), [Views](../commands/views.md#views-transfer), [Functions](../commands/functions.md#functions-transfer), and [Procedures](../commands/procedures.md#procedures-transfer) for the full flag reference, including two caveats worth knowing up front: table transfer is **Data Warehouse only** (it's rejected on a SQL Analytics Endpoint, since moving a table between schemas there can break the OneLake sync), and for views, functions, and procedures the transfer does not rewrite the stored definition text, so the object's `CREATE ... AS` header may still show the old schema name afterward.

**Clear** a table (`TRUNCATE TABLE`: removes all rows, keeps the structure):

```shell
fdw --yes tables clear SalesWH staging.raw_products
```

**Health-check** a table on a **SQL Analytics Endpoint**: this is the inverse of the usual guard: `tables health-check` runs `sp_get_table_health_metrics` (Delta/Parquet layout diagnostics) and is **rejected on a Data Warehouse**:

```shell
fdw tables health-check MySqlEndpoint dbo.FactSales
```

**Tear down** when you're done:

```shell
fdw --yes views drop SalesWH sales.vw_orders_by_month
fdw --yes tables delete SalesWH sales.orders
fdw --yes schemas delete SalesWH sales --cascade
```

`schemas delete --cascade` first drops **all tables, views, functions, and stored procedures** in the schema. Without `--cascade`, the engine rejects `DROP SCHEMA` on a non-empty schema. On a SQL Analytics Endpoint, cascade can't drop tables (no `DROP TABLE` there), so a schema still holding tables won't drop - remove them from the warehouse first.

**MCP equivalents:** `clone_table`, `rename_table` / `rename_view`, `transfer_table` / `transfer_view`, `clear_table`, `get_table_health_metrics`, `drop_view`, `delete_table`, `delete_schema(..., cascade=True)`.

### Destructive operations and the `--yes` flag

These commands prompt for confirmation before they run; pass `--yes` / `-y` to skip the prompt in scripts (at your own risk):

`tables clear`, `tables delete`, `tables cluster-by`, `views drop`, `views update`, `schemas delete`, `statistics delete`, and `tables load --if-exists truncate|replace`.

The matching MCP tools are marked `destructive=True`. An assistant must satisfy the server's destructive-operations guard before they execute - see the [MCP server install](../install.md#mcp) page.

### The SQL Analytics Endpoint read-only guard

A SQL Analytics Endpoint can't mutate tables. The following raise an error when the target item is a SQL Analytics Endpoint:

- `tables create` (CTAS and empty DDL), `tables clone`, `tables rename`, `tables clear`, `tables delete`, `tables cluster-by`,
- all mutating `statistics` operations (`create` / `update` / `delete`).

Everything else works on both item kinds: every `list` / `read` / `columns` / `count` / `get` operation, `schemas create` / `delete`, and **all** `views` mutations (`create` / `update` / `rename` / `drop`). The one inverse is **`tables health-check`**, which is **SQL-Analytics-Endpoint-only** and rejected on a Data Warehouse.

`--json` output and name-or-GUID resolution apply uniformly across every command group.

## Limitations & gotchas

Fabric's T-SQL surface differs from SQL Server. The points below most often trip people up when authoring a schema model; each links the canonical Microsoft Learn guidance.

### Unsupported data types

`tables create --column` accepts any type string you give it, but the **engine** rejects persisting columns whose type isn't supported for warehouse tables/views. Notably **unsupported**: `money` / `smallmoney`, `datetime` / `smalldatetime`, `datetimeoffset`, `nchar` / `nvarchar`, `text` / `ntext`, `image`, `tinyint`, `geography` / `geometry`, `json`, `xml`, CLR UDTs, and `Vector`. Use the documented alternatives instead - `decimal`, `datetime2`, `char` / `varchar`, `varbinary`, `smallint`, and so on. See [Data types in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/data-types?WT.mc_id=MVP_310840#data-types-in-fabric-data-warehouse).

### Constraints require `NOT ENFORCED`, and can't be inline

`fabric-dw` has no constraint command. When you do need keys, reach for the [`fdw sql`](../commands/sql.md) / [`fdw queries`](../commands/queries.md) escape hatch (MCP: `execute_sql`), and respect Fabric's rules:

- `PRIMARY KEY` / `UNIQUE` are only allowed when both `NONCLUSTERED` **and** `NOT ENFORCED`; `FOREIGN KEY` only when `NOT ENFORCED`.
- **No default constraints.**
- Constraints **can't be declared inline** in `CREATE TABLE`: add them afterwards with `ALTER TABLE`.

Because the keys are unenforced, the engine trusts them but doesn't validate them - don't treat an unenforced key as a guaranteed-unique JOIN candidate. See [Table constraints](https://learn.microsoft.com/fabric/data-warehouse/table-constraints?WT.mc_id=MVP_310840) and the [performance guidelines](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#query-performance).

### T-SQL surface area

The Warehouse supports tables, views, procedures, and functions; `TRUNCATE TABLE`, `sp_rename`, CTAS, and a **limited** `ALTER TABLE` (add a nullable column, drop a column, add/drop `NOT ENFORCED` constraints) are supported. Creating, altering, or dropping **tables** and running **DML** are **not** supported on a Lakehouse SQL Analytics Endpoint - only views, table-valued functions, and stored procedures. This grounds the read-only-endpoint guard above. See [T-SQL surface area](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area?WT.mc_id=MVP_310840#t-sql-surface-area).

### Fixed, case-sensitive collation

A warehouse is created with the default `Latin1_General_100_BIN2_UTF8` collation (case-sensitive), and **collation can't be changed after creation**. This makes **schema and object names case-sensitive**: `sales.Orders` and `sales.orders` are different. Settle your naming convention up front. See [Collation in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/collation?WT.mc_id=MVP_310840).

### Naming rules

Table and schema names **can't contain `/` or `\`, and can't end with a `.`**. See [Tables in the warehouse](https://learn.microsoft.com/fabric/data-warehouse/tables?WT.mc_id=MVP_310840#tables-in-the-warehouse).

### Clone limits

`tables clone` is zero-copy and near-instant, and the clone is fully independent of its source. But: point-in-time clones (`--at`) are limited to the configured **data-retention window** (default 30 days, configurable 1–120); clones are **not supported across warehouses or workspaces, nor on a SQL Analytics Endpoint**; and there's no schema-level or warehouse-level clone. See [Clone table](https://learn.microsoft.com/fabric/data-warehouse/clone-table?WT.mc_id=MVP_310840), [clone scenarios](https://learn.microsoft.com/fabric/data-warehouse/clone-table?WT.mc_id=MVP_310840#table-clone-scenarios), and [data retention](https://learn.microsoft.com/fabric/data-warehouse/data-retention?WT.mc_id=MVP_310840).

### Table design

For the schema model itself, follow the [dimensional-modeling](https://learn.microsoft.com/fabric/data-warehouse/dimensional-modeling-overview?WT.mc_id=MVP_310840#star-schema-design) and [table design](https://learn.microsoft.com/fabric/data-warehouse/tables?WT.mc_id=MVP_310840) guidance: fact / dimension / integration table categories, surrogate keys, types matched to semantics (`date` / `datetime2`, integer types for whole numbers, the smallest viable `decimal` precision - see [data-type optimization](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#data-type-optimization)), and [statistics](https://learn.microsoft.com/fabric/data-warehouse/statistics?WT.mc_id=MVP_310840) refreshed after loads. The canonical DDL references are [CREATE TABLE](https://learn.microsoft.com/fabric/data-warehouse/create-table?WT.mc_id=MVP_310840) and [CREATE TABLE AS SELECT](https://learn.microsoft.com/sql/t-sql/statements/create-table-azure-sql-data-warehouse?view=fabric&WT.mc_id=MVP_310840).

## The same workflow via MCP

Every authoring and inspection step above maps to an MCP tool. The exceptions are file-dependent operations (local-file load and Parquet/CSV/JSON inference), which stay CLI-only.

| Workflow step | CLI | MCP tool |
| --- | --- | --- |
| Create schema | `schemas create` | `create_schema` |
| List schemas | `schemas list` | `list_schemas` |
| Delete schema (cascade) | `schemas delete --cascade` | `delete_schema(..., cascade=True)` |
| Create table (CTAS) | `tables create --select` / `--from-file` | `create_table` |
| Create empty table | `tables create --column` | `create_empty_table` |
| Create empty table (file inference) | `tables create --from-parquet` / `--from-csv` / `--from-json` | *(CLI only)* |
| List tables | `tables list` | `list_tables` |
| Read rows | `tables read` | `read_table` |
| Column metadata | `tables columns` | `get_table_columns` |
| Row count | `tables count` | `count_table_rows` |
| Clone table | `tables clone` | `clone_table` |
| Rename table | `tables rename` | `rename_table` |
| Transfer table to another schema | `tables transfer` | `transfer_table` |
| Set / remove clustering | `tables cluster-by` | `set_cluster_columns` |
| Clustering columns | `tables cluster-columns` | `get_cluster_columns` |
| Truncate table | `tables clear` | `clear_table` |
| Drop table | `tables delete` | `delete_table` |
| Health check (SQL Endpoint) | `tables health-check` | `get_table_health_metrics` |
| Load from local file | `tables load --file` | *(CLI only)* |
| Load from URL | `tables load --url` | `load_table_from_url` / `import_table_from_url` |
| Create view | `views create` | `create_view` |
| Update view | `views update` | `update_view` |
| Get view definition | `views get` | `get_view` |
| View column metadata | `views columns` | `get_view_columns` |
| List views | `views list` | `list_views` |
| Read view rows | `views read` | `read_view` |
| View row count | `views count` | `count_view_rows` |
| Rename view | `views rename` | `rename_view` |
| Transfer view to another schema | `views transfer` | `transfer_view` |
| Drop view | `views drop` | `drop_view` |
| Create statistic | `statistics create` | `create_statistics` |
| Update statistic | `statistics update` | `update_statistics` |
| Delete statistic | `statistics delete` | `delete_statistics` |
| List statistics | `statistics list` | `list_statistics` |
| Show statistic | `statistics show` | `show_statistics` |
| Run arbitrary SQL (constraints, etc.) | `sql` / `queries` | `execute_sql` |

For the MCP server setup, destructive-operation guards, and per-tool parameters, see the [MCP server install](../install.md#mcp) page and the per-domain command references.
