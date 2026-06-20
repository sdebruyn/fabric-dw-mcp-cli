---
title: Schemas
---

# Schemas

Manage SQL schemas on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

**Targets:** Data Warehouse · SQL Analytics Endpoint

!!! note "SQL Analytics Endpoints"

    `schemas list`, `schemas create`, and `schemas delete` (CLI) and `list_schemas`, `create_schema`, and `delete_schema` (MCP) all work on both Fabric Data Warehouses and SQL Analytics Endpoints. When `schemas delete --cascade` (CLI) or `delete_schema` with `cascade=True` (MCP) is used on a SQL Analytics Endpoint, views, stored procedures, and functions in the schema are dropped, but tables are **not** dropped (because `DROP TABLE` is a Warehouse-only operation on Fabric). If the schema contains tables, the final `DROP SCHEMA` will be rejected by the engine; remove the tables manually first or omit `--cascade` and drop the schema only after it is empty.

---

## CLI

### schemas create

**Targets:** Data Warehouse · SQL Analytics Endpoint

Create a new SQL schema on a warehouse.

**Usage**

```shell
fdw [-w WORKSPACE] schemas create [OPTIONS] [WAREHOUSE] NAME
```

**Example**

```shell
fdw -w MyWorkspace schemas create SalesWH reporting
```

---

### schemas delete

**Targets:** Data Warehouse · SQL Analytics Endpoint

Drop a schema from a warehouse. You will be asked to confirm unless `--yes` is passed.

Pass `--cascade` to also drop all tables, views, functions, and stored procedures inside the schema before dropping the schema itself. **This is a destructive, irreversible operation.**

**Usage**

```shell
fdw [-w WORKSPACE] schemas delete [OPTIONS] [WAREHOUSE] NAME
```

| Option | Description |
| --- | --- |
| `--cascade` | Drop all tables, views, functions, and stored procedures in the schema first. **WARNING: permanently deletes all contained objects and data.** |

**Example**

```shell
# Drop an empty schema
fdw -w MyWorkspace --yes schemas delete SalesWH staging

# Drop a schema and all its tables, views, functions, and stored procedures
fdw -w MyWorkspace --yes schemas delete SalesWH staging --cascade
```

---

### schemas list

**Targets:** Data Warehouse · SQL Analytics Endpoint

List all user-defined schemas on a warehouse or SQL Analytics Endpoint. System schemas are excluded.

**Usage**

```shell
fdw [-w WORKSPACE] schemas list [OPTIONS] [WAREHOUSE]
```

**Example**

```shell
fdw -w MyWorkspace schemas list SalesWH
```

```
 name     principal_id
 ───────────────────────
 dbo      1
 sales    5
 staging  7
```

---

## MCP tools

### create_schema

**Targets:** Data Warehouse · SQL Analytics Endpoint

Create a new SQL schema on a Fabric Data Warehouse or SQL Analytics Endpoint.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `name` (`str`) — the schema name; must be a valid SQL identifier.

**Returns:** `Schema` — the newly-created schema record with `name` and `principal_id`.

---

### delete_schema

**Targets:** Data Warehouse · SQL Analytics Endpoint

Drop a SQL schema from a Fabric Data Warehouse or SQL Analytics Endpoint.

**CAUTION**: This is a destructive, irreversible operation. The schema will be permanently deleted. If the schema still contains tables or views the operation will fail unless `cascade=True`.

**CAUTION**: When `cascade=True`, **all tables, views, functions, and stored procedures in the schema are permanently deleted along with their data**. Confirm explicitly with the user before calling with `cascade=True`.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL analytics endpoint name or GUID.
- `name` (`str`) — the schema name to drop.
- `cascade` (`bool`, default `False`) — when `True`, drop all tables, views, functions, and stored procedures in the schema first.

**Returns:** `{ "deleted": true }` — confirmation.

---

### list_schemas

**Targets:** Data Warehouse · SQL Analytics Endpoint

List user-defined SQL schemas on a warehouse or SQL Analytics Endpoint. System schemas are excluded automatically.

**Parameters:**

- `workspace` (`str`) — workspace name or GUID.
- `item` (`str`) — warehouse or SQL Analytics Endpoint name or GUID.

**Returns:** `list[Schema]` — array of schema objects, each with `name` and `principal_id`.
