---
title: CLI reference
---

# Command-line reference

`fabric-dw` is a command-line interface for administering Microsoft Fabric Data Warehouses and SQL Analytics Endpoints. The general invocation pattern is:

```
fabric-dw [GLOBAL OPTIONS] <noun> <verb> [ARGS] [OPTIONS]
```

**Name-or-GUID arguments** — wherever a synopsis shows `WORKSPACE`, `WAREHOUSE`, `ENDPOINT`, or `SNAPSHOT`, the value can be either the display name or the item's GUID. The CLI resolves names to GUIDs automatically and caches the mapping locally (see [`fabric-dw cache`](#fabric-dw-cache)).

**Positional arguments with defaults** — positional arguments shown in `[brackets]` may be omitted when a default has been set with [`fabric-dw config set`](#defaults-fabric-dw-config). See [Defaults — fabric-dw config](#defaults-fabric-dw-config) for details.

---

## Global options

These options can be placed immediately after `fabric-dw`, before the noun.

| Flag | Description | Default |
| --- | --- | --- |
| `--json` | Emit machine-readable JSON instead of Rich tables. | off |
| `--auth {default\|sp\|interactive}` | Override `FABRIC_AUTH` for this invocation. | `default` |
| `-y` / `--yes` | Skip confirmation prompts on destructive commands. | off |
| `-v` / `--verbose` | Enable DEBUG-level logging. | INFO |

The `--auth` flag and the `FABRIC_AUTH` environment variable accept the same three values. See [Authentication](install.md#authentication) for the full credential chain.

---

## Defaults — fabric-dw config

`fabric-dw` can store a default workspace and/or warehouse so you do not have to repeat them on every invocation.

```shell
fabric-dw config set workspace MyWorkspace
fabric-dw config set warehouse MyWarehouse
```

Once set, any positional `WORKSPACE` or `WAREHOUSE` argument shown in `[brackets]` in the synopsis below can be omitted. The stored value is resolved in the same way as an explicit argument (name or GUID).

`--all-workspaces` (where available) and a configured default workspace are **mutually exclusive** — passing `-A` always ignores the stored default.

Resolution order for the workspace:

1. Explicit positional argument on the command line.
2. `FABRIC_DW_DEFAULT_WORKSPACE` environment variable.
3. Value stored by `fabric-dw config set workspace`.

The warehouse follows the same order using `FABRIC_DW_DEFAULT_WAREHOUSE`.

For authentication configuration, see [Authentication](install.md#authentication).

Manage your defaults with:

| Command | Effect |
| --- | --- |
| `fabric-dw config show` | Print the current defaults. |
| `fabric-dw config set workspace VALUE` | Set the default workspace (name or GUID). |
| `fabric-dw config set warehouse VALUE` | Set the default warehouse (name or GUID). |
| `fabric-dw config unset workspace` | Clear the default workspace. |
| `fabric-dw config unset warehouse` | Clear the default warehouse. |
| `fabric-dw config clear` | Wipe **all** configuration defaults. |

```shell
# Example — show current config
fabric-dw config show
```

```
workspace  MyWorkspace
warehouse  MyWarehouse
```

---

## fabric-dw workspaces

Manage Microsoft Fabric workspaces.

### workspaces list

List all workspaces the authenticated principal has access to.

**Synopsis**

```
fabric-dw workspaces list
```

**Example**

```shell
fabric-dw workspaces list
```

```
 id                                    displayName       capacityId
 ------------------------------------ ---------------- ------------------------------------
 3f2a1c5d-...                          MyWorkspace       ab12cd34-...
```

---

### workspaces get

Get details for a workspace, including its default Data Warehouse collation.

**Synopsis**

```
fabric-dw workspaces get [WORKSPACE]
```

**Example**

```shell
fabric-dw workspaces get MyWorkspace
```

```
id                                3f2a1c5d-...
displayName                       MyWorkspace
capacityId                        ab12cd34-...
defaultDataWarehouseCollation     Latin1_General_100_CI_AS_KS_WS_SC_UTF8
```

---

### workspaces set-collation

Set the default Data Warehouse collation for a workspace. `COLLATION` must be one of the supported Fabric collations.

**Synopsis**

```
fabric-dw workspaces set-collation [WORKSPACE] COLLATION
```

**Example**

```shell
fabric-dw workspaces set-collation MyWorkspace Latin1_General_100_CI_AS_KS_WS_SC_UTF8
```

---

## fabric-dw warehouses

Manage Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

### warehouses list

List all warehouses in a workspace. Pass `-A` / `--all-workspaces` to aggregate across every visible workspace. `WORKSPACE` and `--all-workspaces` are mutually exclusive.

**Synopsis**

```
fabric-dw warehouses list [-A] [WORKSPACE]
```

| Option | Description |
| --- | --- |
| `-A` / `--all-workspaces` | Scan all visible workspaces and aggregate results. |

**Example**

```shell
fabric-dw warehouses list --all-workspaces
```

```
 workspace      displayName    id
 -------------- -------------- ------------------------------------
 MyWorkspace    SalesWH        7c3f...
 OtherWS        AnalyticsWH    1a2b...
```

---

### warehouses get

Get details for a specific warehouse.

**Synopsis**

```
fabric-dw warehouses get [WORKSPACE] [WAREHOUSE]
```

**Example**

```shell
fabric-dw warehouses get MyWorkspace SalesWH
```

```
id             7c3f...
displayName    SalesWH
description    Main sales warehouse
```

---

### warehouses create

Create a new warehouse in a workspace.

**Synopsis**

```
fabric-dw warehouses create [OPTIONS] [WORKSPACE] NAME
```

| Option | Description |
| --- | --- |
| `--collation TEXT` | Default collation for the new warehouse. |
| `--description TEXT` | Description for the new warehouse. |

**Example**

```shell
fabric-dw warehouses create MyWorkspace NewWH --description "Staging warehouse"
```

---

### warehouses rename

Rename a warehouse and optionally update its description.

**Synopsis**

```
fabric-dw warehouses rename [OPTIONS] [WORKSPACE] [WAREHOUSE] NEW_NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional new description. |

**Example**

```shell
fabric-dw warehouses rename MyWorkspace SalesWH SalesWH_v2 --description "Renamed"
```

---

### warehouses delete

Delete a warehouse. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw warehouses delete [WORKSPACE] [WAREHOUSE]
```

**Example**

```shell
fabric-dw --yes warehouses delete MyWorkspace OldWH
```

---

### warehouses takeover

Take ownership of a warehouse. Not supported for SQL Analytics Endpoints.

**Synopsis**

```
fabric-dw warehouses takeover [WORKSPACE] [WAREHOUSE]
```

**Example**

```shell
fabric-dw warehouses takeover MyWorkspace SalesWH
```

---

### warehouses permissions

List all principals (users, groups, service principals) with access to a warehouse, including their effective permissions. Requires **Fabric Administrator** role.

**Synopsis**

```
fabric-dw [--json] warehouses permissions [WORKSPACE] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--json` | Emit raw JSON instead of a Rich table. Pass on the root command. |

**Example**

```shell
# Tabular output
fabric-dw warehouses permissions MyWorkspace SalesWH

# Raw JSON
fabric-dw --json warehouses permissions MyWorkspace SalesWH
```

```
 Display Name    UPN / App ID             Type    Permissions    Additional Permissions
 --------------- ------------------------ ------- -------------- ----------------------
 Alice           alice@contoso.com        User    Read, Write
 DataPipeline    00000000-0000-...        ServicePrincipal  Read
```

---

## fabric-dw sql-endpoints

Manage Microsoft Fabric SQL Analytics Endpoints.

### sql-endpoints list

List all SQL Analytics Endpoints in a workspace. Supports `-A` / `--all-workspaces` to scan every visible workspace.

**Synopsis**

```
fabric-dw sql-endpoints list [-A] [WORKSPACE]
```

| Option | Description |
| --- | --- |
| `-A` / `--all-workspaces` | Scan all visible workspaces and aggregate results. |

**Example**

```shell
fabric-dw sql-endpoints list MyWorkspace
```

```
 displayName        id
 ------------------ ------------------------------------
 MyLakehouseEP      f9e1...
```

---

### sql-endpoints get

Get details for a specific SQL Analytics Endpoint.

**Synopsis**

```
fabric-dw sql-endpoints get WORKSPACE ENDPOINT
```

**Example**

```shell
fabric-dw sql-endpoints get MyWorkspace MyLakehouseEP
```

---

### sql-endpoints refresh

Refresh metadata for a SQL Analytics Endpoint by triggering a sync from the underlying Lakehouse delta tables. This is a long-running operation (LRO) that is polled to completion.

Results are shown as a Rich table (Table, Status, End Time, Error). Pass `--json` on the root command to emit raw JSON instead.

**Synopsis**

```
fabric-dw sql-endpoints refresh [--recreate-tables] WORKSPACE ENDPOINT
```

**Options**

| Flag | Description |
|------|-------------|
| `--recreate-tables` | Drop and recreate all tables during the refresh. Use to resolve inconsistencies or force a clean rebuild. **Destructive** — use with caution. |

**Example**

```shell
# Standard refresh — shows a per-table Rich table
fabric-dw sql-endpoints refresh MyWorkspace MyLakehouseEP

# Force a full table recreate
fabric-dw sql-endpoints refresh --recreate-tables MyWorkspace MyLakehouseEP

# Emit raw JSON
fabric-dw --json sql-endpoints refresh MyWorkspace MyLakehouseEP
```

---

### sql-endpoints permissions

List all principals (users, groups, service principals) with access to a SQL Analytics Endpoint, including their effective permissions. Requires **Fabric Administrator** role.

**Synopsis**

```
fabric-dw [--json] sql-endpoints permissions [WORKSPACE] ENDPOINT
```

| Option | Description |
| --- | --- |
| `--json` | Emit raw JSON instead of a Rich table. Pass on the root command. |

**Example**

```shell
# Tabular output
fabric-dw sql-endpoints permissions MyWorkspace MyLakehouseEP

# Raw JSON
fabric-dw --json sql-endpoints permissions MyWorkspace MyLakehouseEP
```

```
 Display Name    UPN / App ID             Type    Permissions    Additional Permissions
 --------------- ------------------------ ------- -------------- ----------------------
 Alice           alice@contoso.com        User    Read, Write
 DataPipeline    00000000-0000-...        ServicePrincipal  Read
```

---

## fabric-dw sql

Execute SQL against a Fabric Data Warehouse or SQL Analytics Endpoint.

### sql exec

Execute a SQL statement or file against a warehouse or SQL Analytics Endpoint. Provide the query via `-q`/`--query` or `-f`/`--file` (not both). Multi-statement batches are supported; only the last result set is returned. DDL/DML statements return empty columns and rows.

> **Warning:** This command executes arbitrary SQL, including DDL and DML. Ensure you have the correct target before running destructive statements.

**Synopsis**

```
fabric-dw sql exec [OPTIONS] [WORKSPACE] [ITEM]
```

| Option | Description |
| --- | --- |
| `-q` / `--query TEXT` | SQL statement or batch to execute inline. |
| `-f` / `--file PATH` | Path to a `.sql` file to execute. UTF-8 and UTF-8 BOM files are both supported. |
| `--table` | Render results as a Rich table instead of JSON. |

**Example**

```shell
# Inline query, JSON output (default)
fabric-dw sql exec MyWorkspace SalesWH -q "SELECT TOP 5 * FROM dbo.Sales"

# File input, table output
fabric-dw sql exec MyWorkspace SalesWH -f ./queries/report.sql --table
```

```json
{"columns": ["id", "name"], "rows": [[1, "Alice"], [2, "Bob"]], "rowcount": 2}
```

---

## fabric-dw audit

Manage SQL audit settings for Microsoft Fabric Data Warehouses.

### audit get

Get the current audit settings for a warehouse.

**Synopsis**

```
fabric-dw audit get [WORKSPACE] [WAREHOUSE]
```

**Example**

```shell
fabric-dw audit get MyWorkspace SalesWH
```

```
state            Enabled
retentionDays    7
actionGroups     BATCH_COMPLETED_GROUP
```

---

### audit enable

Enable SQL auditing on a warehouse.

**Synopsis**

```
fabric-dw audit enable [OPTIONS] [WORKSPACE] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--retention-days INTEGER` | Audit log retention in days. `0` means unlimited. | `0` |

**Example**

```shell
fabric-dw audit enable --retention-days 90 MyWorkspace SalesWH
```

---

### audit disable

Disable SQL auditing on a warehouse.

**Synopsis**

```
fabric-dw audit disable [WORKSPACE] [WAREHOUSE]
```

**Example**

```shell
fabric-dw audit disable MyWorkspace SalesWH
```

---

### audit set-retention

Update the audit log retention period without changing the audit enabled/disabled state. Audit must already be enabled; if it is disabled, run `audit enable` first.

**Synopsis**

```
fabric-dw audit set-retention --days INTEGER [WORKSPACE] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--days INTEGER` | Retention period in days (1–3653; 3653 ≈ 10 years). (required) |

**Example**

```shell
fabric-dw audit set-retention --days 90 MyWorkspace SalesWH
```

---

### audit set-groups

Set the audit action groups for a warehouse. Pass `--group` / `-g` once per action group. This replaces the existing list of groups.

**Synopsis**

```
fabric-dw audit set-groups -g GROUP [-g GROUP ...] [WORKSPACE] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `-g` / `--group TEXT` | Audit action group name. Repeat for multiple groups. (required) |

**Example**

```shell
fabric-dw audit set-groups \
  -g BATCH_COMPLETED_GROUP \
  -g SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP \
  MyWorkspace SalesWH
```

---

### audit add-group

Add a single audit action group without overwriting the others. Idempotent — if the group is already present the command succeeds without modifying the configuration. Auditing must already be enabled.

**Synopsis**

```
fabric-dw audit add-group [WORKSPACE] [WAREHOUSE] GROUP
```

**Example**

```shell
fabric-dw audit add-group MyWorkspace SalesWH BATCH_COMPLETED_GROUP
```

---

### audit remove-group

Remove a single audit action group without overwriting the others. Idempotent — if the group is not present the command succeeds without modifying the configuration. Auditing must already be enabled.

**Synopsis**

```
fabric-dw audit remove-group [WORKSPACE] [WAREHOUSE] GROUP
```

**Example**

```shell
fabric-dw audit remove-group MyWorkspace SalesWH BATCH_COMPLETED_GROUP
```

---

## fabric-dw queries

Inspect and manage running queries on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

### queries list

List all currently running queries on a warehouse or SQL Analytics Endpoint.

**Synopsis**

```
fabric-dw queries list [WORKSPACE] [WAREHOUSE]
```

**Example**

```shell
fabric-dw queries list MyWorkspace SalesWH
```

```
 sessionId   loginName   startTime             commandText
 ----------- ----------- --------------------- -------------------------
 42          user@co.io  2026-06-08T10:01:00Z  SELECT * FROM sales ...
```

---

### queries list-connections

List all active SQL connections on a warehouse or SQL Analytics Endpoint. This queries `sys.dm_exec_connections` and shows lower-level connection info (including idle connections) that is not visible in `queries list`.

**Synopsis**

```
fabric-dw queries list-connections [WORKSPACE] [WAREHOUSE]
```

**Example**

```shell
fabric-dw queries list-connections MyWorkspace SalesWH
```

```
 session_id  connect_time          client_net_address  auth_scheme  encrypt_option  net_transport  most_recent_session_id
 ----------  --------------------  ------------------  -----------  --------------  -------------  ----------------------
 10          2026-06-08T10:00:00Z  192.168.1.100       NTLM         TRUE            TCP            10
 20          2026-06-08T10:01:00Z  192.168.1.101       KERBEROS     FALSE           TCP            20
```

---

### queries kill

Kill a specific session on a warehouse or SQL Analytics Endpoint. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw queries kill [WORKSPACE] [WAREHOUSE] SESSION_ID
```

**Example**

```shell
fabric-dw --yes queries kill MyWorkspace SalesWH 42
```

---

## fabric-dw restore-points

Manage Microsoft Fabric Warehouse restore points.

A restore point captures the state of a warehouse at a point in time. User-defined restore points can be created, renamed, and deleted. System-created restore points are managed automatically by Fabric and cannot be deleted. Restore point IDs are timestamp-based strings (e.g. `"1726617378000"`), not GUIDs.

### restore-points list

List all restore points for a warehouse.

**Synopsis**

```
fabric-dw restore-points list [WORKSPACE] [WAREHOUSE]
```

**Example**

```shell
fabric-dw restore-points list MyWorkspace SalesWH
```

```
 id              displayName        creationMode   eventDateTime
 --------------- ------------------ -------------- ---------------------
 1726617378000   Before migration   UserDefined    2024-10-18T22:17:09Z
```

---

### restore-points get

Get details for a single restore point by ID.

**Synopsis**

```
fabric-dw restore-points get WORKSPACE WAREHOUSE RESTORE_POINT_ID
```

**Example**

```shell
fabric-dw restore-points get MyWorkspace SalesWH 1726617378000
```

---

### restore-points create

Create a new restore point for a warehouse at the current timestamp.

**Synopsis**

```
fabric-dw restore-points create [OPTIONS] [WORKSPACE] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Optional display name (max 128 chars). |
| `--description TEXT` | Optional description (max 512 chars). |

**Example**

```shell
fabric-dw restore-points create MyWorkspace SalesWH \
  --name "Before migration" \
  --description "Pre-migration checkpoint"
```

---

### restore-points rename

Rename a restore point and optionally update its description.

**Synopsis**

```
fabric-dw restore-points rename [OPTIONS] WORKSPACE WAREHOUSE RESTORE_POINT_ID NEW_NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional new description. |

**Example**

```shell
fabric-dw restore-points rename MyWorkspace SalesWH 1726617378000 "Post-migration backup"
```

---

### restore-points delete

Delete a user-defined restore point. System-created restore points cannot be deleted. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw restore-points delete WORKSPACE WAREHOUSE RESTORE_POINT_ID
```

**Example**

```shell
fabric-dw --yes restore-points delete MyWorkspace SalesWH 1726617378000
```

---

### restore-points restore

Restore a warehouse in-place to a restore point. **This is a destructive operation** — the warehouse will be unavailable for approximately 10 minutes. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw restore-points restore WORKSPACE WAREHOUSE RESTORE_POINT_ID
```

**Example**

```shell
fabric-dw --yes restore-points restore MyWorkspace SalesWH 1726617378000
```

---

## fabric-dw views

Manage SQL views on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

### views list

List all views on a warehouse or SQL Analytics Endpoint. Pass `--schema` to filter to a single schema.

**Synopsis**

```
fabric-dw views list [OPTIONS] [WORKSPACE] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--schema TEXT` | Only list views in this schema. |

**Example**

```shell
fabric-dw views list MyWorkspace SalesWH --schema dbo
```

```
 schema_name  name         created               modified
 ------------ ------------ --------------------- ---------------------
 dbo          vw_sales     2026-01-10T08:00:00Z  2026-06-01T12:00:00Z
 dbo          vw_monthly   2026-02-01T09:00:00Z  2026-05-15T14:00:00Z
```

---

### views get

Get the full definition of a single view.

**Synopsis**

```
fabric-dw views get [WORKSPACE] [WAREHOUSE] QUALIFIED_NAME
```

`QUALIFIED_NAME` must be a dot-separated `schema.view_name` string, e.g. `dbo.vw_sales`.

**Example**

```shell
fabric-dw views get MyWorkspace SalesWH dbo.vw_sales
```

```
schema_name    dbo
name           vw_sales
qualified_name dbo.vw_sales
created        2026-01-10T08:00:00Z
modified       2026-06-01T12:00:00Z
definition     SELECT id, amount FROM dbo.sales
```

---

### views create

Create a new SQL view.

**Synopsis**

```
fabric-dw views create [WORKSPACE] [WAREHOUSE] QUALIFIED_NAME SELECT_BODY
```

`SELECT_BODY` is the verbatim SELECT statement that forms the view body.

**Example**

```shell
fabric-dw views create MyWorkspace SalesWH dbo.vw_recent \
  "SELECT id, amount FROM dbo.sales WHERE sale_date >= '2026-01-01'"
```

---

### views update

Redefine an existing view using `CREATE OR ALTER VIEW`.

**Synopsis**

```
fabric-dw views update [WORKSPACE] [WAREHOUSE] QUALIFIED_NAME SELECT_BODY
```

**Example**

```shell
fabric-dw views update MyWorkspace SalesWH dbo.vw_recent \
  "SELECT id, amount, region FROM dbo.sales WHERE sale_date >= '2026-01-01'"
```

---

### views drop

Drop a SQL view. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw views drop [WORKSPACE] [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fabric-dw --yes views drop MyWorkspace SalesWH dbo.vw_recent
```

---

### views read

Read up to `--count` rows from a view and emit them as JSON (default), CSV, or Parquet.

CSV and Parquet formats require `--output`. JSON is emitted to stdout by default.

**Synopsis**

```
fabric-dw views read [OPTIONS] [WORKSPACE] [WAREHOUSE] QUALIFIED_NAME
```

| Option | Description | Default |
| --- | --- | --- |
| `--count N` | Maximum rows to return. | `10` |
| `--format {json\|csv\|parquet}` | Output format. | `json` |
| `--output PATH` | Write to file instead of stdout. Required for `csv` and `parquet`. | |

**Example**

```shell
fabric-dw views read MyWorkspace SalesWH dbo.vw_sales --count 5
```

```json
[
  {"id": 1, "amount": 99.99, "customer_id": 42},
  ...
]
```

---

## fabric-dw tables

Manage SQL tables on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

> **List-source note** — no public REST API exists for enumerating warehouse tables. `tables list` falls back to TDS via `sys.tables JOIN sys.schemas`, the same approach used by `views list`.

### tables list

List all tables on a warehouse or SQL Analytics Endpoint. Pass `--schema` to filter to a single schema.

**Synopsis**

```
fabric-dw tables list [OPTIONS] [WORKSPACE] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--schema TEXT` | Only list tables in this schema. |

**Example**

```shell
fabric-dw tables list MyWorkspace SalesWH --schema dbo
```

```
 schema_name  name      created               modified
 ------------ --------- --------------------- ---------------------
 dbo          customers 2026-01-10T08:00:00Z  2026-06-01T12:00:00Z
 dbo          orders    2026-02-01T09:00:00Z  2026-05-15T14:00:00Z
```

---

### tables read

Read up to `--count` rows from a table and emit them as JSON (default), CSV, or Parquet.

CSV and Parquet formats require `--output`. JSON is emitted to stdout by default.

**Synopsis**

```
fabric-dw tables read [OPTIONS] [WORKSPACE] [WAREHOUSE] QUALIFIED_NAME
```

| Option | Description | Default |
| --- | --- | --- |
| `--count N` | Maximum rows to return. | `10` |
| `--format {json\|csv\|parquet}` | Output format. | `json` |
| `--output PATH` | Write to file instead of stdout. Required for `csv` and `parquet`. | |

**Example**

```shell
fabric-dw tables read MyWorkspace SalesWH dbo.orders --count 5
```

```json
[
  {"id": 1, "amount": 99.99, "customer_id": 42},
  ...
]
```

---

### tables create

Create a new table via CTAS (`CREATE TABLE … AS SELECT`). The body must start with `SELECT` (leading block and line comments are allowed).

**Synopsis**

```
fabric-dw tables create [OPTIONS] [WORKSPACE] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--name SCHEMA.TABLE` | **Required.** Qualified table name. |
| `--select TEXT` | Inline SELECT statement. |
| `--from-file PATH` | Path to a `.sql` file containing the SELECT body (UTF-8/UTF-8-sig). |

Exactly one of `--select` or `--from-file` must be provided.

**Example**

```shell
fabric-dw tables create MyWorkspace SalesWH \
  --name dbo.orders_2026 \
  --select "SELECT * FROM dbo.orders WHERE YEAR(sale_date) = 2026"
```

---

### tables delete

Drop a table. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw tables delete [OPTIONS] [WORKSPACE] [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fabric-dw --yes tables delete MyWorkspace SalesWH dbo.orders_2026
```

---

### tables clear

Truncate a table (delete all rows, keep structure). You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw tables clear [OPTIONS] [WORKSPACE] [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fabric-dw --yes tables clear MyWorkspace SalesWH dbo.staging_load
```

---

## fabric-dw schemas

Manage SQL schemas on Microsoft Fabric Data Warehouses.

> **List-source note** — no public REST API exists for enumerating warehouse schemas. `schemas list` falls back to TDS via `sys.schemas`, filtering out well-known system schemas (`sys`, `INFORMATION_SCHEMA`, `guest`, `db_*` fixed-role schemas). `dbo` is always included because it is user-writable.

> **SQL Analytics Endpoints** — `schemas list` works on both warehouses and SQL Analytics Endpoints. `schemas create` and `schemas delete` are DDL operations and are only supported on Fabric Data Warehouses; they will fail with an error if the resolved item is a SQL Analytics Endpoint.

### schemas list

List all user-defined schemas on a warehouse or SQL Analytics Endpoint. System schemas are excluded.

**Usage**

```shell
fabric-dw schemas list [OPTIONS] [WORKSPACE] [WAREHOUSE]
```

**Example**

```shell
fabric-dw schemas list MyWorkspace SalesWH
```

```
 name     principal_id
 ───────────────────────
 dbo      1
 sales    5
 staging  7
```

### schemas create

Create a new SQL schema on a warehouse.

**Usage**

```shell
fabric-dw schemas create [OPTIONS] [WORKSPACE] [WAREHOUSE] NAME
```

**Example**

```shell
fabric-dw schemas create MyWorkspace SalesWH reporting
```

### schemas delete

Drop a schema from a warehouse. You will be asked to confirm unless `--yes` is passed.

Pass `--cascade` to also drop all tables and views inside the schema before dropping the schema itself. **This is a destructive, irreversible operation.**

**Usage**

```shell
fabric-dw schemas delete [OPTIONS] [WORKSPACE] [WAREHOUSE] NAME
```

| Option | Description |
| --- | --- |
| `--cascade` | Drop all tables and views in the schema first. **WARNING: permanently deletes all contained objects and data.** |

**Example**

```shell
# Drop an empty schema
fabric-dw --yes schemas delete MyWorkspace SalesWH staging

# Drop a schema and all its tables/views
fabric-dw --yes schemas delete MyWorkspace SalesWH staging --cascade
```

---

## fabric-dw snapshots

Manage Microsoft Fabric Data Warehouse snapshots.

### snapshots list

List all snapshots for a warehouse.

**Synopsis**

```
fabric-dw snapshots list [WORKSPACE] [WAREHOUSE]
```

**Example**

```shell
fabric-dw snapshots list MyWorkspace SalesWH
```

```
 displayName      id           createdTime
 ---------------- ------------ ---------------------
 snap-2026-06-01  d1e2...      2026-06-01T00:00:00Z
```

---

### snapshots create

Create a new snapshot for a warehouse. Optionally pin it to a specific point in time.

**Synopsis**

```
fabric-dw snapshots create [OPTIONS] [WORKSPACE] [WAREHOUSE] NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional description. |
| `--snapshot-dt TEXT` | Optional snapshot datetime (ISO 8601, UTC). Defaults to the current timestamp. |

**Example**

```shell
fabric-dw snapshots create MyWorkspace SalesWH snap-2026-06-08 \
  --snapshot-dt 2026-06-08T00:00:00Z
```

---

### snapshots rename

Rename a snapshot and optionally update its description.

**Synopsis**

```
fabric-dw snapshots rename [OPTIONS] WORKSPACE SNAPSHOT NEW_NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional new description. |

**Example**

```shell
fabric-dw snapshots rename MyWorkspace snap-2026-06-01 snap-june-2026
```

---

### snapshots delete

Delete a snapshot. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw snapshots delete WORKSPACE SNAPSHOT
```

**Example**

```shell
fabric-dw --yes snapshots delete MyWorkspace snap-old
```

---

### snapshots roll

Roll a snapshot on a warehouse to a new timestamp. `SNAPSHOT_NAME` must be the display name of the snapshot database. `WORKSPACE` and `WAREHOUSE` accept name or GUID.

**Synopsis**

```
fabric-dw snapshots roll [OPTIONS] [WORKSPACE] [WAREHOUSE] SNAPSHOT_NAME
```

| Option | Description |
| --- | --- |
| `--at TEXT` | Target datetime (ISO 8601, UTC). Defaults to `CURRENT_TIMESTAMP`. |

**Example**

```shell
fabric-dw snapshots roll MyWorkspace SalesWH snap-june-2026 \
  --at 2026-06-08T12:00:00Z
```

---

## fabric-dw sql-pools

!!! warning "Beta / preview feature"
    Manages workspace SQL Pools (currently in preview; the API may change before GA).  Callers must hold the **workspace admin role**.

Manage custom SQL Pools at the workspace level with sub-resource commands that mirror the Azure CLI style.

### sql-pools get

Fetch the full SQL Pools configuration (enabled flag + pool list) for a workspace.

**Synopsis**

```
fabric-dw sql-pools get [WORKSPACE]
```

**Example**

```shell
fabric-dw sql-pools get MyWorkspace
```

---

### sql-pools list

List all SQL pools in a workspace.

**Synopsis**

```
fabric-dw sql-pools list [WORKSPACE]
```

**Example**

```shell
fabric-dw sql-pools list MyWorkspace
```

---

### sql-pools show

Show details for a single SQL pool.

**Synopsis**

```
fabric-dw sql-pools show [WORKSPACE] --name POOL
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Pool name to show. (required) |

**Example**

```shell
fabric-dw sql-pools show MyWorkspace --name ETL
```

---

### sql-pools create

Add a new SQL pool to a workspace.

**Synopsis**

```
fabric-dw sql-pools create [OPTIONS] [WORKSPACE]
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Pool name. (required) |
| `--max-percent INTEGER` | Max resource percentage (1–100). (required) |
| `--default` / `--no-default` | Mark as default pool. Default: `--no-default`. |
| `--optimize-for-reads` / `--no-optimize-for-reads` | Enable read optimisation. Default: `--optimize-for-reads`. |
| `--classifier-type TEXT` | Classifier type (e.g. `Application Name`). |
| `--classifier-value TEXT` | Classifier value. Repeat for multiple values. |

**Example**

```shell
fabric-dw sql-pools create MyWorkspace \
  --name ETL \
  --max-percent 30 \
  --no-optimize-for-reads \
  --classifier-type "Application Name" \
  --classifier-value "ETL" \
  --classifier-value "Load"
```

---

### sql-pools update

Update an existing SQL pool. Only the flags you provide are changed; all other fields are preserved.

**Synopsis**

```
fabric-dw sql-pools update [OPTIONS] [WORKSPACE]
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Pool name to update. (required) |
| `--max-percent INTEGER` | New max resource percentage. |
| `--default` / `--no-default` | Set or clear the default flag. |
| `--optimize-for-reads` / `--no-optimize-for-reads` | Enable or disable read optimisation. |
| `--classifier-type TEXT` | New classifier type. |
| `--classifier-value TEXT` | New classifier value(s). Replaces all existing values. |

**Example**

```shell
fabric-dw sql-pools update MyWorkspace --name ETL --max-percent 40
```

---

### sql-pools delete

Remove a SQL pool from a workspace. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw sql-pools delete [OPTIONS] [WORKSPACE]
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Pool name to delete. (required) |
| `--yes` | Skip confirmation prompt. |

**Example**

```shell
fabric-dw --yes sql-pools delete MyWorkspace --name ETL
```

---

### sql-pools enable

Enable custom SQL Pools for a workspace. Preserves the existing pool configuration.

**Synopsis**

```
fabric-dw sql-pools enable [WORKSPACE]
```

**Example**

```shell
fabric-dw sql-pools enable MyWorkspace
```

---

### sql-pools disable

Disable custom SQL Pools for a workspace without deleting pool definitions. Re-enabling with `sql-pools enable` restores the previously saved configuration.

**Synopsis**

```
fabric-dw sql-pools disable [WORKSPACE]
```

**Example**

```shell
fabric-dw sql-pools disable MyWorkspace
```

---

### sql-pools reset

Clear all SQL pools for a workspace. The enabled/disabled state is preserved. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw sql-pools reset [OPTIONS] [WORKSPACE]
```

| Option | Description |
| --- | --- |
| `--yes` | Skip confirmation prompt. |

**Example**

```shell
fabric-dw --yes sql-pools reset MyWorkspace
```

---

## fabric-dw cache

Manage the local name-to-UUID lookup cache. `fabric-dw` caches workspace and item name-to-GUID mappings to avoid repeated API round-trips. Use these commands if you rename items outside the CLI or need to force a fresh lookup.

### cache clear

Clear all cached entries.

**Synopsis**

```
fabric-dw cache clear
```

**Example**

```shell
fabric-dw cache clear
```

```
Cache cleared.
```

---

## fabric-dw completion

Manage shell completion scripts. See [Shell Completion](completion.md) for full installation details.

### completion install

Generate and optionally install the tab-completion script for `bash`, `zsh`, or `fish`. Without `--print`, the script is written to the conventional location for the chosen shell (idempotent for bash and zsh). With `--print`, the script is sent to stdout so you can inspect or source it manually.

**Synopsis**

```
fabric-dw completion install [--print] {bash|zsh|fish}
```

| Option | Description |
| --- | --- |
| `--print` | Print the completion script to stdout instead of installing it. |

| Shell | Install location |
| --- | --- |
| `bash` | Appended to `~/.bashrc` (idempotent) |
| `zsh` | Appended to `~/.zshrc` (idempotent) |
| `fish` | Written to `~/.config/fish/completions/fabric-dw.fish` |

**Example**

```shell
# Install for zsh
fabric-dw completion install zsh

# Inspect the bash script before installing
fabric-dw completion install bash --print
```

For AI-assistant (MCP) usage there is no shell completion — see [MCP server](mcp.md) instead.

---

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | Usage error, aborted confirmation prompt, or a Fabric API error. |
| `2` | Reserved. |
