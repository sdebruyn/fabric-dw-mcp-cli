---
title: SQL Pools (beta)
---

# SQL Pools configuration

!!! warning "Beta / preview feature"
    The SQL Pools API is currently in **preview**.  It is provided for evaluation
    and development purposes only and may change before general availability.
    A mandatory `?beta=true` query parameter is required by the service for every
    call; this is centralised in the library and will be removed in one place once
    the API exits preview.

Custom SQL Pools let you partition a workspace's query resources into named
pools, each with a maximum resource percentage and an optional classifier that
routes specific application sessions into that pool.

This feature is **workspace-level** — there is no warehouse ID in the path
despite the `/warehouses/` segment.  The caller must hold the **workspace admin
role**.

---

## API endpoints

| Operation | Method + Path |
| --- | --- |
| Get configuration | `GET /v1/workspaces/{ws}/warehouses/sqlPoolsConfiguration?beta=true` |
| Update configuration | `PATCH /v1/workspaces/{ws}/warehouses/sqlPoolsConfiguration?beta=true` |

---

## Destructive PATCH semantics

!!! danger "Every PATCH replaces the complete pool list"
    The PATCH endpoint replaces the named-pool set entirely.  **Any pool NOT
    included in the request body is permanently deleted.**  There is no merge
    or partial-update behaviour.

    Always supply the full desired pool list.  Use `sql-pools get` to read the
    current state before constructing a new payload.

The `enable` and `disable` service functions and CLI commands respect this
constraint: they fetch the current pool list first and include it unchanged in
the PATCH body so no pools are accidentally removed.

---

## Safe-edit pattern

Use `sql-pools edit` when you want to interactively modify the configuration
without manually constructing JSON:

```shell
fabric-dw sql-pools edit MyWorkspace
```

The command:

1. Fetches the current configuration.
2. Opens it in `$VISUAL` (or `$EDITOR`, or `vi`/`notepad`).
3. On save, computes a unified diff and shows it.
4. Warns explicitly if any pool would be **deleted** by the change.
5. Asks for confirmation before applying.

This ensures you always see what will change — and which pools would be removed
— before committing.

---

## Payload model

```json
{
  "customSQLPoolsEnabled": true,
  "customSQLPools": [
    {
      "name": "ETL",
      "isDefault": false,
      "maxResourcePercentage": 30,
      "optimizeForReads": false,
      "classifier": {
        "type": "Application Name",
        "value": ["ETL", "Load", "Pipeline"]
      }
    },
    {
      "name": "Reporting",
      "isDefault": false,
      "maxResourcePercentage": 30,
      "optimizeForReads": true,
      "classifier": {
        "type": "Application Name",
        "value": ["Reports"]
      }
    },
    {
      "name": "Default",
      "isDefault": true,
      "maxResourcePercentage": 40,
      "optimizeForReads": false,
      "classifier": {
        "type": "Application Name",
        "value": ["Default"]
      }
    }
  ]
}
```

### Field reference

#### `SqlPoolsConfiguration`

| Field | Type | Description |
| --- | --- | --- |
| `customSQLPoolsEnabled` | `bool` | Whether custom SQL Pools are active. When `false`, the configuration is preserved and restored on re-enable. |
| `customSQLPools` | `list[SqlPool]` | The complete pool list. |

#### `SqlPool`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `str` | Yes | Friendly name for the pool. |
| `isDefault` | `bool` | No | At most one pool may be the default. |
| `maxResourcePercentage` | `int` | Yes | 1–100. Sum across all pools must not exceed 100. |
| `optimizeForReads` | `bool` | No | Enable enhanced caching for read-heavy workloads. Default `true`. |
| `classifier` | `SqlPoolClassifier` | No | Routes sessions into this pool. |

#### `SqlPoolClassifier`

| Field | Type | Description |
| --- | --- | --- |
| `type` | `str` | Classifier type. Known values: `"Application Name"`, `"Application Name Regex"`. The API treats this as an open enum — additional types may be added. |
| `value` | `list[str]` | Application names or patterns to match. |

---

## Client-side validation

The library validates the following constraints **before** sending the PATCH
request, so you get a clear error message without consuming an API call:

- `maxResourcePercentage` must be in the range [1, 100].
- The sum of `maxResourcePercentage` across all pools must not exceed 100.
- At most one pool may have `isDefault: true`.

---

## Disable and re-enable behaviour

Setting `customSQLPoolsEnabled: false` does **not** delete pool definitions.
The service preserves them and restores the full list when you re-enable.

```shell
# Disable without losing pool config
fabric-dw sql-pools disable MyWorkspace

# Re-enable — pools come back exactly as they were
fabric-dw sql-pools enable MyWorkspace
```

---

## Permissions

All operations require the caller to hold the **workspace admin role**.  A 403
response is surfaced as a `PermissionDenied` exception in the service layer and
as a descriptive error message in the CLI and MCP tools.

Required delegated scopes:

- GET: `Workspace.Read.All` or `Workspace.ReadWrite.All`
- PATCH: `Workspace.ReadWrite.All`
