---
title: Home
---

<p align="center">
  <img src="assets/logo.svg" alt="fabric-dw" width="180" />
</p>

# fabric-dw

<p align="center">
  <a href="https://pypi.org/project/fabric-dw/"><img src="https://img.shields.io/pypi/v/fabric-dw" alt="PyPI version"></a>
  <a href="https://pypi.org/project/fabric-dw/"><img src="https://img.shields.io/pypi/pyversions/fabric-dw" alt="Python versions"></a>
</p>

> Python CLI and MCP server for Microsoft Fabric Data Warehouses and SQL Analytics Endpoints: administer, query, optimize, and secure them from your terminal or your AI agent.

!!! tip "Just announced"
    Read the story behind `fabric-dw` in the [announcement blog post](https://debruyn.dev/2026/introducing-the-fabric-data-warehouse-cli-and-mcp-server/).

`fabric-dw` is an open-source Python project that lets you administer Microsoft Fabric Data Warehouses and SQL Analytics Endpoints from the command line or from an AI assistant. It authenticates via the Azure credential chain, Azure CLI, Managed Identity, service principal, environment variables, and more, so it works in both local and automated environments without additional setup. See [Authentication](authentication.md) for the full chain.

Both the CLI and the MCP server surfaces are built from a single Python package and share the same authentication, connection, and business logic layers. This means fixes and new features land in both interfaces simultaneously, and you only need to install one package regardless of how you plan to use it.

<div class="grid cards" markdown>

- :material-download: **[Install](install.md)**

    Get the package installed and verify your setup.

- :material-console: **[Commands](commands/index.md)**

    Per-domain CLI commands and MCP tools.

- :material-book-open-variant: **[Guides](guides/index.md)**

    Task-oriented, end-to-end admin walkthroughs.

- :material-puzzle: **[Agent Skills](skills.md)**

    Multi-step admin workflows for AI assistants.

</div>
