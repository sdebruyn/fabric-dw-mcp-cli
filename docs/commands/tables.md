---
title: Tables
---

# Tables

Manage SQL tables on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints. Commands and tools cover listing, counting, reading, creating (including CTAS, empty DDL from schema inference, and zero-copy clone), deleting, clearing, renaming, and loading data via `COPY INTO` from local files or remote URLs.

**Targets:** Data Warehouse / SQL Analytics Endpoint

## CLI

### tables clear

**Targets:** Data Warehouse only

Truncate a table (delete all rows, keep structure). You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] tables clear [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fdw -w MyWorkspace --yes tables clear SalesWH dbo.staging_load
```

### tables columns

**Targets:** Data Warehouse / SQL Analytics Endpoint

List the columns of a table, including name, formatted data type, nullability, ordinal position, collation, identity, and computed flags.

**Synopsis**

```
fdw [-w WORKSPACE] tables columns [WAREHOUSE] QUALIFIED_NAME
```

`QUALIFIED_NAME` must be a dot-separated `schema.table_name` string, e.g. `dbo.Sales`.

**Example**

```shell
fdw -w MyWorkspace tables columns SalesWH dbo.Sales
```

```
 ordinal  name    data_type     nullable  is_identity  is_computed  collation_name
 -------  ------  ------------  --------  -----------  -----------  ----------------------------
 1        id      INT           False     True         False
 2        amount  DECIMAL(18,2) True      False        False
 3        label   NVARCHAR(100) True      False        False        Latin1_General_CI_AS
```

### tables clear

**Targets:** Data Warehouse only

Truncate a table (delete all rows, keep structure). You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] tables clear [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fdw -w MyWorkspace --yes tables clear SalesWH dbo.staging_load
```

### tables cluster-by

**Targets:** Data Warehouse only

Change (or remove) the data-clustering columns of an existing table via a transactional CTAS-swap.

**Performance note:** This operation copies the entire table. Runtime is proportional to table size.

!!! warning "Dependent views and stored procedures"

    Dependent views and stored procedures that reference this table by name are **NOT** automatically updated by `sp_rename` and may need refreshing after the swap.

The operation is atomic: all three steps run inside a single transaction. Any failure rolls back automatically - no orphan temp table is left behind.

**Synopsis**

```
fdw [-w WORKSPACE] tables cluster-by [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

| Option | Description |
| --- | --- |
| `--cluster-by COL` | Column name for `CLUSTER BY` (repeatable, up to 4). Omit entirely to remove clustering. |
| `--yes` | Skip the confirmation prompt. |

**Examples**

```shell
# Set new clustering columns
fdw -w MyWorkspace --yes tables cluster-by SalesWH dbo.orders \
  --cluster-by CustomerID --cluster-by SaleDate

# Remove clustering entirely
fdw -w MyWorkspace --yes tables cluster-by SalesWH dbo.orders
```

### tables cluster-columns

**Targets:** Data Warehouse only

List the data-clustering columns of a table, ordered by clustering ordinal. Returns an empty table when no clustering is defined (exit 0).

**Synopsis**

```
fdw [-w WORKSPACE] tables cluster-columns [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fdw -w MyWorkspace --json tables cluster-columns SalesWH dbo.orders
```

```json
[
  {"column_name": "city", "clustering_ordinal": 1},
  {"column_name": "country", "clustering_ordinal": 2}
]
```

### tables clone

**Targets:** Data Warehouse only

Create a zero-copy clone of a table using `CREATE TABLE … AS CLONE OF`. Pass `--at` to clone from a point in time within the warehouse data-retention window.

**Synopsis**

```
fdw [-w WORKSPACE] tables clone [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `--source SCHEMA.TABLE` | **Required.** Qualified source table to clone. |
| `--name SCHEMA.TABLE` | **Required.** Qualified name for the new clone. |
| `--at ISO8601` | Optional UTC timestamp for a historical clone (e.g. `2024-05-20T14:00:00`). Must be within the data-retention window. |

**Example**

```shell
# Clone to the current state
fdw -w MyWorkspace tables clone SalesWH \
  --source dbo.orders \
  --name dbo.orders_backup

# Point-in-time clone
fdw -w MyWorkspace tables clone SalesWH \
  --source dbo.orders \
  --name dbo.orders_may_snapshot \
  --at 2024-05-20T14:00:00
```

### tables count

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return the total row count of a table using `SELECT COUNT_BIG(*)`.

**Synopsis**

```
fdw [-w WORKSPACE] tables count [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fdw -w MyWorkspace --json tables count SalesWH dbo.orders
```

```json
{"schema": "dbo", "name": "orders", "row_count": 999999}
```

### tables create

**Targets:** Data Warehouse only

Create a new table on a Fabric Data Warehouse. Two modes are available:

- **CTAS** (`CREATE TABLE … AS SELECT`): supply `--select` or `--from-file`. The body must start with `SELECT` (leading block/line comments are allowed).
- **Empty DDL** (`CREATE TABLE … (col TYPE, …)`): supply exactly one of `--from-parquet`, `--from-csv`, `--from-json`, or one-or-more `--column`. The schema is derived (Parquet/CSV/JSON inference) or listed explicitly; no data is ever read or inserted - this scaffolds the table structure only. To load data afterwards, use [`tables load`](#tables-load).

**Synopsis**

```
fdw [-w WORKSPACE] tables create [OPTIONS] [WAREHOUSE]
```

#### CTAS options

| Option | Description |
| --- | --- |
| `--name SCHEMA.TABLE` | **Required.** Qualified table name. |
| `--select TEXT` | Inline SELECT statement for CTAS. |
| `--from-file PATH` | Path to a `.sql` file containing the SELECT body (UTF-8/UTF-8-sig). |
| `--cluster-by COL` | Column name for `CLUSTER BY` (repeatable, up to 4). Column existence is not validated on the CTAS path because result columns come from the SELECT. |

Exactly one of `--select` or `--from-file` must be provided for the CTAS path. Cannot be combined with empty-DDL options.

#### Empty-DDL options

| Option | Description |
| --- | --- |
| `--name SCHEMA.TABLE` | **Required.** Qualified table name. |
| `--from-parquet PATH` | Derive schema from a Parquet file (reads footer only - no data loaded). |
| `--from-csv PATH` | Derive schema from a CSV header + bounded sample (no data loaded). |
| `--from-json PATH` | Derive schema from JSON **data**: a JSONL file or a JSON file containing an array of objects; types are inferred from a bounded sample (no data loaded). JSONL streams; a JSON array is fully loaded into memory - for very large data prefer JSONL. |
| `--column NAME:TYPE[:null\|notnull]` | Inline column definition (repeatable). |
| `--cluster-by COL` | Column name for `CLUSTER BY` (repeatable, up to 4). Each name must appear in the table schema. |
| `--all-varchar` | (CSV/JSON) Force all columns to `VARCHAR`; skip type inference. |
| `--varchar-length N` | (CSV/JSON) Default VARCHAR/VARBINARY length for string/binary columns (1–8000, default `8000`). |
| `--delimiter CHAR` | (CSV) Field delimiter (default `,`). |
| `--encoding ENC` | (CSV) File encoding (default `utf-8-sig`). |
| `--sample-rows N` | (CSV/JSON) Rows/records to sample for type inference (1–100 000, default `1000`). |

`--from-parquet`, `--from-csv`, `--from-json`, and `--column` are mutually exclusive with each other and with the CTAS path. For the explicit path at least one `--column` must be provided.

**Arrow → T-SQL type mapping (Parquet / CSV / JSON inference)**

| Arrow type | T-SQL type |
| --- | --- |
| `int8`, `int16`, `uint8` | `SMALLINT` |
| `int32`, `uint16` | `INT` |
| `int64`, `uint32`, `uint64` | `BIGINT` |
| `float16`, `float32` | `REAL` |
| `float64` | `FLOAT` |
| `bool` | `BIT` |
| `decimal128(p,s)` | `DECIMAL(p,s)` |
| `date32`, `date64` | `DATE` |
| `time*` | `TIME(7)` |
| `timestamp*` | `DATETIME2(7)` |
| `string`, `large_string` | `VARCHAR(n)` |
| `binary`, `large_binary` | `VARBINARY(n)` |
| nested / list / struct | CSV/JSON: falls back to `VARCHAR(n)` with a warning (or use `--all-varchar`). Parquet: **Error**: define the column explicitly with `--column` instead. |

**Examples**

```shell
# CTAS
fdw -w MyWorkspace tables create SalesWH \
  --name dbo.orders_2026 \
  --select "SELECT * FROM dbo.orders WHERE YEAR(sale_date) = 2026"

# Empty table from Parquet schema
fdw -w MyWorkspace tables create SalesWH \
  --name dbo.sales_empty \
  --from-parquet ./exports/sales.parquet

# Empty table from CSV header (type inference)
fdw -w MyWorkspace tables create SalesWH \
  --name staging.raw_products \
  --from-csv ./data/products.csv --varchar-length 500

# Empty table with explicit inline columns
fdw -w MyWorkspace tables create SalesWH \
  --name dbo.events \
  --column "event_id:BIGINT:notnull" \
  --column "event_type:VARCHAR(100)" \
  --column "occurred_at:DATETIME2(7)"

# Empty table from JSON data - JSONL (schema inferred from the data)
fdw -w MyWorkspace tables create SalesWH \
  --name staging.events \
  --from-json ./data/events.jsonl

# Empty table from JSON data - a JSON array of objects
fdw -w MyWorkspace tables create SalesWH \
  --name dbo.audit_log \
  --from-json ./data/audit_log.json --varchar-length 500

# CTAS with CLUSTER BY (column existence not validated on CTAS path)
fdw -w MyWorkspace tables create SalesWH \
  --name dbo.orders_2026 \
  --select "SELECT CustomerID, SaleDate, Amount FROM dbo.orders WHERE YEAR(SaleDate) = 2026" \
  --cluster-by CustomerID --cluster-by SaleDate

# Empty table with explicit columns and CLUSTER BY
fdw -w MyWorkspace tables create SalesWH \
  --name dbo.events \
  --column "CustomerID:INT:notnull" \
  --column "SaleDate:DATE:notnull" \
  --column "Amount:DECIMAL(18,2)" \
  --cluster-by CustomerID --cluster-by SaleDate
```

### tables delete

**Targets:** Data Warehouse only

Drop a table. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] tables delete [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fdw -w MyWorkspace --yes tables delete SalesWH dbo.orders_2026
```

### tables health-check

**Targets:** SQL Analytics Endpoint

Run `sp_get_table_health_metrics` against a single table to surface Delta/Parquet layout issues (small files, fragmentation, excessive deletes/updates, delayed checkpoints) and decide whether maintenance is needed.

The proc is Generally Available (announced at Build 2026) but Microsoft Learn has no dedicated reference page yet. Output columns are passed through verbatim - the exact column names and types are determined by the proc and may change across Fabric releases.

**Synopsis**

```
fdw [-w WORKSPACE] tables health-check [ENDPOINT] QUALIFIED_NAME
```

**Example**

```shell
fdw -w MyWorkspace tables health-check MySqlEndpoint dbo.FactSales
fdw -w MyWorkspace --json tables health-check MySqlEndpoint dbo.FactSales
```

### tables list

**Targets:** Data Warehouse / SQL Analytics Endpoint

List all tables on a warehouse or SQL Analytics Endpoint. Pass `--schema` to filter to a single schema.

**Synopsis**

```
fdw [-w WORKSPACE] tables list [OPTIONS] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--schema TEXT` | Only list tables in this schema. |

**Example**

```shell
fdw -w MyWorkspace tables list SalesWH --schema dbo
```

```
 schema_name  name      created               modified
 ------------ --------- --------------------- ---------------------
 dbo          customers 2026-01-10T08:00:00Z  2026-06-01T12:00:00Z
 dbo          orders    2026-02-01T09:00:00Z  2026-05-15T14:00:00Z
```

### tables load

**Targets:** Data Warehouse only

Load data into a warehouse table via `COPY INTO` from either a local file or a remote URL.

**Local file path** (`--file`): the file is staged to a temporary Lakehouse in OneLake (chunked DFS upload), loaded into the target table via `COPY INTO`, and the staging Lakehouse is automatically deleted in a `finally` block regardless of success or failure. JSON files are converted client-side to Parquet (requires `pyarrow`) before staging.

**Remote URL** (`--url`): `COPY INTO` is issued directly from the given URL. For OneLake or same-tenant URLs no credential is needed. For secured external URLs (Azure Blob Storage, ADLS Gen2) supply `--credential-type` and `--secret`/`--identity` as appropriate.

**Auto-create (create-and-load)**: Pass `--create` to auto-create the target table from the source schema before loading (local files only; requires `pyarrow`). The schema is inferred from the source:

- **Parquet**: exact types are read from the Parquet footer (no row data is read).
- **CSV**: the header row and up to `--sample-rows` rows are read for type inference. Use `--all-varchar` to skip inference and force every column to `VARCHAR`.
- **JSON**: the file is converted to Parquet internally (as required for staging); the schema is read from the resulting Parquet footer.

Use `--if-exists` to control behaviour when the table already exists:

| `--if-exists` value | Table exists | Table absent |
| --- | --- | --- |
| `fail` (default with `--create`) | Error - table already exists | Create + load |
| `append` | Skip create, `COPY INTO` existing | Create + load |
| `truncate` ⚠️ **DESTRUCTIVE** | `TRUNCATE` existing table, then load | Create + load |
| `replace` ⚠️ **DESTRUCTIVE** | `DROP` + recreate from inferred schema, then load | Create + load |

`truncate` and `replace` are permanently destructive and require confirmation (or `--yes` / `-y`).

Use `--cleanup-on-failure` to drop the table if WE created it in this call and the subsequent `COPY INTO` fails. A pre-existing table is never dropped by this flag.

!!! warning "Not atomic"

    `CREATE TABLE` and `COPY INTO` are separate statements. A failure between them may leave an empty table. Use `--cleanup-on-failure` to auto-drop in that case.

**Synopsis**

```
fdw [-w WORKSPACE] tables load [OPTIONS] [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the dot-separated `schema.table_name` of the destination table.

| Option | Default | Description |
| --- | --- | --- |
| `--file PATH` | - | Local file path (CSV, Parquet, or JSON). |
| `--url TEXT` | - | Remote URL (OneLake DFS or external Azure Blob). |
| `--format [csv\|parquet\|json]` | auto-detect | File format. For `--url`, only `csv` and `parquet` are supported. |
| `--header/--no-header` | `--header` | Whether the CSV file contains a header row. |
| `--delimiter TEXT` | `,` | CSV column delimiter. |
| `--encoding TEXT` | - | CSV encoding (e.g. `UTF8`, `UTF8BOM`). |
| `--field-quote TEXT` | - | CSV field-quote character. |
| `--row-terminator TEXT` | - | CSV row terminator (e.g. `\n`, `\r\n`). |
| `--credential-type [none\|sas\|managed-identity\|service-principal\|account-key]` | `none` | Credential type for secured external URLs. |
| `--secret TEXT` | - | Credential secret (SAS token / client secret / account key). Never echoed. |
| `--identity TEXT` | - | Identity for `managed-identity` or `service-principal`. |
| `--staging-lakehouse TEXT` | auto-generated | Name for the temporary staging Lakehouse (local path only). |
| `--keep-staging` | off | Keep the staging Lakehouse after load (for debugging). |
| `--max-errors INT` | - | Maximum errors before aborting. |
| `--rejected-row-location TEXT` | - | URL to write rejected rows to. |
| `--create` | off | Auto-create the target table from the source schema (local files only). |
| `--if-exists [fail\|append\|truncate\|replace]` | `fail` (with `--create`) | What to do when the target table already exists. `truncate` and `replace` are destructive and require confirmation. |
| `--all-varchar` | off | (CSV, `--create`) Force all columns to `VARCHAR`; skip type inference. |
| `--varchar-length INT` | `8000` | (`--create`) Default VARCHAR/VARBINARY length for inferred columns. |
| `--sample-rows INT` | `1000` | (CSV, `--create`) Maximum rows to sample for type inference. |
| `--cleanup-on-failure` | off | Drop the table if WE created it and the load fails. Never drops a pre-existing table. |
| `--cluster-by COL` | - | (`--create`) Column name for `CLUSTER BY` (repeatable, up to 4). Each name must appear in the inferred schema. |

**Examples**

```shell
# Load a local CSV into an existing table (header row present)
fdw -w MyWorkspace tables load SalesWH dbo.sales --file data.csv

# Load a local Parquet file into an existing table
fdw -w MyWorkspace tables load SalesWH dbo.events --file events.parquet

# Load a local JSON file (converts to Parquet internally; requires pyarrow)
fdw -w MyWorkspace tables load SalesWH dbo.products --file products.json

# Auto-create the table from a Parquet schema, then load
fdw -w MyWorkspace tables load SalesWH dbo.sales --file data.parquet --create

# Auto-create from CSV, force all columns to VARCHAR
fdw -w MyWorkspace tables load SalesWH dbo.raw --file raw.csv --create --all-varchar

# Replace the existing table (drop + recreate schema + load), skip confirmation
fdw -w MyWorkspace tables load SalesWH dbo.sales --file data.parquet --create \
    --if-exists replace -y

# Auto-create; drop the table if the load fails (cleanup_on_failure)
fdw -w MyWorkspace tables load SalesWH dbo.sales --file data.parquet --create \
    --cleanup-on-failure

# Auto-create with CLUSTER BY (columns must exist in the inferred schema)
fdw -w MyWorkspace tables load SalesWH dbo.sales --file data.parquet --create \
    --cluster-by SaleDate --cluster-by CustomerID

# Load from a remote OneLake URL (no credential needed)
fdw -w MyWorkspace tables load SalesWH dbo.orders \
    --url "https://onelake.dfs.fabric.microsoft.com/ws/lh.Lakehouse/Files/orders.parquet" \
    --format parquet

# Load from Azure Blob with SAS token
fdw -w MyWorkspace tables load SalesWH dbo.events \
    --url "https://myaccount.blob.core.windows.net/data/events.csv" \
    --format csv --credential-type sas --secret "?sv=2021&..."
```

### tables read

**Targets:** Data Warehouse / SQL Analytics Endpoint

Read up to `--count` rows from a table and emit them as JSON (default), CSV, or Parquet.

CSV and Parquet formats require `--output`. JSON is emitted to stdout by default.

**Synopsis**

```
fdw [-w WORKSPACE] tables read [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

| Option | Description | Default |
| --- | --- | --- |
| `--count N` | Maximum rows to return. | `10` |
| `--format {json\|csv\|parquet}` | Output format. | `json` |
| `--output PATH` | Write to file instead of stdout. Required for `csv` and `parquet`. | |

**Example**

```shell
fdw -w MyWorkspace tables read SalesWH dbo.orders --count 5
```

```json
[
  {"id": 1, "amount": 99.99, "customer_id": 42},
  ...
]
```

### tables rename

**Targets:** Data Warehouse only

Rename a table via `sp_rename`. The new name must be an unqualified (bare) identifier - `sp_rename` cannot move a table to a different schema.

**Synopsis**

```
fdw [-w WORKSPACE] tables rename [OPTIONS] [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the current dot-separated `schema.table_name`.

| Option | Description |
| --- | --- |
| `--new-name TEXT` | **Required.** New bare table name (no schema prefix). |

**Example**

```shell
fdw -w MyWorkspace tables rename SalesWH dbo.orders_2025 --new-name orders_archive_2025
```

## MCP tools

### clear_table

**Targets:** Data Warehouse only

Truncate a SQL table (remove all rows, preserve structure).

**CAUTION**: This is a destructive, irreversible operation. All rows will be permanently deleted. The table structure is preserved. Confirm with the user before calling.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated table name, e.g. `dbo.sales`.

**Returns:** `{ "truncated": true }`: confirmation.

### clone_table

**Targets:** Data Warehouse only

Create a zero-copy clone of a table using `CREATE TABLE … AS CLONE OF …`. Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse name or GUID.
- `source` (`str`): qualified source table name, e.g. `dbo.sales`.
- `new_table` (`str`): qualified name for the new cloned table, e.g. `dbo.sales_clone`.
- `at` (`str | null`, optional): ISO-8601 UTC timestamp for a point-in-time clone (e.g. `2024-05-20T14:00:00`). Must be within the data-retention window. When omitted, the clone reflects the current state of the source table.

**Returns:** `Table`: the newly-created cloned table record.

### get_table_columns

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return column metadata for a SQL table via `sys.columns`. Works on both Fabric Data Warehouses and SQL Analytics Endpoints.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated table name, e.g. `dbo.Sales`.

**Returns:** `list[dict]`: one dict per column, each containing:

- `ordinal` (`int`): 1-based column position (`column_id`).
- `name` (`str`): column name.
- `data_type` (`str`): formatted T-SQL type string, e.g. `INT`, `VARCHAR(50)`, `NVARCHAR(MAX)`, `DECIMAL(18,2)`, `DATETIME2(7)`.
- `nullable` (`bool`): whether the column allows `NULL`.
- `collation_name` (`str | null`): collation name, if applicable.
- `is_identity` (`bool`): whether the column is an identity column.
- `is_computed` (`bool`): whether the column is a computed column.

Results are ordered by ordinal position. Raises a `ToolError` if the table does not exist.

### count_table_rows

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return the total row count of a table via `SELECT COUNT_BIG(*)`.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated table name, e.g. `dbo.sales`.

**Returns:** `{ "schema": str, "name": str, "row_count": int }`: the schema name, table name, and total row count.

### create_empty_table

**Targets:** Data Warehouse only

Create an empty SQL table from an explicit column specification (DDL only - no data is ever read or inserted). This scaffolds the table structure so that data can be loaded separately.

Server-side file access is unreliable in MCP deployments, so CSV/Parquet schema inference is not available via this tool. Use `fdw tables create --from-parquet` or `--from-csv` (CLI) for file-based schema inference.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse name or GUID. SQL Analytics Endpoints are rejected.
- `qualified_name` (`str`): dot-separated table name, e.g. `dbo.sales`.
- `columns` (`list[object]`): non-empty list of column definitions, each an object with:
  - `name` (`str`): column identifier (must be a valid SQL identifier).
  - `sql_type` (`str`): Fabric-DW-supported T-SQL type, e.g. `"INT"`, `"VARCHAR(255)"`, `"DECIMAL(18,2)"`.
  - `nullable` (`bool`, optional, default `true`): whether the column allows `NULL`.
- `cluster_by` (`list[str]`, optional): column names for the `CLUSTER BY` clause (up to 4). Each name must appear in `columns`.

**Returns:** `Table`: the newly-created table record.

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

### create_table

**Targets:** Data Warehouse only

Create a new SQL table via CTAS (`CREATE TABLE … AS SELECT`).

**CAUTION**: `select_body` is executed verbatim as DDL. Confirm intent before calling. The first non-comment keyword must be `SELECT`.

When `cluster_by` is supplied the DDL becomes `CREATE TABLE … WITH (CLUSTER BY ([c1], [c2])) AS SELECT …`. Column existence is not validated on the CTAS path because result columns come from the SELECT and are not known ahead of time.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated table name, e.g. `dbo.sales`.
- `select_body` (`str`): the SELECT statement for the CTAS source.
- `cluster_by` (`list[str]`, optional): column names for the `CLUSTER BY` clause (up to 4). Column existence is not validated on the CTAS path.

**Returns:** `Table`: the newly-created table record.

### delete_table

**Targets:** Data Warehouse only

Drop a SQL table.

**CAUTION**: This is a destructive, irreversible operation. All data will be permanently deleted. Confirm with the user before calling.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated table name, e.g. `dbo.sales`.

**Returns:** `{ "dropped": true }`: confirmation.

### get_cluster_columns

**Targets:** Data Warehouse only

Return the data-clustering columns of a table, ordered by clustering ordinal. Returns an empty list when no clustering is defined (not an error).

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse name or GUID. SQL Analytics Endpoints are rejected.
- `qualified_name` (`str`): dot-separated table name, e.g. `dbo.sales`.

**Returns:** `list[{ "column_name": str, "clustering_ordinal": int }]`: ordered by ascending `clustering_ordinal`.

### get_table_health_metrics

**Targets:** SQL Analytics Endpoint

Return health metrics for a table via `sp_get_table_health_metrics`. Only supported on SQL Analytics Endpoints (not Data Warehouses).

The proc surfaces Delta/Parquet layout issues such as small files, fragmentation, excessive deletes/updates, and delayed checkpoints - useful for deciding whether table maintenance is needed.

The proc is Generally Available (announced at Build 2026) but its output column schema is not yet documented by Microsoft. Columns and rows are passed through verbatim.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): SQL Analytics Endpoint name or GUID. Data Warehouses are rejected with a `ToolError`.
- `qualified_name` (`str`): dot-separated table name, e.g. `dbo.FactSales`.

**Returns:** `{ "columns": list[str], "rows": list[list] }`: column names and rows passed through verbatim from the proc.

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
| `"fail"` (default) | Error - table already exists | Load normally |
| `"append"` | Load into existing table | Load normally |
| `"truncate"` ⚠️ | `TRUNCATE` existing table, then load | Load normally |
| `"replace"` ⚠️ | Not supported for remote URLs - use CLI | Load normally |

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse name or GUID. SQL Analytics Endpoints are rejected.
- `qualified_name` (`str`): dot-separated qualified table name, e.g. `dbo.sales`.
- `url` (`str`): source URL (OneLake DFS URL or external Azure Blob URL).
- `file_type` (`"CSV" | "PARQUET"`): file type to load. JSON is not supported for remote URLs.
- `if_exists` (`"fail" | "append" | "truncate" | "replace"`, default `"fail"`): what to do when the target table already exists.
- `credential_type` (`"none" | "sas" | "managed-identity" | "service-principal" | "account-key"`, default `"none"`): credential type for secured external URLs.
- `secret` (`str | null`, optional): credential secret (SAS token, client secret, or account key). Never logged.
- `identity` (`str | null`, optional): identity for `managed-identity` or `service-principal`.
- `delimiter` (`str | null`, optional): CSV column delimiter (e.g. `,`, `\t`).
- `has_header` (`bool`, default `true`): when `true`, the first CSV row is a header and is skipped.
- `encoding` (`str | null`, optional): CSV encoding (e.g. `UTF8`, `UTF8BOM`).
- `field_quote` (`str | null`, optional): CSV field-quote character.
- `row_terminator` (`str | null`, optional): CSV row terminator (e.g. `\n`, `\r\n`).
- `max_errors` (`int | null`, optional): maximum errors before aborting.
- `rejected_row_location` (`str | null`, optional): URL to write rejected rows to.

**Returns:** `CopyIntoResult`: `{ "rows_loaded": int, "rows_rejected": int, "target": "schema.table" }`.

### list_tables

**Targets:** Data Warehouse / SQL Analytics Endpoint

List SQL tables on a warehouse or SQL Analytics Endpoint.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `schema` (`str | null`, optional): when provided, only tables in this schema are returned.

**Returns:** `list[Table]`: each with `schema_name`, `name`, `qualified_name`, `created`, `modified`.

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

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse name or GUID. SQL Analytics Endpoints are rejected.
- `qualified_name` (`str`): dot-separated qualified table name, e.g. `dbo.sales`.
- `url` (`str`): source URL (OneLake DFS URL or external Azure Blob URL).
- `file_type` (`"CSV" | "PARQUET"`): file type to load.
- `credential_type` (`"none" | "sas" | "managed-identity" | "service-principal" | "account-key"`, default `"none"`): credential type for secured external URLs.
- `secret` (`str | null`, optional): credential secret (SAS token, client secret, or account key). Never logged.
- `identity` (`str | null`, optional): identity for `managed-identity` or `service-principal`.
- `delimiter` (`str | null`, optional): CSV column delimiter (e.g. `,`, `\t`).
- `has_header` (`bool`, default `true`): when `true`, the first CSV row is a header and is skipped.
- `encoding` (`str | null`, optional): CSV encoding (e.g. `UTF8`, `UTF8BOM`).
- `field_quote` (`str | null`, optional): CSV field-quote character.
- `row_terminator` (`str | null`, optional): CSV row terminator (e.g. `\n`, `\r\n`).
- `max_errors` (`int | null`, optional): maximum errors before aborting.
- `rejected_row_location` (`str | null`, optional): URL to write rejected rows to.

**Returns:** `CopyIntoResult`: `{ "rows_loaded": int, "rows_rejected": int, "target": "schema.table" }`.

### read_table

**Targets:** Data Warehouse / SQL Analytics Endpoint

Return up to `count` rows from a table as JSON-serialisable columns and rows.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated table name, e.g. `dbo.sales`.
- `count` (`int`, default `10`): maximum rows to return.

**Returns:** `{ "columns": list[str], "rows": list[list] }`: column names and row arrays.

### rename_table

**Targets:** Data Warehouse only

Rename a SQL table via `sp_rename`. Only supported on Fabric Data Warehouses (SQL Analytics Endpoints are rejected). The new name must be a bare (unqualified) identifier - `sp_rename` cannot move a table to a different schema.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse name or GUID.
- `qualified_name` (`str`): current dot-separated qualified table name, e.g. `dbo.sales`.
- `new_name` (`str`): new bare table name (no schema prefix), e.g. `sales_v2`.

**Returns:** `Table`: the updated table record.

### set_cluster_columns

**Targets:** Data Warehouse only

Change (or remove) the data-clustering columns of an existing table via a transactional CTAS-swap. Requires `FABRIC_MCP_ALLOW_DESTRUCTIVE=1`.

**Performance note:** This operation copies the entire table. Runtime is proportional to table size.

**CAUTION:** Dependent views and stored procedures that reference this table by name are **NOT** automatically updated by `sp_rename` and may need refreshing after the swap.

The operation is atomic: CTAS + DROP + sp_rename all run in one transaction. Any failure rolls back automatically - no orphan temp table is left behind.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse name or GUID. SQL Analytics Endpoints are rejected.
- `qualified_name` (`str`): dot-separated qualified table name, e.g. `dbo.sales`.
- `cluster_by` (`list[str] | null`, optional): new column names for the `CLUSTER BY` clause (up to 4). Pass `null` or an empty list to remove clustering (rebuilds table without `CLUSTER BY`).

**Returns:** `Table`: the re-clustered table record.
