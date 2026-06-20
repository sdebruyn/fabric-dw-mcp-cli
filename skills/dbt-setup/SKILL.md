---
name: dbt-setup
description: >
  Scaffolds a complete dbt-fabric project for a Fabric Data Warehouse: generates
  profiles.yml, dbt_project.yml, a column-rich _sources.yml (with data types for
  every table column, ready for dbt contracts), requirements.txt, and .gitignore,
  then writes them to the local filesystem. Use when the user asks to "set up dbt",
  "scaffold a dbt project", "create dbt profile", "initialize dbt for my warehouse",
  "generate dbt sources", or "bootstrap dbt-fabric".
user-invocable: true
---

# dbt Project Bootstrap for Fabric DW

Generates a complete dbt-fabric project scaffold using the fabric-dw MCP tools and writes all files to the local filesystem.

## Inputs

Gather these from the user (via `$ARGUMENTS` or natural language) before starting:

- **workspace** — workspace name or GUID
- **warehouse** — Fabric Data Warehouse name or GUID
- **project_name** — desired dbt project name (default: warehouse name, lowercased, spaces replaced with underscores)
- **schema** — default dbt schema (default: `dbo`)
- **authentication** — `default` (DefaultAzureCredential), `sp` (service principal), or `interactive` (default: `default`)
- **with_sources** — whether to generate a column-rich `_sources.yml` (default: `true`, recommended)

## Workflow

### Step 1 — Resolve the warehouse connection

Call `get_warehouse` with the workspace and warehouse name. This returns the warehouse metadata including the connection string used in the dbt profile.

### Step 2 — Confirm connectivity and list objects

Call `list_schemas` and `list_tables` to confirm that the warehouse is reachable and to identify the schemas and tables that will appear as dbt sources.

Show the user a summary: number of schemas found, number of tables found.

### Step 3 — Generate the dbt scaffold

Call `generate_dbt_profile` with:

- `workspace`, `warehouse`, `project_name`, `schema`, `authentication` as provided
- `with_sources=true` (unless the user explicitly opted out)

When `with_sources=true`, the tool performs a bulk column fetch and emits a `models/staging/_sources.yml` that already includes each table's `columns:` block with `name` and formatted `data_type` for every column. This output is ready for dbt [contract enforcement](https://docs.getdbt.com/docs/collaborate/govern/model-contracts) and column-level documentation without any manual assembly.

### Step 4 — Write files to the local filesystem

Write all file contents returned by `generate_dbt_profile` to the user's working directory. The expected set of files:

| Path | Purpose |
| --- | --- |
| `profiles.yml` | dbt connection profile |
| `dbt_project.yml` | dbt project definition |
| `models/staging/_sources.yml` | Source definitions with column types (when `with_sources=true`) |
| `requirements.txt` | Python package dependencies (`dbt-fabric`) |
| `.gitignore` | Excludes secrets and build artefacts |

Before writing, check whether any of these files already exist and confirm with the user before overwriting.

### Step 5 — Provide next steps

After writing the files, give the user the following instructions:

```bash
# Install dependencies
pip install -r requirements.txt

# Verify the connection
dbt debug

# Run your first dbt project
dbt run
```

Also remind the user to:

1. Review `profiles.yml` and fill in any `{{ env_var(...) }}` placeholders (service-principal credentials are never written as literals — they are templated).
2. Check that environment variables (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`) are set before running `dbt debug` if using service-principal auth.

## Guardrails

- **Service-principal auth**: when `authentication=sp`, the generated `profiles.yml` uses `{{ env_var(...) }}` placeholders for tenant ID, client ID, and client secret. Never write literal credential values. Remind the user that these must be set in environment before committing `profiles.yml` to version control.
- **Overwrite protection**: if any target file already exists, list them and ask the user to confirm before overwriting.
- **Fabric auth only**: dbt-fabric requires Entra (Azure AD) authentication. Warn the user if they ask about username/password auth — it is not supported by the dbt-fabric adapter.
- **Column inspection**: `get_table_columns` and `get_view_columns` are available for ad-hoc schema inspection (e.g., if the user wants to review a specific table before deciding which columns to expose), but explicit calls to those tools are not required — column data is built into `generate_dbt_profile` with `with_sources=true`.
