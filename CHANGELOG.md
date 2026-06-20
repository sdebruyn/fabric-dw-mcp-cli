# Changelog

All notable changes to `fabric-dw` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [CalVer](https://calver.org/) in the form `YYYY.MM.MICRO`.

---

## [Unreleased]

### Breaking changes

**Workspace moved from a per-command positional to a global `-w`/`--workspace` option.**

Previously, every command that required a workspace accepted it as the first
positional argument on that command, e.g.:

```
fabric-dw warehouses list MyWorkspace
fabric-dw sql MyWorkspace SalesWH -q "SELECT 1"
```

Starting with this release, the workspace is a **global root option** that sits
before the command group:

```
fabric-dw -w MyWorkspace warehouses list
fabric-dw -w MyWorkspace sql exec SalesWH -q "SELECT 1"
```

**Migration:** replace every `<command> <WORKSPACE> …` invocation with
`-w <WORKSPACE> <command> …`.  Scripts that rely on `FABRIC_DW_DEFAULT_WORKSPACE`
or `fabric-dw config set workspace` require no changes — the environment variable
and the configured default continue to work exactly as before.

**`sql` is now a command group; `exec` is the direct-execute subcommand.**

The `sql` command is now a group with two subcommands:

- `sql exec WAREHOUSE -q "..."` — execute a SQL query (equivalent to the old `sql` command).
- `sql plan WAREHOUSE -q "..."` — capture the estimated SHOWPLAN_XML without executing.

**Migration:** replace every `fdw sql <WAREHOUSE> …` invocation with
`fdw sql exec <WAREHOUSE> …`.

**Workspace resolution order** (unchanged):

1. `-w` / `--workspace` flag on the root command (new, replaces the positional).
2. `FABRIC_DW_DEFAULT_WORKSPACE` environment variable.
3. Value stored by `fabric-dw config set workspace`.

**`workspaces` group exception:** `workspaces get <WORKSPACE>` and
`workspaces set-collation <WORKSPACE> COLLATION` continue to accept the workspace
as a positional argument (the `-w` option does not apply to this group).
`workspaces list` takes no workspace at all.

**`-A` / `--all-workspaces` interaction:** `-w` and `-A` are mutually exclusive
on `warehouses list` and `sql-endpoints list`. A configured default workspace does
not conflict with `-A`.

### Added

- Global `-w` / `--workspace <NAME-OR-ID>` option on the root `fabric-dw` command.
- "Selecting a workspace" section in the CLI reference documenting the four-tier resolution order.
- `sql plan --format dot` — export the execution plan as a Graphviz DOT `digraph` (plain text, no extra dependencies). Pipe the output to `dot -Tsvg` or paste into an online viewer to render the plan graph.

### Changed

- All command synopses and examples in `docs/cli.md` updated to the new form.
- README quick-start updated to show the new invocation pattern.

---

## [v2026.6.0a0] — 2026-06-01

Initial alpha release.
