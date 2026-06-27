---
title: Shell completion (command)
---

# Shell completion (command)

Manage shell completion scripts. See [Shell Completion](../completion.md) for full installation details.

**Targets:** Workspace (not item-specific)

---

## CLI

### completion install

**Targets:** Workspace (not item-specific)

Generate and optionally install the tab-completion script for `bash`, `zsh`, or `fish`. Without `--print`, the script is written to the conventional location for the chosen shell (idempotent for bash and zsh). With `--print`, the script is sent to stdout so you can inspect or source it manually.

**Synopsis**

```
fdw completion install [--print] {bash|zsh|fish}
```

| Option | Description |
| --- | --- |
| `--print` | Print the completion script to stdout instead of installing it. |

| Shell | Install location |
| --- | --- |
| `bash` | Appended to `~/.bashrc` (idempotent) |
| `zsh` | Appended to `~/.zshrc` (idempotent) |
| `fish` | Written to `~/.config/fish/completions/fabric-dw.fish` |

**Example**

```shell
# Install for zsh
fdw completion install zsh

# Inspect the bash script before installing
fdw completion install bash --print
```

For AI-assistant (MCP) usage there is no shell completion - see [MCP server](../install.md#mcp) instead.
