---
title: Stored procedures
---

# Stored procedures

Manage stored procedures on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

**Targets:** Data Warehouse / SQL Analytics Endpoint

## CLI

### procedures create

**Targets:** Data Warehouse / SQL Analytics Endpoint

Create a new stored procedure.

**Synopsis**

```
fdw [-w WORKSPACE] procedures create [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `--name SCHEMA.PROC` | **Required.** Qualified procedure name (e.g. `dbo.usp_load_sales`). |
| `--body TEXT` | Inline procedure body (the `AS …` section). |
| `--from-file PATH` | Path to a `.sql` file containing the procedure body. |

Exactly one of `--body` or `--from-file` must be provided.

**Example**

```shell
fdw -w MyWorkspace procedures create SalesWH \
  --name dbo.usp_archive_orders \
  --body "BEGIN INSERT INTO dbo.archive SELECT * FROM dbo.orders; END"
```

### procedures drop

**Targets:** Data Warehouse / SQL Analytics Endpoint

Drop a stored procedure. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] procedures drop [ITEM] QUALIFIED_NAME
```

**Example**

```shell
fdw -w MyWorkspace --yes procedures drop SalesWH dbo.usp_archive_orders
```

### procedures get

**Targets:** Data Warehouse / SQL Analytics Endpoint

Get the full definition of a single stored procedure.

**Synopsis**

```
fdw [-w WORKSPACE] procedures get [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` must be a dot-separated `schema.proc_name` string, e.g. `dbo.usp_load_sales`.

**Example**

```shell
fdw -w MyWorkspace procedures get SalesWH dbo.usp_load_sales
```

### procedures list

**Targets:** Data Warehouse / SQL Analytics Endpoint

List stored procedures on a warehouse or SQL Analytics Endpoint. Pass `--schema` to filter to a single schema.

**Synopsis**

```
fdw [-w WORKSPACE] procedures list [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `--schema TEXT` | Only list procedures in this schema. |

**Example**

```shell
fdw -w MyWorkspace procedures list SalesWH --schema dbo
```

```
 schema_name  name            created               modified
 ------------ --------------- --------------------- ---------------------
 dbo          usp_load_sales  2026-01-10T08:00:00Z  2026-06-01T12:00:00Z
```

### procedures update

**Targets:** Data Warehouse / SQL Analytics Endpoint

Redefine an existing stored procedure via `CREATE OR ALTER PROCEDURE`.

**Synopsis**

```
fdw [-w WORKSPACE] procedures update [OPTIONS] [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the dot-separated `schema.proc_name` to update.

| Option | Description |
| --- | --- |
| `--body TEXT` | Inline procedure body. |
| `--from-file PATH` | Path to a `.sql` file containing the procedure body. |

Exactly one of `--body` or `--from-file` must be provided. You will be asked to confirm unless `--yes` is passed.

**Example**

```shell
fdw -w MyWorkspace procedures update SalesWH dbo.usp_archive_orders \
  --from-file ./procs/usp_archive_orders_v2.sql
```

## MCP tools

### create_procedure

**Targets:** Data Warehouse / SQL Analytics Endpoint

Create a new stored procedure.

!!! warning "Caution"

    `body` is executed verbatim as DDL. Ensure the body matches the user's intent before calling this tool.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated qualified procedure name, e.g. `dbo.usp_load`.
- `body` (`str`): the procedure body (the `AS …` section).

**Returns:** `StoredProcedure`: the newly-created procedure object.

### drop_procedure

**Targets:** Data Warehouse / SQL Analytics Endpoint

Drop a stored procedure.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated qualified procedure name, e.g. `dbo.usp_load`.

**Returns:** `{ "dropped": true }`: confirmation.

### get_procedure

**Targets:** Data Warehouse / SQL Analytics Endpoint

Fetch the full definition of a single stored procedure.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated qualified procedure name, e.g. `dbo.usp_load`.

**Returns:** `StoredProcedure`: single procedure object with `definition` populated from `sys.sql_modules`.

### list_procedures

**Targets:** Data Warehouse / SQL Analytics Endpoint

List stored procedures on a warehouse or SQL Analytics Endpoint, optionally filtered to a single schema.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `schema` (`str | null`, optional): when provided, only procedures in this schema are returned.

**Returns:** `list[StoredProcedure]`: array of procedure objects, each with `schema_name`, `name`, `qualified_name`, `created`, and `modified`.

### update_procedure

**Targets:** Data Warehouse / SQL Analytics Endpoint

Redefine a stored procedure via `CREATE OR ALTER PROCEDURE`.

!!! warning "Caution"

    `body` is executed verbatim as DDL. Ensure the body matches the user's intent before calling this tool.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL analytics endpoint name or GUID.
- `qualified_name` (`str`): dot-separated qualified procedure name, e.g. `dbo.usp_load`.
- `body` (`str`): the new procedure body (the `AS …` section).

**Returns:** `StoredProcedure`: the updated procedure object.
