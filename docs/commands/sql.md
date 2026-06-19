---
title: Running SQL
---

# Running SQL

SQL execution and query-plan capture against a Fabric Data Warehouse or SQL Analytics Endpoint.

**Targets:** Data Warehouse · SQL Analytics Endpoint

!!! warning "Breaking change (v0.x → current)"

    `fdw sql -q "…"` has been renamed to `fdw sql exec -q "…"`.  Update any scripts or aliases that use the old form.

---

## CLI

### sql exec

Execute a SQL statement or file against a warehouse or SQL Analytics Endpoint. Provide the query via `-q`/`--query` or `-f`/`--file` (not both). Multi-statement batches are supported; only the last result set is returned. DDL/DML statements return empty columns and rows.

!!! warning

    This command executes arbitrary SQL, including DDL and DML. Ensure you have the correct target before running destructive statements.

**Synopsis**

```
fdw [-w WORKSPACE] sql exec [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `-q` / `--query TEXT` | SQL statement or batch to execute inline. |
| `-f` / `--file PATH` | Path to a `.sql` file to execute. UTF-8 and UTF-8 BOM files are both supported. |

Output defaults to a Rich table (rows/columns). Pass `--json` on the root command to emit machine-readable JSON (`{"columns": [...], "rows": [...], "rowcount": N}`).

**Example**

```shell
# Inline query, Rich table output (default)
fdw -w MyWorkspace sql exec SalesWH -q "SELECT TOP 5 * FROM dbo.Sales"

# File input, JSON output
fdw -w MyWorkspace --json sql exec SalesWH -f ./queries/report.sql
```

```json
{"columns": ["id", "name"], "rows": [[1, "Alice"], [2, "Bob"]], "rowcount": 2}
```

---

### sql plan

Capture the **estimated** SHOWPLAN_XML execution plan for a SQL statement without executing it. The query is **not** run — only the plan is returned. This means DDL/DML query text is safe to plan without modifying any data.

By default the plan is rendered as a **Rich terminal tree**: each operator is shown with its physical/logical op name, estimated row count, cost percentage (colour-coded), and badges for parallel execution or warnings. For multi-statement batches, one tree is printed per statement.

The plan XML uses the standard namespace `http://schemas.microsoft.com/sqlserver/2004/07/showplan` and can be opened in SSMS, Azure Data Studio, or uploaded to [pastetheplan.com](https://www.pastetheplan.com) for visual analysis.

**Synopsis**

```
fdw [-w WORKSPACE] sql plan [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `-q` / `--query TEXT` | SQL statement to plan. |
| `-f` / `--file PATH` | Path to a `.sql` file to plan. |
| `-o` / `--output PATH` | Write the raw plan XML to this file (recommended extension: `.sqlplan`). Opens in SSMS / Azure Data Studio. |
| `--raw` / `--xml` | Print the raw SHOWPLAN XML to stdout instead of the Rich terminal tree. Useful for piping or inspection. |

Pass the root `--json` flag to emit the parsed operator tree as machine-readable JSON instead of the Rich tree.

**Example**

```shell
# Default: render a Rich terminal tree in the console
fdw -w MyWorkspace sql plan SalesWH -q "SELECT TOP 5 * FROM dbo.Sales"

# Save raw plan XML to file (opens in SSMS / Azure Data Studio)
fdw -w MyWorkspace sql plan SalesWH -q "SELECT TOP 5 * FROM dbo.Sales" -o plan.sqlplan

# Print raw SHOWPLAN XML to stdout (pipe-friendly)
fdw -w MyWorkspace sql plan SalesWH -q "SELECT TOP 5 * FROM dbo.Sales" --raw

# Emit the operator tree as JSON
fdw -w MyWorkspace --json sql plan SalesWH -q "SELECT TOP 5 * FROM dbo.Sales"
```

---

## MCP tools

### execute_sql

**Targets:** Data Warehouse · SQL Analytics Endpoint

Execute an arbitrary SQL statement or batch against a warehouse or SQL Analytics Endpoint.

!!! warning

    This tool executes arbitrary SQL, including DDL (DROP, ALTER, TRUNCATE) and DML (DELETE, UPDATE). Use only when the user explicitly requests data modification. Default to SELECT when the user's intent is read-only investigation.

Multi-statement batches are supported; only the **last** result set is returned. DDL/DML statements that produce no result set return `columns=[]` and `rows=[]`.

`datetime` and `Decimal` column values are pre-serialised to strings. `bytes`/varbinary columns are base64-encoded and their column names are suffixed with `__base64`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `query` (`str`) — SQL statement or batch to execute.

**Returns:** `{ "columns": list[str], "rows": list[list[Any]], "rowcount": int }` — `rowcount` is `-1` when the driver does not report a count.

---

### get_query_plan

**Targets:** Data Warehouse · SQL Analytics Endpoint

Capture the **estimated** SHOWPLAN_XML execution plan for a SQL query without executing it.

This tool does **not** execute the query — it only retrieves the estimated plan. Because no data is modified, this tool is permitted even when `FABRIC_MCP_READONLY=1`. DDL/DML query text is safe to plan without modifying any data.

The plan XML uses the standard namespace `http://schemas.microsoft.com/sqlserver/2004/07/showplan` and can be opened in SSMS, Azure Data Studio, or uploaded to [pastetheplan.com](https://www.pastetheplan.com) for visual analysis.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.
- `query` (`str`) — SQL statement to generate an estimated execution plan for.

**Returns:** `{ "plan_xml": str }` — the SHOWPLAN_XML string.
