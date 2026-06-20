"""Meta-guard: every dual-target domain must have endpoint integration coverage.

This unit test inspects the integration test suite STATICALLY (AST / source
text) — no live Fabric credentials are needed.  It runs in the standard
``just test`` / ``pytest tests/unit/`` invocation.

## Purpose

The service layer distinguishes two kinds of operations:

- **DWH-only** — service calls ``_assert_not_sql_endpoint``; base-table data
  writes.  These are intentionally only tested against a warehouse.
- **Dual-target** — no such guard; works on both Fabric Data Warehouses and SQL
  Analytics Endpoints.  Their integration tests **must** exercise both targets
  via the parametrized ``read_target`` or ``mutable_schema_target`` fixture.

Without this guard, adding a new dual-target command without an endpoint test
passes CI silently.  This test catches that drift the moment it happens.

## Maintenance contract

``_DUAL_TARGET_DOMAINS`` below is the authoritative registry.  Each entry maps
a human-readable domain name to the integration test module filename that must
contain at least one test requesting ``read_target`` or
``mutable_schema_target``.  When a new dual-target domain is added:

1. Add its integration tests using ``read_target`` or ``mutable_schema_target``.
2. Add an entry to ``_DUAL_TARGET_DOMAINS`` below.

If an operation turns out to be DWH-only (service adds ``_assert_not_sql_endpoint``),
remove its entry from ``_DUAL_TARGET_DOMAINS`` instead and add a comment here
explaining why.
"""

from __future__ import annotations

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Authoritative registry of dual-target domains
# ---------------------------------------------------------------------------

# Maps a human-readable domain label to the integration test module filename
# (relative to tests/integration/) that covers it.  Every entry must have at
# least one test in that module that requests ``read_target`` or
# ``mutable_schema_target``.
#
# Source of truth: service operations that do NOT call _assert_not_sql_endpoint
# (confirmed in issue #592, reconciled against docs Targets: lines and
# service-layer code).
_DUAL_TARGET_DOMAINS: dict[str, str] = {
    # Reads / query / metadata — covered by read_target parametrization
    "tables (read)": "test_services_tables.py",
    "views (read + mutate)": "test_services_views.py",
    "procedures (read + mutate)": "test_services_procedures.py",
    "functions (read + mutate)": "test_services_functions.py",
    "schemas (read + mutate)": "test_services_schemas.py",
    "queries (list/kill/connections)": "test_services_queries.py",
    "query_insights (list history/frequent/long-running)": "test_services_query_insights.py",
    "statistics (list)": "test_services_statistics.py",
    "sql_exec (execute/plan)": "test_services_sql_exec.py",
    "dbt (generate_dbt_profile)": "test_services_dbt_scaffold.py",
}

# The two fixture names that signal dual-target parametrization.
_DUAL_TARGET_FIXTURES = frozenset({"read_target", "mutable_schema_target"})

# Path to the integration test directory (resolved relative to this file).
_INTEGRATION_DIR = Path(__file__).parent.parent / "integration"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _fixture_names_in_module(source: str) -> frozenset[str]:
    """Return the set of fixture names requested by ANY test function in *source*.

    We collect parameter names of every ``async def test_*`` / ``def test_*``
    function in the module.  This is deliberately coarse — we are checking
    whether *any* test in the file uses a dual-target fixture, not whether
    every test does.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            for arg in node.args.args:
                names.add(arg.arg)
    return frozenset(names)


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_every_dual_target_domain_has_endpoint_coverage() -> None:
    """Every dual-target domain must have at least one read_target / mutable_schema_target test.

    For each entry in ``_DUAL_TARGET_DOMAINS``:

    1. The integration test module must exist.
    2. At least one ``test_*`` function in that module must declare
       ``read_target`` or ``mutable_schema_target`` as a parameter —
       proof that the domain's test runs against both SQL targets.

    Failure message is actionable: it names the missing domain and tells the
    developer exactly what to add.
    """
    missing: list[str] = []

    for domain, module_filename in _DUAL_TARGET_DOMAINS.items():
        module_path = _INTEGRATION_DIR / module_filename

        if not module_path.exists():
            missing.append(
                f"  domain {domain!r}: integration test module "
                f"{module_filename!r} does not exist — "
                "create it and add a read_target or mutable_schema_target test"
            )
            continue

        source = module_path.read_text(encoding="utf-8")
        used_fixtures = _fixture_names_in_module(source)
        dual_target_used = used_fixtures & _DUAL_TARGET_FIXTURES

        if not dual_target_used:
            missing.append(
                f"  domain {domain!r} ({module_filename}): no test declares "
                f"'read_target' or 'mutable_schema_target' as a parameter — "
                "add a dual-target test or, if this domain is DWH-only, "
                "remove it from _DUAL_TARGET_DOMAINS in "
                "tests/unit/test_dual_target_coverage.py"
            )

    assert not missing, (
        f"{len(missing)} dual-target domain(s) have no SQL Analytics Endpoint "
        "integration coverage:\n" + "\n".join(missing) + "\n\nFor each domain above, either:\n"
        "  (a) add a test using read_target or mutable_schema_target in the "
        "listed module, OR\n"
        "  (b) remove the entry from _DUAL_TARGET_DOMAINS if the operation is "
        "DWH-only (service must call _assert_not_sql_endpoint)."
    )


def test_dual_target_registry_modules_all_exist() -> None:
    """All modules listed in _DUAL_TARGET_DOMAINS must exist (catches stale renames).

    A separate, fast pre-check: if a test module is renamed and the registry
    is not updated, this fails immediately with a clear message rather than a
    confusing "no dual-target fixture found" failure.
    """
    missing_modules = [
        f"  {domain!r} → {filename!r} (not found)"
        for domain, filename in _DUAL_TARGET_DOMAINS.items()
        if not (_INTEGRATION_DIR / filename).exists()
    ]
    assert not missing_modules, (
        "Some modules listed in _DUAL_TARGET_DOMAINS do not exist "
        "(did a test file get renamed?):\n" + "\n".join(missing_modules)
    )
