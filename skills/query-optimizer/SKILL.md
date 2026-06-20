---
name: query-optimizer
description: >
  Analyzes query performance on a Fabric Data Warehouse: captures the estimated
  execution plan, inspects query insights history, identifies costly operators and
  missing or stale statistics, inspects data-clustering columns, and proposes or
  applies optimizations. Use when the user asks to "optimize a query", "why is my
  query slow", "check query performance", "analyze execution plan", "missing
  statistics", "fix clustering", "re-cluster table", or any variant of diagnosing
  slow Fabric DW SQL.
user-invocable: true
---

# Query Performance Analysis & Optimization

Analyzes and optimizes a query on a Fabric Data Warehouse using the fabric-dw MCP tools.

## Inputs

Gather these from the user (via `$ARGUMENTS` or natural language) before starting:

- **workspace** — workspace name or GUID
- **warehouse** — warehouse name or GUID (must be a Fabric Data Warehouse, not a SQL Analytics Endpoint, for clustering steps)
- **query** — the SQL query text to analyze (or a historical query ID / text snippet to look up)

If the warehouse is a SQL Analytics Endpoint, skip steps 8, 9, 11 (clustering), and 13 (`set_cluster_columns`).

## Workflow

### Step 1 — Capture the estimated execution plan

Call `get_query_plan` with the query text. This returns a SHOWPLAN_XML document without executing the query.

To visualize the plan, use the CLI:

```bash
fdw sql plan "<query>" --format html   # open in browser for visual inspection
fdw sql plan "<query>" --format svg    # embed or save as image
```

### Step 2 — Parse the plan XML

Analyze the returned SHOWPLAN_XML for:

- **Costly operators**: Hash Join, Sort, nested-loop Scan on large row estimates, Spool
- **Residual predicates**: predicates that cannot be applied early (non-SARGable)
- **Missing-index hints** embedded in the plan
- **Data skew warnings** or large estimated vs. actual row count mismatches (if available)
- **Type mismatch conversions**: e.g. `CONVERT_IMPLICIT` on a column used in a predicate

### Step 3 — Pull recent history

Use `list_request_history` (filter by SQL text substring if supported) and `list_long_running_queries` to see whether this query pattern has a history of slow executions. Note elapsed time and status.

### Step 4 — List statistics on referenced tables

For every table referenced in the query, call `list_statistics` to enumerate existing statistics objects. Note which columns lack statistics.

### Step 5 — Inspect histogram for stale or missing stats

For any statistic that appears potentially stale or absent, call `show_statistics` to examine its header (last_updated timestamp, row count, rows sampled) and histogram. Flag statistics whose sample rate or update date suggests staleness — this is heuristic; flag rather than assert.

### Step 6 — Fetch column metadata

For each table referenced in the query, call `get_table_columns` to retrieve: column name, formatted data type (e.g. `VARCHAR(50)`, `DECIMAL(18,2)`), nullable flag, identity flag, computed flag.

### Step 7 — Refine plan analysis with column metadata

Use column types and nullable flags to identify:

- **Implicit type conversions**: e.g. a VARCHAR predicate applied to an NVARCHAR column — causes CONVERT_IMPLICIT, preventing index use
- **Non-nullable columns used in outer joins** — potential over-broad join semantics
- **Computed columns that block predicate pushdown** — the optimizer cannot always push a predicate through a computed expression

### Step 8 — Inspect current clustering columns (DW only)

Call `get_cluster_columns` for each table (skip for SQL Analytics Endpoints). Returns the ordered list of CLUSTER BY columns, or an empty list if no clustering is defined.

### Step 9 — Analyze clustering fit

Compare current clustering columns (and their data types from step 6) against the WHERE predicates, JOIN keys, and aggregation patterns in the query plan. A good clustering candidate:

- Appears frequently in range or equality predicates
- Has relatively low cardinality
- Is non-nullable
- Uses a fixed-width type (INT, DATE, SMALLINT) rather than variable-length (VARCHAR)

Note whether a table lacks clustering entirely but would benefit from it.

### Step 10 — Present findings

Summarize all findings in a structured report:

1. **Expensive operators** — operator name, estimated subtree cost, row estimates
2. **Missing or stale statistics** — table, column(s), issue description
3. **Type-mismatch anti-patterns** — column, expected type vs. predicate type
4. **Non-SARGable predicates** — expression and rewrite suggestion
5. **Clustering assessment** — current columns vs. recommended columns per table

### Step 11 — Propose optimizations

For each finding, propose a concrete action:

- **Statistics**: CREATE or UPDATE with FULLSCAN (or SAMPLE n PERCENT for large tables)
- **Query rewrite**: explicit CAST to avoid CONVERT_IMPLICIT, predicate restructuring for SARGability
- **Clustering**: recommend clustering column(s) with rationale from column metadata

### Step 12 — Apply statistics changes (optional, confirm first)

> **Requires explicit user confirmation before proceeding.** This is a write operation.

If the user approves, apply statistics changes using `create_statistics` (for absent stats) or `update_statistics` (for stale stats). For large tables, prefer `update_statistics` with a SAMPLE percentage rather than FULLSCAN to limit execution time.

### Step 13 — Re-cluster the table (optional, confirm first — destructive)

> **Requires explicit user confirmation before proceeding.**
>
> **Warning:** `set_cluster_columns` performs a full transactional CTAS-swap. This is a complete copy of the table and may take significant time on large tables. During the operation the table is briefly unavailable. Dependent views and stored procedures that reference the table may need to be refreshed or re-validated after the rename completes. Only proceed after the user explicitly acknowledges these consequences.

If the user approves and acknowledges the above, call `set_cluster_columns` with the recommended clustering columns.

## Guardrails

- Steps 12 and 13 each require separate, explicit user confirmation
- Steps 8, 9, 11, and 13 apply only to Fabric Data Warehouses — skip them for SQL Analytics Endpoints
- Stale-statistics detection is heuristic; always frame it as a flag, not a certainty
- Do not run the original query against the warehouse during analysis — `get_query_plan` obtains the plan without execution
