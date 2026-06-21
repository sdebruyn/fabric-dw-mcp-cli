---
title: dbt setup
---

# Set up a dbt environment

This guide walks an analytics engineer end-to-end through standing up a working [dbt](https://docs.getdbt.com/) environment on a Microsoft Fabric Data Warehouse, using `fabric-dw` to do the provisioning and to generate a correct `profiles.yml` automatically:

1. **Provision** a new Warehouse.
2. **Get the SQL connection details** dbt needs (host + database).
3. **Scaffold and configure** a dbt project for the [dbt-fabric](https://docs.getdbt.com/docs/core/connect-data-platform/fabric-setup) adapter.
4. **Verify** the connection with `dbt debug` and build a model with `dbt run`.

Microsoft's own [tutorial](https://learn.microsoft.com/fabric/data-warehouse/tutorial-setup-dbt?WT.mc_id=MVP_310840) requires you to find the SQL analytics endpoint in the portal and hand-author `profiles.yml`. `fabric-dw` automates exactly that step, so this guide closes the gap between Microsoft's manual portal steps and a fully scriptable path.

!!! tip "Using an AI assistant? The `dbt-setup` skill does all of this for you"

    Everything below is the **human, copy-pasteable narrative** for someone driving `fabric-dw` from a terminal. If you drive an AI assistant (Claude) against the [MCP server](../mcp.md), the shipped [`dbt-setup` Agent Skill](../skills.md#dbt-setup) automates the same provision → inspect → scaffold → verify flow through the MCP tools. The CLI commands here and the skill are two front-ends to the same logic — pick whichever fits your workflow.

---

## Prerequisites

- A Fabric **workspace** on an active capacity (Trial, Premium, or Fabric capacity).
- **Python 3.11+** — needed both by `fabric-dw` and by dbt itself.
- **Microsoft ODBC Driver 18 for SQL Server** — required by the dbt-fabric adapter (it connects over [TDS via `pyodbc`](https://learn.microsoft.com/fabric/data-warehouse/how-to-connect?WT.mc_id=MVP_310840#connect-using-dbt)):
    - Windows: [download from Microsoft](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server?WT.mc_id=MVP_310840)
    - macOS: `brew install microsoft/mssql-release/msodbcsql18`
    - Linux: [install instructions](https://learn.microsoft.com/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server?WT.mc_id=MVP_310840)
- **`fabric-dw` installed** — see [Install](../install.md).
- A signed-in identity — `az login`, or service-principal environment variables (see the next step).

!!! note "Entra ID only — no SQL authentication"

    dbt-fabric authenticates with **Microsoft Entra ID** identities (users, service principals); SQL username/password authentication is **not supported**. See [Connect using dbt](https://learn.microsoft.com/fabric/data-warehouse/how-to-connect?WT.mc_id=MVP_310840#connect-using-dbt). `fabric-dw` uses the same Entra-based credential chain, so once you can run `fdw` you have everything dbt needs.

---

## Step 1 — Sign in

`fabric-dw` selects how it authenticates via the global `--auth` option (it is **not** controlled by an environment variable):

| `--auth` value | What it uses |
| --- | --- |
| `default` (default) | `azure-identity`'s `DefaultAzureCredential` chain — Azure CLI, Managed Identity, environment variables, browser fallback. |
| `interactive` | A browser pop-up sign-in. |
| `sp` | A service principal, read from `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`. |

The simplest path is to sign in with the Azure CLI and let the default chain pick it up:

```shell
az login
```

For a service principal (recommended for automation), set the three environment variables and pass `--auth sp`:

```shell
export AZURE_TENANT_ID=<your-tenant-id>
export AZURE_CLIENT_ID=<your-client-id>
export AZURE_CLIENT_SECRET=<your-client-secret>
```

Microsoft recommends interactive (`CLI`) auth for working on a warehouse by hand, and service principals for automation — see the [tutorial's considerations](https://learn.microsoft.com/fabric/data-warehouse/tutorial-setup-dbt?WT.mc_id=MVP_310840#considerations). The mode you choose here is also what the scaffolder maps into the dbt profile in [Step 4](#step-4-scaffold-the-dbt-project).

See the [Authentication reference](../authentication.md) for the full credential chain and every environment variable.

---

## Set your defaults

Once you are signed in, store the workspace so you do not repeat it on every command:

```shell
fdw config set workspace "Sales Workspace"
fdw config set warehouse SalesWH        # optional — once the warehouse exists (Step 2)
```

The rest of this guide assumes the workspace default is set, so the examples omit `-w "Sales Workspace"`. Any command still accepts an explicit `-w`/`--workspace NAME|GUID` to override it. The warehouse default fills optional `[ITEM]` positionals once the warehouse has been provisioned; commands here that take a required name (`warehouses create NAME`, `dbt init … FOLDER`, `sql-endpoints get ENDPOINT`) still spell out the warehouse. See [Configuration & defaults](../commands/config.md).

---

## Step 2 — Provision the warehouse

Create a new warehouse with [`fdw warehouses create`](../commands/warehouses.md#warehouses-create). `NAME` is positional; the workspace comes from the global `-w` option.

```shell
fdw warehouses create SalesWH --description "dbt target warehouse"
```

`fabric-dw` issues the create request, **polls the create operation to completion**, and re-reads the warehouse so the returned object is fully populated — when the command returns, the warehouse is ready to connect to.

If a service principal or team needs access (for example, the identity that will run dbt in CI), grant it now:

- [`fdw warehouses takeover`](../commands/warehouses.md#warehouses-takeover) — take ownership of the warehouse.
- [`fdw warehouses permissions`](../commands/warehouses.md#warehouses-permissions) — list principals and their effective permissions (requires the **Fabric Administrator** role).

!!! tip "AI-assistant equivalent"

    The MCP tool [`create_warehouse`](../commands/warehouses.md#create_warehouse) provisions the warehouse from an AI client with the same polling behaviour.

---

## Step 3 — Inspect the connection details

dbt's `profiles.yml` needs two values, and Fabric maps them like this:

- **`host`** — the **SQL analytics endpoint** connection string (the TDS server, `<guid>.datawarehouse.fabric.microsoft.com`).
- **`database`** — the **warehouse display name** (Fabric uses the item name as the Initial Catalog). For `SalesWH`, the database is simply `SalesWH`.

You can read the host with [`fdw sql-endpoints get`](../commands/sql-endpoints.md#sql-endpoints-get):

```shell
fdw sql-endpoints get SalesWH
```

!!! note "Eventual consistency right after `create`"

    The SQL analytics endpoint is provisioned with eventual consistency, so its connection string can be empty for a short window after `warehouses create`. `fabric-dw` **polls until the connection string is non-empty** (default timeout 120 s), so you do not normally have to retry by hand.

You **do not** have to copy this value anywhere: `fdw dbt init` in the next step resolves the host itself, so the inspection here is just to understand what ends up in the profile. The [Find the warehouse connection string](https://learn.microsoft.com/fabric/data-warehouse/how-to-connect?WT.mc_id=MVP_310840) page confirms the same `host` = SQL connection string, `database` = item name mapping.

---

## Step 4 — Scaffold the dbt project

[`fdw dbt init`](../commands/dbt.md#dbt-init) creates the whole project directory pre-wired to the warehouse. `ITEM` (the warehouse name or GUID) is optional if you set a default warehouse; `FOLDER` is the target directory.

```shell
fdw dbt init SalesWH ./sales_dbt
```

This writes `dbt_project.yml`, `profiles.yml`, `requirements.txt`, `.gitignore`, the standard dbt directories, a sample model, a `README.md`, and (with `--with-sources`) a real `models/staging/_sources.yml` introspected from the warehouse. If `git` is on your PATH and the folder is not already a repository, `git init` runs automatically. No dbt installation is required to scaffold — `fabric-dw` writes every file itself.

Useful options (see the [dbt command reference](../commands/dbt.md#dbt-init) for the full list):

| Option | Default | Description |
| --- | --- | --- |
| `--project-name TEXT` | sanitised folder name | dbt project name. |
| `--profile-name TEXT` | same as project name | dbt profile name. |
| `--schema TEXT` | `dbo` | Default target schema for models. |
| `--target TEXT` | `dev` | dbt target name. |
| `--threads INTEGER` | `4` | Number of dbt threads (1–64). |
| `--auth [auto\|CLI\|ServicePrincipal\|interactive\|sp]` | from the active `--auth` mode | dbt-fabric authentication override. |
| `--profiles-dir [project\|home]` | `project` | `project` writes `profiles.yml` in the folder; `home` merges into `~/.dbt/profiles.yml` (backs up an existing file). |
| `--with-sources` | off | Introspect the live warehouse and generate `models/staging/_sources.yml` from its real schemas, tables, and column types. |
| `--force` | off | Scaffold into a non-empty folder instead of refusing. |

### How the CLI fills in `profiles.yml`

| dbt `profiles.yml` key | Fabric value | How `fabric-dw` supplies it |
| --- | --- | --- |
| `type` | `fabric` | hard-coded by the scaffolder |
| `driver` | `ODBC Driver 18 for SQL Server` | hard-coded by the scaffolder |
| `host` | SQL analytics endpoint (TDS server) | resolved connection string, polled until ready |
| `database` | warehouse display name (Initial Catalog) | resolved item name |
| `schema` | `dbo` (default) | `--schema` |
| `threads` | `4` (default) | `--threads` |
| `authentication` | `auto` / `CLI` / `ServicePrincipal` | mapped from `--auth` |

The **authentication** value is mapped from your CLI auth mode:

| CLI `--auth` | dbt `authentication` |
| --- | --- |
| `default` | `auto` |
| `interactive` | `CLI` |
| `sp` | `ServicePrincipal` |

You can override it independently with `fdw dbt init … --auth`, where `interactive` and `sp` are accepted aliases for `CLI` and `ServicePrincipal`.

### The generated `profiles.yml`

With the default (`auto`) authentication, the generated `profiles.yml` looks like this — note the top-level `config: partial_parse` block:

```yaml
config:
  partial_parse: true
saleswh:
  target: dev
  outputs:
    dev:
      type: fabric
      driver: ODBC Driver 18 for SQL Server
      host: abc123def456ghij.datawarehouse.fabric.microsoft.com
      database: SalesWH
      schema: dbo
      threads: 4
      authentication: auto
```

### Service principal: secrets become `env_var()` placeholders

When you scaffold with `--auth sp` (or `--auth ServicePrincipal`), the profile uses `authentication: ServicePrincipal` and emits the credentials as Jinja2 `env_var()` placeholders — **never literal secrets**:

```yaml
config:
  partial_parse: true
saleswh:
  target: dev
  outputs:
    dev:
      type: fabric
      driver: ODBC Driver 18 for SQL Server
      host: abc123def456ghij.datawarehouse.fabric.microsoft.com
      database: SalesWH
      schema: dbo
      threads: 4
      authentication: ServicePrincipal
      tenant_id: '{{ env_var(''AZURE_TENANT_ID'') }}'
      client_id: '{{ env_var(''AZURE_CLIENT_ID'') }}'
      client_secret: '{{ env_var(''AZURE_CLIENT_SECRET'') }}'
```

Set the matching environment variables before running dbt, so the placeholders resolve at runtime:

```shell
export AZURE_TENANT_ID=<your-tenant-id>
export AZURE_CLIENT_ID=<your-client-id>
export AZURE_CLIENT_SECRET=<your-client-secret>
```

This keeps the generated file safe to commit. See the [dbt-fabric resource configs](https://docs.getdbt.com/reference/resource-configs/fabric-configs) for the full set of profile fields.

---

## Step 5 — Verify

Install the dbt dependencies in a **separate environment** (the scaffolded `requirements.txt` pins `dbt-core` and `dbt-fabric`), then verify the connection and build the sample model:

```shell
cd sales_dbt
pip install -r requirements.txt

# Verify connectivity, driver, and credentials
dbt debug

# Build the sample model
dbt run
```

A passing `dbt debug` confirms the host, database, ODBC driver, and authentication all line up. `dbt run` materialises `models/my_first_model.sql` into the warehouse.

You can cross-check from `fabric-dw` itself that the model landed:

```shell
# Confirm the warehouse answers SQL (warehouse positional spelled out — the warehouse default is optional in this guide)
fdw sql exec SalesWH -q "select 1"

# List the tables/views the run produced
fdw tables list SalesWH
```

!!! warning "Use `sql exec`, not `sql query`"

    The SQL group is `fdw sql exec` / `fdw sql plan`. There is no `fdw sql query` command.

---

## Doing it from an AI assistant

If you drive an AI client over the [MCP server](../mcp.md), the same scaffold is available as the [`generate_dbt_profile`](../commands/dbt.md#generate_dbt_profile) tool. Unlike the CLI, it does **not** write files — the MCP server cannot touch the caller's filesystem, so it returns each file's contents as a string:

- `profiles_yml`
- `dbt_project_yml`
- `sources_yml`
- `requirements_txt`
- `gitignore`

The AI agent writes those strings to disk itself. The shipped [`dbt-setup` Agent Skill](../skills.md#dbt-setup) orchestrates the whole flow — it resolves the warehouse with `get_warehouse`, confirms connectivity with `list_schemas` / `list_tables`, calls `generate_dbt_profile` (with `with_sources=true` for column-rich sources), writes the files, and tells you to run `pip install -r requirements.txt`, `dbt debug`, and `dbt run`. In short: **the guide is the CLI narrative; the skill is the assistant-driven equivalent.**

---

## Limitations & gotchas

- **Entra ID only.** dbt-fabric does not support SQL authentication — only Entra identities. See [Connect using dbt](https://learn.microsoft.com/fabric/data-warehouse/how-to-connect?WT.mc_id=MVP_310840#connect-using-dbt).
- **Remove `MultipleActiveResultSets`.** MARS is not supported by the warehouse TDS endpoint; do not add it to the connection. See [Warehouse connectivity — considerations and limitations](https://learn.microsoft.com/fabric/data-warehouse/connectivity?WT.mc_id=MVP_310840#considerations-and-limitations).
- **T-SQL surface area is reduced.** Not every T-SQL construct from SQL Server is available — check the [T-SQL surface area](https://learn.microsoft.com/fabric/data-warehouse/tsql-surface-area?WT.mc_id=MVP_310840) and [Fabric Data Warehouse limitations](https://learn.microsoft.com/fabric/data-warehouse/limitations?WT.mc_id=MVP_310840) before relying on a feature.
- **Unsupported data types.** Some SQL Server data types are unsupported in Fabric DW; model your sources accordingly (see the limitations page above).
- **Transient connection errors.** The endpoint can return transient errors; the [connectivity guidance](https://learn.microsoft.com/fabric/data-warehouse/connectivity?WT.mc_id=MVP_310840#considerations-and-limitations) recommends retry logic. Throttling surfaces as error `24801`.

---

## Next steps & references

- [dbt command reference](../commands/dbt.md) — every `fdw dbt init` option and the `generate_dbt_profile` MCP tool.
- [Authentication reference](../authentication.md) — the full credential chain and environment variables.
- [MCP server setup](../mcp.md) — configure the MCP server in your AI client.
- [Agent Skills](../skills.md) — including the [`dbt-setup` skill](../skills.md#dbt-setup) that automates this guide.
- [Set up dbt for Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/tutorial-setup-dbt?WT.mc_id=MVP_310840) — Microsoft's canonical tutorial.
- [Microsoft Entra authentication for the warehouse](https://learn.microsoft.com/fabric/data-warehouse/entra-id-authentication?WT.mc_id=MVP_310840) — Entra modes and the `<guid>.datawarehouse.fabric.microsoft.com` server format.
- [Warehouse connectivity](https://learn.microsoft.com/fabric/data-warehouse/connectivity?WT.mc_id=MVP_310840) — TDS on port 1433 and the connection-string semantics.
- [Create a warehouse](https://learn.microsoft.com/fabric/data-warehouse/create-warehouse?WT.mc_id=MVP_310840) — portal/REST context for what `warehouses create` automates.
- [dbt-fabric adapter setup](https://docs.getdbt.com/docs/core/connect-data-platform/fabric-setup) and [resource configs](https://docs.getdbt.com/reference/resource-configs/fabric-configs).
</content>
</invoke>
