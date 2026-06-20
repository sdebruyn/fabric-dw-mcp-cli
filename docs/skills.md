---
title: Agent Skills
---

# Agent Skills

`fabric-dw` ships two [Claude Code Agent Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) that orchestrate multi-step administration workflows on top of the MCP server.

| Skill | Trigger phrases | What it does |
| --- | --- | --- |
| `query-optimizer` | "optimize this query", "why is my query slow", "analyze execution plan", "missing statistics", "fix clustering" | Captures the estimated execution plan, inspects query insights history, audits statistics, examines clustering, and proposes or applies optimizations |
| `dbt-setup` | "set up dbt", "scaffold a dbt project", "create dbt profile", "generate dbt sources" | Generates a complete dbt-fabric scaffold (profiles, project, column-rich sources, requirements) and writes it to disk |

Both skills require the [fabric-dw MCP server](mcp.md) to be configured in your AI client.

## Install via Claude Code Plugin

The recommended installation path for Claude Code users is the **fabric-dw plugin**. Skills are installed from GitHub without cloning the repo and are namespaced as `/fabric-dw:query-optimizer` and `/fabric-dw:dbt-setup`.

Add the following to your `.claude/settings.json` (project-scoped) or `~/.claude/settings.json` (personal):

```json
{
  "extraKnownMarketplaces": {
    "fabric-dw": {
      "source": { "source": "github", "repo": "sdebruyn/fabric-dw-mcp-cli" }
    }
  },
  "enabledPlugins": {
    "fabric-dw@fabric-dw": true
  }
}
```

After saving, the skills are available as slash commands:

```
/fabric-dw:query-optimizer
/fabric-dw:dbt-setup
```

!!! note "Version caching"
    The plugin version is pinned in `.claude-plugin/plugin.json`. If a new release changes skill content, bump the plugin version to force cached copies to update.

## Install as raw SKILL.md files

If you are using a different AI assistant, the Claude API directly, or any tool that supports the `SKILL.md` format but not the Claude Code plugin mechanism, download or reference the raw skill files:

- **query-optimizer**: [`https://raw.githubusercontent.com/sdebruyn/fabric-dw-mcp-cli/main/skills/query-optimizer/SKILL.md`](https://raw.githubusercontent.com/sdebruyn/fabric-dw-mcp-cli/main/skills/query-optimizer/SKILL.md)
- **dbt-setup**: [`https://raw.githubusercontent.com/sdebruyn/fabric-dw-mcp-cli/main/skills/dbt-setup/SKILL.md`](https://raw.githubusercontent.com/sdebruyn/fabric-dw-mcp-cli/main/skills/dbt-setup/SKILL.md)

Place each file at one of the Claude Code personal or project skill paths:

| Path | Scope |
| --- | --- |
| `~/.claude/skills/<name>/SKILL.md` | Personal — applies to all projects |
| `.claude/skills/<name>/SKILL.md` | Project-scoped — committed to repo |

For example, to install `query-optimizer` at project scope:

```bash
mkdir -p .claude/skills/query-optimizer
curl -fsSL https://raw.githubusercontent.com/sdebruyn/fabric-dw-mcp-cli/main/skills/query-optimizer/SKILL.md \
  -o .claude/skills/query-optimizer/SKILL.md
```

## Skill reference

### query-optimizer

Performs a structured query performance analysis on a Fabric Data Warehouse:

1. Captures the estimated execution plan via `get_query_plan` (no query execution)
2. Parses the SHOWPLAN_XML for costly operators, residual predicates, and implicit type conversions
3. Inspects query insights history via `list_request_history` and `list_long_running_queries`
4. Audits statistics per referenced table with `list_statistics` and `show_statistics`
5. Fetches column metadata (types, nullability, identity, computed) via `get_table_columns`
6. Reads current data-clustering columns via `get_cluster_columns` (DW-only; skipped for SQL Analytics Endpoints)
7. Reports findings and recommendations; optionally applies statistics changes (`create_statistics` / `update_statistics`) and re-clustering (`set_cluster_columns`) after explicit user confirmation

To visualize the execution plan, use the CLI:

```bash
fdw sql plan "<query>" --format html   # opens in browser
fdw sql plan "<query>" --format svg    # saves as SVG image
```

### dbt-setup

Bootstraps a dbt-fabric project for a Fabric Data Warehouse:

1. Resolves the warehouse connection string via `get_warehouse`
2. Lists schemas and tables with `list_schemas` and `list_tables` to confirm connectivity
3. Generates all scaffold files with `generate_dbt_profile` using `with_sources=true` — the tool performs a bulk column fetch and emits a `models/staging/_sources.yml` with `columns:` blocks (name and formatted `data_type`) for every table, ready for dbt contract enforcement
4. Writes `profiles.yml`, `dbt_project.yml`, `models/staging/_sources.yml`, `requirements.txt`, and `.gitignore` to disk; confirms before overwriting existing files
5. Provides next steps: `pip install -r requirements.txt`, `dbt debug`, `dbt run`
