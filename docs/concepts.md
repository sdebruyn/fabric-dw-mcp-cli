---
title: Concepts
---

# Concepts

The sections below describe cross-cutting concepts that apply to all `fdw`/`fabric-dw` commands: how the CLI distinguishes between item kinds, which global flags are available on every invocation, and how the target workspace is resolved. Understanding these concepts first makes the rest of the documentation easier to follow.

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

1. **`-w` / `--workspace` flag** — explicit value passed on the root command, e.g. `fdw -w MyWorkspace warehouses list`.
2. **`FABRIC_DW_DEFAULT_WORKSPACE` environment variable** — if the flag is absent, the CLI reads this variable.
3. **Configured default** — set with `fdw config set workspace VALUE`; used when neither the flag nor the environment variable is present.
4. **Error** — if none of the above is set, the CLI prints a helpful message suggesting you set one of the above.

The `workspaces` command group is an exception: `workspaces get` and `workspaces set-collation` take the workspace as an explicit positional argument (not via `-w`), and `workspaces list` takes no workspace at all.

!!! note "-A / --all-workspaces interaction"

    Passing `-A` on the two list commands that support it (`warehouses list`, `sql-endpoints list`) explicitly scans every visible workspace. This flag is mutually exclusive with `-w` (an explicit `-w` conflicts with scanning all workspaces), but it does **not** conflict with a configured default workspace or `FABRIC_DW_DEFAULT_WORKSPACE` — the configured default is silently ignored when `-A` is used.

---

## Name-or-GUID resolution

!!! note "Name-or-GUID resolution"
    All `workspace`, `warehouse`, `endpoint`, and `snapshot` parameters — on both `fdw`/`fabric-dw` commands and the MCP tools — accept either the item's display name or its GUID. The resolver translates names to GUIDs automatically and caches the mapping locally. Use `fdw cache clear` (CLI) or the `clear_cache` MCP tool to force a fresh lookup after renaming items outside this tool.
