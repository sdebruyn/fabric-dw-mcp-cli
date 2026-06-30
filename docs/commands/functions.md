---
title: Functions
---

# Functions

Manage T-SQL user-defined functions on Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

**Targets:** Data Warehouse / SQL Analytics Endpoint

## CLI

### functions create

**Targets:** Data Warehouse / SQL Analytics Endpoint

Create a new T-SQL user-defined function.

**Synopsis**

```
fdw [-w WORKSPACE] functions create [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `--name SCHEMA.FN` | **Required.** Qualified function name (e.g. `dbo.fn_clean_input`). |
| `--body TEXT` | Inline function body (parameter list, RETURNS clause, and implementation). |
| `--from-file PATH` | Path to a `.sql` file containing the function body. |

Exactly one of `--body` or `--from-file` must be provided.

**Example**

```shell
fdw -w MyWorkspace functions create SalesWH \
  --name dbo.fn_clean_input \
  --body "(@input NVARCHAR(100)) RETURNS NVARCHAR(100) AS BEGIN RETURN LTRIM(RTRIM(@input)) END"
```

### functions drop

**Targets:** Data Warehouse / SQL Analytics Endpoint

Drop a T-SQL user-defined function. You will be asked to confirm unless `--yes` is passed.

**Synopsis**

```
fdw [-w WORKSPACE] functions drop [OPTIONS] [ITEM] QUALIFIED_NAME
```

| Option | Description |
| --- | --- |
| `--if-exists` | No-op when the function does not exist (`DROP FUNCTION IF EXISTS`). |

**Example**

```shell
fdw -w MyWorkspace --yes functions drop SalesWH dbo.fn_clean_input
```

### functions get

**Targets:** Data Warehouse / SQL Analytics Endpoint

Get the full definition of a single T-SQL user-defined function, including its parameter list.

**Synopsis**

```
fdw [-w WORKSPACE] functions get [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` must be a dot-separated `schema.fn_name` string, e.g. `dbo.fn_clean_input`.

**Example**

```shell
fdw -w MyWorkspace functions get SalesWH dbo.fn_clean_input
```

### functions list

**Targets:** Data Warehouse / SQL Analytics Endpoint

List T-SQL user-defined functions on a warehouse or SQL Analytics Endpoint. Pass `--schema` to filter by schema, or `--kind` to filter by function kind.

**Synopsis**

```
fdw [-w WORKSPACE] functions list [OPTIONS] [ITEM]
```

| Option | Description |
| --- | --- |
| `--schema TEXT` | Only list functions in this schema. |
| `--kind [scalar\|inline-tvf\|all]` | Filter by function kind: `scalar` (FN), `inline-tvf` (IF), or `all` (default). |

**Example**

```shell
fdw -w MyWorkspace functions list SalesWH --schema dbo --kind scalar
```

```
 schema_name  name           kind    is_inlineable  created               modified
 ------------ -------------- ------- -------------- --------------------- ---------------------
 dbo          fn_clean_input  scalar  True           2026-06-01T08:00:00Z  2026-06-10T12:00:00Z
```

### functions update

**Targets:** Data Warehouse / SQL Analytics Endpoint

Redefine an existing T-SQL user-defined function via `CREATE OR ALTER FUNCTION`.

!!! note

    `ALTER FUNCTION` cannot change the function kind (e.g. scalar to inline TVF). The body must be compatible with the original function's kind.

**Synopsis**

```
fdw [-w WORKSPACE] functions update [OPTIONS] [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the dot-separated `schema.fn_name` to update.

| Option | Description |
| --- | --- |
| `--body TEXT` | Inline function body. |
| `--from-file PATH` | Path to a `.sql` file containing the function body. |

Exactly one of `--body` or `--from-file` must be provided. You will be asked to confirm unless `--yes` is passed.

**Example**

```shell
fdw -w MyWorkspace functions update SalesWH dbo.fn_clean_input \
  --from-file ./fns/fn_clean_input_v2.sql
```

### functions transfer

**Targets:** Data Warehouse / SQL Analytics Endpoint

Move a T-SQL user-defined function to another schema via `ALTER SCHEMA ... TRANSFER OBJECT::...`. The command emits exactly `ALTER SCHEMA [target_schema] TRANSFER OBJECT::[schema].[fn]`, with every identifier validated and bracket-quoted before being embedded in the DDL. Function DDL is supported on both Data Warehouses and SQL Analytics Endpoints, so no endpoint guard applies.

!!! warning "Definition text is not rewritten"
    `ALTER SCHEMA ... TRANSFER` moves the function, but it does **not** rewrite
    the schema name inside the object's stored definition
    (`sys.sql_modules.definition`, `OBJECT_DEFINITION()`). After a transfer,
    `get_function` may still show the *old* schema name in the `CREATE ... AS`
    header, even though the function now lives in the new schema. This tool does
    not rewrite the definition text: doing so would require parsing and
    regenerating SQL, which this project deliberately avoids. See
    [ALTER SCHEMA (Transact-SQL)](https://learn.microsoft.com/sql/t-sql/statements/alter-schema-transact-sql?view=fabric&WT.mc_id=MVP_310840#remarks).

**Synopsis**

```
fdw [-w WORKSPACE] functions transfer [OPTIONS] [ITEM] QUALIFIED_NAME
```

`QUALIFIED_NAME` is the current dot-separated `schema.fn_name`.

| Option | Description |
| --- | --- |
| `--target-schema TEXT` | **Required.** Schema to move the function into. |

You will be asked to confirm unless `--yes` is passed.

**Example**

```shell
fdw -w MyWorkspace functions transfer SalesWH dbo.fn_clean_input --target-schema archive
```

## MCP tools

### create_function

**Targets:** Data Warehouse / SQL Analytics Endpoint

Create a new T-SQL user-defined function.

!!! warning "Caution"

    `body` is executed verbatim as DDL. Ensure the body matches the user's intent before calling this tool.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `qualified_name` (`str`): dot-separated qualified function name, e.g. `dbo.fn_clean_input`.
- `body` (`str`): the function body (parameter list, RETURNS clause, and implementation).

**Returns:** `FunctionDetails`: the newly-created function object.

### drop_function

**Targets:** Data Warehouse / SQL Analytics Endpoint

Drop a T-SQL user-defined function.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `qualified_name` (`str`): dot-separated qualified function name, e.g. `dbo.fn_clean_input`.
- `if_exists` (`bool`, optional): when `true`, emits `DROP FUNCTION IF EXISTS` (no-op when function does not exist). Defaults to `false`.

**Returns:** `{ "dropped": true }`: confirmation.

### get_function

**Targets:** Data Warehouse / SQL Analytics Endpoint

Fetch the full definition of a single T-SQL user-defined function, including its parameter list.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `qualified_name` (`str`): dot-separated qualified function name, e.g. `dbo.fn_clean_input`.

**Returns:** `FunctionDetails`: single function object with `definition` (from `sys.sql_modules`) and `parameters` (from `sys.parameters`).

### list_functions

**Targets:** Data Warehouse / SQL Analytics Endpoint

List T-SQL user-defined functions on a warehouse or SQL Analytics Endpoint, optionally filtered by schema or kind.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `schema` (`str | null`, optional): when provided, only functions in this schema are returned.
- `kind` (`str`, optional): filter by function kind: `"scalar"` (FN only), `"inline-tvf"` (IF only), or `"all"` (FN + IF + TF, the default).

**Returns:** `list[Function]`: array of function objects, each with `schema_name`, `name`, `qualified_name`, `kind`, `is_inlineable`, `created`, and `modified`.

### update_function

**Targets:** Data Warehouse / SQL Analytics Endpoint

Redefine a T-SQL user-defined function via `CREATE OR ALTER FUNCTION`.

!!! note

    `ALTER FUNCTION` cannot change the function kind (e.g. scalar to inline TVF). The body must be compatible with the original function's kind.

!!! warning "Caution"

    `body` is executed verbatim as DDL. Ensure the body matches the user's intent before calling this tool.

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `qualified_name` (`str`): dot-separated qualified function name, e.g. `dbo.fn_clean_input`.
- `body` (`str`): the new function body (parameter list, RETURNS clause, and implementation).

**Returns:** `FunctionDetails`: the updated function object.

### transfer_function

**Targets:** Data Warehouse / SQL Analytics Endpoint

Move a T-SQL user-defined function to another schema via `ALTER SCHEMA ... TRANSFER OBJECT::...`. Function DDL is supported on both Data Warehouses and SQL Analytics Endpoints, so no endpoint guard applies.

!!! warning "Definition text is not rewritten"
    `ALTER SCHEMA ... TRANSFER` moves the function, but it does **not** rewrite
    the schema name inside the object's stored definition
    (`sys.sql_modules.definition`, `OBJECT_DEFINITION()`). After a transfer,
    `get_function` may still show the *old* schema name in the `CREATE ... AS`
    header, even though the function now lives in the new schema. This tool does
    not rewrite the definition text: doing so would require parsing and
    regenerating SQL, which this project deliberately avoids. See
    [ALTER SCHEMA (Transact-SQL)](https://learn.microsoft.com/sql/t-sql/statements/alter-schema-transact-sql?view=fabric&WT.mc_id=MVP_310840#remarks).

**Parameters:**

- `workspace` (`str`): workspace name or GUID.
- `item` (`str`): warehouse or SQL Analytics Endpoint name or GUID.
- `qualified_name` (`str`): current dot-separated qualified function name, e.g. `dbo.fn_clean_input`.
- `target_schema` (`str`): schema to move the function into, e.g. `archive`.

**Returns:** `FunctionDetails`: the moved function record, fetched from the new schema.
