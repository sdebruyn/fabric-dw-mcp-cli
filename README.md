<p align="center">
  <a href="https://fdw.debruyn.dev">
    <img src="docs/assets/logo.svg" alt="fabric-dw logo" width="200" />
  </a>
</p>
<h1 align="center">fabric-dw</h1>

<p align="center">
  <a href="https://github.com/sdebruyn/fabric-dw-mcp-cli/actions/workflows/ci.yml"><img src="https://github.com/sdebruyn/fabric-dw-mcp-cli/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/fabric-dw/"><img src="https://img.shields.io/pypi/v/fabric-dw" alt="PyPI version"></a>
  <a href="https://pypi.org/project/fabric-dw/"><img src="https://img.shields.io/pypi/pyversions/fabric-dw" alt="Python versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/sdebruyn/fabric-dw-mcp-cli" alt="License"></a>
</p>

> **Alpha — work in progress.** The API and CLI interface may change without notice. See the [open issues](https://github.com/sdebruyn/fabric-dw-mcp-cli/issues) for current status.

Python CLI and MCP server for administering Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

## Description

`fabric-dw` provides two interfaces for managing Microsoft Fabric Data Warehouses and SQL Analytics Endpoints:

- **CLI** — a command-line tool for common DW administration tasks.
- **MCP server** — a [Model Context Protocol](https://modelcontextprotocol.io) server that exposes DW operations as tools for AI assistants.

Authentication is configured via the `FABRIC_AUTH` environment variable. The default (`FABRIC_AUTH=default`) uses [`azure-identity` `DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840), which walks environment variables, Workload/Managed Identity, Azure CLI, Azure Developer CLI, Azure PowerShell, and interactive browser in order — any of these will satisfy it. See the [Authentication](https://fdw.debruyn.dev/authentication/) docs for the full chain, all supported sources, and debugging tips.

## Installation

```bash
pip install fabric-dw
# or run without installing:
uvx fabric-dw --help
```

## Quick Start

### CLI

```bash
# List all workspaces you have access to
uvx fabric-dw workspaces list

# List warehouses and SQL Analytics Endpoints in a workspace
uvx fabric-dw warehouses list <workspace-name-or-id>

# Execute a SQL query against a warehouse
uvx fabric-dw sql exec <workspace-name-or-id> <warehouse-name-or-id> "SELECT TOP 10 * FROM dbo.my_table"

# List restore points for a warehouse
uvx fabric-dw restore-points list <workspace-name-or-id> <warehouse-name-or-id>
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

The MCP server exposes all CLI operations (workspaces, warehouses, SQL endpoints, audit, queries, snapshots, restore points, schemas, tables, views) as MCP tools. Set `FABRIC_AUTH` in the environment if you need a non-default auth mode.

## Run in Docker

```bash
docker pull ghcr.io/sdebruyn/fabric-dw:latest
docker run --rm \
  -e AZURE_CLIENT_ID=… \
  -e AZURE_TENANT_ID=… \
  -e AZURE_CLIENT_SECRET=… \
  -e FABRIC_AUTH=sp \
  ghcr.io/sdebruyn/fabric-dw --help
```

Dev images (built from every main merge): `ghcr.io/sdebruyn/fabric-dw:main` or `:<version>.dev<N>`.

Package page: [ghcr.io/sdebruyn/fabric-dw](https://github.com/sdebruyn/fabric-dw-mcp-cli/pkgs/container/fabric-dw)

## Develop in a container

Open the repo in [GitHub Codespaces](https://github.com/codespaces) or VS Code's Remote-Containers extension — the devcontainer pre-installs Python 3.13, uv, Azure CLI, and the GitHub CLI.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/sdebruyn/fabric-dw-mcp-cli)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, branch flow, and how to run tests locally.

📖 Docs: [fdw.debruyn.dev](https://fdw.debruyn.dev) (or run `uv run --only-group docs zensical serve` locally).

## Security

Please report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE) — Copyright (c) 2026 Sam Debruyn
