---
title: Warehouses
---

# Warehouses

Manage Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

**Targets:** Data Warehouse · SQL Analytics Endpoint

---

## CLI

### warehouses create

**Targets:** Data Warehouse only

Create a new warehouse in a workspace.

**Synopsis**

```
fdw [-w WORKSPACE] warehouses create [OPTIONS] NAME
```

| Option | Description |
| --- | --- |
| `--collation TEXT` | Default collation for the new warehouse. |
| `--description TEXT` | Description for the new warehouse. |

**Example**

```shell
fdw -w MyWorkspace warehouses create NewWH --description "Staging warehouse"
```

---

### warehouses delete

**Targets:** Data Warehouse only

Delete a warehouse. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] warehouses delete [WAREHOUSE]
```

**Example**

```shell
fdw -w MyWorkspace --yes warehouses delete OldWH
```

---

### warehouses get

**Targets:** Data Warehouse only

Get details for a specific Data Warehouse. Uses the warehouse-scoped REST path (`GET /workspaces/{ws}/warehouses/{id}`); passing a SQL Analytics Endpoint GUID will return a 404. Use `sql-endpoints get` to retrieve endpoint details.

**Synopsis**

```
fdw [-w WORKSPACE] warehouses get [WAREHOUSE]
```

**Example**

```shell
fdw -w MyWorkspace warehouses get SalesWH
```

```
id             7c3f...
displayName    SalesWH
description    Main sales warehouse
```

---

### warehouses list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all Data Warehouses and SQL Analytics Endpoints in a workspace. Pass `-A` / `--all-workspaces` to aggregate across every visible workspace. `-w` / `--workspace` and `--all-workspaces` are mutually exclusive.

**Synopsis**

```
fdw [-w WORKSPACE] warehouses list [-A] [--warehouses-only]
```

| Option | Description |
| --- | --- |
| `-A` / `--all-workspaces` | Scan all visible workspaces and aggregate results. Mutually exclusive with `-w`. |
| `--warehouses-only` | List only Warehouses; exclude SQL Analytics Endpoints (skips an API call). |

**Example**

```shell
# List warehouses in the default (or configured) workspace
fdw warehouses list

# List warehouses in a specific workspace
fdw -w MyWorkspace warehouses list

# Aggregate across all visible workspaces
fdw warehouses list --all-workspaces
```

```
 workspace      displayName    id
 -------------- -------------- ------------------------------------
 MyWorkspace    SalesWH        7c3f...
 OtherWS        AnalyticsWH    1a2b...
```

---

### warehouses permissions

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all principals (users, groups, service principals) with access to a warehouse, including their effective permissions. Requires **Fabric Administrator** role.

**Synopsis**

```
fdw [-w WORKSPACE] [--json] warehouses permissions [WAREHOUSE]
```

| Option | Description |
| --- | --- |
| `--json` | Emit raw JSON instead of a Rich table. Pass on the root command. |

**Example**

```shell
# Tabular output
fdw -w MyWorkspace warehouses permissions SalesWH

# Raw JSON
fdw -w MyWorkspace --json warehouses permissions SalesWH
```

```
 Display Name    UPN / App ID             Type    Permissions    Additional Permissions
 --------------- ------------------------ ------- -------------- ----------------------
 Alice           alice@contoso.com        User    Read, Write
 DataPipeline    00000000-0000-...        ServicePrincipal  Read
```

---

### warehouses rename

**Targets:** Data Warehouse only

Rename a warehouse and optionally update its description.

**Synopsis**

```
fdw [-w WORKSPACE] warehouses rename [OPTIONS] [WAREHOUSE] NEW_NAME
```

| Option | Description |
| --- | --- |
| `--description TEXT` | Optional new description. |

**Example**

```shell
fdw -w MyWorkspace warehouses rename SalesWH SalesWH_v2 --description "Renamed"
```

---

### warehouses takeover

**Targets:** Data Warehouse only

Take ownership of a warehouse. Not supported for SQL Analytics Endpoints.

**Synopsis**

```
fdw [-w WORKSPACE] warehouses takeover [WAREHOUSE]
```

**Example**

```shell
fdw -w MyWorkspace warehouses takeover SalesWH
```

---

## MCP tools

### create_warehouse

**Targets:** Data Warehouse only

Create a new Warehouse in a workspace.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `name` (`str`): display name for the new warehouse.
- `collation` (`str | null`, optional): default collation for the new warehouse.
- `description` (`str | null`, optional): description for the new warehouse.

**Returns:** `Warehouse`: the newly-created warehouse object.

---

### delete_warehouse

**Targets:** Data Warehouse only

Delete a Warehouse.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.

**Returns:** `{ "deleted": true, "warehouse_id": str }`: confirmation with the warehouse GUID.

---

### get_warehouse

**Targets:** Data Warehouse only

Return details for a single Data Warehouse. Uses the warehouse-scoped REST path (`GET /workspaces/{ws}/warehouses/{id}`); passing a SQL Analytics Endpoint will return a 404. Use `get_sql_endpoint` to retrieve endpoint details.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.

**Returns:** `Warehouse`: single warehouse object (fields as above).

---

### get_warehouse_permissions

**Targets:** Data Warehouse · SQL Analytics Endpoint

Return all principals (users, groups, service principals) with access to a Warehouse, including their effective permissions.

!!! note

    Requires **Fabric Administrator** role (`Tenant.Read.All` or `Tenant.ReadWrite.All` scope). See [Microsoft Fabric admin documentation](https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin?WT.mc_id=MVP_310840) for how to request the role.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.

**Returns:** `list[ItemAccess]`: array of access records, each with `principal` (containing `id`, `displayName`, `type`, and type-specific fields such as `userPrincipalName` or `aadAppId`) and `itemAccessDetails` (containing `type`, `permissions`, and `additionalPermissions`).

---

### list_warehouses

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all warehouses and SQL analytics endpoints in a workspace, or across all visible workspaces.

**Parameters:**

- `workspace` (`str`): workspace name or GUID; required when `all_workspaces` is `false`; ignored when `true`.
- `all_workspaces` (`bool`, default `false`): when `true`, aggregate results across every workspace the caller can see.

**Returns:** `list[Warehouse]`: array of warehouse objects, each with `id`, `displayName`, `description`, `workspaceId`, `kind` (`Warehouse` or `SQLEndpoint`), `connectionString`, `defaultCollation`, and `createdDate`.

---

### rename_warehouse

**Targets:** Data Warehouse only

Rename a Warehouse and optionally update its description.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.
- `new_name` (`str`): new display name.
- `description` (`str | null`, optional): new description; omit to leave unchanged.

**Returns:** `Warehouse`: the updated warehouse object.

---

### takeover_warehouse

**Targets:** Data Warehouse only

Take ownership of a Warehouse. Not supported for SQL analytics endpoints.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `warehouse` (`str`): warehouse name or GUID.

**Returns:** `{ "taken_over": true, "warehouse_id": str }`: confirmation with the warehouse GUID.
