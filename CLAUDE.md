# CLAUDE.md

This repo is `fabric-dw-mcp-cli` — a CLI and MCP server for Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

## No SQL parsing

Never parse, tokenize, regex-match, or rewrite SQL/T-SQL text in this codebase.
Do not extract a function/view body from `sys.sql_modules`, split statements, or
rewrite DDL by string manipulation. It is fragile: it breaks on comments, string
literals, and edge cases.

If an operation cannot be expressed as a parameterized SQL statement (with
bracket-quoted, validated identifiers), do not implement it by parsing SQL.
Prefer a server-side primitive; if none exists, drop the feature rather than
parse. Precedent: the function-rename feature was removed (issue #897) because
Fabric rejects `sp_rename` for scalar UDFs and the only alternative was parsing
stored `CREATE FUNCTION` text.
