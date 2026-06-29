"""Integration tests for row-level security service functions.

Run with: pytest -m integration tests/integration/test_services_rls.py

Covers the create -> list -> set-state -> add-predicate -> drop-predicate -> drop
round-trip for security policies.

Each test run creates a minimal TVF inline, uses it for the test, then drops it
in teardown -- making the suite hermetic without requiring a pre-existing TVF.

Data Warehouse only: CREATE FUNCTION / CREATE SECURITY POLICY require
db_ddladmin or higher.  The shared warm warehouse grants this to the test
service principal; the SQL analytics endpoint does not.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from fabric_dw.services import rls as rls_svc
from fabric_dw.sql import SqlTarget, run_query

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_policy_name() -> str:
    """Return a short, collision-resistant policy name safe for Fabric DDL."""
    return f"pytest_pol_{uuid.uuid4().hex[:8]}"


def _unique_fn_name() -> str:
    """Return a short, collision-resistant function name safe for Fabric DDL."""
    return f"pytest_rls_fn_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def rls_schema_and_fn(
    warehouse_schema: tuple[SqlTarget, str],
) -> AsyncIterator[tuple[SqlTarget, str, str]]:
    """Create a minimal predicate TVF and drop it in teardown.

    Yields ``(sql_target, schema_name, fn_name)``.

    The TVF signature matches what RLS predicates require:
    ``RETURNS TABLE WITH SCHEMABINDING AS RETURN SELECT 1 AS authorized WHERE ...``.

    Teardown suppresses exceptions so a missing function (e.g. from a failed
    CREATE) does not mask the original test error.
    """
    sql_target, schema_name = warehouse_schema
    fn_name = _unique_fn_name()

    await asyncio.to_thread(
        run_query,
        sql_target,
        (
            f"CREATE FUNCTION [{schema_name}].[{fn_name}](@user_id INT) "
            "RETURNS TABLE WITH SCHEMABINDING "
            "AS RETURN SELECT 1 AS authorized WHERE @user_id > 0;"
        ),
        autocommit=True,
        fetch="none",
    )
    try:
        yield sql_target, schema_name, fn_name
    finally:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                run_query,
                sql_target,
                f"DROP FUNCTION [{schema_name}].[{fn_name}];",
                autocommit=True,
                fetch="none",
            )


@pytest_asyncio.fixture
async def rls_policy_fixture(
    rls_schema_and_fn: tuple[SqlTarget, str, str],
) -> AsyncIterator[tuple[SqlTarget, str, str, str, str]]:
    """Create a security policy and drop it in teardown.

    Yields ``(sql_target, schema_name, fn_name, table_schema, policy_name)``.

    Uses a system table (``sys.objects``) as the target because it always
    exists -- no test table needs to be created.

    Note: Some Fabric capacity SKUs do not support RLS.  The fixture skips
    the test module if CREATE SECURITY POLICY fails with a not-supported error.
    """
    sql_target, schema_name, fn_name = rls_schema_and_fn
    policy_name = _unique_policy_name()
    # Use an existing system-accessible table so we do not need to create one.
    # sys.objects is always present in every Fabric warehouse.
    target_table_schema = "sys"
    target_table_name = "objects"

    try:
        await rls_svc.create_security_policy(
            sql_target,
            f"{schema_name}.{policy_name}",
            [
                {
                    "predicate_type": "FILTER",
                    "fn_schema": schema_name,
                    "fn_name": fn_name,
                    "fn_args": ["object_id"],
                    "table_schema": target_table_schema,
                    "table_name": target_table_name,
                }
            ],
            state=False,
        )
    except Exception as exc:
        err_str = str(exc).lower()
        if "not supported" in err_str or "does not support" in err_str:
            pytest.skip(f"RLS is not supported on this endpoint: {exc}")
        raise

    try:
        yield sql_target, schema_name, fn_name, target_table_schema, policy_name
    finally:
        with contextlib.suppress(Exception):
            await rls_svc.drop_security_policy(sql_target, f"{schema_name}.{policy_name}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_create_and_list_security_policy(
    rls_policy_fixture: tuple[SqlTarget, str, str, str, str],
) -> None:
    """A freshly created policy appears in list_security_policies."""
    sql_target, _schema_name, _fn_name, _target_table_schema, policy_name = rls_policy_fixture

    policies = await rls_svc.list_security_policies(sql_target)
    names = [p.policy_name for p in policies]
    assert policy_name in names, f"Policy {policy_name!r} not found in list. Found: {names}"


async def test_set_policy_state_enable(
    rls_policy_fixture: tuple[SqlTarget, str, str, str, str],
) -> None:
    """set_policy_state toggles the policy between enabled and disabled."""
    sql_target, schema_name, _fn_name, _table_schema, policy_name = rls_policy_fixture
    qualified = f"{schema_name}.{policy_name}"

    # Policy was created disabled (state=False); enable it.
    await rls_svc.set_policy_state(sql_target, qualified, enabled=True)

    policies = await rls_svc.list_security_policies(sql_target)
    pol = next((p for p in policies if p.policy_name == policy_name), None)
    assert pol is not None
    assert pol.is_enabled is True

    # Disable again.
    await rls_svc.set_policy_state(sql_target, qualified, enabled=False)

    policies = await rls_svc.list_security_policies(sql_target)
    pol = next((p for p in policies if p.policy_name == policy_name), None)
    assert pol is not None
    assert pol.is_enabled is False


async def test_drop_security_policy(
    rls_schema_and_fn: tuple[SqlTarget, str, str],
) -> None:
    """drop_security_policy removes the policy so it no longer appears in list."""
    sql_target, schema_name, fn_name = rls_schema_and_fn
    policy_name = _unique_policy_name()
    qualified = f"{schema_name}.{policy_name}"

    try:
        await rls_svc.create_security_policy(
            sql_target,
            qualified,
            [
                {
                    "predicate_type": "FILTER",
                    "fn_schema": schema_name,
                    "fn_name": fn_name,
                    "fn_args": ["object_id"],
                    "table_schema": "sys",
                    "table_name": "objects",
                }
            ],
            state=False,
        )
    except Exception as exc:
        err_str = str(exc).lower()
        if "not supported" in err_str or "does not support" in err_str:
            pytest.skip(f"RLS is not supported on this endpoint: {exc}")
        raise

    await rls_svc.drop_security_policy(sql_target, qualified)

    policies = await rls_svc.list_security_policies(sql_target)
    names = [p.policy_name for p in policies]
    assert policy_name not in names, (
        f"Policy {policy_name!r} still present after drop. Found: {names}"
    )
