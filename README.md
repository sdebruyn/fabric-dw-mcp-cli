<p align="center">
  <a href="https://fdw.debruyn.dev">
    <img src="docs/assets/logo.svg" alt="fabric-dw" width="140" />
  </a>
</p>

# fabric-dw

> Python CLI and MCP server for administering Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

## Status

**Alpha — work in progress.** The API and CLI interface may change without notice. See the [open issues](https://github.com/sdebruyn/fabric-dw-mcp-cli/issues) for current status.

## Description

`fabric-dw` provides two interfaces for managing Microsoft Fabric Data Warehouses and SQL Analytics Endpoints:

- **CLI** — a command-line tool for common DW administration tasks.
- **MCP server** — a [Model Context Protocol](https://modelcontextprotocol.io) server that exposes DW operations as tools for AI assistants.

Authentication is handled via the Azure CLI (`az login`).

## Installation

```bash
pip install fabric-dw
```

> Note: placeholder release; CLI/MCP under active development. Installation instructions will be updated on first release.

## Quick Start

### CLI

```bash
# Coming soon
fabric-dw --help
```

### MCP Server

```json
// Coming soon — add to your MCP client configuration
{
  "mcpServers": {
    "fabric-dw": {
      "command": "fabric-dw-mcp"
    }
  }
}
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, branch flow, and how to run tests locally.

📖 Docs: [fdw.debruyn.dev](https://fdw.debruyn.dev) (or run `uv run --only-group docs zensical serve` locally).

## Security

Please report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## Code of Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE) — Copyright (c) 2026 Sam Debruyn
