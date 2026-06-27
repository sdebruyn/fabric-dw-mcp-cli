---
title: dbt setup
---

# Set up a dbt environment

This guide walks an analytics engineer end-to-end through standing up a working [dbt](https://docs.getdbt.com/) environment on a Microsoft Fabric Data Warehouse with `fabric-dw`. The headline feature is `fdw dbt init --with-sources`: it **introspects the live warehouse and writes a complete `models/staging/_sources.yml`**: one dbt `source:` per schema, listing every table (with column names and types) - so you never hand-author source definitions:

1. **Provision** a new Warehouse.
2. **Scaffold** a dbt project for the [dbt-fabric](https://docs.getdbt.com/docs/core/connect-data-platform/fabric-setup) adapter, with the connection details (host + database) filled in automatically.
3. **Provision all your dbt sources**: `--with-sources` generates `_sources.yml` from the warehouse's real schemas and tables.
4. **Verify** the connection with `dbt debug` and build a model with `dbt run`.

Microsoft's own [tutorial](https://learn.microsoft.com/fabric/data-warehouse/tutorial-setup-dbt?WT.mc_id=MVP_310840) requires you to find the SQL analytics endpoint in the portal, hand-author `profiles.yml`, and write every source definition yourself. `fabric-dw` automates all of that, so this guide closes the gap between Microsoft's manual portal steps and a fully scriptable path.

!!! tip "Using an AI assistant? The `dbt-setup` skill does all of this for you"

    Everything below is the **human, copy-pasteable narrative** for someone driving `fabric-dw` from a terminal. If you drive an AI assistant (Claude) against the [MCP server](../install.md#mcp), the shipped [`dbt-setup` Agent Skill](../skills.md#dbt-setup) automates the same provision → scaffold → sources → verify flow through the MCP tools. The CLI commands here and the skill are two front-ends to the same logic - pick whichever fits your workflow.

## Prerequisites

- A Fabric **workspace** on an active capacity (Trial, Premium, or Fabric capacity).
- **Python 3.11+**: needed both by `fabric-dw` and by dbt itself.
- **Microsoft ODBC Driver 18 for SQL Server**: required by the dbt-fabric adapter (it connects over [TDS via `pyodbc`](https://learn.microsoft.com/fabric/data-warehouse/how-to-connect?WT.mc_id=MVP_310840#connect-using-dbt)):
    - Windows: [download from Microsoft](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server?WT.mc_id=MVP_310840)
    - macOS: `brew install microsoft/mssql-release/msodbcsql18`
    - Linux: [install instructions](https://learn.microsoft.com/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server?WT.mc_id=MVP_310840)
- **`fabric-dw` installed**: see [Install](../install.md).
- A signed-in identity - `az login`, or service-principal environment variables (see the next step).

!!! note "Entra ID only - no SQL authentication"

    dbt-fabric authenticates with **Microsoft Entra ID** identities (users, service principals); SQL username/password authentication is **not supported**. See [Connect using dbt](https://learn.microsoft.com/fabric/data-warehouse/how-to-connect?WT.mc_id=MVP_310840#connect-using-dbt). `fabric-dw` uses the same Entra-based credential chain, so once you can run `fdw` you have everything dbt needs.

## Step 1 - Sign in

`fabric-dw` selects how it authenticates via the global `--auth` option (it is **not** controlled by an environment variable):

| `--auth` value | What it uses |
| --- | --- |
| `default` (default) | `azure-identity`'s `DefaultAzureCredential` chain - Azure CLI, Managed Identity, environment variables, browser fallback. |
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

Microsoft recommends interactive (`CLI`) auth for working on a warehouse by hand, and service principals for automation - see the [tutorial's considerations](https://learn.microsoft.com/fabric/data-warehouse/tutorial-setup-dbt?WT.mc_id=MVP_310840#considerations). The mode you choose here is also what the scaffolder maps into the dbt profile in [Step 3](#step-3-scaffold-the-dbt-project).

See the [Authentication reference](../authentication.md) for the full credential chain and every environment variable.

## Set your defaults

Once you are signed in, store the workspace so you do not repeat it on every command:

```shell
fdw config set workspace "Sales Workspace"
fdw config set warehouse SalesWH        # optional - once the warehouse exists (Step 2)
```

The rest of this guide assumes the workspace default is set, so the examples omit `-w "Sales Workspace"`. Any command still accepts an explicit `-w`/`--workspace NAME|GUID` to override it. The warehouse default fills optional `[ITEM]` positionals once the warehouse has been provisioned; commands here that take a required name (`warehouses create NAME`, `dbt init … FOLDER`, `sql-endpoints get ENDPOINT`) still spell out the warehouse. See [Configuration & defaults](../commands/config.md).

## Step 2 - Provision the warehouse

Create a new warehouse with [`fdw warehouses create`](../commands/warehouses.md#warehouses-create). `NAME` is positional; the workspace comes from the global `-w` option.

```shell
fdw warehouses create SalesWH --description "dbt target warehouse"
```

`fabric-dw` issues the create request, **polls the create operation to completion**, and re-reads the warehouse so the returned object is fully populated - when the command returns, the warehouse is ready to connect to.

If a service principal or team needs access (for example, the identity that will run dbt in CI), grant it now:

- [`fdw warehouses takeover`](../commands/warehouses.md#warehouses-takeover) - take ownership of the warehouse.
- [`fdw warehouses permissions`](../commands/warehouses.md#warehouses-permissions) - list principals and their effective permissions (requires the **Fabric Administrator** role).

!!! tip "AI-assistant equivalent"

    The MCP tool [`create_warehouse`](../commands/warehouses.md#create_warehouse) provisions the warehouse from an AI client with the same polling behaviour.

## Step 3 - Scaffold the dbt project

[`fdw dbt init`](../commands/dbt.md#dbt-init) creates the whole project directory pre-wired to the warehouse. `ITEM` (the warehouse name or GUID) is optional if you set a default warehouse; `FOLDER` is the target directory.

```shell
fdw dbt init SalesWH ./sales_dbt
```

This writes `dbt_project.yml`, `profiles.yml`, `requirements.txt`, `.gitignore`, the standard dbt directories, a sample model, a `README.md`, and a `models/staging/_sources.yml` (a placeholder, or **real source definitions** with `--with-sources` - see [Step 4](#step-4-provision-all-your-dbt-sources)). If `git` is on your PATH and the folder is not already a repository, `git init` runs automatically. No dbt installation is required to scaffold - `fabric-dw` writes every file itself.

### The connection is configured for you

You do **not** hand-author `profiles.yml`. `fdw dbt init` resolves the warehouse's SQL analytics endpoint itself and fills in the two values dbt needs:

- **`host`**: the SQL analytics endpoint TDS server (`<guid>.datawarehouse.fabric.microsoft.com`), polled until the connection string is ready (the endpoint is eventually consistent right after `create`).
- **`database`**: the warehouse display name (Fabric uses the item name as the Initial Catalog). For `SalesWH`, the database is simply `SalesWH`.

The `authentication` value is mapped from your sign-in mode (`default` → `auto`, `interactive` → `CLI`, `sp` → `ServicePrincipal`). With `--auth sp`, secrets are emitted as Jinja2 `env_var()` placeholders - never literal secrets - so the file is safe to commit. The full option list (`--schema`, `--target`, `--threads`, `--profiles-dir`, `--auth`, …), the exact generated `profiles.yml`, and the auth mapping live in the [dbt command reference](../commands/dbt.md#dbt-init); the credential chain is in the [Authentication reference](../authentication.md).

## Step 4 - Provision all your dbt sources

This is the part you would otherwise do by hand. Add `--with-sources` and `fdw dbt init` **introspects the live warehouse** and writes a complete `models/staging/_sources.yml`: so you never type out a source definition:

```shell
fdw dbt init SalesWH ./sales_dbt --with-sources
```

The `--with-sources` flag generates `models/staging/_sources.yml` **from the warehouse's actual schemas and tables**: one dbt `source:` entry **per schema**, each listing every table in that schema (with its column names and data types). Without `--with-sources`, you get a minimal placeholder `_sources.yml` to fill in yourself.

### The generated `models/staging/_sources.yml`

With `--with-sources`, the file has one `sources:` entry per schema. Each source's `database` is the warehouse name and its `schema` is the schema name; every table is listed under `tables:`, with columns and their data types:

```yaml
version: 2
sources:
  - name: sales
    database: SalesWH
    schema: sales
    tables:
      - name: customer
        columns:
          - name: customer_id
            data_type: bigint
          - name: region
            data_type: varchar
      - name: orders
        columns:
          - name: order_id
            data_type: bigint
          - name: order_date
            data_type: date
  - name: dbo
    database: SalesWH
    schema: dbo
    tables:
      - name: my_first_model
```

Without `--with-sources`, the placeholder looks like this - replace it with your own definitions:

```yaml
version: 2
sources:
  - name: placeholder
    description: Replace with your source definitions.
    database: "{{ env_var('DBT_DATABASE', 'SalesWH') }}"
    schema: dbo
    tables: []
```

### Reference the sources from your models

dbt models read from these sources with the [`source()`](https://docs.getdbt.com/reference/dbt-jinja-functions/source) function - `{{ source('<schema>', '<table>') }}`, where the first argument is the `source:` name (the schema) and the second is the table:

```sql
-- models/staging/stg_customers.sql
select
    customer_id,
    region
from {{ source('sales', 'customer') }}
```

Because every schema and table is already declared in `_sources.yml`, `source()` resolves immediately and `dbt run` / `dbt test` can build on top of the real warehouse objects without any manual source authoring.

## Step 5 - Verify

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
# Confirm the warehouse answers SQL (warehouse positional spelled out - the warehouse default is optional in this guide)
fdw sql exec SalesWH -q "select 1"

# List the tables/views the run produced
fdw tables list SalesWH
```

!!! warning "Use `sql exec`, not `sql query`"

    The SQL group is `fdw sql exec` / `fdw sql plan`. There is no `fdw sql query` command.

## Doing it from an AI assistant

If you drive an AI client over the [MCP server](../install.md#mcp), the same scaffold is available as the [`generate_dbt_profile`](../commands/dbt.md#generate_dbt_profile) tool. Unlike the CLI, it does **not** write files - the MCP server cannot touch the caller's filesystem, so it returns each file's contents as a string:

- `profiles_yml`
- `dbt_project_yml`
- `sources_yml`: the same `models/staging/_sources.yml` content as the CLI: **real source definitions for every schema and table when called with `with_sources=True`**, otherwise the placeholder.
- `requirements_txt`
- `gitignore`

The AI agent writes those strings to disk itself. The shipped [`dbt-setup` Agent Skill](../skills.md#dbt-setup) orchestrates the whole flow - it resolves the warehouse with `get_warehouse`, confirms connectivity with `list_schemas` / `list_tables`, calls `generate_dbt_profile` with `with_sources=True` to provision the sources, writes the files, and tells you to run `pip install -r requirements.txt`, `dbt debug`, and `dbt run`. In short: **the guide is the CLI narrative; the skill is the assistant-driven equivalent.**

## Next steps & references

- [dbt command reference](../commands/dbt.md) - every `fdw dbt init` option and the `generate_dbt_profile` MCP tool.
- [Authentication reference](../authentication.md) - the full credential chain and environment variables.
- [MCP server install](../install.md#mcp) - configure the MCP server in your AI client.
- [Agent Skills](../skills.md) - including the [`dbt-setup` skill](../skills.md#dbt-setup) that automates this guide.
- [Set up dbt for Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/tutorial-setup-dbt?WT.mc_id=MVP_310840) - Microsoft's canonical tutorial.
- [Microsoft Entra authentication for the warehouse](https://learn.microsoft.com/fabric/data-warehouse/entra-id-authentication?WT.mc_id=MVP_310840) - Entra modes and the `<guid>.datawarehouse.fabric.microsoft.com` server format.
- [Warehouse connectivity](https://learn.microsoft.com/fabric/data-warehouse/connectivity?WT.mc_id=MVP_310840) - TDS on port 1433 and the connection-string semantics.
- [Create a warehouse](https://learn.microsoft.com/fabric/data-warehouse/create-warehouse?WT.mc_id=MVP_310840) - portal/REST context for what `warehouses create` automates.
- [dbt-fabric adapter setup](https://docs.getdbt.com/docs/core/connect-data-platform/fabric-setup) and [resource configs](https://docs.getdbt.com/reference/resource-configs/fabric-configs).
</content>
</invoke>
