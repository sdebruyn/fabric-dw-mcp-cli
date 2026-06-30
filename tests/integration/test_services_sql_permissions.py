"""Integration tests for T-SQL permission services (GRANT/DENY/REVOKE).

Run with: pytest -m integration tests/integration/test_services_sql_permissions.py

Uses a temporary database ROLE as the grantee so no real Entra identity is needed.
Each test gets an isolated schema (via ``warehouse_schema``) and an isolated role
(via ``temp_role``) that are created fresh and dropped in teardown, making the suite
hermetic against leftover state from previous runs or concurrent workers.

These tests cover the T-SQL in-database permission plane only. The item-level REST
admin API plane is covered by ``test_services_permissions.py``.

Data Warehouse only: CREATE ROLE / DROP ROLE require db_securityadmin or higher.
The shared warm warehouse grants this to the test service principal; the SQL analytics
endpoint does not, so no ``mutable_schema_target`` dual-target parametrisation is used.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from fabric_dw.models import DatabasePrincipal
from fabric_dw.services import permissions
from fabric_dw.sql import SqlTarget, run_query

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_role_name() -> str:
    """Return a short, collision-resistant role name safe for Fabric DDL."""
    return f"pytest_role_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def temp_role(
    warehouse_schema: tuple[SqlTarget, str],
) -> AsyncIterator[tuple[SqlTarget, str, str]]:
    """Create a temporary database ROLE and drop it in teardown.

    Yields ``(sql_target, schema_name, role_name)``.

    The role name is a fixed UUID-derived identifier; no SQL text is parsed or
    rewritten.  The bracket-quoted DDL is built in one place and never modified
    after creation, satisfying the no-SQL-parsing rule.

    Teardown suppresses exceptions so a missing role (e.g. from a failed CREATE)
    does not mask the original test error.
    """
    sql_target, schema_name = warehouse_schema
    role_name = _unique_role_name()

    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE ROLE [{role_name}];",
        autocommit=True,
        fetch="none",
    )
    try:
        yield sql_target, schema_name, role_name
    finally:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                run_query,
                sql_target,
                f"DROP ROLE [{role_name}];",
                autocommit=True,
                fetch="none",
            )


# ---------------------------------------------------------------------------
# SCHEMA scope: grant / deny / revoke round-trips
# ---------------------------------------------------------------------------


async def test_grant_select_on_schema_appears_in_list(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """grant_permission SELECT on SCHEMA scope must produce a GRANT row in list_sql_permissions."""
    sql_target, schema_name, role_name = temp_role

    await permissions.grant_permission(
        sql_target, "SELECT", role_name, "SCHEMA", schema=schema_name
    )

    result = await permissions.list_sql_permissions(
        sql_target, principal=role_name, schema=schema_name
    )
    assert any(
        p.permission_name == "SELECT"
        and p.securable_class == "SCHEMA"
        and p.state in {"GRANT", "GRANT_WITH_GRANT_OPTION"}
        for p in result
    ), f"Expected GRANT SELECT on SCHEMA {schema_name!r} for role {role_name!r}; got: {result!r}"


async def test_deny_select_on_schema_appears_in_list(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """deny_permission SELECT on SCHEMA scope must produce a DENY row in list_sql_permissions."""
    sql_target, schema_name, role_name = temp_role

    await permissions.deny_permission(sql_target, "SELECT", role_name, "SCHEMA", schema=schema_name)

    result = await permissions.list_sql_permissions(
        sql_target, principal=role_name, schema=schema_name
    )
    assert any(
        p.permission_name == "SELECT" and p.securable_class == "SCHEMA" and p.state == "DENY"
        for p in result
    ), f"Expected DENY SELECT on SCHEMA {schema_name!r} for role {role_name!r}; got: {result!r}"


async def test_revoke_removes_schema_grant_from_list(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """revoke_permission must remove a previously granted SCHEMA permission row."""
    sql_target, schema_name, role_name = temp_role

    await permissions.grant_permission(
        sql_target, "SELECT", role_name, "SCHEMA", schema=schema_name
    )
    await permissions.revoke_permission(
        sql_target, "SELECT", role_name, "SCHEMA", schema=schema_name
    )

    result = await permissions.list_sql_permissions(
        sql_target, principal=role_name, schema=schema_name
    )
    assert not any(
        p.permission_name == "SELECT" and p.securable_class == "SCHEMA" for p in result
    ), (
        f"SELECT on SCHEMA {schema_name!r} still present after revoke for {role_name!r}; "
        f"got: {result!r}"
    )


# ---------------------------------------------------------------------------
# WITH GRANT OPTION
# ---------------------------------------------------------------------------


async def test_grant_with_grant_option_produces_correct_state(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """grant_permission with with_grant_option=True must produce GRANT_WITH_GRANT_OPTION state."""
    sql_target, schema_name, role_name = temp_role

    await permissions.grant_permission(
        sql_target,
        "SELECT",
        role_name,
        "SCHEMA",
        schema=schema_name,
        with_grant_option=True,
    )

    result = await permissions.list_sql_permissions(
        sql_target, principal=role_name, schema=schema_name
    )
    assert any(
        p.permission_name == "SELECT"
        and p.securable_class == "SCHEMA"
        and p.state == "GRANT_WITH_GRANT_OPTION"
        for p in result
    ), (
        f"Expected GRANT_WITH_GRANT_OPTION for role {role_name!r} on schema {schema_name!r}; "
        f"got: {result!r}"
    )


# ---------------------------------------------------------------------------
# OBJECT scope
# ---------------------------------------------------------------------------


async def test_grant_select_on_object_appears_in_list(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """grant_permission SELECT on OBJECT scope must produce a GRANT row in list_sql_permissions."""
    sql_target, schema_name, role_name = temp_role
    table_name = "pytest_perms_tbl"
    qualified_name = f"{schema_name}.{table_name}"

    # Create a minimal table to serve as the securable.
    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE TABLE [{schema_name}].[{table_name}] (id INT);",
        autocommit=True,
        fetch="none",
    )

    await permissions.grant_permission(
        sql_target, "SELECT", role_name, "OBJECT", object_name=qualified_name
    )

    result = await permissions.list_sql_permissions(
        sql_target, principal=role_name, object_name=qualified_name
    )
    assert any(
        p.permission_name == "SELECT"
        and p.securable_class == "OBJECT"
        and p.state in {"GRANT", "GRANT_WITH_GRANT_OPTION"}
        for p in result
    ), f"Expected GRANT SELECT on OBJECT {qualified_name!r} for role {role_name!r}; got: {result!r}"


# ---------------------------------------------------------------------------
# list_database_principals
# ---------------------------------------------------------------------------


async def test_list_database_principals_returns_non_empty_list(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """list_database_principals must return a non-empty list of DatabasePrincipal objects.

    Fabric Data Warehouses always have at least the ``dbo`` user and the connecting
    service principal, so the list is guaranteed non-empty on any live warehouse.
    """
    sql_target, _schema_name = warehouse_schema

    result = await permissions.list_database_principals(sql_target)

    assert isinstance(result, list)
    assert len(result) > 0, "Expected at least one database principal"
    for p in result:
        assert isinstance(p, DatabasePrincipal)
        assert p.name
        assert p.type


async def test_list_database_principals_all_have_required_fields(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """Every DatabasePrincipal returned must have non-empty name, type, and authentication_type."""
    sql_target, _schema_name = warehouse_schema

    result = await permissions.list_database_principals(sql_target)

    for p in result:
        assert p.name, f"Empty name on principal: {p!r}"
        assert p.type, f"Empty type on principal: {p!r}"
        # authentication_type may be 'NONE' for built-in principals — non-null is sufficient.
        assert p.authentication_type is not None, f"None authentication_type on {p!r}"


# ---------------------------------------------------------------------------
# my_permissions
# ---------------------------------------------------------------------------


async def test_my_permissions_database_scope_is_non_empty(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """my_permissions at DATABASE scope must return at least one permission row.

    The connecting service principal always has at least CONNECT on the database,
    so the result must be non-empty on any live warehouse.
    """
    sql_target, _schema_name = warehouse_schema

    result = await permissions.my_permissions(sql_target)

    assert isinstance(result, list)
    assert len(result) > 0, "Expected at least one DATABASE permission for the connecting principal"
    for row in result:
        assert "permission_name" in row, f"Missing 'permission_name' key in row: {row!r}"
        assert "entity_name" in row, f"Missing 'entity_name' key in row: {row!r}"


async def test_my_permissions_schema_scope_returns_list(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """my_permissions at SCHEMA scope must return a valid list (possibly empty).

    The temp schema is freshly created and the connecting principal may have no
    explicit schema-level permissions, so an empty list is an acceptable result.
    The call must succeed without raising.
    """
    sql_target, schema_name = warehouse_schema

    result = await permissions.my_permissions(sql_target, scope=f"schema:{schema_name}")

    assert isinstance(result, list)
    for row in result:
        assert "permission_name" in row, f"Missing 'permission_name' key in row: {row!r}"


# ---------------------------------------------------------------------------
# DATABASE scope grant (validates the BLOCKER fix: no ON clause emitted)
# ---------------------------------------------------------------------------


async def test_grant_select_on_database_scope(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """grant_permission SELECT at DATABASE scope must succeed and appear in list_sql_permissions.

    This validates the fix for the DATABASE scope ON clause: Fabric T-SQL requires
    ``GRANT SELECT TO [role]`` without an ON clause for database-level grants.
    """
    sql_target, _schema_name, role_name = temp_role

    await permissions.grant_permission(sql_target, "SELECT", role_name, "DATABASE")

    result = await permissions.list_sql_permissions(sql_target, principal=role_name)
    assert any(p.permission_name == "SELECT" and p.securable_class == "DATABASE" for p in result), (
        f"Expected DATABASE-scope GRANT SELECT for role {role_name!r}; got: {result!r}"
    )


async def test_deny_select_on_database_scope(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """deny_permission SELECT at DATABASE scope must produce a DENY row."""
    sql_target, _schema_name, role_name = temp_role

    await permissions.deny_permission(sql_target, "SELECT", role_name, "DATABASE")

    result = await permissions.list_sql_permissions(sql_target, principal=role_name)
    assert any(
        p.permission_name == "SELECT" and p.securable_class == "DATABASE" and p.state == "DENY"
        for p in result
    ), f"Expected DATABASE-scope DENY SELECT for role {role_name!r}; got: {result!r}"


async def test_revoke_removes_database_scope_grant(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """revoke_permission must remove a DATABASE-scope grant."""
    sql_target, _schema_name, role_name = temp_role

    await permissions.grant_permission(sql_target, "SELECT", role_name, "DATABASE")
    await permissions.revoke_permission(sql_target, "SELECT", role_name, "DATABASE")

    result = await permissions.list_sql_permissions(sql_target, principal=role_name)
    assert not any(
        p.permission_name == "SELECT" and p.securable_class == "DATABASE" for p in result
    ), f"DATABASE GRANT SELECT still present after revoke for {role_name!r}; got: {result!r}"


# ---------------------------------------------------------------------------
# Column-level security (CLS)
# ---------------------------------------------------------------------------


async def test_grant_select_on_column_appears_in_list(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """grant_permission SELECT on a specific column must produce a column-level row.

    The row must have column_name set (minor_id != 0 in sys.database_permissions).
    """
    sql_target, schema_name, role_name = temp_role
    table_name = "pytest_cls_tbl"
    qualified_name = f"{schema_name}.{table_name}"

    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE TABLE [{schema_name}].[{table_name}] (id INT, email VARCHAR(255));",
        autocommit=True,
        fetch="none",
    )

    await permissions.grant_permission(
        sql_target,
        "SELECT",
        role_name,
        "OBJECT",
        object_name=qualified_name,
        columns=["email"],
    )

    result = await permissions.list_sql_permissions(
        sql_target, principal=role_name, object_name=qualified_name
    )
    col_rows = [p for p in result if p.column_name is not None]
    assert any(
        p.permission_name == "SELECT"
        and p.column_name == "email"
        and p.state in {"GRANT", "GRANT_WITH_GRANT_OPTION"}
        for p in col_rows
    ), (
        f"Expected column-level GRANT SELECT on email for role {role_name!r}; "
        f"column rows: {col_rows!r}"
    )


async def test_revoke_removes_column_level_grant(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """revoke_permission on a specific column must remove the column-level row."""
    sql_target, schema_name, role_name = temp_role
    table_name = "pytest_cls_rev_tbl"
    qualified_name = f"{schema_name}.{table_name}"

    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE TABLE [{schema_name}].[{table_name}] (id INT, phone VARCHAR(50));",
        autocommit=True,
        fetch="none",
    )

    await permissions.grant_permission(
        sql_target,
        "SELECT",
        role_name,
        "OBJECT",
        object_name=qualified_name,
        columns=["phone"],
    )
    await permissions.revoke_permission(
        sql_target,
        "SELECT",
        role_name,
        "OBJECT",
        object_name=qualified_name,
        columns=["phone"],
    )

    result = await permissions.list_sql_permissions(
        sql_target, principal=role_name, object_name=qualified_name
    )
    col_rows = [p for p in result if p.column_name == "phone"]
    assert not col_rows, (
        f"Column-level SELECT on phone still present after revoke for {role_name!r}; "
        f"got: {col_rows!r}"
    )


async def test_deny_select_on_column_appears_in_list(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """deny_permission SELECT on a specific column must produce a DENY row with column_name set.

    Fabric T-SQL: ``DENY SELECT ON OBJECT::[schema].[table] ([col]) TO [role]``
    The resulting row in sys.database_permissions has state_desc = 'DENY' and minor_id != 0.
    """
    sql_target, schema_name, role_name = temp_role
    table_name = "pytest_cls_deny_tbl"
    qualified_name = f"{schema_name}.{table_name}"

    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE TABLE [{schema_name}].[{table_name}] (id INT, ssn VARCHAR(20));",
        autocommit=True,
        fetch="none",
    )

    await permissions.deny_permission(
        sql_target,
        "SELECT",
        role_name,
        "OBJECT",
        object_name=qualified_name,
        columns=["ssn"],
    )

    result = await permissions.list_sql_permissions(
        sql_target, principal=role_name, object_name=qualified_name
    )
    col_rows = [p for p in result if p.column_name is not None]
    assert any(
        p.permission_name == "SELECT" and p.column_name == "ssn" and p.state == "DENY"
        for p in col_rows
    ), f"Expected column-level DENY SELECT on ssn for role {role_name!r}; column rows: {col_rows!r}"


async def test_revoke_grant_option_for_column_level_grant(
    temp_role: tuple[SqlTarget, str, str],
) -> None:
    """REVOKE GRANT OPTION FOR on a column-level grant must downgrade to a plain GRANT.

    Fabric T-SQL: first GRANT SELECT ... WITH GRANT OPTION, then
    ``REVOKE GRANT OPTION FOR SELECT ON OBJECT::[schema].[table] ([col]) FROM [role] CASCADE``.
    The resulting row must have state GRANT (not GRANT_WITH_GRANT_OPTION).

    CASCADE is required here, not optional: per the T-SQL REVOKE reference, "The REVOKE
    statement will fail if CASCADE is not specified when you are revoking a permission
    from a principal that was granted that permission with GRANT OPTION specified." This
    applies regardless of whether the grantee has actually re-granted the permission to
    anyone else, and it is not a column-level limitation.
    """
    sql_target, schema_name, role_name = temp_role
    table_name = "pytest_cls_gof_tbl"
    qualified_name = f"{schema_name}.{table_name}"

    await asyncio.to_thread(
        run_query,
        sql_target,
        f"CREATE TABLE [{schema_name}].[{table_name}] (id INT, revenue DECIMAL(18,2));",
        autocommit=True,
        fetch="none",
    )

    await permissions.grant_permission(
        sql_target,
        "SELECT",
        role_name,
        "OBJECT",
        object_name=qualified_name,
        columns=["revenue"],
        with_grant_option=True,
    )

    await permissions.revoke_permission(
        sql_target,
        "SELECT",
        role_name,
        "OBJECT",
        object_name=qualified_name,
        columns=["revenue"],
        grant_option_only=True,
        cascade=True,
    )

    result = await permissions.list_sql_permissions(
        sql_target, principal=role_name, object_name=qualified_name
    )
    col_rows = [p for p in result if p.column_name == "revenue"]
    assert any(p.state == "GRANT" for p in col_rows), (
        f"Expected plain GRANT after REVOKE GRANT OPTION FOR on column revenue "
        f"for role {role_name!r}; got: {col_rows!r}"
    )
