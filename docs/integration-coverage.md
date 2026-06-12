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
| `warehouses create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/conftest.py#L39) [^create-wh] | ✅ | ✅ |
| `warehouses rename` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_warehouses.py#L29) | ✅ | ✅ |
| `warehouses delete` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_warehouses.py#L37) | ✅ | ✅ |
| `warehouses takeover` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_ownership.py#L13) | ✅ | ✅ |
| `warehouses permissions` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_permissions.py#L44) | ✅ | ✅ |

[^create-wh]: `warehouses.create` is exercised indirectly via the `ephemeral_warehouse` fixture in `conftest.py` (line 39), which is shared by dozens of tests.

### SQL Analytics Endpoints

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `sql-endpoints list` | ❌ | ✅ | ✅ |
| `sql-endpoints get` | ❌ | ✅ | ✅ |
| `sql-endpoints refresh` | ❌ | ✅ | ✅ |
| `sql-endpoints permissions` | ❌ | ✅ | ✅ |

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
| `audit add-group` | ❌ | ✅ | ✅ |
| `audit remove-group` | ❌ | ✅ | ✅ |

### Snapshots

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `snapshots list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_snapshots.py#L13) | ✅ | ✅ |
| `snapshots create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_snapshots.py#L13) | ✅ | ✅ |
| `snapshots rename` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_snapshots.py#L13) | ✅ | ✅ |
| `snapshots delete` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_snapshots.py#L13) | ✅ | ✅ |
| `snapshots roll` (roll timestamp) | ❌ | ✅ | ✅ |

### Restore Points

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `restore-points list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_restore.py#L28) | ✅ | ✅ |
| `restore-points get` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_restore.py#L28) | ✅ | ✅ |
| `restore-points create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_restore.py#L28) | ✅ | ✅ |
| `restore-points rename` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_restore.py#L28) | ✅ | ✅ |
| `restore-points delete` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_restore.py#L28) | ✅ | ✅ |
| `restore-points restore` (restore in place) | ❌ [^restore-in-place] | ✅ | ✅ |

[^restore-in-place]: `restore_in_place` is intentionally excluded from integration tests because it mutates the warehouse for ~10 minutes, breaking concurrent tests. It is covered by unit tests with full LRO mocking.

### Tables

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `tables list` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L24) | ✅ | ✅ |
| `tables read` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L29) | ✅ | ✅ |
| `tables create` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L29) | ✅ | ✅ |
| `tables delete` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L29) | ✅ | ✅ |
| `tables clear` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_tables.py#L29) | ✅ | ✅ |

### Views

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `views list` | ❌ | ✅ | ✅ |
| `views read` | ❌ | ✅ | ✅ |
| `views get` | ❌ | ✅ | ✅ |
| `views create` | ❌ | ✅ | ✅ |
| `views update` | ❌ | ✅ | ✅ |
| `views drop` | ❌ | ✅ | ✅ |

### Schemas

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `schemas list` | ❌ | ✅ | ✅ |
| `schemas create` | ❌ | ✅ | ✅ |
| `schemas delete` | ❌ | ✅ | ✅ |

### Queries

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `queries list` (running queries) | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_queries.py#L9) | ✅ | ✅ |
| `queries list-connections` | ❌ | ✅ | ✅ |
| `queries kill` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_queries.py#L14) [^kill] | ✅ | ✅ |

[^kill]: The integration test for `kill` validates input-validation errors against a live warehouse; it does not kill a running session (none exist on an ephemeral warehouse). The happy-path kill path is covered by unit tests.

### Query Insights

| Feature | Integration test | CLI | MCP |
|---|---|---|---|
| `query-insights request-history` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_query_insights.py#L34) | ✅ | ✅ |
| `query-insights session-history` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_query_insights.py#L46) | ✅ | ✅ |
| `query-insights frequent` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_query_insights.py#L58) | ✅ | ✅ |
| `query-insights long-running` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_query_insights.py#L70) | ✅ | ✅ |
| `query-insights pool-insights` | [✅](https://github.com/sdebruyn/fabric-dw-mcp-cli/blob/main/tests/integration/test_services_query_insights.py#L82) | ✅ | ✅ |

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

The integration tests do **not** exercise the upper adapter layers:

- **CLI layer** — Click argument parsing, output rendering (Rich tables / JSON), and the mapping of service exceptions to `click.ClickException` / exit codes. These are covered by unit tests in `tests/unit/cli/`.
- **MCP tool layer** — FastMCP tool registration, the `_guards.py` security middleware (readonly / destructive / allowlist guards, row-cap enforcement), and the `fabric_err` error-funnel. These are covered by unit tests in `tests/unit/mcp/`. In particular, the MCP security guards are **unit-tested only** and do not run during integration tests.
- **Config, cache, and completion commands** — `config show/set/unset/clear`, `cache clear`, and `completion install` are local-state operations that do not call the Fabric API and therefore have no integration tests.

### CLI and MCP columns

The **CLI** and **MCP** columns indicate only whether the feature is exposed in that frontend — they say nothing about integration-test coverage of those frontends.
Every operation listed has both a CLI command and an MCP tool; the CLI and MCP columns are ✅ across the board for all operations listed above.
