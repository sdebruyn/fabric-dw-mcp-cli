---
title: CLI reference
---

# Command-line reference

`fabric-dw` is a command-line interface for administering Microsoft Fabric Data Warehouses and SQL Analytics Endpoints. The general invocation pattern is:

```
fabric-dw [-w WORKSPACE] [GLOBAL OPTIONS] <noun> <verb> [ARGS] [OPTIONS]
```

The short alias `fdw` is identical to `fabric-dw` in every respect.

**Name-or-GUID arguments** — wherever a synopsis shows `WAREHOUSE`, `ENDPOINT`, `SNAPSHOT`, or `WORKSPACE` (positional in the `workspaces` group only), the value can be either the display name or the item's GUID. The CLI resolves names to GUIDs automatically and caches the mapping locally (see [`fabric-dw cache`](#fabric-dw-cache)).

**Positional arguments with defaults** — positional arguments shown in `[brackets]` may be omitted when a default has been set with [`fabric-dw config set`](#defaults-fabric-dw-config). See [Defaults — fabric-dw config](#defaults-fabric-dw-config) for details.

---

## Item targets: Data Warehouse vs SQL Analytics Endpoint

Fabric has two SQL-surface item kinds:

- **Data Warehouse** — read-write, supports full DDL (CREATE/DROP/TRUNCATE TABLE, CREATE/DROP SCHEMA, CREATE/ALTER VIEW, etc.).
- **SQL Analytics Endpoint** — read-only SQL surface auto-generated over a Lakehouse. DDL and mutating operations are not supported; only read/query operations are allowed.

Each command below is labelled with one of:

- **`Targets: Data Warehouse · SQL Analytics Endpoint`** — the command works on both item kinds.
- **`Targets: Data Warehouse only`** — the command is blocked on SQL Analytics Endpoints (either by an explicit client-side guard in the source code, because it requires write/DDL capability that endpoints do not have, or because it calls warehouse-scoped REST API paths that are not available for SQL Analytics Endpoints).
- **`Targets: SQL Analytics Endpoint`** — the command operates on SQL Analytics Endpoints specifically (not on Data Warehouses).
- **`Targets: Workspace (not item-specific)`** — the command operates at the workspace level and does not target a specific DW or SQL Analytics Endpoint item.

---

## Global options

These options are placed immediately after `fabric-dw` (or `fdw`), before the command group.

| Flag | Description | Default |
| --- | --- | --- |
| `-w` / `--workspace TEXT` | Target workspace (name or GUID). Overrides the `FABRIC_DW_DEFAULT_WORKSPACE` environment variable and the configured default. See [Selecting a workspace](#selecting-a-workspace). | — |
| `--json` | Emit machine-readable JSON instead of Rich tables. | off |
| `--auth {default\|sp\|interactive}` | Override `FABRIC_AUTH` for this invocation. | `default` |
| `-y` / `--yes` | Skip confirmation prompts on destructive commands. | off |
| `-v` / `--verbose` | Enable DEBUG-level logging. | INFO |

The `--auth` flag and the `FABRIC_AUTH` environment variable accept the same three values. See [Authentication](install.md#authentication) for the full credential chain.

---

## Selecting a workspace

Every command that operates on a workspace (everything except `workspaces list` and `cache clear`) resolves the target workspace from the following sources, in priority order:

1. **`-w` / `--workspace` flag** — explicit value passed on the root command, e.g. `fabric-dw -w MyWorkspace warehouses list`.
2. **`FABRIC_DW_DEFAULT_WORKSPACE` environment variable** — if the flag is absent, the CLI reads this variable.
3. **Configured default** — set with `fabric-dw config set workspace VALUE`; used when neither the flag nor the environment variable is present.
4. **Error** — if none of the above is set, the CLI prints a helpful message suggesting you set one of the above.

The `workspaces` command group is an exception: `workspaces get` and `workspaces set-collation` take the workspace as an explicit positional argument (not via `-w`), and `workspaces list` takes no workspace at all.

> **`-A` / `--all-workspaces` interaction:** passing `-A` on the two list commands that support it (`warehouses list`, `sql-endpoints list`) explicitly scans every visible workspace. This flag is mutually exclusive with `-w` (an explicit `-w` conflicts with scanning all workspaces), but it does **not** conflict with a configured default workspace or `FABRIC_DW_DEFAULT_WORKSPACE` — the configured default is silently ignored when `-A` is used.

---

## Defaults — fabric-dw config

`fabric-dw` can store a default workspace and/or warehouse so you do not have to repeat them on every invocation.

```shell
fabric-dw config set workspace MyWorkspace
fabric-dw config set warehouse MyWarehouse
```

Once set, the stored workspace is used whenever `-w` / `--workspace` is not passed and `FABRIC_DW_DEFAULT_WORKSPACE` is not set. The stored warehouse value fills in optional `[WAREHOUSE]` / `[ITEM]` positionals shown in `[brackets]` in the synopsis below. All stored values are resolved in the same way as explicit arguments (name or GUID).

Resolution order for the workspace (see also [Selecting a workspace](#selecting-a-workspace)):

1. `-w` / `--workspace` flag on the root command.
2. `FABRIC_DW_DEFAULT_WORKSPACE` environment variable.
3. Value stored by `fabric-dw config set workspace`.

The warehouse follows the same order using the optional `[WAREHOUSE]` / `[ITEM]` positional or `FABRIC_DW_DEFAULT_WAREHOUSE`.

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

**Targets:** Workspace (not item-specific)

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

**Targets:** Workspace (not item-specific)

Get details for a workspace, including its default Data Warehouse collation.

> **Note:** The `workspaces` group is exempt from the global `-w/--workspace` option. Pass the workspace name or GUID as a positional argument instead.

**Synopsis**

```
fabric-dw workspaces get <WORKSPACE>
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

**Targets:** Workspace (not item-specific)

Set the default Data Warehouse collation for a workspace. `COLLATION` must be one of the supported Fabric collations.

> **Note:** The `workspaces` group is exempt from the global `-w/--workspace` option. Pass the workspace name or GUID as a positional argument instead.

**Synopsis**

```
fabric-dw workspaces set-collation <WORKSPACE> COLLATION
```

**Example**

```shell
fabric-dw workspaces set-collation MyWorkspace Latin1_General_100_CI_AS_KS_WS_SC_UTF8
```

---

## fabric-dw warehouses

Manage Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

### warehouses list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all Data Warehouses and SQL Analytics Endpoints in a workspace. Pass `-A` / `--all-workspaces` to aggregate across every visible workspace. `-w` / `--workspace` and `--all-workspaces` are mutually exclusive.

**Synopsis**

```
fabric-dw [-w WORKSPACE] warehouses list [-A]
```

| Option | Description |
| --- | --- |
| `-A` / `--all-workspaces` | Scan all visible workspaces and aggregate results. Mutually exclusive with `-w`. |

**Example**

```shell
# List warehouses in the default (or configured) workspace
fabric-dw warehouses list

# List warehouses in a specific workspace
fabric-dw -w MyWorkspace warehouses list

# Aggregate across all visible workspaces
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

**Targets:** Data Warehouse only

Get details for a specific Data Warehouse. Uses the warehouse-scoped REST path (`GET /workspaces/{ws}/warehouses/{id}`); passing a SQL Analytics Endpoint GUID will return a 404. Use `sql-endpoints get` to retrieve endpoint details.

**Synopsis**

```
fabric-dw [-w WORKSPACE] warehouses get [WAREHOUSE]
```

**Example**

```shell
fabric-dw -w MyWorkspace warehouses get SalesWH
```

```
id             7c3f...
displayName    SalesWH
description    Main sales warehouse
```

---

### warehouses create

**Targets:** Data Warehouse only

Create a new warehouse in a workspace.

**Synopsis**

```
fabric-dw [-w WORKSPACE] warehouses create [OPTIONS] NAME
```

| Option | Description |
| --- | --- |
| `--collation TEXT` | Default collation for the new warehouse. |
| `--description TEXT` | Description for the new warehouse. |

**Example**

```shell
fabric-dw -w MyWorkspace warehouses create NewWH --description "Staging warehouse"
```

---

### warehouses rename

**Targets:** Data Warehouse only

Rename a warehouse and optionally update its description.

**Synopsis**

```
fabric-dw [-w WORKSPACE] warehouses rename [OPTIONS] [WAREHOUSE] NEW_NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional new description. |

**Example**

```shell
fabric-dw -w MyWorkspace warehouses rename SalesWH SalesWH_v2 --description "Renamed"
```

---

### warehouses delete

**Targets:** Data Warehouse only

Delete a warehouse. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] warehouses delete [WAREHOUSE]
```

**Example**

```shell
fabric-dw -w MyWorkspace --yes warehouses delete OldWH
```

---

### warehouses takeover

**Targets:** Data Warehouse only

Take ownership of a warehouse. Not supported for SQL Analytics Endpoints.

**Synopsis**

```
fabric-dw [-w WORKSPACE] warehouses takeover [WAREHOUSE]
```

**Example**

```shell
fabric-dw -w MyWorkspace warehouses takeover SalesWH
```

---

### warehouses permissions

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all principals (users, groups, service principals) with access to a warehouse, including their effective permissions. Requires **Fabric Administrator** role.

**Synopsis**

```
fabric-dw [-w WORKSPACE] [--json] warehouses permissions [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--json` | Emit raw JSON instead of a Rich table. Pass on the root command. |

**Example**

```shell
# Tabular output
fabric-dw -w MyWorkspace warehouses permissions SalesWH

# Raw JSON
fabric-dw -w MyWorkspace --json warehouses permissions SalesWH
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

**Targets:** SQL Analytics Endpoint

List all SQL Analytics Endpoints in a workspace. Supports `-A` / `--all-workspaces` to scan every visible workspace. `-w` / `--workspace` and `--all-workspaces` are mutually exclusive.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-endpoints list [-A]
```

| Option | Description |
| --- | --- |
| `-A` / `--all-workspaces` | Scan all visible workspaces and aggregate results. Mutually exclusive with `-w`. |

**Example**

```shell
# List endpoints in the default (or configured) workspace
fabric-dw sql-endpoints list

# List endpoints in a specific workspace
fabric-dw -w MyWorkspace sql-endpoints list

# Aggregate across all visible workspaces
fabric-dw sql-endpoints list --all-workspaces
```

```
 displayName        id
 ------------------ ------------------------------------
 MyLakehouseEP      f9e1...
```

---

### sql-endpoints get

**Targets:** SQL Analytics Endpoint

Get details for a specific SQL Analytics Endpoint.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-endpoints get ENDPOINT
```

**Example**

```shell
fabric-dw -w MyWorkspace sql-endpoints get MyLakehouseEP
```

---

### sql-endpoints refresh

**Targets:** SQL Analytics Endpoint

Refresh metadata for a SQL Analytics Endpoint by triggering a sync from the underlying Lakehouse delta tables. This is a long-running operation (LRO) that is polled to completion.

Results are shown as a Rich table (Table, Status, End Time, Error). Pass `--json` on the root command to emit raw JSON instead.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-endpoints refresh [--recreate-tables] ENDPOINT
```

**Options**

| Flag | Description |
|------|-------------|
| `--recreate-tables` | Drop and recreate all tables during the refresh. Use to resolve inconsistencies or force a clean rebuild. **Destructive** — use with caution. |

**Example**

```shell
# Standard refresh — shows a per-table Rich table
fabric-dw -w MyWorkspace sql-endpoints refresh MyLakehouseEP

# Force a full table recreate
fabric-dw -w MyWorkspace sql-endpoints refresh --recreate-tables MyLakehouseEP

# Emit raw JSON
fabric-dw -w MyWorkspace --json sql-endpoints refresh MyLakehouseEP
```

---

### sql-endpoints permissions

**Targets:** SQL Analytics Endpoint

List all principals (users, groups, service principals) with access to a SQL Analytics Endpoint, including their effective permissions. Requires **Fabric Administrator** role.

**Synopsis**

```
fabric-dw [-w WORKSPACE] [--json] sql-endpoints permissions ENDPOINT
```

| Option | Description |
| --- | --- |
| `--json` | Emit raw JSON instead of a Rich table. Pass on the root command. |

**Example**

```shell
# Tabular output
fabric-dw -w MyWorkspace sql-endpoints permissions MyLakehouseEP

# Raw JSON
fabric-dw -w MyWorkspace --json sql-endpoints permissions MyLakehouseEP
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

> **Breaking change:** The former `sql exec` subcommand was promoted to `sql` itself.
> Replace `fdw sql exec ...` with `fdw sql ...`.

**Targets:** Data Warehouse · SQL Analytics Endpoint

Execute a SQL statement or file against a warehouse or SQL Analytics Endpoint. Provide the query via `-q`/`--query` or `-f`/`--file` (not both). Multi-statement batches are supported; only the last result set is returned. DDL/DML statements return empty columns and rows.

> **Warning:** This command executes arbitrary SQL, including DDL and DML. Ensure you have the correct target before running destructive statements.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `-q` / `--query TEXT` | SQL statement or batch to execute inline. |
| `-f` / `--file PATH` | Path to a `.sql` file to execute. UTF-8 and UTF-8 BOM files are both supported. |

Output defaults to a Rich table (rows/columns). Pass `--json` on the root command to emit machine-readable JSON (`{"columns": [...], "rows": [...], "rowcount": N}`).

**Example**

```shell
# Inline query, Rich table output (default)
fabric-dw -w MyWorkspace sql SalesWH -q "SELECT TOP 5 * FROM dbo.Sales"

# File input, JSON output
fabric-dw -w MyWorkspace --json sql SalesWH -f ./queries/report.sql
```

```json
{"columns": ["id", "name"], "rows": [[1, "Alice"], [2, "Bob"]], "rowcount": 2}
```

---

## fabric-dw audit

Manage SQL audit settings for Microsoft Fabric Data Warehouses.

### audit get

**Targets:** Data Warehouse only

Get the current audit settings for a warehouse.

**Synopsis**

```
fabric-dw [-w WORKSPACE] audit get [WAREHOUSE]
```

**Example**

```shell
fabric-dw -w MyWorkspace audit get SalesWH
```

```
state            Enabled
retentionDays    7
actionGroups     BATCH_COMPLETED_GROUP
```

---

### audit enable

**Targets:** Data Warehouse only

Enable SQL auditing on a warehouse.

**Synopsis**

```
fabric-dw [-w WORKSPACE] audit enable [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--retention-days INTEGER` | Audit log retention in days (>= 1). Mutually exclusive with `--unlimited`. | — |
| `--unlimited` | Set unlimited audit log retention (service value 0). Mutually exclusive with `--retention-days`. | off |

Omitting both `--retention-days` and `--unlimited` defaults to unlimited retention. Passing `0` for `--retention-days` is rejected — use `--unlimited` for no-limit retention.

**Example**

```shell
# Retain logs for 90 days
fabric-dw -w MyWorkspace audit enable --retention-days 90 SalesWH

# Unlimited retention
fabric-dw -w MyWorkspace audit enable --unlimited SalesWH
```

---

### audit disable

**Targets:** Data Warehouse only

Disable SQL auditing on a warehouse.

**Synopsis**

```
fabric-dw [-w WORKSPACE] audit disable [WAREHOUSE]
```

**Example**

```shell
fabric-dw -w MyWorkspace audit disable SalesWH
```

---

### audit set-retention

**Targets:** Data Warehouse only

Update the audit log retention period without changing the audit enabled/disabled state. Audit must already be enabled; if it is disabled, run `audit enable` first.

**Synopsis**

```
fabric-dw [-w WORKSPACE] audit set-retention --days INTEGER [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--days INTEGER` | Retention period in days (1–3653; 3653 ≈ 10 years). (required) |

**Example**

```shell
fabric-dw -w MyWorkspace audit set-retention --days 90 SalesWH
```

---

### audit set-groups

**Targets:** Data Warehouse only

Set the audit action groups for a warehouse. Pass `--group` / `-g` once per action group. This replaces the existing list of groups.

**Synopsis**

```
fabric-dw [-w WORKSPACE] audit set-groups -g GROUP [-g GROUP ...] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `-g` / `--group TEXT` | Audit action group name. Repeat for multiple groups. (required) |

**Example**

```shell
fabric-dw -w MyWorkspace audit set-groups \
  -g BATCH_COMPLETED_GROUP \
  -g SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP \
  SalesWH
```

---

### audit add-group

**Targets:** Data Warehouse only

Add a single audit action group without overwriting the others. Idempotent — if the group is already present the command succeeds without modifying the configuration. Auditing must already be enabled.

**Synopsis**

```
fabric-dw [-w WORKSPACE] audit add-group [WAREHOUSE] GROUP
```

**Example**

```shell
fabric-dw -w MyWorkspace audit add-group SalesWH BATCH_COMPLETED_GROUP
```

---

### audit remove-group

**Targets:** Data Warehouse only

Remove a single audit action group without overwriting the others. Idempotent — if the group is not present the command succeeds without modifying the configuration. Auditing must already be enabled.

**Synopsis**

```
fabric-dw [-w WORKSPACE] audit remove-group [WAREHOUSE] GROUP
```

**Example**

```shell
fabric-dw -w MyWorkspace audit remove-group SalesWH BATCH_COMPLETED_GROUP
```

---

## fabric-dw queries

Inspect and manage running queries on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

### queries list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all currently running queries on a warehouse or SQL Analytics Endpoint.

**Synopsis**

```
fabric-dw [-w WORKSPACE] queries list [WAREHOUSE]
```

**Example**

```shell
fabric-dw -w MyWorkspace queries list SalesWH
```

```
 sessionId   loginName   startTime             commandText
 ----------- ----------- --------------------- -------------------------
 42          user@co.io  2026-06-08T10:01:00Z  SELECT * FROM sales ...
```

---

### queries list-connections

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all active SQL connections on a warehouse or SQL Analytics Endpoint. This queries `sys.dm_exec_connections` and shows lower-level connection info (including idle connections) that is not visible in `queries list`.

**Synopsis**

```
fabric-dw [-w WORKSPACE] queries list-connections [WAREHOUSE]
```

**Example**

```shell
fabric-dw -w MyWorkspace queries list-connections SalesWH
```

```
 session_id  connect_time          client_net_address  auth_scheme  encrypt_option  net_transport  most_recent_session_id
 ----------  --------------------  ------------------  -----------  --------------  -------------  ----------------------
 10          2026-06-08T10:00:00Z  192.168.1.100       NTLM         TRUE            TCP            10
 20          2026-06-08T10:01:00Z  192.168.1.101       KERBEROS     FALSE           TCP            20
```

---

### queries kill

**Targets:** Data Warehouse · SQL Analytics Endpoint

Kill a specific session on a warehouse or SQL Analytics Endpoint. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] queries kill [WAREHOUSE] SESSION_ID
```

**Example**

```shell
fabric-dw -w MyWorkspace --yes queries kill SalesWH 42
```

---

### queries request-history

**Targets:** Data Warehouse · SQL Analytics Endpoint

List completed SQL requests from `queryinsights.exec_requests_history`. Supports optional time-range filtering with `--since` and `--until` (ISO-8601 strings). The `--limit` option caps the number of rows returned (default: 100, max: 10 000).

**Synopsis**

```
fabric-dw [-w WORKSPACE] queries request-history [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1–10 000). | `100` |
| `--since ISO8601` | Return rows with timestamp >= this value. | — |
| `--until ISO8601` | Return rows with timestamp <= this value. | — |

**Example**

```shell
fabric-dw -w MyWorkspace queries request-history SalesWH --limit 50 --since 2026-06-01T00:00:00
```

---

### queries session-history

**Targets:** Data Warehouse · SQL Analytics Endpoint

List completed sessions from `queryinsights.exec_sessions_history`.

**Synopsis**

```
fabric-dw [-w WORKSPACE] queries session-history [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1–10 000). | `100` |
| `--since ISO8601` | Return rows with session_start_time >= this value. | — |
| `--until ISO8601` | Return rows with session_start_time <= this value. | — |

**Example**

```shell
fabric-dw -w MyWorkspace queries session-history SalesWH
```

---

### queries frequent

**Targets:** Data Warehouse · SQL Analytics Endpoint

List frequently-run queries from `queryinsights.frequently_run_queries`.

**Synopsis**

```
fabric-dw [-w WORKSPACE] queries frequent [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1–10 000). | `100` |
| `--since ISO8601` | Return rows with last_run_start_time >= this value. | — |
| `--until ISO8601` | Return rows with last_run_start_time <= this value. | — |

**Example**

```shell
fabric-dw -w MyWorkspace queries frequent SalesWH --limit 20
```

---

### queries long-running

**Targets:** Data Warehouse · SQL Analytics Endpoint

List long-running queries from `queryinsights.long_running_queries`.

**Synopsis**

```
fabric-dw [-w WORKSPACE] queries long-running [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1–10 000). | `100` |
| `--since ISO8601` | Return rows with last_run_start_time >= this value. | — |
| `--until ISO8601` | Return rows with last_run_start_time <= this value. | — |

**Example**

```shell
fabric-dw -w MyWorkspace queries long-running SalesWH
```

---

## fabric-dw restore-points

Manage Microsoft Fabric Warehouse restore points.

A restore point captures the state of a warehouse at a point in time. User-defined restore points can be created, renamed, and deleted. System-created restore points are managed automatically by Fabric and cannot be deleted. Restore point IDs are timestamp-based strings (e.g. `"1726617378000"`), not GUIDs.

### restore-points list

**Targets:** Data Warehouse only

List all restore points for a warehouse.

**Synopsis**

```
fabric-dw [-w WORKSPACE] restore-points list [WAREHOUSE]
```

**Example**

```shell
fabric-dw -w MyWorkspace restore-points list SalesWH
```

```
 id              displayName        creationMode   eventDateTime
 --------------- ------------------ -------------- ---------------------
 1726617378000   Before migration   UserDefined    2024-10-18T22:17:09Z
```

---

### restore-points get

**Targets:** Data Warehouse only

Get details for a single restore point by ID.

**Synopsis**

```
fabric-dw [-w WORKSPACE] restore-points get WAREHOUSE RESTORE_POINT_ID
```

**Example**

```shell
fabric-dw -w MyWorkspace restore-points get SalesWH 1726617378000
```

---

### restore-points create

**Targets:** Data Warehouse only

Create a new restore point for a warehouse at the current timestamp.

**Synopsis**

```
fabric-dw [-w WORKSPACE] restore-points create [OPTIONS] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Optional display name (max 128 chars). |
| `--description TEXT` | Optional description (max 512 chars). |

**Example**

```shell
fabric-dw -w MyWorkspace restore-points create SalesWH \
  --name "Before migration" \
  --description "Pre-migration checkpoint"
```

---

### restore-points rename

**Targets:** Data Warehouse only

Rename a restore point and optionally update its description.

**Synopsis**

```
fabric-dw [-w WORKSPACE] restore-points rename [OPTIONS] WAREHOUSE RESTORE_POINT_ID NEW_NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional new description. |

**Example**

```shell
fabric-dw -w MyWorkspace restore-points rename SalesWH 1726617378000 "Post-migration backup"
```

---

### restore-points delete

**Targets:** Data Warehouse only

Delete a user-defined restore point. System-created restore points cannot be deleted. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] restore-points delete WAREHOUSE RESTORE_POINT_ID
```

**Example**

```shell
fabric-dw -w MyWorkspace --yes restore-points delete SalesWH 1726617378000
```

---

### restore-points restore

**Targets:** Data Warehouse only

Restore a warehouse in-place to a restore point. **This is a destructive operation** — the warehouse will be unavailable for approximately 10 minutes. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] restore-points restore WAREHOUSE RESTORE_POINT_ID
```

**Example**

```shell
fabric-dw -w MyWorkspace --yes restore-points restore SalesWH 1726617378000
```

---

## fabric-dw views

Manage SQL views on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

### views list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all views on a warehouse or SQL Analytics Endpoint. Pass `--schema` to filter to a single schema.

**Synopsis**

```
fabric-dw [-w WORKSPACE] views list [OPTIONS] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--schema TEXT` | Only list views in this schema. |

**Example**

```shell
fabric-dw -w MyWorkspace views list SalesWH --schema dbo
```

```
 schema_name  name         created               modified
 ------------ ------------ --------------------- ---------------------
 dbo          vw_sales     2026-01-10T08:00:00Z  2026-06-01T12:00:00Z
 dbo          vw_monthly   2026-02-01T09:00:00Z  2026-05-15T14:00:00Z
```

---

### views get

**Targets:** Data Warehouse · SQL Analytics Endpoint

Get the full definition of a single view.

**Synopsis**

```
fabric-dw [-w WORKSPACE] views get [WAREHOUSE] QUALIFIED_NAME
```

`QUALIFIED_NAME` must be a dot-separated `schema.view_name` string, e.g. `dbo.vw_sales`.

**Example**

```shell
fabric-dw -w MyWorkspace views get SalesWH dbo.vw_sales
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

**Targets:** Data Warehouse · SQL Analytics Endpoint

Create a new SQL view.

**Synopsis**

```
fabric-dw [-w WORKSPACE] views create [OPTIONS] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--name SCHEMA.VIEW` | **Required.** Qualified view name (e.g. `dbo.vw_sales`). |
| `--select TEXT` | Inline SELECT statement for the view body. |
| `--from-file PATH` | Path to a `.sql` file containing the SELECT statement. |

Exactly one of `--select` or `--from-file` must be provided.

**Example**

```shell
fabric-dw -w MyWorkspace views create SalesWH \
  --name dbo.vw_recent \
  --select "SELECT id, amount FROM dbo.sales WHERE sale_date >= '2026-01-01'"
```

---

### views update

**Targets:** Data Warehouse · SQL Analytics Endpoint

Redefine an existing view using `CREATE OR ALTER VIEW`.

**Synopsis**

```
fabric-dw [-w WORKSPACE] views update [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the dot-separated `schema.view_name` to update.

| Option | Description |
| --- | --- |
| `--select TEXT` | Inline SELECT statement for the new view body. |
| `--from-file PATH` | Path to a `.sql` file containing the new SELECT statement. |

Exactly one of `--select` or `--from-file` must be provided.

**Example**

```shell
fabric-dw -w MyWorkspace views update SalesWH dbo.vw_recent \
  --select "SELECT id, amount, region FROM dbo.sales WHERE sale_date >= '2026-01-01'"
```

---

### views drop

**Targets:** Data Warehouse · SQL Analytics Endpoint

Drop a SQL view. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] views drop [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fabric-dw -w MyWorkspace --yes views drop SalesWH dbo.vw_recent
```

---

### views rename

**Targets:** Data Warehouse · SQL Analytics Endpoint

Rename a SQL view via `sp_rename`. The new name must be an unqualified (bare) identifier — `sp_rename` cannot move a view to a different schema.

**Synopsis**

```
fabric-dw [-w WORKSPACE] views rename [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the current dot-separated `schema.view_name`.

| Option | Description |
| --- | --- |
| `--new-name TEXT` | **Required.** New bare view name (no schema prefix). |

**Example**

```shell
fabric-dw -w MyWorkspace views rename SalesWH dbo.vw_recent --new-name vw_revenue
```

---

### views read

**Targets:** Data Warehouse · SQL Analytics Endpoint

Read up to `--count` rows from a view and emit them as JSON (default), CSV, or Parquet.

CSV and Parquet formats require `--output`. JSON is emitted to stdout by default.

**Synopsis**

```
fabric-dw [-w WORKSPACE] views read [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

| Option | Description | Default |
| --- | --- | --- |
| `--count N` | Maximum rows to return. | `10` |
| `--format {json\|csv\|parquet}` | Output format. | `json` |
| `--output PATH` | Write to file instead of stdout. Required for `csv` and `parquet`. | |

**Example**

```shell
fabric-dw -w MyWorkspace views read SalesWH dbo.vw_sales --count 5
```

```json
[
  {"id": 1, "amount": 99.99, "customer_id": 42},
  ...
]
```

---

## fabric-dw procedures

Manage stored procedures on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

### procedures list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List stored procedures on a warehouse or SQL Analytics Endpoint. Pass `--schema` to filter to a single schema.

**Synopsis**

```
fabric-dw [-w WORKSPACE] procedures list [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `--schema TEXT` | Only list procedures in this schema. |

**Example**

```shell
fabric-dw -w MyWorkspace procedures list SalesWH --schema dbo
```

```
 schema_name  name            created               modified
 ------------ --------------- --------------------- ---------------------
 dbo          usp_load_sales  2026-01-10T08:00:00Z  2026-06-01T12:00:00Z
```

---

### procedures get

**Targets:** Data Warehouse · SQL Analytics Endpoint

Get the full definition of a single stored procedure.

**Synopsis**

```
fabric-dw [-w WORKSPACE] procedures get [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` must be a dot-separated `schema.proc_name` string, e.g. `dbo.usp_load_sales`.

**Example**

```shell
fabric-dw -w MyWorkspace procedures get SalesWH dbo.usp_load_sales
```

---

### procedures create

**Targets:** Data Warehouse · SQL Analytics Endpoint

Create a new stored procedure.

**Synopsis**

```
fabric-dw [-w WORKSPACE] procedures create [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `--name SCHEMA.PROC` | **Required.** Qualified procedure name (e.g. `dbo.usp_load_sales`). |
| `--body TEXT` | Inline procedure body (the `AS …` section). |
| `--from-file PATH` | Path to a `.sql` file containing the procedure body. |

Exactly one of `--body` or `--from-file` must be provided.

**Example**

```shell
fabric-dw -w MyWorkspace procedures create SalesWH \
  --name dbo.usp_archive_orders \
  --body "BEGIN INSERT INTO dbo.archive SELECT * FROM dbo.orders; END"
```

---

### procedures update

**Targets:** Data Warehouse · SQL Analytics Endpoint

Redefine an existing stored procedure via `CREATE OR ALTER PROCEDURE`.

**Synopsis**

```
fabric-dw [-w WORKSPACE] procedures update [OPTIONS] [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the dot-separated `schema.proc_name` to update.

| Option | Description |
| --- | --- |
| `--body TEXT` | Inline procedure body. |
| `--from-file PATH` | Path to a `.sql` file containing the procedure body. |

Exactly one of `--body` or `--from-file` must be provided. You will be asked to confirm unless `--yes` is passed.

**Example**

```shell
fabric-dw -w MyWorkspace procedures update SalesWH dbo.usp_archive_orders \
  --from-file ./procs/usp_archive_orders_v2.sql
```

---

### procedures drop

**Targets:** Data Warehouse · SQL Analytics Endpoint

Drop a stored procedure. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] procedures drop [ITEM] QUALIFIED_NAME
```

**Example**

```shell
fabric-dw -w MyWorkspace --yes procedures drop SalesWH dbo.usp_archive_orders
```

---

## fabric-dw functions

Manage T-SQL user-defined functions on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

> **Preview:** Scalar UDFs (`FN`) and inline TVFs (`IF`) are preview features as of mid-2026. Function DDL is supported on both Data Warehouses and SQL Analytics Endpoints — no endpoint guard is applied.

### functions list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List T-SQL user-defined functions on a warehouse or SQL Analytics Endpoint. Pass `--schema` to filter by schema, or `--kind` to filter by function kind.

**Synopsis**

```
fabric-dw [-w WORKSPACE] functions list [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `--schema TEXT` | Only list functions in this schema. |
| `--kind [scalar\|inline-tvf\|all]` | Filter by function kind: `scalar` (FN), `inline-tvf` (IF), or `all` (default). |

**Example**

```shell
fabric-dw -w MyWorkspace functions list SalesWH --schema dbo --kind scalar
```

```
 schema_name  name           kind    is_inlineable  created               modified
 ------------ -------------- ------- -------------- --------------------- ---------------------
 dbo          fn_clean_input  scalar  True           2026-06-01T08:00:00Z  2026-06-10T12:00:00Z
```

---

### functions get

**Targets:** Data Warehouse · SQL Analytics Endpoint

Get the full definition of a single T-SQL user-defined function, including its parameter list.

**Synopsis**

```
fabric-dw [-w WORKSPACE] functions get [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` must be a dot-separated `schema.fn_name` string, e.g. `dbo.fn_clean_input`.

**Example**

```shell
fabric-dw -w MyWorkspace functions get SalesWH dbo.fn_clean_input
```

---

### functions create

**Targets:** Data Warehouse · SQL Analytics Endpoint

Create a new T-SQL user-defined function. Scalar UDFs and inline TVFs are preview features.

**Synopsis**

```
fabric-dw [-w WORKSPACE] functions create [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `--name SCHEMA.FN` | **Required.** Qualified function name (e.g. `dbo.fn_clean_input`). |
| `--body TEXT` | Inline function body (parameter list, RETURNS clause, and implementation). |
| `--from-file PATH` | Path to a `.sql` file containing the function body. |

Exactly one of `--body` or `--from-file` must be provided.

**Example**

```shell
fabric-dw -w MyWorkspace functions create SalesWH \
  --name dbo.fn_clean_input \
  --body "(@input NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN LTRIM(RTRIM(@input)) END"
```

---

### functions update

**Targets:** Data Warehouse · SQL Analytics Endpoint

Redefine an existing T-SQL user-defined function via `CREATE OR ALTER FUNCTION`.

> **Note:** `ALTER FUNCTION` cannot change the function kind (e.g. scalar to inline TVF). The body must be compatible with the original function's kind.

**Synopsis**

```
fabric-dw [-w WORKSPACE] functions update [OPTIONS] [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the dot-separated `schema.fn_name` to update.

| Option | Description |
| --- | --- |
| `--body TEXT` | Inline function body. |
| `--from-file PATH` | Path to a `.sql` file containing the function body. |

Exactly one of `--body` or `--from-file` must be provided. You will be asked to confirm unless `--yes` is passed.

**Example**

```shell
fabric-dw -w MyWorkspace functions update SalesWH dbo.fn_clean_input \
  --from-file ./fns/fn_clean_input_v2.sql
```

---

### functions drop

**Targets:** Data Warehouse · SQL Analytics Endpoint

Drop a T-SQL user-defined function. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] functions drop [OPTIONS] [ITEM] QUALIFIED_NAME
```

| Option | Description |
| --- | --- |
| `--if-exists` | No-op when the function does not exist (`DROP FUNCTION IF EXISTS`). |

**Example**

```shell
fabric-dw -w MyWorkspace --yes functions drop SalesWH dbo.fn_clean_input
```

---

### functions rename

**Targets:** Data Warehouse · SQL Analytics Endpoint

Rename a T-SQL user-defined function via `EXEC sp_rename`. The new name must be a bare (unqualified) identifier — `sp_rename` cannot move a function to a different schema. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] functions rename [OPTIONS] [ITEM] QUALIFIED_NAME
```

| Option | Description |
| --- | --- |
| `--new-name TEXT` | **Required.** New bare (unqualified) function name. |

**Example**

```shell
fabric-dw -w MyWorkspace --yes functions rename SalesWH dbo.fn_clean_input \
  --new-name fn_sanitize_input
```

---

## fabric-dw tables

Manage SQL tables on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

> **List-source note** — no public REST API exists for enumerating warehouse tables. `tables list` falls back to TDS via `sys.tables JOIN sys.schemas`, the same approach used by `views list`.

### tables list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all tables on a warehouse or SQL Analytics Endpoint. Pass `--schema` to filter to a single schema.

**Synopsis**

```
fabric-dw [-w WORKSPACE] tables list [OPTIONS] [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--schema TEXT` | Only list tables in this schema. |

**Example**

```shell
fabric-dw -w MyWorkspace tables list SalesWH --schema dbo
```

```
 schema_name  name      created               modified
 ------------ --------- --------------------- ---------------------
 dbo          customers 2026-01-10T08:00:00Z  2026-06-01T12:00:00Z
 dbo          orders    2026-02-01T09:00:00Z  2026-05-15T14:00:00Z
```

---

### tables read

**Targets:** Data Warehouse · SQL Analytics Endpoint

Read up to `--count` rows from a table and emit them as JSON (default), CSV, or Parquet.

CSV and Parquet formats require `--output`. JSON is emitted to stdout by default.

**Synopsis**

```
fabric-dw [-w WORKSPACE] tables read [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

| Option | Description | Default |
| --- | --- | --- |
| `--count N` | Maximum rows to return. | `10` |
| `--format {json\|csv\|parquet}` | Output format. | `json` |
| `--output PATH` | Write to file instead of stdout. Required for `csv` and `parquet`. | |

**Example**

```shell
fabric-dw -w MyWorkspace tables read SalesWH dbo.orders --count 5
```

```json
[
  {"id": 1, "amount": 99.99, "customer_id": 42},
  ...
]
```

---

### tables create

**Targets:** Data Warehouse only

Create a new table on a Fabric Data Warehouse. Two modes are available:

- **CTAS** (`CREATE TABLE … AS SELECT`) — supply `--select` or `--from-file`. The body must start with `SELECT` (leading block/line comments are allowed).
- **Empty DDL** (`CREATE TABLE … (col TYPE, …)`) — supply one or more of `--from-parquet`, `--from-csv`, `--from-schema`, or `--column`. No data is ever read or inserted; this scaffolds the table structure only.

**Synopsis**

```
fabric-dw [-w WORKSPACE] tables create [OPTIONS] [WAREHOUSE]
```

#### CTAS options

| Option | Description |
| --- | --- |
| `--name SCHEMA.TABLE` | **Required.** Qualified table name. |
| `--select TEXT` | Inline SELECT statement for CTAS. |
| `--from-file PATH` | Path to a `.sql` file containing the SELECT body (UTF-8/UTF-8-sig). |

Exactly one of `--select` or `--from-file` must be provided for the CTAS path. Cannot be combined with empty-DDL options.

#### Empty-DDL options

| Option | Description |
| --- | --- |
| `--name SCHEMA.TABLE` | **Required.** Qualified table name. |
| `--from-parquet PATH` | Derive schema from a Parquet file (reads footer only — no data loaded). |
| `--from-csv PATH` | Derive schema from a CSV header + bounded sample (no data loaded). |
| `--from-schema PATH` | JSON file with column specs: `[{"name": "…", "type": "…", "nullable": true}]`. |
| `--column NAME:TYPE[:null\|notnull]` | Inline column definition (repeatable). Can be combined with `--from-schema`. |
| `--all-varchar` | (CSV) Force all columns to `VARCHAR`; skip type inference. |
| `--varchar-length N` | Default VARCHAR/VARBINARY length for string/binary columns (1–8000, default `8000`). |
| `--delimiter CHAR` | (CSV) Field delimiter (default `,`). |
| `--encoding ENC` | (CSV) File encoding (default `utf-8-sig`). |
| `--sample-rows N` | (CSV) Rows to sample for type inference (1–100 000, default `1000`). |

`--from-parquet`, `--from-csv`, and `--from-schema`/`--column` are mutually exclusive with each other and with the CTAS path. For the explicit-schema path at least one `--from-schema` or `--column` must be provided.

**Arrow → T-SQL type mapping (Parquet / CSV inference)**

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
| nested / list / struct | **Error** — use `--all-varchar` or `--from-schema` to override |

**Examples**

```shell
# CTAS
fabric-dw -w MyWorkspace tables create SalesWH \
  --name dbo.orders_2026 \
  --select "SELECT * FROM dbo.orders WHERE YEAR(sale_date) = 2026"

# Empty table from Parquet schema
fabric-dw -w MyWorkspace tables create SalesWH \
  --name dbo.sales_empty \
  --from-parquet ./exports/sales.parquet

# Empty table from CSV header (type inference)
fabric-dw -w MyWorkspace tables create SalesWH \
  --name staging.raw_products \
  --from-csv ./data/products.csv --varchar-length 500

# Empty table with explicit inline columns
fabric-dw -w MyWorkspace tables create SalesWH \
  --name dbo.events \
  --column "event_id:BIGINT:notnull" \
  --column "event_type:VARCHAR(100)" \
  --column "occurred_at:DATETIME2(7)"

# Explicit schema from JSON file + extra columns
fabric-dw -w MyWorkspace tables create SalesWH \
  --name dbo.audit_log \
  --from-schema ./schemas/audit_log.json \
  --column "inserted_at:DATETIME2(7):notnull"
```

---

### tables delete

**Targets:** Data Warehouse only

Drop a table. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] tables delete [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fabric-dw -w MyWorkspace --yes tables delete SalesWH dbo.orders_2026
```

---

### tables clear

**Targets:** Data Warehouse only

Truncate a table (delete all rows, keep structure). You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] tables clear [OPTIONS] [WAREHOUSE] QUALIFIED_NAME
```

**Example**

```shell
fabric-dw -w MyWorkspace --yes tables clear SalesWH dbo.staging_load
```

---

### tables clone

**Targets:** Data Warehouse only

Create a zero-copy clone of a table using `CREATE TABLE … AS CLONE OF`. Pass `--at` to clone from a point in time within the warehouse data-retention window.

**Synopsis**

```
fabric-dw [-w WORKSPACE] tables clone [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `--source SCHEMA.TABLE` | **Required.** Qualified source table to clone. |
| `--name SCHEMA.TABLE` | **Required.** Qualified name for the new clone. |
| `--at ISO8601` | Optional UTC timestamp for a historical clone (e.g. `2024-05-20T14:00:00`). Must be within the data-retention window. |

**Example**

```shell
# Clone to the current state
fabric-dw -w MyWorkspace tables clone SalesWH \
  --source dbo.orders \
  --name dbo.orders_backup

# Point-in-time clone
fabric-dw -w MyWorkspace tables clone SalesWH \
  --source dbo.orders \
  --name dbo.orders_may_snapshot \
  --at 2024-05-20T14:00:00
```

---

### tables rename

**Targets:** Data Warehouse only

Rename a table via `sp_rename`. The new name must be an unqualified (bare) identifier — `sp_rename` cannot move a table to a different schema.

**Synopsis**

```
fabric-dw [-w WORKSPACE] tables rename [OPTIONS] [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the current dot-separated `schema.table_name`.

| Option | Description |
| --- | --- |
| `--new-name TEXT` | **Required.** New bare table name (no schema prefix). |

**Example**

```shell
fabric-dw -w MyWorkspace tables rename SalesWH dbo.orders_2025 --new-name orders_archive_2025
```

---

### tables load

**Targets:** Data Warehouse only

Load data into a warehouse table via `COPY INTO` from either a local file or a remote URL.

**Local file path** (`--file`): the file is staged to a temporary Lakehouse in OneLake (chunked DFS upload), loaded into the target table via `COPY INTO`, and the staging Lakehouse is automatically deleted in a `finally` block regardless of success or failure. JSON files are converted client-side to Parquet (requires `pyarrow`) before staging.

**Remote URL** (`--url`): `COPY INTO` is issued directly from the given URL. For OneLake or same-tenant URLs no credential is needed. For secured external URLs (Azure Blob Storage, ADLS Gen2) supply `--credential-type` and `--secret`/`--identity` as appropriate.

**Auto-create (create-and-load)** — Pass `--create` to auto-create the target table from the source schema before loading (local files only; requires `pyarrow`). The schema is inferred from the source:

- **Parquet**: exact types are read from the Parquet footer (no row data is read).
- **CSV**: the header row and up to `--sample-rows` rows are read for type inference. Use `--all-varchar` to skip inference and force every column to `VARCHAR`.
- **JSON**: the file is converted to Parquet internally (as required for staging); the schema is read from the resulting Parquet footer.

Use `--if-exists` to control behaviour when the table already exists:

| `--if-exists` value | Table exists | Table absent |
| --- | --- | --- |
| `fail` (default with `--create`) | Error — table already exists | Create + load |
| `append` | Skip create, `COPY INTO` existing | Create + load |
| `truncate` ⚠️ **DESTRUCTIVE** | `TRUNCATE` existing table, then load | Create + load |
| `replace` ⚠️ **DESTRUCTIVE** | `DROP` + recreate from inferred schema, then load | Create + load |

`truncate` and `replace` are permanently destructive and require confirmation (or `--yes` / `-y`).

Use `--cleanup-on-failure` to drop the table if WE created it in this call and the subsequent `COPY INTO` fails. A pre-existing table is never dropped by this flag.

> **Not atomic.** `CREATE TABLE` and `COPY INTO` are separate statements. A failure between them may leave an empty table. Use `--cleanup-on-failure` to auto-drop in that case.

**Synopsis**

```
fabric-dw [-w WORKSPACE] tables load [OPTIONS] [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the dot-separated `schema.table_name` of the destination table.

| Option | Default | Description |
| --- | --- | --- |
| `--file PATH` | — | Local file path (CSV, Parquet, or JSON). |
| `--url TEXT` | — | Remote URL (OneLake DFS or external Azure Blob). |
| `--format [csv\|parquet\|json]` | auto-detect | File format. For `--url`, only `csv` and `parquet` are supported. |
| `--header/--no-header` | `--header` | Whether the CSV file contains a header row. |
| `--delimiter TEXT` | `,` | CSV column delimiter. |
| `--encoding TEXT` | — | CSV encoding (e.g. `UTF8`, `UTF8BOM`). |
| `--field-quote TEXT` | — | CSV field-quote character. |
| `--row-terminator TEXT` | — | CSV row terminator (e.g. `\n`, `\r\n`). |
| `--credential-type [none\|sas\|managed-identity\|service-principal\|account-key]` | `none` | Credential type for secured external URLs. |
| `--secret TEXT` | — | Credential secret (SAS token / client secret / account key). Never echoed. |
| `--identity TEXT` | — | Identity for `managed-identity` or `service-principal`. |
| `--staging-lakehouse TEXT` | auto-generated | Name for the temporary staging Lakehouse (local path only). |
| `--keep-staging` | off | Keep the staging Lakehouse after load (for debugging). |
| `--max-errors INT` | — | Maximum errors before aborting. |
| `--rejected-row-location TEXT` | — | URL to write rejected rows to. |
| `--create` | off | Auto-create the target table from the source schema (local files only). |
| `--if-exists [fail\|append\|truncate\|replace]` | `fail` (with `--create`) | What to do when the target table already exists. `truncate` and `replace` are destructive and require confirmation. |
| `--all-varchar` | off | (CSV, `--create`) Force all columns to `VARCHAR`; skip type inference. |
| `--varchar-length INT` | `8000` | (`--create`) Default VARCHAR/VARBINARY length for inferred columns. |
| `--sample-rows INT` | `1000` | (CSV, `--create`) Maximum rows to sample for type inference. |
| `--cleanup-on-failure` | off | Drop the table if WE created it and the load fails. Never drops a pre-existing table. |

**Examples**

```shell
# Load a local CSV into an existing table (header row present)
fabric-dw -w MyWorkspace tables load SalesWH dbo.sales --file data.csv

# Load a local Parquet file into an existing table
fabric-dw -w MyWorkspace tables load SalesWH dbo.events --file events.parquet

# Load a local JSON file (converts to Parquet internally; requires pyarrow)
fabric-dw -w MyWorkspace tables load SalesWH dbo.products --file products.json

# Auto-create the table from a Parquet schema, then load
fabric-dw -w MyWorkspace tables load SalesWH dbo.sales --file data.parquet --create

# Auto-create from CSV, force all columns to VARCHAR
fabric-dw -w MyWorkspace tables load SalesWH dbo.raw --file raw.csv --create --all-varchar

# Replace the existing table (drop + recreate schema + load), skip confirmation
fabric-dw -w MyWorkspace tables load SalesWH dbo.sales --file data.parquet --create \
    --if-exists replace -y

# Auto-create; drop the table if the load fails (cleanup_on_failure)
fabric-dw -w MyWorkspace tables load SalesWH dbo.sales --file data.parquet --create \
    --cleanup-on-failure

# Load from a remote OneLake URL (no credential needed)
fabric-dw -w MyWorkspace tables load SalesWH dbo.orders \
    --url "https://onelake.dfs.fabric.microsoft.com/ws/lh.Lakehouse/Files/orders.parquet" \
    --format parquet

# Load from Azure Blob with SAS token
fabric-dw -w MyWorkspace tables load SalesWH dbo.events \
    --url "https://myaccount.blob.core.windows.net/data/events.csv" \
    --format csv --credential-type sas --secret "?sv=2021&..."
```

---

## fabric-dw schemas

Manage SQL schemas on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

> **List-source note** — no public REST API exists for enumerating warehouse schemas. `schemas list` falls back to TDS via `sys.schemas`, filtering out well-known system schemas (`sys`, `INFORMATION_SCHEMA`, `guest`, `db_*` fixed-role schemas). `dbo` is always included because it is user-writable.

> **SQL Analytics Endpoints** — `schemas list`, `schemas create`, and `schemas delete` all work on both Fabric Data Warehouses and SQL Analytics Endpoints. When `schemas delete --cascade` is used on a SQL Analytics Endpoint, views, stored procedures, and functions in the schema are dropped, but tables are **not** dropped (because `DROP TABLE` is a Warehouse-only operation on Fabric). If the schema contains tables, the final `DROP SCHEMA` will be rejected by the engine; remove the tables manually first or omit `--cascade` and drop the schema only after it is empty.

### schemas list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all user-defined schemas on a warehouse or SQL Analytics Endpoint. System schemas are excluded.

**Usage**

```shell
fabric-dw [-w WORKSPACE] schemas list [OPTIONS] [WAREHOUSE]
```

**Example**

```shell
fabric-dw -w MyWorkspace schemas list SalesWH
```

```
 name     principal_id
 ───────────────────────
 dbo      1
 sales    5
 staging  7
```

### schemas create

**Targets:** Data Warehouse · SQL Analytics Endpoint

Create a new SQL schema on a warehouse.

**Usage**

```shell
fabric-dw [-w WORKSPACE] schemas create [OPTIONS] [WAREHOUSE] NAME
```

**Example**

```shell
fabric-dw -w MyWorkspace schemas create SalesWH reporting
```

### schemas delete

**Targets:** Data Warehouse · SQL Analytics Endpoint

Drop a schema from a warehouse. You will be asked to confirm unless `--yes` is passed.

Pass `--cascade` to also drop all tables and views inside the schema before dropping the schema itself. **This is a destructive, irreversible operation.**

**Usage**

```shell
fabric-dw [-w WORKSPACE] schemas delete [OPTIONS] [WAREHOUSE] NAME
```

| Option | Description |
| --- | --- |
| `--cascade` | Drop all tables and views in the schema first. **WARNING: permanently deletes all contained objects and data.** |

**Example**

```shell
# Drop an empty schema
fabric-dw -w MyWorkspace --yes schemas delete SalesWH staging

# Drop a schema and all its tables/views
fabric-dw -w MyWorkspace --yes schemas delete SalesWH staging --cascade
```

---

## fabric-dw snapshots

Manage Microsoft Fabric Data Warehouse snapshots.

### snapshots list

**Targets:** Data Warehouse only

List all snapshots for a warehouse.

**Synopsis**

```
fabric-dw [-w WORKSPACE] snapshots list [WAREHOUSE]
```

**Example**

```shell
fabric-dw -w MyWorkspace snapshots list SalesWH
```

```
 displayName      id           createdTime
 ---------------- ------------ ---------------------
 snap-2026-06-01  d1e2...      2026-06-01T00:00:00Z
```

---

### snapshots create

**Targets:** Data Warehouse only

Create a new snapshot for a warehouse. Optionally pin it to a specific point in time.

**Synopsis**

```
fabric-dw [-w WORKSPACE] snapshots create [OPTIONS] [WAREHOUSE] NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional description. |
| `--snapshot-dt TEXT` | Optional snapshot datetime (ISO 8601, UTC). Defaults to the current timestamp. |

**Example**

```shell
fabric-dw -w MyWorkspace snapshots create SalesWH snap-2026-06-08 \
  --snapshot-dt 2026-06-08T00:00:00Z
```

---

### snapshots rename

**Targets:** Data Warehouse only

Rename a snapshot and optionally update its description.

**Synopsis**

```
fabric-dw [-w WORKSPACE] snapshots rename [OPTIONS] SNAPSHOT NEW_NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional new description. |

**Example**

```shell
fabric-dw -w MyWorkspace snapshots rename snap-2026-06-01 snap-june-2026
```

---

### snapshots delete

**Targets:** Data Warehouse only

Delete a snapshot. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] snapshots delete SNAPSHOT
```

**Example**

```shell
fabric-dw -w MyWorkspace --yes snapshots delete snap-old
```

---

### snapshots roll

**Targets:** Data Warehouse only

Roll a snapshot on a warehouse to a new timestamp. `SNAPSHOT_NAME` must be the display name of the snapshot database. The warehouse and workspace are resolved via the usual precedence rules.

**Synopsis**

```
fabric-dw [-w WORKSPACE] snapshots roll [OPTIONS] [WAREHOUSE] SNAPSHOT_NAME
```

| Option | Description |
| --- | --- |
| `--at TEXT` | Target datetime (ISO 8601, UTC). Defaults to `CURRENT_TIMESTAMP`. |

**Example**

```shell
fabric-dw -w MyWorkspace snapshots roll SalesWH snap-june-2026 \
  --at 2026-06-08T12:00:00Z
```

---

## fabric-dw sql-pools

!!! warning "Beta / preview feature"
    Manages workspace SQL Pools (currently in preview; the API may change before GA).  Callers must hold the **workspace admin role**.

Manage custom SQL Pools at the workspace level with sub-resource commands that mirror the Azure CLI style.

### sql-pools get

**Targets:** Workspace (not item-specific)

Fetch the full SQL Pools configuration (enabled flag + pool list) for a workspace.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-pools get
```

**Example**

```shell
fabric-dw -w MyWorkspace sql-pools get
```

---

### sql-pools list

**Targets:** Workspace (not item-specific)

List all SQL pools in a workspace.

When no custom SQL pools are defined, Fabric Data Warehouse uses the default
(autonomous) workload management instead: the SQL analytics endpoint compute is
split evenly (50/50) into two isolated pools, `SELECT` (read/analytics queries)
and `NON-SELECT` (DML/DDL/ETL/ingestion statements). In that case this command
reports the default pools rather than printing an empty list. The default split
is documented in
[Workload management](https://learn.microsoft.com/fabric/data-warehouse/workload-management#compute-pool-isolation)
and [Custom SQL pools](https://learn.microsoft.com/fabric/data-warehouse/custom-sql-pools).

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-pools list
```

**Example**

```shell
fabric-dw -w MyWorkspace sql-pools list
```

**Output**

When custom pools exist, `--json` returns the array of custom pool objects (as
before). When none are defined, `--json` returns an object that stays honest
about there being no custom pools:

```json
{
  "customSQLPools": [],
  "default_workload_active": true,
  "default_pools": [
    {"name": "SELECT", "maxResourcePercentage": 50, "isDefault": true, "description": "Handles SELECT (read/analytics) queries."},
    {"name": "NON-SELECT", "maxResourcePercentage": 50, "isDefault": true, "description": "Handles non-SELECT (DML/DDL/ETL/ingestion) statements."}
  ]
}
```

---

### sql-pools show

**Targets:** Workspace (not item-specific)

Show details for a single SQL pool.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-pools show --name POOL
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Pool name to show. (required) |

**Example**

```shell
fabric-dw -w MyWorkspace sql-pools show --name ETL
```

---

### sql-pools create

**Targets:** Workspace (not item-specific)

Add a new SQL pool to a workspace.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-pools create [OPTIONS]
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
fabric-dw -w MyWorkspace sql-pools create \
  --name ETL \
  --max-percent 30 \
  --no-optimize-for-reads \
  --classifier-type "Application Name" \
  --classifier-value "ETL" \
  --classifier-value "Load"
```

---

### sql-pools update

**Targets:** Workspace (not item-specific)

Update an existing SQL pool. Only the flags you provide are changed; all other fields are preserved.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-pools update [OPTIONS]
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
fabric-dw -w MyWorkspace sql-pools update --name ETL --max-percent 40
```

---

### sql-pools delete

**Targets:** Workspace (not item-specific)

Remove a SQL pool from a workspace. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-pools delete [OPTIONS]
```

| Option | Description |
| --- | --- |
| `--name TEXT` | Pool name to delete. (required) |
| `--yes` | Skip confirmation prompt. |

**Example**

```shell
fabric-dw -w MyWorkspace --yes sql-pools delete --name ETL
```

---

### sql-pools enable

**Targets:** Workspace (not item-specific)

Enable custom SQL Pools for a workspace. Preserves the existing pool configuration.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-pools enable
```

**Example**

```shell
fabric-dw -w MyWorkspace sql-pools enable
```

---

### sql-pools disable

**Targets:** Workspace (not item-specific)

Disable custom SQL Pools for a workspace without deleting pool definitions. Re-enabling with `sql-pools enable` restores the previously saved configuration.

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-pools disable
```

**Example**

```shell
fabric-dw -w MyWorkspace sql-pools disable
```

---

### sql-pools insights

**Targets:** Data Warehouse · SQL Analytics Endpoint

List SQL pool insight events from `queryinsights.sql_pool_insights`. Supports optional time-range filtering with `--since` and `--until` (ISO-8601 strings). The `--limit` option caps the number of rows returned (default: 100, max: 10 000).

**Synopsis**

```
fabric-dw [-w WORKSPACE] sql-pools insights [OPTIONS] [WAREHOUSE]
```

| Option | Description | Default |
| --- | --- | --- |
| `--limit INTEGER` | Maximum rows to return (1–10 000). | `100` |
| `--since ISO8601` | Return rows with timestamp >= this value. | — |
| `--until ISO8601` | Return rows with timestamp <= this value. | — |

**Example**

```shell
fabric-dw -w MyWorkspace sql-pools insights SalesWH
```

---

## fabric-dw statistics

Manage user-defined statistics on Fabric Data Warehouses and read their details on SQL Analytics Endpoints.

> **Note:** Only **single-column, histogram-based** statistics can be created or updated (Fabric limitation). Multi-column statistics are not supported.
> DDL operations (`create`, `update`, `delete`) require a **Data Warehouse** — they are rejected client-side on SQL Analytics Endpoints. `list` and `show` work on both item kinds.

### statistics list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List statistics on an item.

```
fabric-dw [-w WORKSPACE] statistics list [ITEM] [OPTIONS]
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
fabric-dw [-w WORKSPACE] statistics show [ITEM] QUALIFIED_TABLE STAT_NAME [OPTIONS]
```

`QUALIFIED_TABLE` must be a dot-separated qualified name, e.g. `dbo.sales`.

| Option | Description | Default |
| --- | --- | --- |
| `--histogram` | Show only the histogram steps (skip header and density vector). | off |

### statistics create

**Targets:** Data Warehouse only

Create a new single-column statistic.

```
fabric-dw [-w WORKSPACE] statistics create [ITEM] --table schema.table --column COL --name NAME [OPTIONS]
```

| Option | Description | Default |
| --- | --- | --- |
| `--table schema.table` | Qualified table name (required). | — |
| `--column COL` | Column name to build the statistic on (required). Single column only. | — |
| `--name NAME` | Statistic name (required). | — |
| `--fullscan` | Use `WITH FULLSCAN` (default). Mutually exclusive with `--sample-percent`. | on |
| `--sample-percent N` | Sample `N`% of the table (1–100). Overrides `--fullscan`. | — |

### statistics update

**Targets:** Data Warehouse only

Update an existing statistic via `UPDATE STATISTICS`.

```
fabric-dw [-w WORKSPACE] statistics update [ITEM] QUALIFIED_TABLE STAT_NAME [OPTIONS]
```

| Option | Description | Default |
| --- | --- | --- |
| `--fullscan` | Use `WITH FULLSCAN` (default). | on |
| `--sample-percent N` | Sample `N`% of the table (1–100). Overrides `--fullscan`. | — |

### statistics delete

**Targets:** Data Warehouse only

Drop a statistic via `DROP STATISTICS`. Prompts for confirmation unless `--yes` is passed.

```
fabric-dw [-w WORKSPACE] statistics delete [ITEM] QUALIFIED_TABLE STAT_NAME
```

---

## fabric-dw cache

Manage the local name-to-UUID lookup cache. `fabric-dw` caches workspace and item name-to-GUID mappings to avoid repeated API round-trips. Use these commands if you rename items outside the CLI or need to force a fresh lookup.

### cache clear

**Targets:** Workspace (not item-specific)

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

## fabric-dw dbt

Scaffold a [dbt](https://docs.getdbt.com/) project pre-wired to a Microsoft Fabric Data Warehouse using the [dbt-fabric](https://docs.getdbt.com/docs/core/connect-data-platform/fabric-setup) adapter.

No dbt installation is required to run these commands — `fabric-dw` generates all project files itself. A `requirements.txt` inside the scaffolded project lists the required pip packages (`dbt-core`, `dbt-fabric`) so you can install them in a separate environment when you are ready to run dbt.

### dbt init

**Targets:** Data Warehouse

Scaffold a new dbt project directory connected to a Fabric Data Warehouse. The command creates the folder, writes `dbt_project.yml`, `profiles.yml`, `requirements.txt`, `.gitignore`, standard dbt model directories, a sample model, and a README. If `git` is on your PATH and the target folder is not already a git repository, `git init` is run automatically.

> **Security note** — when `--auth sp` (Service Principal) is used, `profiles.yml` emits Jinja2 `env_var()` placeholders (`{{ env_var('AZURE_TENANT_ID') }}` etc.) instead of literal secrets. You must set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET` in your environment before running dbt.

**Usage**

```shell
fabric-dw [-w WORKSPACE] dbt init [OPTIONS] [ITEM] FOLDER
```

**Arguments**

| Argument | Description |
| --- | --- |
| `ITEM` | Name or ID of the Fabric Data Warehouse item (optional if set via `fabric-dw config set warehouse`). |
| `FOLDER` | Path to the folder to create. Must not exist (unless `--force` is passed). |

**Options**

| Option | Default | Description |
| --- | --- | --- |
| `--project-name TEXT` | derived from `ITEM` name | dbt project name (sanitised: lowercase, non-alphanumeric chars replaced with `_`). |
| `--profile-name TEXT` | same as `--project-name` | dbt profile name written into `profiles.yml` and `dbt_project.yml`. |
| `--schema TEXT` | `dbo` | Default target schema for dbt models. |
| `--target TEXT` | `dev` | dbt target name inside the profile. |
| `--threads INTEGER RANGE` | `4` | Number of dbt threads (1–64). |
| `--auth [auto\|CLI\|ServicePrincipal\|interactive\|sp]` | derived from active credential mode | Authentication method. `interactive` is an alias for `CLI`; `sp` is an alias for `ServicePrincipal`. |
| `--profiles-dir [project\|home]` | `project` | Where to write `profiles.yml`. `project` writes it next to `dbt_project.yml`; `home` merges it into `~/.dbt/profiles.yml` (backs up existing file first). |
| `--with-sources` | off | Introspect the live warehouse and generate a `_sources.yml` file listing all schemas and tables. |
| `--force` | off | Overwrite an existing non-empty directory. |

**Examples**

```shell
# Minimal — uses configured default workspace and warehouse
fabric-dw dbt init SalesWH ./my_dbt_project

# Explicit workspace via -w
fabric-dw -w MyWorkspace dbt init SalesWH ./my_dbt_project

# Service Principal auth; write profiles.yml to ~/.dbt/
fabric-dw -w MyWorkspace dbt init SalesWH ./sales_dbt \
  --auth sp --profiles-dir home

# Scaffold with live source introspection (auto-generates _sources.yml)
fabric-dw -w MyWorkspace dbt init SalesWH ./sales_dbt --with-sources

# Force-overwrite an existing folder
fabric-dw -w MyWorkspace dbt init SalesWH ./sales_dbt --force
```

**Scaffolded layout**

```
<FOLDER>/
├── .gitignore
├── README.md
├── dbt_project.yml
├── profiles.yml          # only when --profiles-dir project (default)
├── requirements.txt      # pip install -r requirements.txt
├── models/
│   ├── staging/
│   │   └── _sources.yml  # placeholder, or real entries with --with-sources
│   └── my_first_model.sql
├── seeds/
├── snapshots/
├── tests/
├── macros/
└── analyses/
```

---

## fabric-dw completion

Manage shell completion scripts. See [Shell Completion](completion.md) for full installation details.

### completion install

**Targets:** Workspace (not item-specific)

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
