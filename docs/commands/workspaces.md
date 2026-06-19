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

### get_workspace

**Targets:** Workspace (not item-specific)

Return details for a single workspace.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.

**Returns:** `Workspace` — single workspace object (fields as above).

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
