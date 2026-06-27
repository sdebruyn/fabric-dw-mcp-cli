# Integration test architecture

This document describes the fixture rule that governs which SQL target(s) each
integration test must run against.  **Violations of this rule mean silent
coverage drift**. A dual-target operation can break on SQL Analytics Endpoints
without any failing CI test.  A unit-level meta-guard
(`tests/unit/test_dual_target_coverage.py`) enforces the rule automatically.

## Target model (source of truth: service-layer guards)

The single source of truth for whether an operation is DWH-only or dual-target
is the **service guard** `_assert_not_sql_endpoint(kind)` in the service layer:

- **DWH-only**: the service calls `_assert_not_sql_endpoint`; the operation is
  rejected on SQL Analytics Endpoints at the service layer.
- **Dual-target**: no such guard; the operation works on both Fabric Data
  Warehouses and SQL Analytics Endpoints.

## Fixture rule

| Fixture | Targets | Used for |
|---|---|---|
| `read_target` | warehouse + sql_endpoint (parametrized) | Pure READ tests (list, get, count, query, metadata, audit, insights) |
| `mutable_schema_target` | warehouse + sql_endpoint (parametrized) | MUTATING object-DDL tests (views / procedures / functions / schemas CRUD) |
| `warehouse_schema` | warehouse only | DWH-only DDL (table CREATE/DROP/TRUNCATE/CLONE/RENAME, statistics create/update/drop, load) |
| `shared_warehouse` | warehouse only | DWH-only tests that do not need schema isolation (snapshots, restore, takeover, ownership) |
| `shared_sql_endpoint` | sql_endpoint only | Endpoint-specific tests (health-check, endpoint-domain tests) |

### Dual-target read tests (`read_target`)

Any test that only **reads** data (`list_*`, `get_*`, `count_*`, `execute_sql`,
`get_query_plan`, query-insights views, audit settings read, statistics read,
`generate_dbt_profile`, column metadata) must use `read_target`.  This fixture
is parametrized over `["warehouse", "sql_endpoint"]` so the test body runs once
per target automatically.

Both targets expose the same read-only seed schema (`sample`) with
`sample.colors` and `sample.numbers`.  Tests MUST NOT mutate the seed schema.

### Dual-target mutating object-DDL tests (`mutable_schema_target`)

Tests that CREATE, UPDATE, or DROP **views, procedures, functions, or schemas**
must use `mutable_schema_target`.  This fixture:

1. Picks either the shared warehouse (`[warehouse]` leg) or the shared SQL
   analytics endpoint (`[sql_endpoint]` leg) on each parametrized run.
2. Creates a uniquely-named schema (`pytest_<8hex>`) on the chosen target
   before yielding `(sql_target, schema_name)`.
3. Cascade-drops the schema (and all objects inside it) on teardown.

Both `create_schema` and `delete_schema(cascade=True)` are themselves
dual-target, so teardown works identically on both targets.

### DWH-only tests (`warehouse_schema` / `shared_warehouse`)

Base-table data writes (`create_table`, `create_empty_table`, `create_table_from_parquet`,
`create_table_from_csv`, `clone_table`, `delete_table`, `clear_table`, `rename_table`,
`set_cluster_columns`, `copy_into_from_url`, `import_table_from_url`, `load_local_file`,
`create_statistics`, `update_statistics`, `drop_statistics`) are rejected by the service
layer on SQL Analytics Endpoints.  Their tests use `warehouse_schema` (for per-test
schema isolation) or `shared_warehouse` directly and do NOT need `read_target` or
`mutable_schema_target`.

Restore-point and snapshot operations are warehouse-only API calls; their tests use
`shared_warehouse` or the ephemeral warehouse fixtures.

## `sql_endpoint` marker and local deselection

The `sql_endpoint` parameter of both `read_target` and `mutable_schema_target`
carries `pytest.mark.sql_endpoint`.  Local runs deselect it via `addopts` in
`pyproject.toml`:

```
addopts = "-m 'not (integration or sql_endpoint)'"
```

CI runs the full matrix (no deselection).  To run endpoint tests locally:

```bash
pytest -m "integration and sql_endpoint" -n0 -v tests/integration/
```

## Lazy fixture resolution pattern

`shared_sql_endpoint` is **not** declared as a signature parameter of
`read_target` or `mutable_schema_target`.  It is resolved lazily via
`request.getfixturevalue("shared_sql_endpoint")` only on the `sql_endpoint`
leg.  This prevents pytest from provisioning the SQL analytics endpoint
(~6 min: Lakehouse create + poll + seed) during local runs or during the
`[warehouse]` leg. Provisioning is paid only when the endpoint leg actually
runs.

`read_target` is a sync fixture, so its `getfixturevalue` runs outside any
event loop.  `mutable_schema_target` is async, so it **must not** call
`getfixturevalue` on the async `shared_sql_endpoint` itself. Doing so makes
pytest-asyncio call `runner.run()` nested inside the running loop and raises
`RuntimeError: Runner.run() cannot be called from a running event loop`.
Instead, the parametrisation and the lazy resolution live on a **sync**
indirection fixture (`_mutable_schema_sql_target`) that the async fixture
depends on, so the endpoint is materialised outside the loop while the marks
still propagate through the dependency closure.

**Rule of thumb:** never lazily resolve an async session fixture from inside an
`async def` fixture.  Put the `getfixturevalue` call on a sync fixture and
depend on it.

## Parallelism and shared-budget tests

The integration suite runs under `pytest-xdist` with `--dist loadgroup` (see
`.github/workflows/integration.yml`).  `loadgroup` keeps xdist's work-stealing
scheduler but guarantees that tests sharing an `xdist_group` mark run on the
**same** worker.

`test_services_sql_pools.py` carries `pytest.mark.xdist_group("sql_pools")` at
module scope because every test there mutates the single shared workspace's
`maxResourcePercentage` budget (a global per-workspace quota that must sum to
≤ 100).  Pinning the module to one worker serialises those tests so a 100%
default pool from one test can never coexist with another test's pool and push
the sum over 100.

## Meta-guard

`tests/unit/test_dual_target_coverage.py` contains a unit test
(`test_every_dual_target_domain_has_endpoint_coverage`) that:

1. Defines the authoritative registry of dual-target domains.
2. For each domain, asserts the corresponding integration test module
   contains at least one test that requests `read_target` or
   `mutable_schema_target` (detected by AST inspection of the test source).
3. Fails with an actionable message if a domain is missing endpoint coverage.

**When adding a new dual-target operation, you must either:**

- Add a `read_target` or `mutable_schema_target` test in the appropriate
  `tests/integration/test_services_*.py` module, OR
- Add the operation to the DWH-only list in the meta-guard with a comment
  explaining why it is DWH-only.

The meta-guard is a pure unit test (no live Fabric credentials needed) and
runs in the standard `just test` / `pytest tests/unit/` invocation.
