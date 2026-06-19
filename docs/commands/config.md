---
title: Configuration & defaults
---

# Configuration & defaults

`fabric-dw` can store a default workspace and/or warehouse so you do not have to repeat them on every invocation.

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

Set a default value. Accepts `workspace` or `warehouse` as the key.

**Synopsis**

```
fdw config set workspace VALUE
fdw config set warehouse VALUE
```

**Example**

```shell
fdw config set workspace MyWorkspace
fdw config set warehouse MyWarehouse
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

Clear a single default value. Accepts `workspace` or `warehouse` as the key.

**Synopsis**

```
fdw config unset workspace
fdw config unset warehouse
```
