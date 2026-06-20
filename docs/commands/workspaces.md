---
title: Workspaces
---

# Workspaces

Manage Microsoft Fabric workspaces — list all workspaces the authenticated principal can see, inspect a single workspace's details (including its default Data Warehouse collation), and update that collation. All workspace commands operate at the workspace level and do not target a specific Data Warehouse or SQL Analytics Endpoint item.

**Targets:** Workspace (not item-specific)

!!! note "Workspace selection"

    The `workspaces` CLI group is exempt from the global `-w/--workspace` option. Pass the workspace name or GUID as a positional argument to `workspaces get` and `workspaces set-collation` instead.

---

## CLI

### workspaces assign-capacity

**Targets:** Workspace (not item-specific)

Assign a workspace to a Fabric capacity.

!!! note

    The `workspaces` group is exempt from the global `-w/--workspace` option. Pass the workspace name or GUID as a positional argument instead.

**Synopsis**

```
fdw workspaces assign-capacity [WORKSPACE] --capacity-id <UUID>
```

**Options**

- `--capacity-id` (`UUID`, required) — UUID of the capacity to assign the workspace to.

**Example**

```shell
fdw workspaces assign-capacity MyWorkspace --capacity-id ab12cd34-ef56-7890-abcd-ef1234567890
```

---

### workspaces get

**Targets:** Workspace (not item-specific)

Get details for a workspace, including its default Data Warehouse collation.

!!! note

    The `workspaces` group is exempt from the global `-w/--workspace` option. Pass the workspace name or GUID as a positional argument instead.

**Synopsis**

```
fdw workspaces get [WORKSPACE]
```

**Example**

```shell
fdw workspaces get MyWorkspace
```

```
id                                3f2a1c5d-...
displayName                       MyWorkspace
capacityId                        ab12cd34-...
defaultDataWarehouseCollation     Latin1_General_100_CI_AS_KS_WS_SC_UTF8
```

---

### workspaces list

**Targets:** Workspace (not item-specific)

List all workspaces the authenticated principal has access to.

**Synopsis**

```
fdw workspaces list
```

**Example**

```shell
fdw workspaces list
```

```
 id                                    displayName       capacityId
 ------------------------------------ ---------------- ------------------------------------
 3f2a1c5d-...                          MyWorkspace       ab12cd34-...
```

---

### workspaces list-capacities

**Targets:** Workspace (not item-specific)

List all Fabric capacities the authenticated principal has access to. Requires the `Capacity.Read.All` permission.

**Synopsis**

```
fdw workspaces list-capacities
```

**Example**

```shell
fdw workspaces list-capacities
```

```
 id                                    displayName    sku   region        state
 ------------------------------------ -------------- ----- ------------- ------
 ab12cd34-...                          MyCapacity     F64   West Europe   Active
```

---

### workspaces set-collation

**Targets:** Workspace (not item-specific)

Set the default Data Warehouse collation for a workspace. `COLLATION` must be one of the supported Fabric collations.

!!! note

    The `workspaces` group is exempt from the global `-w/--workspace` option. Pass the workspace name or GUID as a positional argument instead.

**Synopsis**

```
fdw workspaces set-collation [WORKSPACE] COLLATION
```

**Example**

```shell
fdw workspaces set-collation MyWorkspace Latin1_General_100_CI_AS_KS_WS_SC_UTF8
```

---

## MCP tools

### assign_workspace_to_capacity

**Targets:** Workspace (not item-specific)

Assign a workspace to a Fabric capacity. This is a mutating operation and is blocked when the MCP server is running in read-only mode.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `capacity_id` (`str`) — UUID of the capacity to assign the workspace to.

**Returns:** `{ "workspace_id": str, "capacity_id": str }` — the workspace GUID and the capacity GUID.

---

### get_workspace

**Targets:** Workspace (not item-specific)

Return details for a single workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `Workspace` — single workspace object (fields as above).

---

### list_capacities

**Targets:** Workspace (not item-specific)

List all Fabric capacities the authenticated principal has access to. Requires the `Capacity.Read.All` permission.

**Parameters:** None

**Returns:** `list[Capacity]` — array of capacity objects, each with `id`, `displayName`, `sku`, `region`, and `state`.

---

### list_workspaces

**Targets:** Workspace (not item-specific)

List all Fabric workspaces the authenticated principal has access to.

**Parameters:** None

**Returns:** `list[Workspace]` — array of workspace objects, each with `id`, `displayName`, `description`, `capacityId`, and `defaultDatasetStorageFormat`.

---

### set_workspace_collation

**Targets:** Workspace (not item-specific)

Set the default Data Warehouse collation for a workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `collation` (`str`) — collation name (must be a supported Fabric collation).

**Returns:** `{ "workspace_id": str, "collation": str }` — the workspace GUID and the newly-set collation.
