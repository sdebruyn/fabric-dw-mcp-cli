---
title: Home
---

<p align="center">
  <img src="assets/logo.svg" alt="fabric-dw" width="180" />
</p>

# fabric-dw

> Python CLI + MCP server for Microsoft Fabric Data Warehouse administration.

`fabric-dw` is an open-source Python project that lets you administer Microsoft Fabric Data Warehouses and SQL Analytics Endpoints from the command line or from an AI assistant. It authenticates via the Azure credential chain — Azure CLI, Managed Identity, service principal, environment variables, and more — so it works in both local and automated environments without additional setup. See [Authentication](authentication.md) for the full chain.

Both the CLI and the MCP server surfaces are built from a single Python package and share the same authentication, connection, and business logic layers. This means fixes and new features land in both interfaces simultaneously, and you only need to install one package regardless of how you plan to use it.

<div class="grid cards" markdown>

- :material-download: **[Install](install.md)**

    Get the package installed and verify your setup.

- :material-console: **[CLI Reference](cli.md)**

    Command-line interface for DW administration tasks.

- :material-server: **[MCP Reference](mcp.md)**

    MCP server tools for AI assistant integration.

</div>
