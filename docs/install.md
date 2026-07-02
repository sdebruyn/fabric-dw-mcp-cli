---
title: Install
---

# Install

`fabric-dw` ships two surfaces from a single package: a **CLI** (`fabric-dw`, short alias `fdw`) and an **MCP server** (`fabric-dw-mcp`) for AI assistants. Both share the same authentication, connection, and business logic, so you install one package regardless of how you plan to use it.

Pick your path:

- **[Install the CLI](#cli)**: drive Fabric from a terminal.
- **[Install the MCP server](#mcp)**: wire Fabric tools into an AI assistant (Claude Code, Cursor, GitHub Copilot, Continue, Codex).
- **Both**: follow the [Prerequisites](#prerequisites) and [Authentication](#authentication) sections once, then complete each install section below.

## Prerequisites

- Python 3.11 or later.
- An Azure credential. The package picks one up automatically; see [Authentication](#authentication).

## Authentication {#authentication}

Both surfaces resolve a credential the same way. `fabric-dw` selects a credential source via the `FABRIC_AUTH` environment variable:

| `FABRIC_AUTH` value | What it uses |
| --- | --- |
| `default` (default) | [`azure-identity` `DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840): tries environment variables, Workload/Managed Identity, Azure CLI, Azure Developer CLI, Azure PowerShell, and interactive browser in order |
| `sp` | Service-principal (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`) |
| `interactive` | Browser pop-up |

See [Authentication](authentication.md) for the full credential chain, all supported sources, and debugging tips.

## Install the CLI {#cli}

```bash
pip install fabric-dw
# or run without installing:
uvx fabric-dw --help
```

[`uvx`](https://docs.astral.sh/uv/guides/tools/) fetches and runs the published package on demand without a global install. No setup is required beyond having `uv` available.

### Persistent install with uv tool install

If you want `fdw` to stay on `PATH` between sessions instead of being fetched on demand, use [`uv tool install`](https://docs.astral.sh/uv/guides/tools/) - the `uv` equivalent of `pipx install`:

```bash
uv tool install fabric-dw
```

Unlike `uvx`, which re-fetches and runs the package on demand without installing anything, `uv tool install` installs both entry points the package ships - `fdw` (this CLI) and `fabric-dw-mcp` (the [MCP server](#mcp)) - as persistent executables on `PATH`.

Upgrade to the latest release with:

```bash
uv tool upgrade fabric-dw
```

`fabric-dw` also publishes prerelease dev builds. To install the latest one, allow prereleases explicitly:

```bash
uv tool install --prerelease allow fabric-dw
```

Uninstall with:

```bash
uv tool uninstall fabric-dw
```

### Verify

After installation, confirm the CLI is available:

```bash
fdw --help
```

You should see the top-level help output listing available commands.

!!! tip "Short alias"
    `fdw` is an equivalent short alias for `fabric-dw`; both commands invoke the same entry point.

## Install the MCP server {#mcp}

`fabric-dw-mcp` is an [MCP](https://modelcontextprotocol.io) server that exposes Fabric Data Warehouse administration as tools your AI assistant can call. Pick your client below.

### TL;DR: uvx

The recommended runner is [`uvx`](https://docs.astral.sh/uv/guides/tools/), which fetches and runs the published package on demand without any global install:

```bash
uvx --from fabric-dw fabric-dw-mcp
```

The package is named `fabric-dw`, but the MCP entry point it ships is `fabric-dw-mcp`. Because the two names differ, `uvx` needs `--from fabric-dw` to know which package provides the `fabric-dw-mcp` command - `uvx fabric-dw-mcp` on its own would try to fetch a (non-existent) package called `fabric-dw-mcp` and fail.

Every client snippet below configures `uvx --from fabric-dw fabric-dw-mcp` as the entry point, so your AI tool always picks up the latest published version. Pin a version with `uvx --from fabric-dw==2026.6.0 fabric-dw-mcp` if you need reproducibility.

Prefer a persistent install over fetching on demand? Run `uv tool install fabric-dw` once (see [Persistent install with uv tool install](#persistent-install-with-uv-tool-install) above); it also puts `fabric-dw-mcp` directly on `PATH`, so client configs can point their `command` at `fabric-dw-mcp` with no `args` instead of `uvx --from fabric-dw fabric-dw-mcp`.

You will also need an Azure credential the server can use to call the Fabric REST and SQL APIs. Set the `FABRIC_AUTH` environment variable to select a source (see [Authentication](#authentication) above):

- `FABRIC_AUTH=default` (default): delegates to [`azure-identity` `DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential?WT.mc_id=MVP_310840), which tries environment variables, Workload/Managed Identity, Azure CLI, Azure Developer CLI, Azure PowerShell, and interactive browser in order.
- `FABRIC_AUTH=sp` plus `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` for service-principal auth.
- `FABRIC_AUTH=interactive` to force browser sign-in.

Two optional environment variables tune the HTTP retry budget:

| Variable | Description | Default |
|---|---|---|
| `FABRIC_DW_MAX_429_RETRIES` | Maximum consecutive 429 responses before raising `RateLimitedError` | 10 |
| `FABRIC_DW_RETRY_DEADLINE_S` | Combined wall-clock deadline (seconds) for 429-loop + 5xx retries | 300.0 |

### Claude Code

Source: <https://code.claude.com/docs/en/mcp>

Add the server at **user scope** (available in all your projects):

```bash
claude mcp add fabric-dw --scope user \
  --env FABRIC_AUTH=default \
  -- uvx --from fabric-dw fabric-dw-mcp
```

The server name (`fabric-dw`) must come before the `--env` flags: `--env` accepts multiple values, so a name placed after it is swallowed as an environment variable and the command fails with `Invalid environment variable format`.

For service-principal auth, pass the extra variables:

```bash
claude mcp add fabric-dw --scope user \
  --env FABRIC_AUTH=sp \
  --env AZURE_TENANT_ID=<tenant-id> \
  --env AZURE_CLIENT_ID=<client-id> \
  --env AZURE_CLIENT_SECRET=<client-secret> \
  -- uvx --from fabric-dw fabric-dw-mcp
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
      "args": ["--from", "fabric-dw", "fabric-dw-mcp"],
      "env": {
        "FABRIC_AUTH": "default"
      }
    }
  }
}
```

After adding, verify with `/mcp` inside a Claude Code session. You should see 27 tools including `list_workspaces`, `get_warehouse`, `kill_session`, and `clear_cache`.

### Cursor

Source: <https://learn.microsoft.com/microsoft-cloud/dev/dev-proxy/how-to/use-mcp-server?WT.mc_id=MVP_310840#configure-the-mcp-server>

Add this snippet to `~/.cursor/mcp.json` (global, all projects) or `.cursor/mcp.json` in a specific project root:

```json
{
  "mcpServers": {
    "fabric-dw": {
      "command": "uvx",
      "args": ["--from", "fabric-dw", "fabric-dw-mcp"],
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
      "args": ["--from", "fabric-dw", "fabric-dw-mcp"],
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

### GitHub Copilot

#### VS Code

Source: <https://code.visualstudio.com/docs/copilot/customization/mcp-servers>

Add the server in `.vscode/mcp.json` (workspace-level, check into VCS) or open the Command Palette and run **MCP: Open User Configuration** for a user-level file:

```json
{
  "servers": {
    "fabric-dw": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "fabric-dw", "fabric-dw-mcp"],
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
      "args": ["--from", "fabric-dw", "fabric-dw-mcp"],
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

#### CLI

Source: <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers>

The Copilot CLI (`gh copilot`) stores its MCP configuration separately from VS Code. Add the server to `~/.copilot/mcp-config.json` (user-level, applies to all sessions):

```json
{
  "mcpServers": {
    "fabric-dw": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "fabric-dw", "fabric-dw-mcp"],
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
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "fabric-dw", "fabric-dw-mcp"],
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

You can also add the server interactively without editing the file: start the CLI, enter `/mcp add`, choose **STDIO** as the server type, set the command to `uvx` with arguments `--from fabric-dw fabric-dw-mcp`, and add `FABRIC_AUTH=default` as an environment variable.

To share the configuration with a project instead of setting it globally, add a `.mcp.json` file (or `.github/mcp.json`) in the project root with the same `mcpServers` block. Project-level configuration takes precedence over user-level when server names conflict.

#### Desktop

Source: <https://docs.github.com/en/copilot/how-tos/github-copilot-app/customize-github-copilot-app>

The GitHub Copilot desktop app is built on top of Copilot CLI and shares the same MCP configuration. **Any MCP servers you have added to `~/.copilot/mcp-config.json` for the CLI are automatically available in the desktop app.** No separate config file is needed.

To add or manage MCP servers directly from the desktop app, open **Settings → MCP Servers**. The app provides a catalog of popular servers and accepts custom server definitions without requiring manual file edits.

To register `fabric-dw-mcp` via the settings UI, choose **Add custom server**, select **STDIO** as the type, set the command to `uvx`, the arguments to `--from fabric-dw fabric-dw-mcp`, and add `FABRIC_AUTH=default` as an environment variable. The app writes the result to `~/.copilot/mcp-config.json` using the same format shown in the [CLI section](#cli_1) above.

### Continue

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
      - --from
      - fabric-dw
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
      "args": ["--from", "fabric-dw", "fabric-dw-mcp"],
      "env": {
        "FABRIC_AUTH": "default"
      }
    }
  ]
}
```

### Codex CLI

Source: <https://developers.openai.com/codex/mcp>

Add to `~/.codex/config.toml` (global) or `.codex/config.toml` in a project directory:

```toml
[mcp_servers.fabric-dw]
command = "uvx"
args = ["--from", "fabric-dw", "fabric-dw-mcp"]

[mcp_servers.fabric-dw.env]
FABRIC_AUTH = "default"
```

For service-principal auth:

```toml
[mcp_servers.fabric-dw]
command = "uvx"
args = ["--from", "fabric-dw", "fabric-dw-mcp"]

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
args = ["--from", "fabric-dw", "fabric-dw-mcp"]
env_vars = ["FABRIC_AUTH", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"]
```

Codex picks up config changes on the next invocation; no explicit reload is needed.

### Security environment variables {#security-environment-variables}

The MCP server reads the following environment variables to restrict what it may do. Pass them via your client's `env` block or your shell environment:

| Variable | Default | Description |
|---|---|---|
| `FABRIC_MCP_READONLY` | unset | Set to `1` to restrict `execute_sql` to SELECT/WITH and block all mutating tools. |
| `FABRIC_MCP_ALLOW_DESTRUCTIVE` | unset | Set to `1` to enable permanently-destructive tools (`delete_*`, `clear_table`, `restore_warehouse_in_place`). Disabled by default. |
| `FABRIC_MCP_WORKSPACES` | unset | Comma-separated workspace names or GUIDs the server may touch. Highest-priority layer of the workspace allowlist knob; see below. An empty or whitespace-only value is treated as absent (falls through to the config layer). |
| `FABRIC_MCP_ALLOW_REMOTE` | unset | Set to `1` to allow the HTTP transport (`--transport http`) to bind on a non-loopback address. Always front with an authenticating reverse proxy that handles TLS. |

#### Workspace allowlist

`FABRIC_MCP_WORKSPACES` is the highest-priority layer of a 3-layer workspace allowlist knob. Resolution order (highest first):

1. `FABRIC_MCP_WORKSPACES` env var: an empty or whitespace-only value falls through to layer 2.
2. `[mcp] workspace_allowlist` in `config.toml`, set via `fdw config set mcp workspace-allowlist`: an empty array `[]` falls through to layer 3.
3. Built-in default: no restriction (all workspaces allowed).

An empty list at any layer is **not** treated as "block everything"; it falls through to the next layer. This prevents an accidental `FABRIC_MCP_WORKSPACES=` from locking out all workspaces. See [MCP workspace allowlist](commands/config.md#mcp-workspace-allowlist) for the config knob.

### Logging

The MCP server log level is resolved in priority order: `FABRIC_LOG_LEVEL` env var (highest) > `[logging] level` in `config.toml` > `INFO` (built-in default).

| Variable | Default | Description |
|---|---|---|
| `FABRIC_LOG_LEVEL` | `INFO` | Log level for the MCP server (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). Empty or unrecognised values fall through to the config/default layer with a warning. |

To persist the log level across restarts without setting an env var each time, use the config knob; see [MCP server log level](commands/config.md#mcp-server-log-level).

### HTTP transport

The MCP server can be started in HTTP mode for remote clients:

```bash
fabric-dw-mcp --transport http [--host 127.0.0.1] [--port 8000]
```

Binding to non-loopback addresses requires `FABRIC_MCP_ALLOW_REMOTE=1`. The HTTP transport has **no built-in authentication or TLS** - always front it with an authenticating reverse proxy.

### Verify the MCP server

After configuring your client, restart it and ask the assistant to list its available tools. You should see entries like `list_workspaces`, `get_warehouse`, `kill_session`, `clear_cache`, and 23 others (27 tools total).

### MCP troubleshooting

- **Permission denied calling Fabric**: run `az account show` and confirm your account has at least Workspace Contributor on the target Fabric workspace.
- **`uv: command not found`**: install uv from <https://docs.astral.sh/uv/>.
- **`uvx: command not found`**: `uvx` ships with uv; install uv and it will be available.
- **Server hangs at startup**: the server is likely waiting for a credential to be resolved. Ensure your Azure credential is set up (see [Authentication](authentication.md)) before starting your assistant.
- **Tools not visible in Copilot Chat**: ensure you are in **Agent** mode, not Ask or Edit mode.
- **Tools not visible in Continue**: ensure you are in **Agent** mode.

For a more complete reference of error messages and resolutions, see the [Troubleshooting](troubleshooting.md) page.

See also the [Authentication](authentication.md) page for the full credential chain and the [Troubleshooting](troubleshooting.md) page for common failure modes such as expired tokens and 403 permission errors.
