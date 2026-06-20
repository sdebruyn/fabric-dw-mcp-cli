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
| Combined deadline (s) | `--retry-deadline SECONDS` | `FABRIC_DW_COMBINED_DEADLINE_S` | `combined_deadline_s` | 300.0 |

To persist a higher retry budget for all invocations:

```shell
fdw config set max-429-retries 20
fdw config set combined-deadline 600.0
```

To revert to the built-in defaults:

```shell
fdw config unset max-429-retries
fdw config unset combined-deadline
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

Set a default value. Accepts `workspace`, `warehouse`, `max-429-retries`, or `combined-deadline` as the key.

**Synopsis**

```
fdw config set workspace VALUE
fdw config set warehouse VALUE
fdw config set max-429-retries N
fdw config set combined-deadline SECONDS
```

**Example**

```shell
fdw config set workspace MyWorkspace
fdw config set warehouse MyWarehouse
fdw config set max-429-retries 20
fdw config set combined-deadline 600.0
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

Clear a single default value. Accepts `workspace`, `warehouse`, `max-429-retries`, or `combined-deadline` as the key.

**Synopsis**

```
fdw config unset workspace
fdw config unset warehouse
fdw config unset max-429-retries
fdw config unset combined-deadline
```
