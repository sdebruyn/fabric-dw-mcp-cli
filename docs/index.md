---
title: Home
---

# fabric-dw

> Python CLI + MCP server for Microsoft Fabric Data Warehouse administration.

`fabric-dw` is an open-source Python project that lets you administer Microsoft Fabric Data Warehouses and SQL Analytics Endpoints from the command line or from an AI assistant. It authenticates via the Azure CLI (`az login`) so no additional credentials need to be managed.

Both the CLI and the MCP server surfaces are built from a single Python package and share the same authentication, connection, and business logic layers. This means fixes and new features land in both interfaces simultaneously, and you only need to install one package regardless of how you plan to use it.

<div class="grid cards" markdown>

- :material-download: **[Install](install.md)**

    Get the package installed and verify your setup.

- :material-console: **[CLI Reference](cli.md)**

    Command-line interface for DW administration tasks.

- :material-server: **[MCP Reference](mcp.md)**

    MCP server tools for AI assistant integration.

</div>
