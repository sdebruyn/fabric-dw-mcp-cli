---
title: Configuration & defaults
---

# Configuration & defaults

`fabric-dw` can store persistent defaults so you do not have to repeat options on every invocation. Stored defaults apply when neither the corresponding CLI option nor environment variable is set.

```shell
fdw config set workspace MyWorkspace
fdw config set warehouse MyWarehouse
```

Once set, the stored workspace is used whenever `-w` / `--workspace` is not passed and `FABRIC_DW_DEFAULT_WORKSPACE` is not set. The stored warehouse value fills in optional `[WAREHOUSE]` / `[ITEM]` positionals shown in `[brackets]` in the synopsis below. All stored values are resolved in the same way as explicit arguments (name or GUID).

Resolution order for the workspace (see also [Selecting a workspace](../concepts.md#selecting-a-workspace)):

1. `-w` / `--workspace` flag on the root command.
2. `FABRIC_DW_DEFAULT_WORKSPACE` environment variable.
3. Value stored by `fdw config set workspace`.

The warehouse follows the same order using the optional `[WAREHOUSE]` / `[ITEM]` positional or `FABRIC_DW_DEFAULT_WAREHOUSE`.

## HTTP retry budget

The 429 retry budget (consecutive retries and combined wall-clock deadline) can also be configured. Resolution order:

| Knob | CLI option | Env var | Config key | Built-in default |
|---|---|---|---|---|
| Max consecutive 429 retries | `--max-429-retries N` | `FABRIC_DW_MAX_429_RETRIES` | `max_429_retries` | 10 |
| Combined deadline (s) | `--retry-deadline SECONDS` | `FABRIC_DW_RETRY_DEADLINE_S` | `retry_deadline_s` | 300 |

To persist a higher retry budget for all invocations:

```shell
fdw config set max-429-retries 20
fdw config set retry-deadline 600
```

To revert to the built-in defaults:

```shell
fdw config unset max-429-retries
fdw config unset retry-deadline
```

## SQL retry budget

The SQL/TDS connect-phase and execute-phase retry budget is a separate layer from the HTTP retry knobs above and operates at the driver level. Resolution order (env var > config > built-in):

| Knob | Env var | Config key | Built-in default |
|---|---|---|---|
| Connect+execute deadline (s) | `FABRIC_SQL_RETRY_TIMEOUT_S` | `sql_retry_deadline_s` | 120 |
| Retry non-idempotent statements | `FABRIC_SQL_RETRY_EXECUTES` | `sql_retry_executes` | false |

`sql_retry_deadline_s` sets the total wall-clock budget for both the connect-phase and execute-phase retry loops. The built-in 120 s covers the observed Fabric warehouse warm-up window (~60–90 s).

`sql_retry_executes` opts in to retrying statements that use `fetch="none"` (INSERT, UPDATE, DELETE, DDL) on transient TDS errors. **WARNING: enabling this flag can cause a non-idempotent statement to execute more than once if a transient error occurs after the server begins processing it. Only enable when all such statements are idempotent.**

To persist a longer SQL retry budget:

```shell
fdw config set sql-retry-deadline 300
```

To opt in to retrying non-idempotent statements:

```shell
fdw config set sql-retry-executes true
```

To revert to the built-in defaults:

```shell
fdw config unset sql-retry-deadline
fdw config unset sql-retry-executes
```

## MCP workspace allowlist {#mcp-workspace-allowlist}

!!! warning "Security control"
    The workspace allowlist is an **access-control** setting. Mistakes here can silently allow or deny access to workspaces. Read the empty-value semantics carefully before configuring this knob.

The MCP server workspace allowlist restricts which workspaces the server may operate on. It is resolved in the following priority order (highest first):

| Layer | Mechanism | Description |
|---|---|---|
| 1 | `FABRIC_MCP_WORKSPACES` env var | Comma-separated workspace names or GUIDs. An empty or whitespace-only value (including `FABRIC_MCP_WORKSPACES=`) is treated as **absent** and falls through to the next layer - it does **not** block all workspaces. |
| 2 | `[mcp] workspace_allowlist` in `config.toml` | Stored with `fdw config set mcp workspace-allowlist`. An empty TOML array `[]` is treated as absent (no restriction) - consistent with the unset case. |
| 3 | Built-in default | No restriction - all workspaces allowed. |

Matching is case-insensitive and whitespace-trimmed. Both workspace names and GUIDs are accepted.

To restrict the MCP server to specific workspaces:

```shell
fdw config set mcp workspace-allowlist "Sales WS,Finance WS"
```

To revert to the built-in default (no restriction):

```shell
fdw config unset mcp workspace-allowlist
```

The env var takes highest priority. When both are set, `FABRIC_MCP_WORKSPACES` wins over the config file value.

## MCP server log level

The MCP server log level can be configured via a 3-layer stack. Resolution order (highest priority first):

| Layer | Mechanism | Description |
|---|---|---|
| 1 | `FABRIC_LOG_LEVEL` env var | Read at server start-up; takes precedence over everything else. Invalid or empty values fall through to the next layer with a warning. |
| 2 | `[logging] level` in `config.toml` | Stored with `fdw config set logging level`. Invalid values are discarded (treated as unset). |
| 3 | Built-in default | `INFO` |

Valid levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (case-insensitive).

To persist a level across MCP server restarts:

```shell
fdw config set logging level DEBUG
```

To revert to the built-in `INFO` default:

```shell
fdw config unset logging level
```

## SQL connection pooling

Connection pooling reuses open TDS connections across queries to avoid the TCP+TLS+TDS handshake overhead on every call. Resolution order (env var > config > built-in):

| Knob | Env var | Config key | Built-in default |
|---|---|---|---|
| Connection pooling enabled | `FABRIC_CONN_POOLING` | `conn_pooling` | true |

Pooling is enabled by default. Disable it only when diagnosing connection issues or when running in an environment where persistent connections are not supported.

To disable connection pooling:

```shell
fdw config set conn-pooling false
```

To re-enable pooling (or to revert to the built-in default):

```shell
fdw config set conn-pooling true
fdw config unset conn-pooling
```

## Telemetry

`fabric-dw` collects **anonymous, opt-out** usage telemetry - it is **on by default**. Any of the following independently disables it - no events are emitted and the SDK is never imported:

| Mechanism | Type | Effect |
|---|---|---|
| `FABRIC_DW_TELEMETRY_OPT_OUT` | env var | Any truthy value (not in `""`, `0`, `false`, `no`, `off`, case-insensitive) disables telemetry. |
| `DO_NOT_TRACK` | env var | The [consoledonottrack.com](https://consoledonottrack.com/) standard. Any truthy value (same rules as above) disables telemetry. |
| `[telemetry] disabled = true` in `config.toml` | config key | Set with `fdw config set telemetry disabled true`. |

To opt out via the config file:

```shell
fdw config set telemetry disabled true
```

To re-enable telemetry:

```shell
fdw config set telemetry disabled false
```

To revert to the built-in default:

```shell
fdw config unset telemetry disabled
```

See [Telemetry](../telemetry.md) for the full list of collected fields and lifecycle events.

## Credential mode

Both the `fdw` CLI and the MCP server (`fdw mcp`) resolve the credential mode from the same
4-layer stack. Resolution order (highest priority first):

| Layer | Mechanism | Description |
|---|---|---|
| 1 | `--auth` CLI flag | Wins when explicitly passed on the command line. |
| 2 | `FABRIC_AUTH` env var | Wins when non-empty/non-whitespace. An empty or whitespace-only value is treated as absent (falls through to the next layer). An unrecognised non-empty value raises a configuration error. |
| 3 | `[defaults] auth_mode` in `config.toml` | Stored with `fdw config set auth-mode`. Invalid values are discarded (treated as unset) with a warning. |
| 4 | Built-in default | `default` (DefaultAzureCredential chain) |

Valid modes: `default`, `interactive`, `sp` (case-insensitive).

!!! note "Security note"
    An empty or invalid value is never silently downgraded or substituted with an unexpected credential. Empty/whitespace `FABRIC_AUTH` always falls through to config/default; a non-empty but unrecognised value raises an error.

To persist a credential mode across CLI and MCP server invocations:

```shell
fdw config set auth-mode interactive
```

To revert to the built-in default:

```shell
fdw config unset auth-mode
```

To override for a single CLI invocation (highest priority):

```shell
fdw --auth sp warehouses list
```

See [Authentication](../authentication.md) for the full list of valid modes and their requirements.

---

## CLI

### config clear

Wipe **all** configuration defaults.

**Synopsis**

```
fdw config clear
```

---

### config set

Set a default value. Accepts `workspace`, `warehouse`, `max-429-retries`, `retry-deadline`, `sql-retry-deadline`, `sql-retry-executes`, `conn-pooling`, or `auth-mode` as a flat key, or the nested sub-commands `telemetry disabled`, `logging level`, and `mcp workspace-allowlist` for section-scoped knobs.

**Synopsis**

```
fdw config set workspace VALUE
fdw config set warehouse VALUE
fdw config set max-429-retries N
fdw config set retry-deadline SECONDS
fdw config set sql-retry-deadline SECONDS
fdw config set sql-retry-executes true|false
fdw config set conn-pooling true|false
fdw config set auth-mode MODE
fdw config set telemetry disabled true|false
fdw config set logging level LEVEL
fdw config set mcp workspace-allowlist WS1,WS2,...
```

**Example**

```shell
fdw config set workspace MyWorkspace
fdw config set warehouse MyWarehouse
fdw config set max-429-retries 20
fdw config set retry-deadline 600
fdw config set sql-retry-deadline 300
fdw config set sql-retry-executes true
fdw config set conn-pooling false
fdw config set auth-mode interactive
fdw config set telemetry disabled true
fdw config set logging level DEBUG
fdw config set mcp workspace-allowlist "Sales WS,Finance WS"
```

---

### config show

Print the current defaults.

**Synopsis**

```
fdw config show
```

**Example**

```shell
# Example - show current config
fdw config show
```

```
workspace  MyWorkspace
warehouse  MyWarehouse
```

---

### config unset

Clear a single default value. Accepts `workspace`, `warehouse`, `max-429-retries`, `retry-deadline`, `sql-retry-deadline`, `sql-retry-executes`, `conn-pooling`, or `auth-mode` as a flat key, or the nested sub-commands `telemetry disabled`, `logging level`, and `mcp workspace-allowlist` for section-scoped knobs.

**Synopsis**

```
fdw config unset workspace
fdw config unset warehouse
fdw config unset max-429-retries
fdw config unset retry-deadline
fdw config unset sql-retry-deadline
fdw config unset sql-retry-executes
fdw config unset conn-pooling
fdw config unset auth-mode
fdw config unset telemetry disabled
fdw config unset logging level
fdw config unset mcp workspace-allowlist
```
