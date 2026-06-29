<p align="center">
  <a href="https://fdw.debruyn.dev">
    <img src="https://raw.githubusercontent.com/sdebruyn/fabric-dw-mcp-cli/main/docs/assets/logo.svg" alt="fabric-dw logo" width="200" />
  </a>
</p>
<h1 align="center">fabric-dw</h1>

<p align="center">
  <a href="https://fdw.debruyn.dev"><img src="https://img.shields.io/badge/docs-fdw.debruyn.dev-blue" alt="Documentation"></a>
  <a href="https://github.com/sdebruyn/fabric-dw-mcp-cli/actions/workflows/ci.yml"><img src="https://github.com/sdebruyn/fabric-dw-mcp-cli/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/sdebruyn/fabric-dw-mcp-cli"><img src="https://codecov.io/gh/sdebruyn/fabric-dw-mcp-cli/graph/badge.svg" alt="codecov"></a>
  <a href="https://pypi.org/project/fabric-dw/"><img src="https://img.shields.io/pypi/v/fabric-dw" alt="PyPI version"></a>
  <a href="https://pypi.org/project/fabric-dw/"><img src="https://img.shields.io/pypi/pyversions/fabric-dw" alt="Python versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/sdebruyn/fabric-dw-mcp-cli" alt="License"></a>
</p>

Python CLI and MCP server for administering Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

**Full documentation: [fdw.debruyn.dev](https://fdw.debruyn.dev)**

📣 **Just announced!** Read the story behind `fabric-dw` in the [announcement blog post](https://debruyn.dev/2026/introducing-the-fabric-data-warehouse-cli-and-mcp-server/).

## Description

`fabric-dw` provides two interfaces for managing Microsoft Fabric Data Warehouses and SQL Analytics Endpoints:

- **CLI**: a command-line tool for common DW administration tasks.
- **MCP server**: a [Model Context Protocol](https://modelcontextprotocol.io) server that exposes DW operations as tools for AI assistants.

Authentication is configured via the `FABRIC_AUTH` environment variable. The default (`FABRIC_AUTH=default`) uses [`azure-identity` `DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840), which walks environment variables, Workload/Managed Identity, Azure CLI, Azure Developer CLI, Azure PowerShell, and interactive browser in order. Any of these will satisfy it. See the [Authentication](https://fdw.debruyn.dev/authentication/) docs for the full chain, all supported sources, and debugging tips.

## Installation

```bash
pip install fabric-dw
# or run without installing:
uvx fabric-dw --help
```

After installation, the `fdw` command is a short alias for `fabric-dw`; both invoke the same entry point.

## Quick Start

### CLI

`fdw` is a short alias for `fabric-dw`; both invoke the same entry point.

The workspace is a **global root option** `-w` / `--workspace` placed before the command group. Set a default once with `fdw config set workspace <NAME>` and omit `-w` on every subsequent call. Workspace resolution order: (1) `-w` flag, (2) `FABRIC_DW_DEFAULT_WORKSPACE` env var, (3) configured default.

```bash
# Run without installing; install to get the fdw alias
uvx fabric-dw --help

# Set a default workspace once; all subsequent commands pick it up
fdw config set workspace SalesWS
```

```bash
# -- Run and explain SQL --

# Execute a query against a warehouse
fdw sql exec SalesWH -q "SELECT TOP 10 * FROM dbo.orders ORDER BY order_date DESC"

# Capture an estimated execution plan as SVG -- no SSMS or Windows needed
fdw sql plan SalesWH -f query.sql --format svg -o plan.svg

# -- Performance mission-control --

# See what is running right now
fdw queries running SalesWH

# Long-running queries from the past hour
fdw queries long-running SalesWH --ago 1h

# Kill a runaway session by ID
fdw queries kill SalesWH 55

# Most-repeated queries over the past 24 hours
fdw queries frequent SalesWH --ago 24h

# -- Optimize --

# Inspect a statistics histogram with inline terminal bar charts
fdw statistics show SalesWH dbo.orders st_order_date --histogram

# Re-cluster a table on a new key (transactional CTAS-swap, auto-rollback on failure)
fdw tables cluster-by SalesWH dbo.orders --cluster-by customer_id

# -- Time travel + export --

# Browse the table as it looked 2 hours ago
fdw tables read SalesWH dbo.orders --ago 2h

# Export a point-in-time snapshot to Parquet
fdw tables export SalesWH dbo.orders --output snapshot.parquet --ago 2h

# -- Governance --

# Grant SELECT on a specific table
fdw permissions sql grant SalesWH SELECT --to analyst@company.com --object dbo.orders

# Deny access to sensitive columns (column-level security)
fdw permissions cls deny SalesWH SELECT --to contractor@company.com \
    --object dbo.orders --columns salary,bonus

# Create a row-level security policy (filter rows by SalesRep)
fdw permissions rls create SalesWH rls.SalesFilter \
    --filter "rls.fn_sales_filter(SalesRep)" --on dbo.orders

# -- Load + scaffold --

# Load a local Parquet file and auto-create the table from its schema
fdw tables load SalesWH dbo.orders --file orders.parquet --create

# Scaffold a full dbt-fabric project wired to the warehouse
fdw dbt init SalesWH ./my-dbt-project --project-name sales_dw --with-sources
```

### MCP Server

Add to your MCP client configuration (e.g. Claude Desktop, VS Code):

```json
{
  "mcpServers": {
    "fabric-dw": {
      "command": "uvx",
      "args": ["--from", "fabric-dw", "fabric-dw-mcp"]
    }
  }
}
```

The MCP server exposes all CLI operations as MCP tools (workspaces, warehouses, SQL endpoints, schemas, tables, views, queries, snapshots, restore points, audit, statistics, permissions, sql-pools). Bundled Claude Code agent skills (query-optimizer, warehouse-performance, dbt-setup) are included for deeper AI-assisted analysis. Set `FABRIC_AUTH` in the environment if you need a non-default auth mode.

## Run in Docker

The Docker image's default `ENTRYPOINT` is the **MCP server** (`fabric-dw-mcp`). Use it as-is with your MCP client, or override the entrypoint to run the CLI instead.

```bash
docker pull ghcr.io/sdebruyn/fabric-dw:latest

# Run the MCP server (default entrypoint, connect via stdio from your MCP client):
docker run --rm -i \
  -e AZURE_CLIENT_ID=… \
  -e AZURE_TENANT_ID=… \
  -e AZURE_CLIENT_SECRET=… \
  -e FABRIC_AUTH=sp \
  ghcr.io/sdebruyn/fabric-dw

# Run the CLI instead (override the entrypoint):
docker run --rm \
  --entrypoint fabric-dw \
  -e AZURE_CLIENT_ID=… \
  -e AZURE_TENANT_ID=… \
  -e AZURE_CLIENT_SECRET=… \
  -e FABRIC_AUTH=sp \
  ghcr.io/sdebruyn/fabric-dw --help
```

Dev images (built from every main merge): `ghcr.io/sdebruyn/fabric-dw:main` or `:<version>.dev<N>`.

Package page: [ghcr.io/sdebruyn/fabric-dw](https://github.com/sdebruyn/fabric-dw-mcp-cli/pkgs/container/fabric-dw)

#### Security environment variables

| Variable | Default | Description |
|---|---|---|
| `FABRIC_MCP_READONLY` | unset | Set to `1` to restrict `execute_sql` to SELECT/WITH and block all mutating tools. |
| `FABRIC_MCP_ALLOW_DESTRUCTIVE` | unset | Set to `1` to enable permanently-destructive tools (`delete_*`, `clear_table`, `restore_warehouse_in_place`). Disabled by default. |
| `FABRIC_MCP_WORKSPACES` | unset | Comma-separated workspace names or GUIDs the server may touch. Unset = all workspaces allowed. |
| `FABRIC_MCP_ALLOW_REMOTE` | unset | Set to `1` to allow the HTTP transport (`--transport http`) to bind on a non-loopback address. A warning is logged; ensure an authenticating reverse proxy with TLS fronts the endpoint. |

#### HTTP transport

The MCP server can be started in HTTP mode for remote clients:

```bash
fabric-dw-mcp --transport http [--host 127.0.0.1] [--port 8000]
```

Binding to non-loopback addresses requires `FABRIC_MCP_ALLOW_REMOTE=1`. The HTTP transport has **no built-in authentication or TLS**. Always front it with an authenticating reverse proxy.

## Develop in a container

Open the repo in [GitHub Codespaces](https://github.com/codespaces) or VS Code's Remote-Containers extension. The devcontainer pre-installs Python 3.14, uv, Azure CLI, and the GitHub CLI.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/sdebruyn/fabric-dw-mcp-cli)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, branch flow, and how to run tests locally.

📖 Docs: [fdw.debruyn.dev](https://fdw.debruyn.dev) (or run `uv run --only-group docs zensical serve` locally).

## Telemetry

`fabric-dw` collects opt-out usage telemetry. No SQL statements or credentials are ever sent. To opt out, set `FABRIC_DW_TELEMETRY_OPT_OUT=1`. See the [Telemetry docs](https://fdw.debruyn.dev/telemetry/) for the full list of collected fields and all opt-out methods.

## Security

Please report vulnerabilities privately. See [SECURITY.md](SECURITY.md).

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE). Copyright (c) 2026 Sam Debruyn
