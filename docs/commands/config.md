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
| Combined deadline (s) | `--retry-deadline SECONDS` | `FABRIC_DW_RETRY_DEADLINE_S` | `retry_deadline_s` | 300.0 |

To persist a higher retry budget for all invocations:

```shell
fdw config set max-429-retries 20
fdw config set retry-deadline 600.0
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
| Connect+execute deadline (s) | `FABRIC_SQL_RETRY_TIMEOUT_S` | `sql_retry_deadline_s` | 120.0 |
| Retry non-idempotent statements | `FABRIC_SQL_RETRY_EXECUTES` | `sql_retry_executes` | false |

`sql_retry_deadline_s` sets the total wall-clock budget for both the connect-phase and execute-phase retry loops. The built-in 120 s covers the observed Fabric warehouse warm-up window (~60–90 s).

`sql_retry_executes` opts in to retrying statements that use `fetch="none"` (INSERT, UPDATE, DELETE, DDL) on transient TDS errors. **WARNING: enabling this flag can cause a non-idempotent statement to execute more than once if a transient error occurs after the server begins processing it. Only enable when all such statements are idempotent.**

To persist a longer SQL retry budget:

```shell
fdw config set sql-retry-deadline 300.0
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
| Connection pooling enabled | `FABRIC_SQL_POOL` | `sql_pool` | true |

Pooling is enabled by default. Disable it only when diagnosing connection issues or when running in an environment where persistent connections are not supported.

To disable connection pooling:

```shell
fdw config set sql-pool false
```

To re-enable pooling (or to revert to the built-in default):

```shell
fdw config set sql-pool true
fdw config unset sql-pool
```

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

Set a default value. Accepts `workspace`, `warehouse`, `max-429-retries`, `retry-deadline`, `sql-retry-deadline`, `sql-retry-executes`, `sql-pool`, or `auth-mode` as a flat key, or the nested sub-commands `telemetry disabled` and `logging level` for section-scoped knobs.

**Synopsis**

```
fdw config set workspace VALUE
fdw config set warehouse VALUE
fdw config set max-429-retries N
fdw config set retry-deadline SECONDS
fdw config set sql-retry-deadline SECONDS
fdw config set sql-retry-executes true|false
fdw config set sql-pool true|false
fdw config set auth-mode MODE
fdw config set telemetry disabled true|false
fdw config set logging level LEVEL
```

**Example**

```shell
fdw config set workspace MyWorkspace
fdw config set warehouse MyWarehouse
fdw config set max-429-retries 20
fdw config set retry-deadline 600.0
fdw config set sql-retry-deadline 300.0
fdw config set sql-retry-executes true
fdw config set sql-pool false
fdw config set auth-mode interactive
fdw config set telemetry disabled true
fdw config set logging level DEBUG
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
# Example — show current config
fdw config show
```

```
workspace  MyWorkspace
warehouse  MyWarehouse
```

---

### config unset

Clear a single default value. Accepts `workspace`, `warehouse`, `max-429-retries`, `retry-deadline`, `sql-retry-deadline`, `sql-retry-executes`, `sql-pool`, or `auth-mode` as a flat key, or the nested sub-commands `telemetry disabled` and `logging level` for section-scoped knobs.

**Synopsis**

```
fdw config unset workspace
fdw config unset warehouse
fdw config unset max-429-retries
fdw config unset retry-deadline
fdw config unset sql-retry-deadline
fdw config unset sql-retry-executes
fdw config unset sql-pool
fdw config unset auth-mode
fdw config unset telemetry disabled
fdw config unset logging level
```
