---
name: warehouse-performance
description: >
  Investigates the performance of an ENTIRE Fabric Data Warehouse (warehouse-wide,
  not a single query): surfaces long-running and frequent queries, reads SQL pool
  resource-pressure insights, audits statistics health, checks result-set caching,
  reviews and tunes SQL pool configuration, and produces a prioritized findings +
  recommendations report. Use when the user asks to "investigate warehouse
  performance", "why is my warehouse slow", "tune sql pools", "find expensive
  queries", "find the most frequent queries", "check sql pool pressure", or "enable
  result set caching". For diagnosing or rewriting ONE specific query (execution
  plan, missing statistics on its tables, clustering), use /query-optimizer instead.
user-invocable: true
---

# Warehouse-Wide Performance Investigation & Tuning

Investigates the performance of an entire Fabric Data Warehouse using the fabric-dw CLI and MCP tools, then proposes (and optionally applies) tuning actions. This is the warehouse-wide counterpart to `query-optimizer`, which diagnoses a single query.

## Inputs

Gather these from the user (via `$ARGUMENTS` or natural language) before starting:

- **workspace** — workspace name or GUID
- **warehouse** — warehouse name or GUID. May be a Fabric Data Warehouse (DWH) or a SQL Analytics Endpoint; some steps are DWH-only (noted per step)
- **window** *(optional)* — a time range for the query-hotspot views (`--since` / `--until`, ISO-8601)

The CLI binary is `fdw` (also installed as `fabric-dw`). All MCP tool names below are exposed by the fabric-dw MCP server.

Read-only steps (1–4 reads) are safe to run unprompted. Every mutating action (step 3 toggle, step 4 create/update/delete/enable/disable) is gated behind explicit user confirmation — see **Guardrails**.

## When NOT to use this skill

If the user wants to diagnose or rewrite ONE specific query — execution plan, statistics on that query's tables, or clustering — hand off to **`/query-optimizer`**. This skill stops at warehouse-wide hotspots and emits each query's `query_hash` so the user can feed a specific query to `query-optimizer`.

## Workflow

### Step 1 — Find query hotspots (DWH + SQL Analytics Endpoint)

Identify the warehouse's most expensive and most frequent workloads. These views read Query Insights and work on both DWH and SQL Analytics Endpoints.

```bash
fdw queries long-running <workspace>/<warehouse> --limit 20   # server-ordered by median elapsed time DESC
fdw queries frequent     <workspace>/<warehouse> --limit 20   # server-ordered by run count DESC
fdw sql-pools insights    <workspace>/<warehouse>             # resource-pressure events (beta/preview, see step 4)
```

MCP equivalents: `list_long_running_queries`, `list_frequent_queries`, `list_sql_pool_insights`.

- There is **no `--order-by` flag** — each view is ordered server-side (`long-running` by median total elapsed time DESC; `frequent` by number of runs DESC). "Top N" is simply `--limit N` on the already-sorted view. `--limit` defaults to `100` and is capped at `10000`.
- `--since` / `--until` (ISO-8601) optionally bound the time window.
- Each row carries a **`query_hash`** — record it for the synthesis report so the user can hand a specific query to `/query-optimizer`.

Optional drill-down once a suspect appears:

```bash
fdw queries history  <workspace>/<warehouse> --limit 50   # individual executions, server-ordered by submit time DESC
fdw queries sessions <workspace>/<warehouse> --limit 50   # session history, server-ordered by session start time DESC
```

MCP equivalents: `list_request_history`, `list_session_history`.

**Graceful degradation:** if a Query Insights view returns empty, is unavailable, or fails with permission denied, note it (Query Insights needs **Contributor or higher** on the workspace) and continue with the remaining steps — do **not** abort the whole investigation.

### Step 2 — Audit statistics health (read both; DDL is DWH-only)

Missing or stale statistics are a common cause of poor plan choices across many queries. Reading statistics works on both DWH and SQL Analytics Endpoints; only the DDL fixes (create/update/delete) require a DWH.

```bash
fdw statistics list <workspace>/<warehouse>                                       # enumerate statistics objects
fdw statistics show <workspace>/<warehouse> <schema.table> <stat_name> --histogram # header + histogram steps
```

MCP equivalents: `list_statistics`, `show_statistics`.

- Flag columns that lack statistics, and statistics whose last-updated date / sample rate suggests staleness. This is a **heuristic — flag it, never assert** that a statistic is definitely stale.
- Fabric supports **single-column statistics only**. Defer the actual `create_statistics` / `update_statistics` (DWH-only DDL) to `query-optimizer` for a specific query, or surface them as recommendations here for the user to confirm (see **Guardrails**).

### Step 3 — Check result-set caching (read both; toggle DWH-only)

Result-set caching can cut elapsed time for repeated identical queries.

```bash
fdw settings show <workspace>/<warehouse>   # reads current settings (DWH and SQL Analytics Endpoint)
```

MCP equivalent: `get_warehouse_settings`.

If caching is off and the `frequent` view (step 1) shows repeated identical queries, **recommend** enabling it. Toggling is **mutating, DWH-only**, and **must be confirmed first** (see **Guardrails**):

```bash
fdw settings result-set-caching <workspace>/<warehouse> on    # ALTER DATABASE ... SET RESULT_SET_CACHING ON
```

MCP equivalent: `set_result_set_caching`. SQL Analytics Endpoints are **rejected with an error** for this toggle.

> **Do not confuse three distinct things:**
> 1. **Result-set caching** — `settings result-set-caching` / `set_result_set_caching` (this step).
> 2. **The local name→UUID lookup cache** — `fdw cache clear` / `clear_cache`. This only erases cached workspace/item name-to-UUID mappings on the client; it has **nothing** to do with query result caching. Never suggest `cache clear` to influence query performance.
> 3. **Cache cooldown** — see the observe-only gap below.

### Step 4 — Review and tune SQL pool configuration (workspace-scoped, beta/preview, admin)

> **SQL Pools is a beta / preview feature.** It is **workspace-scoped** (configuration applies to the workspace, not a single warehouse) and requires the **workspace admin** role. The underlying API may change before general availability. State this to the user once, up front.

Read the current configuration first:

```bash
fdw sql-pools get  <workspace>   # autonomous configuration (enabled/disabled, defaults)
fdw sql-pools list <workspace>   # custom pools, or the default 50/50 baseline if none exist
fdw sql-pools show <workspace> --name <pool>   # one pool's detail
```

MCP equivalents: `get_sql_pools_configuration`, `list_sql_pools`, `get_sql_pool`.

When no custom pools exist, `list` shows the **default autonomous 50/50 split** — one pool at 50% `maxResourcePercentage` for SELECT (read/analytics) and one at 50% for non-SELECT (DML/DDL/ETL/ingestion). Treat this as the baseline.

If step 1's `sql-pools insights` shows resource pressure concentrated on one workload type, the actionable levers are (all **mutating** — confirm first, see **Guardrails**):

```bash
# Carve out a read-optimized pool routed by application name (the only routing key available — see gaps below)
fdw sql-pools create <workspace> --name reads-pool --max-percent 60 --optimize-for-reads \
  --classifier-type "Application Name" --classifier-value "PowerBI" --classifier-value "dbt"

fdw sql-pools update  <workspace> --name reads-pool --max-percent 70   # adjust a lever on an existing pool
fdw sql-pools delete  <workspace> --name reads-pool                     # remove a custom pool
fdw sql-pools enable  <workspace>   # enable custom SQL pools (preserves pool config; default state with no custom pools is autonomous WLM)
fdw sql-pools disable <workspace>   # disable custom SQL pools (preserves pool config; re-enabling restores it)
```

MCP equivalents: `create_sql_pool`, `update_sql_pool`, `delete_sql_pool`, `enable_sql_pools`, `disable_sql_pools`.

Tuning levers: `--max-percent` (the pool's `maxResourcePercentage`, 1–100), `--optimize-for-reads/--no-optimize-for-reads`, and the **application-name classifier** (`--classifier-type "Application Name"` with one or more repeatable `--classifier-value`).

### Step 5 — Synthesize a prioritized report

Pull the steps together into a single report the user can act on:

1. **Top cost drivers** — from `long-running` and `frequent`, with each query's `query_hash`. Point the user to `/query-optimizer <workspace>/<warehouse>` (plus the query text) for per-query diagnosis.
2. **Resource-pressure events** — what `sql-pools insights` surfaced, and which workload (SELECT vs non-SELECT) is under pressure.
3. **Statistics health** — tables/columns flagged as missing or likely-stale (heuristic).
4. **Caching recommendation** — whether result-set caching is on, and whether the frequent-query pattern justifies enabling it (DWH-only).
5. **SQL pool tuning suggestions** — concrete `create`/`update` levers vs. the 50/50 baseline (beta, workspace admin).

List every proposed mutating action explicitly and ask the user which (if any) to apply before touching anything.

## Observe-only gaps (do NOT fake these)

There is no API for the following. Document them honestly; never present them as available capabilities.

- **Statement-type routing** — there is **no API** to route a SQL pool by statement type (e.g. "send all SELECTs here"). The **only** routing key is the application-name classifier shown in step 4. Recommend application-name classifiers; do **not** claim statement-type routing exists. Tracked in issue **#596**.
- **Cache cooldown** — there is **no `ALTER DATABASE` / REST option** to configure a result-set cache cooldown. Do not invent a command for it. This is distinct from the result-set caching toggle (step 3) and from the local lookup cache (`cache clear`). Tracked in issue **#595**.

## Guardrails

- **Reads are free; writes require explicit confirmation.** Steps 1–4's *read* commands (`queries *`, `sql-pools insights/get/list/show`, `statistics list/show`, `settings show`) are safe to run unprompted. Every *mutating* action — `settings result-set-caching` (toggle), statistics DDL (`create`/`update`/`delete`), and `sql-pools create/update/delete/enable/disable` — must be confirmed by the user first. The MCP server enforces its own write-guard (`FABRIC_MCP_ALLOW_WRITES` / `FABRIC_MCP_ALLOW_DESTRUCTIVE`), but it does **not** pop a user dialog — asking is the agent's responsibility. List the exact action and ask before proceeding.
- **DWH vs SQL Analytics Endpoint.** `queries *`, `statistics list`/`show`, `settings show`, and the `sql-pools` reads work on both. The **result-set-caching toggle** and **statistics DDL** are **DWH-only** — SQL Analytics Endpoints are rejected with an error; surface that as a limitation rather than retrying.
- **SQL Pools is beta/preview, workspace-scoped, and requires workspace admin** — state this before recommending any `sql-pools` change, and note the API may change before GA.
- **Graceful degradation.** If Query Insights views are unavailable or permission-denied (needs Contributor+), note it and continue — do not abort the investigation.
- **Heuristics stay heuristics.** Frame missing/stale statistics and pressure interpretations as flags to investigate, not certainties.
- **Don't confuse the caches.** Result-set caching, the local name→UUID lookup cache (`cache clear`), and cache cooldown (#595) are three different things — keep them distinct in every recommendation.
- **Hand single queries to `/query-optimizer`.** This skill diagnoses warehouse-wide; per-query plan/statistics/clustering analysis belongs to `query-optimizer`.
