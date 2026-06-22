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

For authentication configuration, see [Authentication](../install.md#authentication).

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

Set a default value. Accepts `workspace`, `warehouse`, `max-429-retries`, `retry-deadline`, `sql-retry-deadline`, `sql-retry-executes`, or `sql-pool` as a flat key, or the nested sub-commands `telemetry disabled` and `logging level` for section-scoped knobs.

**Synopsis**

```
fdw config set workspace VALUE
fdw config set warehouse VALUE
fdw config set max-429-retries N
fdw config set retry-deadline SECONDS
fdw config set sql-retry-deadline SECONDS
fdw config set sql-retry-executes true|false
fdw config set sql-pool true|false
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

Clear a single default value. Accepts `workspace`, `warehouse`, `max-429-retries`, `retry-deadline`, `sql-retry-deadline`, `sql-retry-executes`, or `sql-pool` as a flat key, or the nested sub-commands `telemetry disabled` and `logging level` for section-scoped knobs.

**Synopsis**

```
fdw config unset workspace
fdw config unset warehouse
fdw config unset max-429-retries
fdw config unset retry-deadline
fdw config unset sql-retry-deadline
fdw config unset sql-retry-executes
fdw config unset sql-pool
fdw config unset telemetry disabled
fdw config unset logging level
```
