---
title: dbt
---

# dbt

Scaffold a [dbt](https://docs.getdbt.com/) project pre-wired to a Microsoft Fabric Data Warehouse using the [dbt-fabric](https://docs.getdbt.com/docs/core/connect-data-platform/fabric-setup) adapter.

No dbt installation is required to run these commands — `fabric-dw` generates all project files itself. A `requirements.txt` inside the scaffolded project lists the required pip packages (`dbt-core`, `dbt-fabric`) so you can install them in a separate environment when you are ready to run dbt.

**Targets:** Data Warehouse

---

## CLI

### dbt init

**Targets:** Data Warehouse

Scaffold a new dbt project directory connected to a Fabric Data Warehouse. The command creates the folder, writes `dbt_project.yml`, `profiles.yml`, `requirements.txt`, `.gitignore`, standard dbt model directories, a sample model, and a README. If `git` is on your PATH and the target folder is not already a git repository, `git init` is run automatically.

!!! warning "Security"

    When `--auth sp` (Service Principal) is used, `profiles.yml` emits Jinja2 `env_var()` placeholders (`{{ env_var('AZURE_TENANT_ID') }}` etc.) instead of literal secrets. You must set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, and `AZURE_CLIENT_SECRET` in your environment before running dbt.

**Usage**

```shell
fdw [-w WORKSPACE] dbt init [OPTIONS] [ITEM] FOLDER
```

**Arguments**

| Argument | Description |
| --- | --- |
| `ITEM` | Name or ID of the Fabric Data Warehouse item (optional if set via `fdw config set warehouse`). |
| `FOLDER` | Path to the folder to create. Must not exist (unless `--force` is passed). |

**Options**

| Option | Default | Description |
| --- | --- | --- |
| `--project-name TEXT` | derived from `ITEM` name | dbt project name (sanitised: lowercase, non-alphanumeric chars replaced with `_`). |
| `--profile-name TEXT` | same as `--project-name` | dbt profile name written into `profiles.yml` and `dbt_project.yml`. |
| `--schema TEXT` | `dbo` | Default target schema for dbt models. |
| `--target TEXT` | `dev` | dbt target name inside the profile. |
| `--threads INTEGER RANGE` | `4` | Number of dbt threads (1–64). |
| `--auth [auto\|CLI\|ServicePrincipal\|interactive\|sp]` | derived from active credential mode | Authentication method. `interactive` is an alias for `CLI`; `sp` is an alias for `ServicePrincipal`. |
| `--profiles-dir [project\|home]` | `project` | Where to write `profiles.yml`. `project` writes it next to `dbt_project.yml`; `home` merges it into `~/.dbt/profiles.yml` (backs up existing file first). |
| `--with-sources` | off | Introspect the live warehouse and generate a `_sources.yml` file listing all schemas and tables. |
| `--force` | off | Overwrite an existing non-empty directory. |

**Examples**

```shell
# Minimal — uses configured default workspace and warehouse
fdw dbt init SalesWH ./my_dbt_project

# Explicit workspace via -w
fdw -w MyWorkspace dbt init SalesWH ./my_dbt_project

# Service Principal auth; write profiles.yml to ~/.dbt/
fdw -w MyWorkspace dbt init SalesWH ./sales_dbt \
  --auth sp --profiles-dir home

# Scaffold with live source introspection (auto-generates _sources.yml)
fdw -w MyWorkspace dbt init SalesWH ./sales_dbt --with-sources

# Force-overwrite an existing folder
fdw -w MyWorkspace dbt init SalesWH ./sales_dbt --force
```

**Scaffolded layout**

```
<FOLDER>/
├── .gitignore
├── README.md
├── dbt_project.yml
├── profiles.yml          # only when --profiles-dir project (default)
├── requirements.txt      # pip install -r requirements.txt
├── models/
│   ├── staging/
│   │   └── _sources.yml  # placeholder, or real entries with --with-sources
│   └── my_first_model.sql
├── seeds/
├── snapshots/
├── tests/
├── macros/
└── analyses/
```

---

## MCP tools

Generate [dbt-fabric](https://docs.getdbt.com/docs/core/connect-data-platform/fabric-setup) project file contents pre-wired to a Microsoft Fabric Data Warehouse. Unlike the CLI `dbt init` command, these tools return file contents as text rather than writing files to disk, making them suitable for AI-assisted workflows where the AI agent writes the files.

!!! warning "Security"

    When `authentication="ServicePrincipal"` is used, the returned `profiles_yml` contains Jinja2 `env_var()` placeholders (`{{ env_var('AZURE_TENANT_ID') }}` etc.) rather than literal secrets. Never hard-code secrets into source-controlled files.

### generate_dbt_profile

**Targets:** Data Warehouse

Return the contents of all files needed to bootstrap a dbt project that connects to a Fabric Data Warehouse. No files are written; the caller is responsible for persisting the returned strings.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — Data Warehouse name or GUID.
- `project_name` (`str`, optional) — dbt project name. Defaults to the warehouse display name (sanitised to lowercase + underscores).
- `profile_name` (`str`, optional) — dbt profile name. Defaults to the project name.
- `schema` (`str`, default `"dbo"`) — default target schema for dbt models.
- `target` (`str`, default `"dev"`) — dbt target name.
- `threads` (`int`, default `4`) — number of dbt threads (1–64).
- `authentication` (`str`, optional) — authentication method (`"auto"`, `"CLI"`, `"ServicePrincipal"`). Defaults to the MCP server's active credential mode.
- `with_sources` (`bool`, default `False`) — when `True`, introspect the live warehouse and include all schemas/tables in the returned `sources_yml`.

**Returns:** object with:

- `profiles_yml` (`str`) — contents for `profiles.yml` (write next to `dbt_project.yml` or into `~/.dbt/profiles.yml`).
- `dbt_project_yml` (`str`) — contents for `dbt_project.yml`.
- `sources_yml` (`str`) — contents for `models/staging/_sources.yml` (placeholder or real entries when `with_sources=True`).
- `requirements_txt` (`str`) — pip requirements listing `dbt-core` and `dbt-fabric`.
- `gitignore` (`str`) — contents for `.gitignore`, pre-configured for dbt projects.
