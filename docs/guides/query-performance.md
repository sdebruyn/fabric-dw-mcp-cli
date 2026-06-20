---
title: Query performance
---

# Investigating and improving query performance

A task-oriented walkthrough for using `fabric-dw` (binary alias **`fdw`**) to find and fix slow queries on a Microsoft Fabric Data Warehouse or SQL Analytics Endpoint. It threads a single loop — **investigate → diagnose → improve → verify** — across the `queries`, `sql`, `statistics`, `sql-pools`, and `settings` command groups, and grounds each step in Microsoft Learn guidance.

**Targets:** Data Warehouse · SQL Analytics Endpoint

!!! tip "Driving `fabric-dw` from an AI assistant?"

    The [`query-optimizer`](../skills.md#query-optimizer) Agent Skill automates exactly this single-query workflow — capture the plan, pull history, audit statistics, inspect clustering, then propose or apply fixes. This page is the human-facing version of the same loop, followed by hand at the terminal. For the **warehouse-wide** angle (hotspots across every query, SQL-pool pressure, caching health) see the [`warehouse-performance`](../skills.md#warehouse-performance) skill and its companion human guide tracked in [#433](https://github.com/sdebruyn/fabric-dw-mcp-cli/issues/433).

---

## Overview & when to use this guide

Reach for this guide when a query, report, or dashboard is slow and you want a repeatable method to find out *why* and make it faster. The loop is:

1. **Investigate** — see what is running now and what ran recently (`queries running`, `queries connections`, `queries history`, `queries sessions`).
2. **Diagnose** — rank the worst offenders and read the root cause (`queries long-running`, `queries frequent`, `sql-pools insights`, `sql plan`, `statistics list`/`show`).
3. **Improve** — apply a fix (refresh statistics, rewrite the query, toggle result-set caching, tune SQL pools).
4. **Verify** — re-run and compare in `queries history` using a query label, ignoring the cold-start first run.

Every command works both from the CLI and from the MCP server; the MCP tool that backs each command is listed alongside it, and the full mapping is collected in [MCP equivalents](#mcp-equivalents) at the end.

!!! note "Name-or-GUID resolution"

    All `workspace`, `warehouse`, and `endpoint`/`item` arguments accept either the display name or the GUID — the resolver translates and caches the mapping locally. See [Name-or-GUID resolution](../concepts.md#name-or-guid-resolution). `fdw cache clear` (MCP `clear_cache`) only flushes that local name→GUID lookup cache; it is **unrelated** to query or result-set caching.

Throughout, the workspace comes from the global `-w`/`--workspace` option (or `FABRIC_DW_DEFAULT_WORKSPACE`, or the configured default; see [Selecting a workspace](../concepts.md#selecting-a-workspace)). The warehouse or endpoint is an **optional positional** `[WAREHOUSE]`/`[ITEM]` that falls back to the configured default — there is no positional `<workspace>` argument.

---

## Prerequisites & permissions

| You want to… | You need |
| --- | --- |
| Query `queryinsights.*` views (`history`, `sessions`, `long-running`, `frequent`, `sql-pools insights`) | **Contributor** or above on the workspace |
| Read live DMVs (`queries running`, `queries connections`) | **Admin** |
| Kill a runaway session (`queries kill`) | **Admin** on the service |
| Create/update/delete statistics, toggle result-set caching | **Data Warehouse** target (DDL is rejected on SQL Analytics Endpoints) |

The `queryinsights` views are documented in [Query insights in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/query-insights?WT.mc_id=MVP_310840) and the underlying system view permissions in [`queryinsights.exec_requests_history`](https://learn.microsoft.com/sql/relational-databases/system-views/queryinsights-exec-requests-history-transact-sql?view=fabric&WT.mc_id=MVP_310840). For where these tools sit in the wider observability story (Capacity Metrics app, DW Monitor, Query Insights, DMVs), see the [monitoring overview](https://learn.microsoft.com/fabric/data-warehouse/monitoring-overview?WT.mc_id=MVP_310840).

!!! warning "New-warehouse delay"

    The `queryinsights.*` views are **not populated immediately** on a brand-new warehouse and completed queries lag behind real time. If a view comes back empty on a fresh item, give it time and re-run. See [Caveats & limits](#caveats--limits).

---

## Step 1 — Investigate live activity

Start with what is happening **right now**.

```shell
# Queries executing this instant (live DMVs — Admin)
fdw -w MyWorkspace queries running SalesWH

# Active SQL connections/sessions, including idle ones not shown above
fdw -w MyWorkspace queries connections SalesWH
```

`queries running` (MCP [`list_running_queries`](../commands/queries.md#list_running_queries)) returns each in-flight query with its `session_id`, `start_time`, `total_elapsed_time`, `login_name`, and `query_text` — enough to spot a single session that is dominating the warehouse. `queries connections` (MCP [`list_connections`](../commands/queries.md#list_connections)) reads `sys.dm_exec_connections` and surfaces lower-level connection detail (including idle connections) that the running view omits.

If a live query is clearly runaway and you have Admin rights, jump straight to [Step 9 — Mitigate runaway queries](#step-9-mitigate-runaway-queries). Otherwise, move on to the history views to build a picture over time.

---

## Step 2 — Review query history

Live DMVs only show the present moment. To see what *happened*, read the per-request and per-session history from Query Insights.

```shell
# Completed requests in a time window (queryinsights.exec_requests_history)
fdw -w MyWorkspace queries history SalesWH --limit 50 --since 2026-06-01T00:00:00

# Completed sessions (queryinsights.exec_sessions_history)
fdw -w MyWorkspace queries sessions SalesWH --since 2026-06-01T00:00:00 --until 2026-06-08T00:00:00
```

Both commands accept `--limit` (1–10 000, default 100), `--since`, and `--until` (ISO-8601). `queries history` (MCP [`list_request_history`](../commands/queries.md#list_request_history)) is the raw per-request feed — one row per execution — carrying the columns you will lean on for diagnosis: `total_elapsed_time_ms`, `data_scanned_remote_storage_mb`, `data_scanned_memory_mb`, `data_scanned_disk_mb`, `result_cache_hit`, `query_hash`, and `label`. `queries sessions` (MCP [`list_session_history`](../commands/queries.md#list_session_history)) rolls the same activity up per session.

These are the **raw** insight views. Step 3 uses the **aggregated** views that rank queries server-side. The distinction matters — see [aggregated vs raw views](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#query-metadata-views).

---

## Step 3 — Find the worst offenders

Rather than eyeballing raw history, let Fabric rank the queries for you.

```shell
# Ranked by median total elapsed time DESC
fdw -w MyWorkspace queries long-running SalesWH

# High-frequency queries (biggest aggregate cost), ranked by run count DESC
fdw -w MyWorkspace queries frequent SalesWH --limit 20

# Capacity-pressure / read-optimization signals (beta/preview)
fdw -w MyWorkspace sql-pools insights SalesWH
```

- `queries long-running` (MCP [`list_long_running_queries`](../commands/queries.md#list_long_running_queries)) reads `queryinsights.long_running_queries`, ordered server-side by `median_total_elapsed_time_ms` DESC. "Top N" is simply `--limit N`.
- `queries frequent` (MCP [`list_frequent_queries`](../commands/queries.md#list_frequent_queries)) reads `queryinsights.frequently_run_queries`, ordered by run count DESC. A query that is individually fast but runs thousands of times can be a bigger cost driver than a single slow report.
- `sql-pools insights` (MCP [`list_sql_pool_insights`](../commands/sql-pools.md#list_sql_pool_insights)) reads `queryinsights.sql_pool_insights` for capacity-pressure and read-optimization events. Treat persistent pressure here as a signal that the worst offenders are competing for compute — the [`warehouse-performance`](../skills.md#warehouse-performance) skill covers SQL-pool tuning in depth.

Microsoft Learn shows how to combine these views to find top consumers — top CPU via `allocated_cpu_time_ms`, most-data-scanned via `data_scanned_remote_storage_mb`, and frequent/long-running by command substring: see the [Query insights examples](https://learn.microsoft.com/fabric/data-warehouse/query-insights?WT.mc_id=MVP_310840#examples) and the [performance guidelines](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#query-performance).

Pick one query from the top of these lists and carry it through the rest of the loop.

---

## Step 4 — Capture & read the execution plan

With a specific slow query in hand, capture its **estimated** execution plan. `sql plan` (MCP [`get_query_plan`](../commands/sql.md#get_query_plan)) retrieves the estimated `SHOWPLAN_XML` **without executing the query** — so it is safe to plan even DDL/DML text, and reading it costs nothing.

```shell
# Default: a colour-coded Rich operator tree in the terminal
fdw -w MyWorkspace sql plan SalesWH -q "SELECT * FROM dbo.Sales s JOIN dbo.Customer c ON s.cust_id = c.id WHERE c.region = 'EU'"

# Raw SHOWPLAN_XML to stdout (pipe-friendly)
fdw -w MyWorkspace sql plan SalesWH -q "..." --raw

# Save a .sqlplan that opens graphically in SSMS / Azure Data Studio
fdw -w MyWorkspace sql plan SalesWH -q "..." -o plan.sqlplan

# Parsed operator tree as JSON (root --json flag)
fdw -w MyWorkspace --json sql plan SalesWH -q "..."

# Diagram exports
fdw -w MyWorkspace sql plan SalesWH -q "..." --format mermaid          # paste into mermaid.live or GitHub Markdown
fdw -w MyWorkspace sql plan SalesWH -q "..." --format svg  -o plan.svg  # requires Graphviz
fdw -w MyWorkspace sql plan SalesWH -q "..." --format html -o plan.html # self-contained, offline-viewable
```

Representation (`--raw`/`--xml`, root `--json`, `--format mermaid|dot|svg|html`) and destination (`-o FILE`) are orthogonal — see [`sql plan`](../commands/sql.md#sql-plan) for the full matrix. Via MCP, `get_query_plan` takes a `format` parameter (`xml` | `tree` | `json` | `mermaid`); the artifact formats **SVG, HTML, and DOT are CLI-only** because the MCP server never writes files.

**What to look for in the plan:**

- **Costly operators** — a high-percentage **Hash Join**, **Sort**, or **Spool** is where the time goes.
- **`CONVERT_IMPLICIT` in a predicate** — a type mismatch between a column and a literal/parameter, which forces a conversion and can defeat a clean scan. Fix it with [data-type parity](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#data-type-optimization) in Step 7.
- **Large estimated row counts** — wildly off estimates usually mean missing or stale statistics; chase that in Step 6.
- **"Non-scalable operation" warnings** — flagged in the plan per the [performance guidelines](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#query-performance).

Microsoft recommends reading `SHOWPLAN_XML` to understand a query and reserving query hints (e.g. `OPTION (FORCE DISTRIBUTED PLAN)`) as a last resort. Clustering inspection (`get_cluster_columns` / `set_cluster_columns`) is a deeper-dive lever beyond this guide's scope — the [`query-optimizer`](../skills.md#query-optimizer) skill handles it, and the cluster columns concept is covered in the [performance guidelines](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#query-performance).

---

## Step 5 — Diagnose root cause from history columns

Cross-reference the plan with the numbers from `queries history`. The key columns:

| Column | What it tells you |
| --- | --- |
| `total_elapsed_time_ms` | Wall-clock duration of the request. |
| `data_scanned_remote_storage_mb` | Data pulled from OneLake. **Non-zero ⇒ cold start** (first run after the data left cache). |
| `data_scanned_memory_mb` / `data_scanned_disk_mb` | Data served from the in-memory / local disk cache — the warm path. |
| `result_cache_hit` | Whether the result was served from the result-set cache (no compute at all). |
| `query_hash` | Stable fingerprint of the query shape — use it to find every execution of "the same query". |
| `label` | Your own tag from `OPTION (LABEL='...')` — use it to track one specific query over time. |

!!! tip "Don't judge a query by its first run"

    A **non-zero `data_scanned_remote_storage_mb`** means the data was fetched from OneLake — a cold start. The first run after a cache eviction is *not* representative; **subsequent runs are**. Always discard the cold-start run before drawing conclusions. — [Query performance guidelines](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#query-performance)

Caching in Fabric is **automatic and not user-clearable** — the in-memory and disk caches are transparent, which is why you interpret cache columns rather than manage them. See [Caching in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/caching?WT.mc_id=MVP_310840).

To drill into a specific shape, run ad-hoc T-SQL with `sql exec` (MCP [`execute_sql`](../commands/sql.md#execute_sql)) and filter `exec_requests_history` by `query_hash` or `label`:

```shell
fdw -w MyWorkspace sql exec SalesWH -q "SELECT TOP 20 submit_time, total_elapsed_time_ms, data_scanned_remote_storage_mb, result_cache_hit FROM queryinsights.exec_requests_history WHERE label = 'sales-eu-report' ORDER BY submit_time DESC"
```

Using a **query label** to track and compare one query across executions is a first-class Microsoft pattern — see [Query labeling](https://learn.microsoft.com/fabric/data-warehouse/query-label?WT.mc_id=MVP_310840).

---

## Step 6 — Check & fix statistics

Bad row estimates in the plan (Step 4) almost always trace back to missing or stale statistics. Accurate statistics are **critical** to good plans.

```shell
# List statistics on the referenced tables (spot missing ones)
fdw -w MyWorkspace statistics list SalesWH --schema dbo --table Customer

# Inspect a statistic's histogram and staleness
fdw -w MyWorkspace statistics show SalesWH dbo.Customer _WA_Sys_00000003_12345678 --histogram
```

`statistics list` (MCP [`list_statistics`](../commands/statistics.md#list_statistics)) shows both user-created and Fabric's automatic `_WA_Sys_*` statistics; `statistics show` (MCP [`show_statistics`](../commands/statistics.md#show_statistics)) runs `DBCC SHOW_STATISTICS` to return the header, density vector, and histogram so you can judge staleness. Verifying the auto-created stats and inspecting them via `DBCC SHOW_STATISTICS` is the documented diagnostic — see [Automatic statistics at query time](https://learn.microsoft.com/fabric/data-warehouse/statistics?WT.mc_id=MVP_310840#automatic-statistics-at-query).

When a column in a `GROUP BY` / `ORDER BY` / `WHERE` / `JOIN` lacks a good statistic, create or refresh one. **DDL targets a Data Warehouse only — these commands are rejected on SQL Analytics Endpoints**, and Fabric supports **single-column** statistics only.

```shell
# Create a single-column histogram statistic (--name is required)
fdw -w MyWorkspace statistics create SalesWH --table dbo.Customer --column region --name stat_customer_region --fullscan

# Refresh after a large data change
fdw -w MyWorkspace statistics update SalesWH dbo.Customer stat_customer_region --fullscan

# Drop a redundant user statistic (prompts unless -y)
fdw -w MyWorkspace statistics delete SalesWH dbo.Customer stat_customer_region
```

These map to MCP [`create_statistics`](../commands/statistics.md#create_statistics), [`update_statistics`](../commands/statistics.md#update_statistics), and [`delete_statistics`](../commands/statistics.md#delete_statistics). All three are **mutating** MCP tools gated behind the write-guard (`delete_statistics` is additionally destructive-gated via `FABRIC_MCP_ALLOW_DESTRUCTIVE`); they do **not** pop a confirmation dialog, so an AI assistant must ask before calling them.

Microsoft recommends creating/updating single-column histogram statistics **during maintenance windows** — so user `SELECT`s do not pay for synchronous auto-stats creation — focusing on columns used in `GROUP BY` / `ORDER BY` / `WHERE` / `JOIN`, and refreshing after big data changes. See [Statistics in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/statistics?WT.mc_id=MVP_310840).

---

## Step 7 — Improve the query

With statistics healthy, turn to the query text itself. Common, high-leverage rewrites:

- **Return less data** — avoid `SELECT *`, project only the columns you need, and apply `WHERE` filters *before* joins. Filtering on a low-cardinality column early shrinks everything downstream. — [Query performance guidelines](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#query-performance)
- **Match data types in comparisons** — eliminate the `CONVERT_IMPLICIT` you spotted in Step 4 by comparing like types, and use the smallest sufficient string lengths. — [Data type optimization](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#data-type-optimization)
- **Tag the query with a label** so you can find every run of the improved version in `queries history`, and apply hints only as a last resort.

Re-run the improved query through `sql exec` with a label (and any hint):

```shell
fdw -w MyWorkspace sql exec SalesWH -q "SELECT s.id, s.amount FROM dbo.Sales s JOIN dbo.Customer c ON s.cust_id = c.id WHERE c.region = 'EU' OPTION (LABEL='sales-eu-report')"
```

---

## Step 8 — Verify the improvement

Confirm the fix actually helped, on the **warm** path.

1. Run the labelled query **twice** — the first run may be a cold start (non-zero `data_scanned_remote_storage_mb`); ignore it.
2. Pull the history filtered by your label and compare `total_elapsed_time_ms` against the original:

   ```shell
   fdw -w MyWorkspace queries history SalesWH --limit 10 --since 2026-06-08T00:00:00
   ```

   Or filter precisely with `sql exec` on `label` / `query_hash` as in Step 5.
3. If the query (or an identical one) is run repeatedly, consider enabling **result-set caching** so identical re-runs skip compute entirely:

   ```shell
   fdw -w MyWorkspace settings show SalesWH                      # check current state
   fdw -w MyWorkspace settings result-set-caching SalesWH on     # enable (DW only)
   ```

   `settings result-set-caching` maps to MCP [`set_result_set_caching`](../commands/settings.md#set_result_set_caching); `settings show` maps to [`get_warehouse_settings`](../commands/settings.md#get_warehouse_settings).

!!! note "Result-set caching in Fabric is ON by default"

    Unlike the Synapse model (OFF by default), **Fabric Data Warehouse enables result-set caching ON by default** per item. A single query can still opt out with `OPTION (USE HINT('DISABLE_RESULT_SET_CACHE'))`. The toggle is **DW-only** — SQL Analytics Endpoints are rejected. See [Result set caching](https://learn.microsoft.com/fabric/data-warehouse/result-set-caching?WT.mc_id=MVP_310840) (preview known issue: [aka.ms/fabricdwrscki](https://aka.ms/fabricdwrscki)).

A `result_cache_hit = true` in `queries history` on the second and later runs confirms the cache is doing its job.

---

## Step 9 — Mitigate runaway queries

When a live query is clearly out of control and you have Admin rights, terminate its session. `KILL` is **Admin-only** and **destructive** — `queries kill` prompts unless `-y` is passed.

```shell
# Find the runaway session
fdw -w MyWorkspace queries running SalesWH

# Terminate it (prompts unless -y)
fdw -w MyWorkspace --yes queries kill SalesWH 42
```

`queries kill` maps to MCP [`kill_session`](../commands/queries.md#kill_session), which is write-gated. Identifying and killing a long-running query via DMVs is the documented incident path — see [Monitor using DMVs](https://learn.microsoft.com/fabric/data-warehouse/monitor-using-dmv?WT.mc_id=MVP_310840).

---

## Worked example — end to end

A nightly EU sales report is slow. The full loop:

```shell
# 1. INVESTIGATE — confirm it is not a one-off live spike
fdw -w MyWorkspace queries running SalesWH

# 2. DIAGNOSE — it tops the long-running list
fdw -w MyWorkspace queries long-running SalesWH --limit 10

# 3. CAPTURE THE PLAN — a Hash Join with a huge estimated row count on Customer,
#    plus CONVERT_IMPLICIT on c.region
fdw -w MyWorkspace sql plan SalesWH -q "SELECT * FROM dbo.Sales s JOIN dbo.Customer c ON s.cust_id = c.id WHERE c.region = N'EU'"

# 4. READ HISTORY — the slow runs all show non-zero data_scanned_remote_storage_mb
#    on the first execution only (cold start); warm runs are still slow
fdw -w MyWorkspace queries history SalesWH --limit 20 --since 2026-06-01T00:00:00

# 5. CHECK STATISTICS — no user statistic on Customer.region
fdw -w MyWorkspace statistics list SalesWH --schema dbo --table Customer

# 6. IMPROVE — create the missing statistic, and fix the type mismatch (region is varchar, not nvarchar)
fdw -w MyWorkspace statistics create SalesWH --table dbo.Customer --column region --name stat_customer_region --fullscan
fdw -w MyWorkspace sql exec SalesWH -q "SELECT s.id, s.amount FROM dbo.Sales s JOIN dbo.Customer c ON s.cust_id = c.id WHERE c.region = 'EU' OPTION (LABEL='sales-eu-report')"

# 7. VERIFY — run twice (discard the cold-start run), then compare warm runs by label
fdw -w MyWorkspace sql exec SalesWH -q "SELECT s.id, s.amount FROM dbo.Sales s JOIN dbo.Customer c ON s.cust_id = c.id WHERE c.region = 'EU' OPTION (LABEL='sales-eu-report')"
fdw -w MyWorkspace sql exec SalesWH -q "SELECT TOP 10 submit_time, total_elapsed_time_ms, data_scanned_remote_storage_mb, result_cache_hit FROM queryinsights.exec_requests_history WHERE label = 'sales-eu-report' ORDER BY submit_time DESC"
```

Median elapsed time on the warm runs drops once the statistic gives the optimizer an accurate estimate and the implicit conversion is gone. Because the report runs identically every night, enabling `settings result-set-caching SalesWH on` lets unchanged re-runs return instantly.

---

## MCP equivalents

The same loop, driven by an AI assistant. Every step above maps to one MCP tool — the [`query-optimizer`](../skills.md#query-optimizer) skill chains exactly these.

| Step | CLI | MCP tool |
| --- | --- | --- |
| Investigate (live) | `queries running` | [`list_running_queries`](../commands/queries.md#list_running_queries) |
| Investigate (live) | `queries connections` | [`list_connections`](../commands/queries.md#list_connections) |
| Investigate (history) | `queries history` | [`list_request_history`](../commands/queries.md#list_request_history) |
| Investigate (history) | `queries sessions` | [`list_session_history`](../commands/queries.md#list_session_history) |
| Diagnose | `queries long-running` | [`list_long_running_queries`](../commands/queries.md#list_long_running_queries) |
| Diagnose | `queries frequent` | [`list_frequent_queries`](../commands/queries.md#list_frequent_queries) |
| Diagnose | `sql-pools insights` | [`list_sql_pool_insights`](../commands/sql-pools.md#list_sql_pool_insights) |
| Diagnose (plan) | `sql plan` | [`get_query_plan`](../commands/sql.md#get_query_plan) (`format`: `xml` \| `tree` \| `json` \| `mermaid`) |
| Diagnose (ad-hoc) | `sql exec` | [`execute_sql`](../commands/sql.md#execute_sql) |
| Diagnose (stats) | `statistics list` | [`list_statistics`](../commands/statistics.md#list_statistics) |
| Diagnose (stats) | `statistics show` | [`show_statistics`](../commands/statistics.md#show_statistics) |
| Improve (stats) | `statistics create` | [`create_statistics`](../commands/statistics.md#create_statistics) |
| Improve (stats) | `statistics update` | [`update_statistics`](../commands/statistics.md#update_statistics) |
| Improve (stats) | `statistics delete` | [`delete_statistics`](../commands/statistics.md#delete_statistics) |
| Improve (cache) | `settings result-set-caching` | [`set_result_set_caching`](../commands/settings.md#set_result_set_caching) |
| Verify (cache) | `settings show` | [`get_warehouse_settings`](../commands/settings.md#get_warehouse_settings) |
| Mitigate | `queries kill` | [`kill_session`](../commands/queries.md#kill_session) |

The mutating MCP tools (`create_statistics`, `update_statistics`, `delete_statistics`, `set_result_set_caching`, `kill_session`) are gated behind the server's write-guard (`FABRIC_MCP_ALLOW_WRITES` / `FABRIC_MCP_ALLOW_DESTRUCTIVE`) and do **not** raise a user dialog, so an AI assistant must confirm with you before calling them.

---

## Caveats & limits

- **Query Insights lag.** Completed queries do not appear in `queryinsights.*` instantly, and the views are empty for a period on a new warehouse. Re-run if a view comes back empty.
- **Aggregated vs raw views.** `exec_requests_history` / `exec_sessions_history` are raw (one row per execution, ~30-day window); `long_running_queries` / `frequently_run_queries` are aggregated and ranked. See [query metadata views](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840#query-metadata-views).
- **Permissions.** `queryinsights.*` needs Contributor+; live DMVs and `KILL` need Admin. See [`queryinsights.exec_requests_history`](https://learn.microsoft.com/sql/relational-databases/system-views/queryinsights-exec-requests-history-transact-sql?view=fabric&WT.mc_id=MVP_310840) and [Monitor using DMVs](https://learn.microsoft.com/fabric/data-warehouse/monitor-using-dmv?WT.mc_id=MVP_310840).
- **DW-only DDL.** Creating/updating/deleting statistics and toggling result-set caching require a **Data Warehouse**; SQL Analytics Endpoints reject them. Reads (`statistics list`/`show`, `settings show`, all `queries`/`sql-pools insights` views) work on both surfaces.
- **Single-column statistics only.** Fabric supports single-column, histogram-based statistics; multi-column statistics are not available.
- **`fdw cache clear` ≠ query caching.** It only clears the local name→GUID lookup cache; it has no effect on query, result-set, or cache-cooldown behaviour.

### Gaps (no command exists — do not invent one)

- **Result-set cache cooldown has no API** ([#595](https://github.com/sdebruyn/fabric-dw-mcp-cli/issues/595)). This is distinct from the result-set-caching toggle and from the local lookup cache.
- **Statement-type SQL-pool routing does not exist** ([#596](https://github.com/sdebruyn/fabric-dw-mcp-cli/issues/596)). The only routing key is the application-name classifier; do not imply statement-type routing.
- **Data clustering** (`get_cluster_columns` / `set_cluster_columns`) is a deeper-dive topic handled by the [`query-optimizer`](../skills.md#query-optimizer) skill rather than expanded here.

---

## See also

- [Queries](../commands/queries.md) · [Running SQL](../commands/sql.md) · [Statistics](../commands/statistics.md) · [Settings](../commands/settings.md) · [SQL Pools](../commands/sql-pools.md)
- [Agent Skills](../skills.md) — `query-optimizer` (single-query automation of this loop) and `warehouse-performance` (warehouse-wide).
- [Performance guidelines for Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/guidelines-warehouse-performance?WT.mc_id=MVP_310840) · [Query insights](https://learn.microsoft.com/fabric/data-warehouse/query-insights?WT.mc_id=MVP_310840) · [Monitoring overview](https://learn.microsoft.com/fabric/data-warehouse/monitoring-overview?WT.mc_id=MVP_310840)
</content>
</invoke>
