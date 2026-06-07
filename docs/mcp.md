---
title: MCP server install
---

# Install the fabric-dw MCP server

`fabric-dw-mcp` is an [MCP](https://modelcontextprotocol.io) server that exposes Fabric Data Warehouse administration as tools your AI assistant can call. Pick your client below.

## TL;DR — uvx

The recommended runner is [`uvx`](https://docs.astral.sh/uv/guides/tools/), which fetches and runs the published package on demand without any global install:

```bash
uvx fabric-dw-mcp
```

Every client snippet below configures `uvx fabric-dw-mcp` as the entry point, so your AI tool always picks up the latest published version. Pin a version with `uvx fabric-dw-mcp@2026.6.0` if you need reproducibility.

You will also need:

- Azure CLI logged in (`az login`) so the server can call the Fabric REST and SQL APIs as you.
- Optionally, environment variables to switch auth modes:
    - `FABRIC_AUTH=default` (default — uses your `az login` session).
    - `FABRIC_AUTH=sp` plus `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` for service-principal auth.
    - `FABRIC_AUTH=interactive` to force browser sign-in.

## Claude Code

Source: <https://code.claude.com/docs/en/mcp>

Add the server at **user scope** (available in all your projects):

```bash
claude mcp add --scope user \
  --env FABRIC_AUTH=default \
  fabric-dw -- uvx fabric-dw-mcp
```

For service-principal auth, pass the extra variables:

```bash
claude mcp add --scope user \
  --env FABRIC_AUTH=sp \
  --env AZURE_TENANT_ID=<tenant-id> \
  --env AZURE_CLIENT_ID=<client-id> \
  --env AZURE_CLIENT_SECRET=<client-secret> \
  fabric-dw -- uvx fabric-dw-mcp
```

To share the server configuration with your team instead, use `--scope project`. Claude Code writes a `.mcp.json` file to the project root; check that file into version control. Scope reference:

| Scope | Stored in | Shared |
| --- | --- | --- |
| `local` (default) | `~/.claude.json` | No |
| `project` | `.mcp.json` in project root | Yes, via VCS |
| `user` | `~/.claude.json` | No |

The equivalent `.mcp.json` snippet (project scope, `default` auth):

```json
{
  "mcpServers": {
    "fabric-dw": {
      "command": "uvx",
      "args": ["fabric-dw-mcp"],
      "env": {
        "FABRIC_AUTH": "default"
      }
    }
  }
}
```

After adding, verify with `/mcp` inside a Claude Code session. You should see 23 tools including `list_workspaces`, `get_warehouse`, `kill_session`, and `clear_cache`.

## Cursor

Source: <https://learn.microsoft.com/microsoft-cloud/dev/dev-proxy/how-to/use-mcp-server#configure-the-mcp-server>

Add this snippet to `~/.cursor/mcp.json` (global, all projects) or `.cursor/mcp.json` in a specific project root:

```json
{
  "mcpServers": {
    "fabric-dw": {
      "command": "uvx",
      "args": ["fabric-dw-mcp"],
      "env": {
        "FABRIC_AUTH": "default"
      }
    }
  }
}
```

For service-principal auth:

```json
{
  "mcpServers": {
    "fabric-dw": {
      "command": "uvx",
      "args": ["fabric-dw-mcp"],
      "env": {
        "FABRIC_AUTH": "sp",
        "AZURE_TENANT_ID": "<tenant-id>",
        "AZURE_CLIENT_ID": "<client-id>",
        "AZURE_CLIENT_SECRET": "<client-secret>"
      }
    }
  }
}
```

Restart Cursor after saving. Open **Settings → MCP** to confirm the server status.

## GitHub Copilot (VS Code)

Source: <https://code.visualstudio.com/docs/copilot/customization/mcp-servers>

Add the server in `.vscode/mcp.json` (workspace-level, check into VCS) or open the Command Palette and run **MCP: Open User Configuration** for a user-level file:

```json
{
  "servers": {
    "fabric-dw": {
      "type": "stdio",
      "command": "uvx",
      "args": ["fabric-dw-mcp"],
      "env": {
        "FABRIC_AUTH": "default"
      }
    }
  }
}
```

For service-principal auth:

```json
{
  "servers": {
    "fabric-dw": {
      "type": "stdio",
      "command": "uvx",
      "args": ["fabric-dw-mcp"],
      "env": {
        "FABRIC_AUTH": "sp",
        "AZURE_TENANT_ID": "<tenant-id>",
        "AZURE_CLIENT_ID": "<client-id>",
        "AZURE_CLIENT_SECRET": "<client-secret>"
      }
    }
  }
}
```

!!! note "VS Code Copilot MCP support"
    MCP support in VS Code Copilot is generally available as of VS Code 1.99 (April 2025). Use **Agent mode** in Copilot Chat to access MCP tools; they are not available in Ask or Edit modes.

After saving, open GitHub Copilot Chat, switch to **Agent** mode, and click the tools icon to confirm `fabric-dw` appears.

The Copilot CLI (`gh copilot`) uses a separate config file. Add to `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "fabric-dw": {
      "command": "uvx",
      "args": ["fabric-dw-mcp"],
      "env": {
        "FABRIC_AUTH": "default"
      }
    }
  }
}
```

Source for Copilot CLI file path: <https://learn.microsoft.com/power-apps/maker/data-platform/data-platform-mcp-vscode#github-copilot-cli>

## Continue

Source: <https://docs.continue.dev/customize/deep-dives/mcp>

Create `.continue/mcpServers/fabric-dw.yaml` in your workspace root (or `~/.continue/mcpServers/fabric-dw.yaml` for a global config):

```yaml
name: fabric-dw
version: 0.0.1
schema: v1
mcpServers:
  - name: fabric-dw
    type: stdio
    command: uvx
    args:
      - fabric-dw-mcp
    env:
      FABRIC_AUTH: default
```

For service-principal auth, replace the `env` block:

```yaml
    env:
      FABRIC_AUTH: sp
      AZURE_TENANT_ID: <tenant-id>
      AZURE_CLIENT_ID: <client-id>
      AZURE_CLIENT_SECRET: <client-secret>
```

!!! note
    MCP servers are only available in **Agent** mode in Continue. Switch to Agent mode in the chat panel before invoking tools.

Alternatively, if you prefer to stay in JSON and already have a `.continue/config.json`, add to the top-level `mcpServers` array:

```json
{
  "mcpServers": [
    {
      "name": "fabric-dw",
      "type": "stdio",
      "command": "uvx",
      "args": ["fabric-dw-mcp"],
      "env": {
        "FABRIC_AUTH": "default"
      }
    }
  ]
}
```

## Codex CLI

Source: <https://developers.openai.com/codex/mcp>

Add to `~/.codex/config.toml` (global) or `.codex/config.toml` in a project directory:

```toml
[mcp_servers.fabric-dw]
command = "uvx"
args = ["fabric-dw-mcp"]

[mcp_servers.fabric-dw.env]
FABRIC_AUTH = "default"
```

For service-principal auth:

```toml
[mcp_servers.fabric-dw]
command = "uvx"
args = ["fabric-dw-mcp"]

[mcp_servers.fabric-dw.env]
FABRIC_AUTH = "sp"
AZURE_TENANT_ID = "<tenant-id>"
AZURE_CLIENT_ID = "<client-id>"
AZURE_CLIENT_SECRET = "<client-secret>"
```

If you prefer to read secrets from the environment rather than hard-coding them in the config file, use `env_vars` to pass through variables that are already set in your shell:

```toml
[mcp_servers.fabric-dw]
command = "uvx"
args = ["fabric-dw-mcp"]
env_vars = ["FABRIC_AUTH", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"]
```

Codex picks up config changes on the next invocation; no explicit reload is needed.

## Verifying

After configuring your client, restart it and ask the assistant to list its available tools. You should see entries like `list_workspaces`, `get_warehouse`, `kill_session`, `clear_cache`, and 19 others (23 tools total).

## Troubleshooting

- **Permission denied calling Fabric**: run `az account show` and confirm your account has at least Workspace Contributor on the target Fabric workspace.
- **`uv: command not found`**: install uv from <https://docs.astral.sh/uv/>.
- **`uvx: command not found`**: `uvx` ships with uv — install uv and it will be available.
- **Server hangs at startup**: the server is likely waiting for an Azure CLI token refresh. Run `az login` before starting your assistant.
- **Tools not visible in Copilot Chat**: ensure you are in **Agent** mode, not Ask or Edit mode.
- **Tools not visible in Continue**: ensure you are in **Agent** mode.

For a more complete reference of error messages and resolutions, see the [Troubleshooting](troubleshooting.md) page.
