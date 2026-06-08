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

**Synopsis**

```
fabric-dw sql-endpoints refresh WORKSPACE ENDPOINT
```

**Example**

```shell
fabric-dw sql-endpoints refresh MyWorkspace MyLakehouseEP
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
