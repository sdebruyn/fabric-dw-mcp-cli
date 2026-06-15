"""Integration tests for services.functions — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_functions.py

Fixture note: uses ``warehouse_schema`` from conftest, which creates a uniquely-named
schema inside the session-shared warm warehouse and cascade-drops it on teardown.
All functions created here are created inside that schema; the schema cascade drop
handles cleanup, with additional try/finally guards for mid-test failures.

The SQL Analytics Endpoint read test uses ``ephemeral_sql_endpoint`` (SQL Analytics
Endpoint, a ``Warehouse`` object typed as ``WarehouseKind.SQL_ENDPOINT``) because
the shared warehouse fixture does not cover endpoint-only behaviour.

Note on scope:
- Scalar UDFs and inline TVFs are **preview** features on Fabric DW as of mid-2026.
- Function DDL is supported on **both** Fabric Data Warehouses and SQL Analytics
  Endpoints per the Microsoft Fabric T-SQL reference.  No endpoint guard applies.
- Non-inlineable scalar UDFs (e.g. using GETDATE()) cannot be used inside
  SELECT ... FROM <user_table> but can still be created and called standalone.
"""

from __future__ import annotations

import contextlib
from uuid import UUID

import pytest

from fabric_dw.exceptions import NotFoundError
from fabric_dw.models import FunctionDetails, FunctionKind, Warehouse
from fabric_dw.services import functions
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Sample function bodies (scalar UDFs)
# ---------------------------------------------------------------------------

_SCALAR_INLINEABLE_BODY = """\
(@input NVARCHAR(100))
RETURNS NVARCHAR(100)
AS
BEGIN
    RETURN LTRIM(RTRIM(@input))
END
"""

_SCALAR_UPDATED_BODY = """\
(@input NVARCHAR(100))
RETURNS NVARCHAR(100)
AS
BEGIN
    RETURN LOWER(LTRIM(RTRIM(@input)))
END
"""

_INLINE_TVF_BODY = """\
(@min_val INT)
RETURNS TABLE
AS
RETURN (SELECT 1 AS id WHERE 1 >= @min_val)
"""


async def test_list_functions_returns_list(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """list_functions on the shared warehouse must return a (possibly empty) list."""
    sql_target, _schema = warehouse_schema
    result = await functions.list_functions(sql_target)
    assert isinstance(result, list)


async def test_create_scalar_function_returns_function_details(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """create_function must return FunctionDetails with correct schema/name/kind."""
    sql_target, schema = warehouse_schema
    fn_name = "pytest_fns_create_scalar"

    try:
        created = await functions.create_function(
            sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY
        )
        assert isinstance(created, FunctionDetails)
        assert created.schema_name == schema
        assert created.name == fn_name
        assert created.qualified_name == f"{schema}.{fn_name}"
        assert created.kind == FunctionKind.SCALAR
        assert created.definition is not None
        assert "LTRIM" in created.definition.upper() or "ltrim" in created.definition.lower()
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(sql_target, schema, fn_name)


async def test_list_functions_includes_created_function(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """A newly created function must appear in list_functions results."""
    sql_target, schema = warehouse_schema
    fn_name = "pytest_fns_list"

    try:
        await functions.create_function(sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY)

        all_fns = await functions.list_functions(sql_target)
        names = {f.name for f in all_fns if f.schema_name == schema}
        assert fn_name in names

        # Schema filter must narrow to only this schema
        dbo_fns = await functions.list_functions(sql_target, schema=schema)
        dbo_names = {f.name for f in dbo_fns}
        assert fn_name in dbo_names

        # Non-existent schema returns empty
        other_fns = await functions.list_functions(sql_target, schema="nonexistent_schema_x")
        assert other_fns == []
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(sql_target, schema, fn_name)


async def test_list_functions_kind_filter_scalar(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """list_functions with kind='scalar' must only return FN functions."""
    sql_target, schema = warehouse_schema
    fn_name = "pytest_fns_kind_scalar"

    try:
        await functions.create_function(sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY)

        scalar_fns = await functions.list_functions(sql_target, kind="scalar")
        for f in scalar_fns:
            assert f.kind == FunctionKind.SCALAR
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(sql_target, schema, fn_name)


async def test_get_function_returns_definition(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """get_function must return FunctionDetails with definition and parameters populated."""
    sql_target, schema = warehouse_schema
    fn_name = "pytest_fns_get"

    try:
        await functions.create_function(sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY)

        fetched = await functions.get_function(sql_target, schema, fn_name)
        assert isinstance(fetched, FunctionDetails)
        assert fetched.schema_name == schema
        assert fetched.name == fn_name
        assert fetched.definition is not None
        assert "ltrim" in fetched.definition.lower() or "LTRIM" in fetched.definition
        # Parameters should include the return value (parameter_id=0) and @input (id=1)
        assert len(fetched.parameters) >= 1
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(sql_target, schema, fn_name)


async def test_get_function_raises_not_found_for_missing_function(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """get_function must raise NotFoundError when the function does not exist."""
    sql_target, schema = warehouse_schema
    with pytest.raises(NotFoundError):
        await functions.get_function(sql_target, schema, "pytest_fns_does_not_exist_xyz")


async def test_update_function_changes_definition(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """update_function must redefine the function and return the updated definition."""
    sql_target, schema = warehouse_schema
    fn_name = "pytest_fns_update"

    try:
        await functions.create_function(sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY)

        updated = await functions.update_function(sql_target, schema, fn_name, _SCALAR_UPDATED_BODY)
        assert isinstance(updated, FunctionDetails)
        assert updated.definition is not None
        assert "lower" in updated.definition.lower() or "LOWER" in updated.definition
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(sql_target, schema, fn_name)


async def test_drop_function_removes_function(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """drop_function must remove the function so it no longer appears in list_functions."""
    sql_target, schema = warehouse_schema
    fn_name = "pytest_fns_drop"

    await functions.create_function(sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY)

    # Confirm it exists before dropping
    before = await functions.list_functions(sql_target, schema=schema)
    assert any(f.name == fn_name for f in before)

    await functions.drop_function(sql_target, schema, fn_name)

    # Must not appear in listing after drop
    after = await functions.list_functions(sql_target, schema=schema)
    assert not any(f.name == fn_name for f in after)

    # get_function must raise NotFoundError
    with pytest.raises(NotFoundError):
        await functions.get_function(sql_target, schema, fn_name)


async def test_rename_function_roundtrip(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """rename_function must rename the function and return updated details."""
    sql_target, schema = warehouse_schema
    fn_name = "pytest_fns_rename_src"
    new_name = "pytest_fns_rename_dst"

    try:
        await functions.create_function(sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY)

        renamed = await functions.rename_function(sql_target, f"{schema}.{fn_name}", new_name)
        assert isinstance(renamed, FunctionDetails)
        assert renamed.name == new_name
        assert renamed.schema_name == schema

        # Old name must be gone, new name must appear
        after = await functions.list_functions(sql_target, schema=schema)
        assert not any(f.name == fn_name for f in after)
        assert any(f.name == new_name for f in after)
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(sql_target, schema, fn_name)
        with contextlib.suppress(Exception):
            await functions.drop_function(sql_target, schema, new_name)


async def test_create_function_full_roundtrip(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """End-to-end: create -> list -> get -> update -> rename -> drop."""
    sql_target, schema = warehouse_schema
    fn_name = "pytest_fns_roundtrip"
    renamed = "pytest_fns_roundtrip_v2"

    try:
        # --- create ---
        created = await functions.create_function(
            sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY
        )
        assert created.name == fn_name
        assert created.kind == FunctionKind.SCALAR

        # --- list (schema-filtered to avoid cross-test noise) ---
        all_fns = await functions.list_functions(sql_target, schema=schema)
        assert any(f.name == fn_name for f in all_fns)

        # --- get (definition contains body) ---
        fetched = await functions.get_function(sql_target, schema, fn_name)
        assert fetched.definition is not None
        assert "LTRIM" in fetched.definition.upper() or "ltrim" in fetched.definition.lower()

        # --- update (definition changes) ---
        updated = await functions.update_function(sql_target, schema, fn_name, _SCALAR_UPDATED_BODY)
        assert updated.definition is not None
        assert "lower" in updated.definition.lower() or "LOWER" in updated.definition

        # --- rename ---
        rn = await functions.rename_function(sql_target, f"{schema}.{fn_name}", renamed)
        assert rn.name == renamed

        # --- drop renamed ---
        await functions.drop_function(sql_target, schema, renamed)
        after = await functions.list_functions(sql_target, schema=schema)
        assert not any(f.name == renamed for f in after)

    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(sql_target, schema, fn_name)
        with contextlib.suppress(Exception):
            await functions.drop_function(sql_target, schema, renamed)


async def test_create_inline_tvf_and_list_by_kind(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """create_function with an inline TVF body must return kind=inline_tvf
    and list_functions with kind='inline_tvf' must include it."""
    sql_target, schema = warehouse_schema
    fn_name = "pytest_fns_inline_tvf"

    try:
        created = await functions.create_function(sql_target, schema, fn_name, _INLINE_TVF_BODY)
        assert isinstance(created, FunctionDetails)
        assert created.schema_name == schema
        assert created.name == fn_name
        assert created.kind == FunctionKind.INLINE_TVF

        # Kind filter must include the TVF
        tvf_fns = await functions.list_functions(sql_target, schema=schema, kind="inline-tvf")
        assert any(f.name == fn_name for f in tvf_fns)
        for f in tvf_fns:
            assert f.kind == FunctionKind.INLINE_TVF

        # Scalar filter must exclude it
        scalar_fns = await functions.list_functions(sql_target, schema=schema, kind="scalar")
        assert not any(f.name == fn_name for f in scalar_fns)
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(sql_target, schema, fn_name)


# ---------------------------------------------------------------------------
# SQL Analytics Endpoint — list is allowed (no endpoint guard on functions)
# ---------------------------------------------------------------------------


async def test_list_functions_on_sql_analytics_endpoint(
    ephemeral_sql_endpoint: Warehouse,
    workspace_id: UUID,
) -> None:
    """list_functions on a SQL analytics endpoint must succeed (read OK, no guard)."""
    if ephemeral_sql_endpoint.connection_string is None:
        pytest.skip("SQL analytics endpoint has no connection string")
    target = SqlTarget(
        workspace_id=str(workspace_id),
        database=ephemeral_sql_endpoint.name,
        connection_string=ephemeral_sql_endpoint.connection_string,
    )
    result = await functions.list_functions(target)
    assert isinstance(result, list)
