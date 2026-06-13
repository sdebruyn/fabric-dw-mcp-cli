# Integration test coverage

These are end-to-end tests that run against a **real Microsoft Fabric workspace**
(gated behind the `integration` pytest marker) and exercise the shared service layer
that both the CLI and MCP server delegate to.
The service layer calls either the Fabric REST API via `http_client.py` or the
warehouse TDS endpoint via `sql.py`; both paths hit real Fabric infrastructure
during these tests.

## Coverage table

### Workspaces

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `workspaces list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_workspaces.py#L12) | ✅ | ✅ |
| `workspaces get` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_workspaces.py#L17) | ✅ | ✅ |
| `workspaces set-collation` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_workspaces.py#L31) | ✅ | ✅ |

### Warehouses

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `warehouses list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_warehouses.py#L13) | ✅ | ✅ |
| `warehouses get` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_warehouses.py#L20) | ✅ | ✅ |
| `warehouses create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/conftest.py#L44) [^create-wh] | ✅ | ✅ |
| `warehouses rename` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_warehouses.py#L29) | ✅ | ✅ |
| `warehouses delete` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_warehouses.py#L37) | ✅ | ✅ |
| `warehouses takeover` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_ownership.py#L13) | ✅ | ✅ |
| `warehouses permissions` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_permissions.py#L44) | ✅ | ✅ |

[^create-wh]: `warehouses.create` is exercised indirectly via the `ephemeral_warehouse` fixture in `conftest.py` (line 44), which is shared by dozens of tests.

### SQL Analytics Endpoints

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `sql-endpoints list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_endpoints.py#L44) | ✅ | ✅ |
| `sql-endpoints get` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_endpoints.py#L95) | ✅ | ✅ |
| `sql-endpoints refresh` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_endpoints.py#L131) | ✅ | ✅ |
| `sql-endpoints permissions` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_permissions.py#L70) [^endpoint-permissions] | ✅ | ✅ |

[^endpoint-permissions]: `sql-endpoints permissions` delegates to `permissions.list_item_access`, which is item-type-agnostic. The integration test at line 70 exercises the non-admin error path against a live warehouse item (the same code path the endpoint follows); the admin happy-path (lines 44 and 57) is conditionally skipped unless `FABRIC_TEST_IS_ADMIN=1` is set.

### SQL Pools

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `sql-pools show` (get configuration) | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_pools.py#L21) | ✅ | ✅ |
| `sql-pools list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_pools.py#L21) [^pools-list] | ✅ | ✅ |
| `sql-pools get` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_pools.py#L21) [^pools-get] | ✅ | ✅ |
| `sql-pools create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_pools.py#L59) | ✅ | ✅ |
| `sql-pools update` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_pools.py#L59) | ✅ | ✅ |
| `sql-pools delete` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_pools.py#L59) | ✅ | ✅ |
| `sql-pools enable` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_pools.py#L28) | ✅ | ✅ |
| `sql-pools disable` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_pools.py#L28) | ✅ | ✅ |
| `sql-pools reset` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_pools.py#L112) | ✅ | ✅ |
| `sql-pools insights` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_query_insights.py#L82) | ✅ | ✅ |

[^pools-list]: `list_sql_pools` reads the same configuration response as `get_configuration`; the `test_get_configuration_returns_model` test covers that path.
[^pools-get]: `get_sql_pool` reads a named pool from the same configuration response; covered by the same test.

### Audit

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `audit get` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_audit.py#L12) | ✅ | ✅ |
| `audit enable` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_audit.py#L19) | ✅ | ✅ |
| `audit disable` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_audit.py#L19) | ✅ | ✅ |
| `audit set-retention` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_audit.py#L48) | ✅ | ✅ |
| `audit set-groups` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_audit.py#L30) | ✅ | ✅ |
| `audit add-group` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_audit.py#L67) | ✅ | ✅ |
| `audit remove-group` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_audit.py#L129) | ✅ | ✅ |

### Snapshots

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `snapshots list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_snapshots.py#L15) | ✅ | ✅ |
| `snapshots create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_snapshots.py#L15) | ✅ | ✅ |
| `snapshots rename` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_snapshots.py#L15) | ✅ | ✅ |
| `snapshots delete` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_snapshots.py#L15) | ✅ | ✅ |
| `snapshots roll` (roll timestamp) | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_snapshots.py#L31) | ✅ | ✅ |

### Restore Points

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `restore-points list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_restore.py#L44) | ✅ | ✅ |
| `restore-points get` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_restore.py#L44) | ✅ | ✅ |
| `restore-points create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_restore.py#L44) | ✅ | ✅ |
| `restore-points rename` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_restore.py#L44) | ✅ | ✅ |
| `restore-points delete` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_restore.py#L44) | ✅ | ✅ |
| `restore-points restore` (restore in place) | ❌ [^restore-in-place] | ✅ | ✅ |

[^restore-in-place]: `restore_in_place` is not run in standard CI: the integration test `test_restore_in_place_reverts_warehouse_state` (`tests/integration/test_services_restore.py#L119`) is skip-guarded behind the `FABRIC_RESTORE_IN_PLACE_TESTS` environment variable because `restore_in_place` takes ~10 minutes for the LRO to complete, making it impractical as an automated gate. It is covered by unit tests with full LRO mocking, and can be run manually by setting `FABRIC_RESTORE_IN_PLACE_TESTS=1`.

### Tables

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `tables list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L41) | ✅ | ✅ |
| `tables read` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L46) | ✅ | ✅ |
| `tables create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L46) | ✅ | ✅ |
| `tables delete` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L46) | ✅ | ✅ |
| `tables clear` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L46) | ✅ | ✅ |
| `tables clone` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L79) | ✅ | ✅ |
| `tables clone` (point-in-time) | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L118) [^clone-at] | ✅ | ✅ |
| `tables rename` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L167) | ✅ | ✅ |

[^clone-at]: The point-in-time clone test (`clone_table` with an `AT` timestamp) is skipped at runtime when the engine rejects the timestamp because the freshly-created source table has no committed history at the requested instant. This is an expected SQL engine constraint, not a code defect; the code path is verified on every run where the engine accepts the timestamp.

### Views

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `views list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_views.py#L24) | ✅ | ✅ |
| `views read` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_views.py#L103) | ✅ | ✅ |
| `views get` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_views.py#L75) | ✅ | ✅ |
| `views create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_views.py#L30) | ✅ | ✅ |
| `views update` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_views.py#L136) | ✅ | ✅ |
| `views drop` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_views.py#L161) | ✅ | ✅ |
| `views rename` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_views.py#L224) | ✅ | ✅ |

### Schemas

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `schemas list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_schemas.py#L39) | ✅ | ✅ |
| `schemas create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_schemas.py#L70) | ✅ | ✅ |
| `schemas delete` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_schemas.py#L83) [^schemas-delete] | ✅ | ✅ |

[^schemas-delete]: The `test_create_list_delete_roundtrip` test at line 83 covers plain delete. A dedicated cascade test at line 105 covers `delete_schema(cascade=True)`, which drops contained tables before dropping the schema.

### Stored Procedures

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `procedures list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_procedures.py#L30) | ✅ | ✅ |
| `procedures get` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_procedures.py#L87) | ✅ | ✅ |
| `procedures create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_procedures.py#L36) | ✅ | ✅ |
| `procedures update` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_procedures.py#L115) | ✅ | ✅ |
| `procedures drop` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_procedures.py#L136) | ✅ | ✅ |

### Queries

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `queries list` (running queries) | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_queries.py#L10) | ✅ | ✅ |
| `queries list-connections` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_queries.py#L22) | ✅ | ✅ |
| `queries kill` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_queries.py#L15) [^kill] | ✅ | ✅ |
| `queries request-history` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_query_insights.py#L34) | ✅ | ✅ |
| `queries session-history` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_query_insights.py#L46) | ✅ | ✅ |
| `queries frequent` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_query_insights.py#L58) | ✅ | ✅ |
| `queries long-running` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_query_insights.py#L70) | ✅ | ✅ |

[^kill]: The integration test for `kill` validates input-validation errors against a live warehouse; it does not kill a running session (none exist on an ephemeral warehouse). The happy-path kill path is covered by unit tests.

### SQL Execution

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `sql exec` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_sql_exec.py#L13) | ✅ | ✅ |

## What this does and doesn't cover

### What a ✅ in "Integration test" means

A ✅ means the **shared service function** behind the feature has been called against a real Fabric workspace and its result has been asserted.
The service layer is the common path that both the CLI and the MCP server delegate to:

```
CLI (Click command) ──┐
                       ├──► service function ──► http_client / sql.py ──► real Fabric API
MCP (FastMCP tool) ───┘
```

So a ✅ in "Integration test" gives confidence that the Fabric API contract is honoured: the correct HTTP calls are made, pagination and LRO polling work, and the response is deserialised correctly.

### What is NOT covered by these tests

The integration tests provision two ephemeral fixtures:

- An **ephemeral Data Warehouse** (`ephemeral_warehouse` / `ephemeral_sql_target` in `conftest.py`) used by warehouse-focused tests (tables, views, schemas, stored procedures, audit, snapshots, restore points, queries, query insights, sql exec).
- A **schema-enabled Lakehouse** with its paired **SQL Analytics Endpoint** (`ephemeral_lakehouse` / `ephemeral_sql_endpoint` in `conftest.py`) used by the SQL Analytics Endpoint tests. The Lakehouse is created with `enableSchemas=true`; Fabric auto-provisions the SQL endpoint alongside it. The fixture polls until provisioning reaches `Success` (up to 5 minutes) and skips rather than fails if it does not complete in time.

The one remaining ❌ in the coverage table (`restore-points restore`) is not run in standard CI:

- **`restore_in_place`** has an integration test (`test_restore_in_place_reverts_warehouse_state`) but it is skip-guarded behind `FABRIC_RESTORE_IN_PLACE_TESTS` because the LRO takes ~10 minutes, making it impractical as an automated gate. It is covered by unit tests with full LRO mocking.

The integration tests do **not** exercise the upper adapter layers:

- **CLI layer** — Click argument parsing, output rendering (Rich tables / JSON), and the mapping of service exceptions to `click.ClickException` / exit codes. These are covered by unit tests in `tests/unit/cli/`.
- **MCP tool layer** — FastMCP tool registration, the `_guards.py` security middleware (readonly / destructive / allowlist guards, row-cap enforcement), and the `fabric_err` error-funnel. These are covered by unit tests in `tests/unit/mcp/`. In particular, the MCP security guards are **unit-tested only** and do not run during integration tests.
- **Config, cache, and completion commands** — `config show/set/unset/clear`, `cache clear`, and `completion install` are local-state operations that do not call the Fabric API and therefore have no integration tests.

### CLI and MCP columns

The **CLI** and **MCP** columns indicate only whether the feature is exposed in that frontend — they say nothing about integration-test coverage of those frontends.
Every operation listed has both a CLI command and an MCP tool; the CLI and MCP columns are ✅ across the board for all operations listed above.
