---
title: MCP capabilities
---

# MCP capabilities

The `list_capabilities` tool returns all available MCP tools grouped by domain. Call it first to discover what dedicated tools exist before falling back to `execute_sql`.

## Why use it

Dedicated tools return typed, structured results and have no SQL dialect pitfalls. `execute_sql` returns only the last result set of a batch and base64-encodes varbinary columns. Knowing which dedicated tools are available helps you choose the right tool for each task.

## Return format

The tool returns a `dict[str, list[str]]` mapping each domain name to a sorted list of tool names in that domain. The dict itself is sorted by domain key.

Example (truncated):

```json
{
  "audit": ["disable_audit", "enable_audit", "get_audit_settings", "..."],
  "cache": ["clear_cache"],
  "server": ["list_capabilities"],
  "tables": ["clear_table", "clone_table", "count_table_rows", "..."],
  "..."
}
```

## Parameters

None.

## Usage

Call `list_capabilities` at the start of a session to orient yourself, then use the domain grouping to pick the most specific tool for each operation. Fall back to `execute_sql` only when no dedicated tool covers the required SQL.
