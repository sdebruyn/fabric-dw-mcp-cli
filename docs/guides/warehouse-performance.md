---
title: Warehouse performance
---

# Investigating & improving warehouse performance

This guide is a repeatable **monitor → diagnose → tune/scale** playbook for answering
*"the warehouse feels slow / we're being throttled — what now?"* using `fabric-dw`. It
operates at the **warehouse / capacity (compute) level** — the whole-warehouse story — not
the tuning of a single query.

> **For single-query tuning** (execution plans, missing statistics on one query, clustering),
> see the [`query-optimizer` Agent Skill](../skills.md#query-optimizer) and the
> [`fdw sql plan`](../commands/sql.md) command instead. This guide is about the warehouse and
> the capacity it runs on.

**Audience.** Fabric workspace admins, data-platform / DWH operators, and SREs running Fabric
Data Warehouses or SQL Analytics Endpoints from the CLI or via an MCP-connected assistant. It
assumes you are comfortable with T-SQL and Fabric workspaces, but **not** with Fabric's internal
compute model — that model is explained below, because it changes which lever is the right one.

Every command appears in **both** its CLI form (`fdw …`) and its MCP tool name, so you can drive
this workflow by hand or through an AI assistant. CLI subcommand names and MCP tool names mostly
match; where they diverge (the `queries` group), both are given.

---

## Relationship to the `warehouse-performance` Agent Skill

`fabric-dw` ships a [`warehouse-performance` Agent Skill](../skills.md#warehouse-performance)
(`/fabric-dw:warehouse-performance`) that automates **exactly** this monitor → diagnose → tune
workflow: it surfaces long-running and frequent queries plus SQL pool insights, audits statistics
health, checks result-set caching, reviews and tunes SQL pool configuration, and produces a
prioritized findings report — with every mutating action gated on your confirmation.

The guide and the skill are **complementary, not duplicates**:

- **This guide is the human narrative** — the *why*, and *how Fabric serverless compute works* —
  for an operator driving `fabric-dw` (or any SQL/REST client) by hand and reasoning about whether
  the right lever is statistics, caching, workload isolation, or a capacity change.
- **The skill is the AI-assistant automation** — the *do it for me* path: it runs the same read
  commands, synthesizes the report, and proposes the mutating actions for you to confirm via an
  MCP-connected assistant.

If you have an MCP-connected assistant configured, you can let the skill drive the steps below for
you. Either way, the underlying commands and their guarantees are identical.

---

## How Fabric Warehouse compute actually works

Before reaching for a lever, it helps to know what you can and cannot tune. Fabric's Warehouse
compute model is different from a provisioned MPP warehouse, and that difference decides the fix.

### Serverless, autonomously scaling compute

A Fabric **Warehouse** and a **SQL Analytics Endpoint** share the same *serverless, autonomously
scaling* distributed compute engine. Backend nodes are provisioned in seconds, scaling is an online
operation, and **there is no compute to provision or resize** — scaling happens autonomously as the
workload demands it. You do not pick a node count or a "DWU" tier per warehouse.

See [Workload management in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/workload-management?WT.mc_id=MVP_310840).

### Capacity, bursting, and smoothing

Performance and cost headroom come from the **Fabric capacity SKU** the workspace runs on, measured
in **Capacity Units (CU)**:

- **Bursting** — a query can temporarily use *more* CU than the SKU baseline (up to 12× baseline)
  so a single heavy operation finishes fast. The available burst is governed by a per-SKU scale
  factor (`CU / duration / Baseline CU`); F2–F2048 each have a guardrail.
  See [Burstable capacity](https://learn.microsoft.com/fabric/data-warehouse/burstable-capacity?WT.mc_id=MVP_310840).
- **Smoothing** — CU accounting is *spread over time* (roughly 5 min for interactive queries,
  up to 24 h for background operations) rather than billed at the instant of execution. Smoothing
  does **not** change how long a query takes; it only spreads the *accounting* so short spikes don't
  immediately exhaust the capacity.
  See [Smoothing and throttling](https://learn.microsoft.com/fabric/data-warehouse/compute-capacity-smoothing-throttling?WT.mc_id=MVP_310840)
  and the [Fabric throttling policy](https://learn.microsoft.com/fabric/enterprise/throttling?WT.mc_id=MVP_310840)
  (overage, carryforward, burndown, and the `CapacityLimitExceeded` error).

The practical consequence: when the warehouse "feels slow across the board" or you see
`CapacityLimitExceeded` / *"query rejected due to current capacity constraints"*, the problem is
usually **sustained CU demand exceeding the SKU** (a capacity decision) or **a few expensive queries
consuming the shared engine** (a query/statistics/caching decision) — *not* an under-provisioned
warehouse you can resize.

### The default SELECT vs non-SELECT pool split

Even with no custom configuration, the engine isolates work into two pools, split **50/50**:

- a **`SELECT`** pool for read/analytics queries, and
- a **`NON-SELECT`** pool for DML/DDL/ETL/ingestion statements.

This keeps ingestion from starving interactive reads (and vice-versa) by default. You can change
this baseline with **custom SQL pools** (preview) — covered in [Step 3](#step-3-tune-apply-the-right-lever).
See [Workload management — compute pool isolation](https://learn.microsoft.com/fabric/data-warehouse/workload-management?WT.mc_id=MVP_310840#compute-pool-isolation).

---

## When to use this guide

Reach for this playbook when you see warehouse-wide symptoms, not a single slow query:

- queries are slow **across the board**, not just one report;
- you see `CapacityLimitExceeded` or *"query rejected due to current capacity constraints"*;
- you are getting **throttling** alerts on the capacity;
- ingestion and interactive workloads appear to be **contending** for the same compute.

If instead a *specific* query regressed, start with the [`query-optimizer` skill](../skills.md#query-optimizer).

---

## Set your defaults

Store the workspace and warehouse once so you do not repeat them on every command:

```shell
fdw config set workspace MyWorkspace
fdw config set warehouse SalesWH
```

The rest of this guide assumes these defaults are set, so the examples omit `-w MyWorkspace` and drop the warehouse positional where it is optional. Any command still accepts an explicit `-w`/`--workspace` or a positional `[WAREHOUSE]`/`[ITEM]` to override them. Commands that take a trailing required argument (such as `queries kill … SESSION_ID` or `settings result-set-caching … on|off`) keep the warehouse positional so the remaining arguments stay unambiguous. The workspace-wide commands (`warehouses list`, `workspaces list-capacities`) take no per-warehouse target. See [Configuration & defaults](../commands/config.md).

---

## Step 1 — Monitor: is there a problem, and where?

Establish what exists, what is running right now, and whether the capacity itself is the constraint.

### Inventory the warehouses

Confirm which items exist and which kind each is (Data Warehouse vs SQL Analytics Endpoint — they
behave differently for several tuning levers below).

| Action | CLI | MCP tool |
| --- | --- | --- |
| List warehouses (optionally across all workspaces) | [`fdw warehouses list`](../commands/warehouses.md#warehouses-list) (`-A/--all-workspaces`, `--warehouses-only`) | `list_warehouses` (`all_workspaces`) |
| Inspect one warehouse | [`fdw warehouses get`](../commands/warehouses.md#warehouses-get) | `get_warehouse` |

```shell
# Everything visible, across every workspace
fdw warehouses list --all-workspaces
```

### Look at live load

What is executing right now, and how many connections are open?

| Action | CLI | MCP tool |
| --- | --- | --- |
| Currently running queries | [`fdw queries running`](../commands/queries.md#queries-running) | `list_running_queries` |
| Active SQL connections (incl. idle) | [`fdw queries connections`](../commands/queries.md#queries-connections) | `list_connections` |

```shell
fdw queries running
fdw queries connections
```

!!! note "CLI vs MCP names diverge here"

    The `queries` CLI subcommands map to differently-named MCP tools:
    `queries running` → `list_running_queries`, `queries connections` → `list_connections`,
    `queries history` → `list_request_history`, and `queries sessions` → `list_session_history`.

### Read SQL pool pressure

`sql-pools insights` reads `queryinsights.sql_pool_insights` — the warehouse's own resource-pressure
signal, including `is_pool_under_pressure` and `max_resource_percentage`. This is the single most
direct *"is compute the bottleneck?"* read.

| Action | CLI | MCP tool |
| --- | --- | --- |
| SQL pool insight events | [`fdw sql-pools insights`](../commands/sql-pools.md#sql-pools-insights) | `list_sql_pool_insights` |

```shell
fdw sql-pools insights --since 2026-06-13T00:00:00
```

!!! warning "Preview feature"

    `sql-pools insights` (and the wider custom SQL pools feature it reports on) is a **preview /
    beta** capability and may change. See
    [Custom SQL pools (preview)](https://learn.microsoft.com/fabric/data-warehouse/custom-sql-pools?WT.mc_id=MVP_310840)
    and the [`queryinsights.sql_pool_insights` column reference](https://learn.microsoft.com/sql/relational-databases/system-views/queryinsights-sql-pool-insights-transact-sql?view=fabric&WT.mc_id=MVP_310840).

### Check the capacity state

Is the capacity even active, and what SKU is it? A paused capacity, or an undersized SKU, explains
warehouse-wide slowness on its own.

| Action | CLI | MCP tool |
| --- | --- | --- |
| List capacities with `sku` and `state` | [`fdw workspaces list-capacities`](../commands/workspaces.md#workspaces-list-capacities) | `list_capacities` |

```shell
fdw workspaces list-capacities
```

```
 id            displayName    sku   region        state
 ------------- -------------- ----- ------------- ------
 ab12cd34-...  MyCapacity     F64   West Europe   Active
```

`fabric-dw` also reads `GET /v1/capacities` internally so that an all-workspaces scan
(`warehouses list -A`) automatically skips paused/inactive capacities.

---

## Step 2 — Diagnose: what is consuming compute, and why?

Once you know there's pressure, find the cost drivers. Fabric's **Query Insights** views retain
about 30 days of history and aggregate repeated statements by `query_hash`. All of the commands
below accept `--since` / `--until` / `--limit` to scope a window (`--limit` defaults to 100, capped
at 10 000). There is **no `--order-by` flag** — each view is ordered server-side, so *"top N"* is
simply `--limit N`.

See [Query Insights](https://learn.microsoft.com/fabric/data-warehouse/query-insights?WT.mc_id=MVP_310840)
and [Monitor Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/monitoring-overview?WT.mc_id=MVP_310840).

### Find the worst offenders

| Action | CLI | MCP tool |
| --- | --- | --- |
| Long-running queries (by median elapsed, DESC) | [`fdw queries long-running`](../commands/queries.md#queries-long-running) | `list_long_running_queries` |
| Frequently-run queries (by run count, DESC) | [`fdw queries frequent`](../commands/queries.md#queries-frequent) | `list_frequent_queries` |

```shell
# Top 20 slowest queries over the last week
fdw queries long-running --limit 20

# Top 20 most-frequently-run queries — a cheap query run 100k times is also a cost driver
fdw queries frequent --limit 20
```

A query that is individually fast but runs *constantly* can dominate CU usage just as much as one
slow query — check both lists.
See the [`queryinsights.long_running_queries` column reference](https://learn.microsoft.com/sql/relational-databases/system-views/queryinsights-long-running-queries-transact-sql?view=fabric&WT.mc_id=MVP_310840).

### Drill into completed requests and sessions

Per-request detail tells you *why* a query is expensive: CPU time, how much data it scanned, and
whether the result-set cache helped.

| Action | CLI | MCP tool |
| --- | --- | --- |
| Completed requests (`exec_requests_history`) | [`fdw queries history`](../commands/queries.md#queries-history) | `list_request_history` |
| Completed sessions (`exec_sessions_history`) | [`fdw queries sessions`](../commands/queries.md#queries-sessions) | `list_session_history` |

```shell
fdw queries history --limit 50 --since 2026-06-13T00:00:00
```

Useful fields in `queries history` include `allocated_cpu_time_ms`,
`data_scanned_remote_storage_mb` / `_memory_mb` / `_disk_mb`, `result_cache_hit`, `sql_pool_name`,
`program_name`, `query_hash`, and `label`.

!!! tip "Correlate across runs and tools"

    Use `query_hash` to aggregate every run of the same statement, and add
    `OPTION (LABEL = '...')` to your queries so they surface under a known `label` in the history.
    `program_name` / `sql_pool_name` tell you which application and pool a request landed in.
    For end-to-end traceability between the portal and these views, see
    [Observe Fabric Data Warehouse utilization](https://learn.microsoft.com/fabric/data-warehouse/how-to-observe-utilization?WT.mc_id=MVP_310840)
    (Operation Id ↔ `distributed_statement_id`).

!!! note "Insights latency"

    Query Insights is populated asynchronously: a request you just ran may take a short while to
    appear in the history views. If a recent query is "missing", give it a moment and re-query.

### Check statistics health

Stale or missing statistics make the optimizer choose bad plans, which inflates CPU and data scanned
warehouse-wide. Audit whether the optimizer has good statistics on hot columns. Reads work on both
**Data Warehouses and SQL Analytics Endpoints**.

| Action | CLI | MCP tool |
| --- | --- | --- |
| List statistics (`--schema`, `--table`, `--user-only`, `--auto-only`) | [`fdw statistics list`](../commands/statistics.md#statistics-list) | `list_statistics` |
| Show one statistic's histogram | [`fdw statistics show … [--histogram]`](../commands/statistics.md#statistics-show) | `show_statistics` |

```shell
fdw statistics list --table dbo.sales --user-only
fdw statistics show SalesWH dbo.sales _stat_order_date --histogram   # warehouse positional kept — names follow
```

See [Statistics in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/statistics?WT.mc_id=MVP_310840)
for when automatic statistics suffice and when to create/update manually, and the umbrella
[Performance guidelines](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840).

---

## Step 3 — Tune: apply the right lever

Now act on the diagnosis. Levers are ordered from *cheapest/safest* to *most structural*.

!!! warning "MCP write guards"

    Every mutating MCP tool below is gated behind the server's write guard
    (`assert_writes_allowed`), and the destructive ones additionally behind `assert_destructive_allowed`
    (`FABRIC_MCP_ALLOW_DESTRUCTIVE=1`). In CLI, destructive commands prompt for confirmation unless
    you pass `--yes`. None of these are read-only — treat them as changes.

### Result-set caching (Data Warehouse only — the fast win)

If the diagnosis shows the same identical queries repeating with low `result_cache_hit`, enabling
result-set caching can cut elapsed time for repeated identical queries dramatically.

| Action | CLI | MCP tool |
| --- | --- | --- |
| Read current server-side settings | [`fdw settings show`](../commands/settings.md#settings-show) | `get_warehouse_settings` |
| Toggle result-set caching on/off | [`fdw settings result-set-caching … on\|off`](../commands/settings.md#settings-result-set-caching) | `set_result_set_caching` |

```shell
fdw settings show
fdw settings result-set-caching SalesWH on   # warehouse positional kept — on|off follows
```

`settings result-set-caching` runs `ALTER DATABASE CURRENT SET RESULT_SET_CACHING { ON | OFF }`.
The toggle is **Data-Warehouse-only** — SQL Analytics Endpoints are rejected with an error. (Note
that `settings show` itself works on both kinds; only the *toggle* is DW-only.)
See [Result set caching in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/result-set-caching?WT.mc_id=MVP_310840).

!!! note

    `settings retention --days N` (MCP `set_time_travel_retention`, also DW-only) lives in the same
    `settings` group but controls **time-travel retention**, not performance.

### Statistics tuning (Data Warehouse only — DDL)

If [Step 2](#check-statistics-health) found missing or stale statistics on hot columns, create or
refresh them. Fabric supports **single-column, histogram-based** statistics only.

| Action | CLI | MCP tool | Notes |
| --- | --- | --- | --- |
| Create a statistic | [`fdw statistics create --table --column --name [--fullscan \| --sample-percent]`](../commands/statistics.md#statistics-create) | `create_statistics` | write-guarded |
| Update a statistic | [`fdw statistics update … [--fullscan \| --sample-percent]`](../commands/statistics.md#statistics-update) | `update_statistics` | write-guarded |
| Drop a statistic | [`fdw statistics delete …`](../commands/statistics.md#statistics-delete) | `delete_statistics` | **destructive** |

```shell
fdw statistics create \
  --table dbo.sales --column order_date --name _stat_order_date --fullscan
```

The MCP equivalents **exist and are not read-only**: `create_statistics`, `update_statistics`, and
`delete_statistics` (the last flagged destructive) all run behind the MCP write/destructive guards.
SQL Analytics Endpoints are rejected for these DDL operations.

### Workload isolation via custom SQL pools (preview, workspace admin)

If [Step 2](#read-sql-pool-pressure) showed one workload (e.g. heavy ETL) starving interactive
reads, override the default 50/50 split with **custom SQL pools**: you cap each workload's
`maxResourcePercentage` and route work to a pool by an **application-name classifier**. These are
workspace-scoped and require the **workspace admin** role.

| Action | CLI | MCP tool | Notes |
| --- | --- | --- | --- |
| Read full configuration | [`fdw sql-pools get`](../commands/sql-pools.md#sql-pools-get) | `get_sql_pools_configuration` | read |
| List pools | [`fdw sql-pools list`](../commands/sql-pools.md#sql-pools-list) | `list_sql_pools` | read |
| Show one pool | [`fdw sql-pools show --name`](../commands/sql-pools.md#sql-pools-show) | `get_sql_pool` | read |
| Create a pool | [`fdw sql-pools create`](../commands/sql-pools.md#sql-pools-create) | `create_sql_pool` | write |
| Update a pool | [`fdw sql-pools update`](../commands/sql-pools.md#sql-pools-update) | `update_sql_pool` | write |
| Delete a pool | [`fdw sql-pools delete --name`](../commands/sql-pools.md#sql-pools-delete) | `delete_sql_pool` | **destructive** |
| Enable custom pools | [`fdw sql-pools enable`](../commands/sql-pools.md#sql-pools-enable) | `enable_sql_pools` | write |
| Disable custom pools | [`fdw sql-pools disable`](../commands/sql-pools.md#sql-pools-disable) | `disable_sql_pools` | write |

```shell
# Carve out an isolated ETL pool capped at 30% so it can't starve interactive reads
fdw sql-pools create \
  --name ETL \
  --max-percent 30 \
  --no-optimize-for-reads \
  --classifier-type "Application Name" \
  --classifier-value "ETL" \
  --classifier-value "Load"
```

!!! warning "Preview feature"

    Custom SQL pools are a **preview / beta** capability. When no custom pools exist, `sql-pools list`
    reports the default autonomous **50/50 split** (one `SELECT` pool, one `NON-SELECT` pool) rather
    than an empty list. The sum of every pool's `maxResourcePercentage` must be ≤ 100 and exactly one
    pool is the default. See
    [Custom SQL pools (preview)](https://learn.microsoft.com/fabric/data-warehouse/custom-sql-pools?WT.mc_id=MVP_310840)
    and [Configure custom SQL pools via REST API](https://learn.microsoft.com/fabric/data-warehouse/configure-custom-sql-pools-api?WT.mc_id=MVP_310840).

### Relieve live contention

If a single runaway session is blocking everything else, terminate it.

| Action | CLI | MCP tool |
| --- | --- | --- |
| Kill a session | [`fdw queries kill … <session_id>`](../commands/queries.md#queries-kill) | `kill_session` |

```shell
fdw --yes queries kill SalesWH 42   # warehouse positional kept — SESSION_ID follows
```

---

## Step 4 — Decide whether to scale

If the cost drivers are genuinely justified work — not a tuning problem — and you are still
throttled, the lever is the **capacity**, not the warehouse. Distinguish the two cases:

- **A design / tuning problem** — a handful of queries dominate CU, statistics are stale, or one
  workload is starving another. Fix it in [Step 2](#step-2-diagnose-what-is-consuming-compute-and-why) / [Step 3](#step-3-tune-apply-the-right-lever);
  scaling just pays more for the same waste.
- **Sustained, legitimate demand exceeding the SKU** — throttling persists even after tuning, and
  the [Capacity Metrics app](https://learn.microsoft.com/fabric/enterprise/metrics-app?WT.mc_id=MVP_310840)
  shows steady overage. This is a capacity decision.

For a real capacity decision you have two levers, only one of which `fabric-dw` performs:

| Lever | Tool support |
| --- | --- |
| **Move the workspace to another capacity** (load-balance across capacities) | [`fdw workspaces assign-capacity <workspace> --capacity-id <uuid>`](../commands/workspaces.md#workspaces-assign-capacity) · MCP `assign_workspace_to_capacity` |
| **Resize the capacity SKU** (e.g. F64 → F128) | **Out of CLI scope** — a capacity-admin action in the Fabric/Azure portal |

```shell
# Spread load by moving a workspace onto a different (less-loaded) capacity
fdw workspaces assign-capacity MyWorkspace \
  --capacity-id ab12cd34-ef56-7890-abcd-ef1234567890
```

`assign-capacity` **moves** the workspace; it does **not resize** a SKU. A SKU resize is a
capacity-admin action — see
[Scale your Fabric capacity](https://learn.microsoft.com/fabric/enterprise/scale-capacity?WT.mc_id=MVP_310840)
and [Evaluate and optimize your Fabric capacity](https://learn.microsoft.com/fabric/enterprise/optimize-capacity?WT.mc_id=MVP_310840)
for right-sizing and load-balancing guidance.

!!! note "Auditing is supporting, not diagnostic"

    The [`fdw audit`](../commands/audit.md) commands (`get` / `enable` / `set-retention` /
    `set-groups` / `add-group` / `remove-group`) provide traceability that *complements* performance
    work but is not itself a diagnosis tool. Enable auditing for accountability, not to find slow
    queries.

---

## Worked example

A copy-pasteable session that walks the whole loop on a warehouse that "feels slow". Or let the
[`warehouse-performance` skill](../skills.md#warehouse-performance) drive these same steps for you.

```shell
# 1. MONITOR — confirm the warehouse exists, check live load and pool pressure, check the SKU
fdw warehouses get
fdw queries running
fdw sql-pools insights --since 2026-06-13T00:00:00
fdw workspaces list-capacities

# 2. DIAGNOSE — find the worst offenders and inspect their CPU / data-scanned / cache hits
fdw queries long-running --limit 20
fdw queries frequent --limit 20
fdw queries history --limit 50 --since 2026-06-13T00:00:00
fdw statistics list --table dbo.sales --user-only

# 3. TUNE — apply the cheapest effective lever(s) for what you found
fdw settings result-set-caching SalesWH on          # repeated identical queries (on|off follows, so warehouse positional kept)
fdw statistics create \
  --table dbo.sales --column order_date --name _stat_order_date --fullscan   # stale/missing stats
fdw sql-pools create \
  --name ETL --max-percent 30 --no-optimize-for-reads \
  --classifier-type "Application Name" --classifier-value "ETL"     # ETL starving reads

# 4. RE-MEASURE — confirm the change helped before reaching for a capacity change
fdw queries long-running --limit 20
fdw sql-pools insights --since 2026-06-13T00:00:00
```

Only if throttling persists after re-measuring should you consider
[Step 4](#step-4-decide-whether-to-scale) — reassigning the workspace to another capacity, or
escalating a SKU resize to a capacity admin.

---

## Limits of this tool

`fabric-dw` covers the *read* and most of the *tune* levers, but a few things are deliberately out
of scope:

- **No capacity SKU resize.** `fabric-dw` reads capacity state, lists capacities, and can reassign a
  workspace to another capacity — but a SKU resize (e.g. F64 → F128) is a capacity-admin action in
  the Fabric/Azure portal.
- **No Capacity Metrics app.** For CU-usage and throttling trends over time, use the
  [Capacity Metrics app](https://learn.microsoft.com/fabric/enterprise/metrics-app?WT.mc_id=MVP_310840) —
  this tool does not replace it.
- **Custom SQL pools are preview** and may change.
- **Application-name classifier only.** The only SQL-pool routing key today is the application-name
  classifier; statement-type routing is observe-only with no API
  ([#596](https://github.com/sdebruyn/fabric-dw-mcp-cli/issues/596)).
- **No cache-cooldown control** for result-set caching
  ([#595](https://github.com/sdebruyn/fabric-dw-mcp-cli/issues/595)).

---

## See also

- [`warehouse-performance` Agent Skill](../skills.md#warehouse-performance) — the AI-assistant
  automation of this workflow.
- [`query-optimizer` Agent Skill](../skills.md#query-optimizer) — single-query tuning.
- Command pages: [Queries](../commands/queries.md) · [SQL Pools](../commands/sql-pools.md) ·
  [Settings](../commands/settings.md) · [Statistics](../commands/statistics.md) ·
  [Warehouses](../commands/warehouses.md) · [Workspaces](../commands/workspaces.md)
