"""Integration tests for services.functions — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_functions.py

Fixture note: uses ``ephemeral_sql_target`` from conftest.  The target points at
a freshly-created warehouse for each test session; all functions created here are
cleaned up inside each test via try/finally.

Note on scope:
- Scalar UDFs and inline TVFs are **preview** features on Fabric DW as of mid-2026.
- Function DDL is supported on **both** Fabric Data Warehouses and SQL Analytics
  Endpoints per the Microsoft Fabric T-SQL reference.  No endpoint guard applies.
- Non-inlineable scalar UDFs (e.g. using GETDATE()) cannot be used inside
  SELECT ... FROM <user_table> but can still be created and called standalone.
"""

from __future__ import annotations

import contextlib

import pytest

from fabric_dw.exceptions import NotFoundError
from fabric_dw.models import FunctionDetails, FunctionKind
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


async def test_list_functions_returns_list(ephemeral_sql_target: SqlTarget) -> None:
    """list_functions on a fresh warehouse must return a (possibly empty) list."""
    result = await functions.list_functions(ephemeral_sql_target)
    assert isinstance(result, list)


async def test_create_scalar_function_returns_function_details(
    ephemeral_sql_target: SqlTarget,
) -> None:
    """create_function must return FunctionDetails with correct schema/name/kind."""
    schema = "dbo"
    fn_name = "pytest_fns_create_scalar"

    try:
        created = await functions.create_function(
            ephemeral_sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY
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
            await functions.drop_function(ephemeral_sql_target, schema, fn_name)


async def test_list_functions_includes_created_function(
    ephemeral_sql_target: SqlTarget,
) -> None:
    """A newly created function must appear in list_functions results."""
    schema = "dbo"
    fn_name = "pytest_fns_list"

    try:
        await functions.create_function(
            ephemeral_sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY
        )

        all_fns = await functions.list_functions(ephemeral_sql_target)
        names = {f.name for f in all_fns}
        assert fn_name in names

        # Schema filter
        dbo_fns = await functions.list_functions(ephemeral_sql_target, schema=schema)
        dbo_names = {f.name for f in dbo_fns}
        assert fn_name in dbo_names

        # Non-existent schema returns empty
        other_fns = await functions.list_functions(
            ephemeral_sql_target, schema="nonexistent_schema_x"
        )
        assert other_fns == []
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(ephemeral_sql_target, schema, fn_name)


async def test_list_functions_kind_filter_scalar(
    ephemeral_sql_target: SqlTarget,
) -> None:
    """list_functions with kind='scalar' must only return FN functions."""
    schema = "dbo"
    fn_name = "pytest_fns_kind_scalar"

    try:
        await functions.create_function(
            ephemeral_sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY
        )

        scalar_fns = await functions.list_functions(ephemeral_sql_target, kind="scalar")
        for f in scalar_fns:
            assert f.kind == FunctionKind.SCALAR
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(ephemeral_sql_target, schema, fn_name)


async def test_get_function_returns_definition(ephemeral_sql_target: SqlTarget) -> None:
    """get_function must return FunctionDetails with definition and parameters populated."""
    schema = "dbo"
    fn_name = "pytest_fns_get"

    try:
        await functions.create_function(
            ephemeral_sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY
        )

        fetched = await functions.get_function(ephemeral_sql_target, schema, fn_name)
        assert isinstance(fetched, FunctionDetails)
        assert fetched.schema_name == schema
        assert fetched.name == fn_name
        assert fetched.definition is not None
        assert "ltrim" in fetched.definition.lower() or "LTRIM" in fetched.definition
        # Parameters should include the return value (parameter_id=0) and @input (id=1)
        assert len(fetched.parameters) >= 1
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(ephemeral_sql_target, schema, fn_name)


async def test_get_function_raises_not_found_for_missing_function(
    ephemeral_sql_target: SqlTarget,
) -> None:
    """get_function must raise NotFoundError when the function does not exist."""
    with pytest.raises(NotFoundError):
        await functions.get_function(ephemeral_sql_target, "dbo", "pytest_fns_does_not_exist_xyz")


async def test_update_function_changes_definition(ephemeral_sql_target: SqlTarget) -> None:
    """update_function must redefine the function and return the updated definition."""
    schema = "dbo"
    fn_name = "pytest_fns_update"

    try:
        await functions.create_function(
            ephemeral_sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY
        )

        updated = await functions.update_function(
            ephemeral_sql_target, schema, fn_name, _SCALAR_UPDATED_BODY
        )
        assert isinstance(updated, FunctionDetails)
        assert updated.definition is not None
        assert "lower" in updated.definition.lower() or "LOWER" in updated.definition
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(ephemeral_sql_target, schema, fn_name)


async def test_drop_function_removes_function(ephemeral_sql_target: SqlTarget) -> None:
    """drop_function must remove the function so it no longer appears in list_functions."""
    schema = "dbo"
    fn_name = "pytest_fns_drop"

    await functions.create_function(ephemeral_sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY)

    # Confirm it exists before dropping
    before = await functions.list_functions(ephemeral_sql_target)
    assert any(f.name == fn_name for f in before)

    await functions.drop_function(ephemeral_sql_target, schema, fn_name)

    # Must not appear in listing after drop
    after = await functions.list_functions(ephemeral_sql_target)
    assert not any(f.name == fn_name for f in after)

    # get_function must raise NotFoundError
    with pytest.raises(NotFoundError):
        await functions.get_function(ephemeral_sql_target, schema, fn_name)


async def test_rename_function_roundtrip(ephemeral_sql_target: SqlTarget) -> None:
    """rename_function must rename the function and return updated details."""
    schema = "dbo"
    fn_name = "pytest_fns_rename_src"
    new_name = "pytest_fns_rename_dst"

    try:
        await functions.create_function(
            ephemeral_sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY
        )

        renamed = await functions.rename_function(
            ephemeral_sql_target, f"{schema}.{fn_name}", new_name
        )
        assert isinstance(renamed, FunctionDetails)
        assert renamed.name == new_name
        assert renamed.schema_name == schema

        # Old name must be gone
        after = await functions.list_functions(ephemeral_sql_target)
        assert not any(f.name == fn_name for f in after)
        assert any(f.name == new_name for f in after)
    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(ephemeral_sql_target, schema, fn_name)
        with contextlib.suppress(Exception):
            await functions.drop_function(ephemeral_sql_target, schema, new_name)


async def test_create_function_full_roundtrip(ephemeral_sql_target: SqlTarget) -> None:
    """End-to-end: create -> list -> get -> update -> rename -> drop."""
    schema = "dbo"
    fn_name = "pytest_fns_roundtrip"
    renamed = "pytest_fns_roundtrip_v2"

    try:
        # --- create ---
        created = await functions.create_function(
            ephemeral_sql_target, schema, fn_name, _SCALAR_INLINEABLE_BODY
        )
        assert created.name == fn_name
        assert created.kind == FunctionKind.SCALAR

        # --- list ---
        all_fns = await functions.list_functions(ephemeral_sql_target)
        assert any(f.name == fn_name for f in all_fns)

        # --- get (definition contains body) ---
        fetched = await functions.get_function(ephemeral_sql_target, schema, fn_name)
        assert fetched.definition is not None
        assert "LTRIM" in fetched.definition.upper() or "ltrim" in fetched.definition.lower()

        # --- update (definition changes) ---
        updated = await functions.update_function(
            ephemeral_sql_target, schema, fn_name, _SCALAR_UPDATED_BODY
        )
        assert updated.definition is not None
        assert "lower" in updated.definition.lower() or "LOWER" in updated.definition

        # --- rename ---
        rn = await functions.rename_function(ephemeral_sql_target, f"{schema}.{fn_name}", renamed)
        assert rn.name == renamed

        # --- drop renamed ---
        await functions.drop_function(ephemeral_sql_target, schema, renamed)
        after = await functions.list_functions(ephemeral_sql_target)
        assert not any(f.name == renamed for f in after)

    finally:
        with contextlib.suppress(Exception):
            await functions.drop_function(ephemeral_sql_target, schema, fn_name)
        with contextlib.suppress(Exception):
            await functions.drop_function(ephemeral_sql_target, schema, renamed)


async def test_list_functions_on_sql_analytics_endpoint(
    ephemeral_sql_endpoint,
    workspace_id,
) -> None:
    """list_functions on a SQL analytics endpoint must succeed (read OK, no guard)."""
    from fabric_dw.models import Warehouse  # noqa: PLC0415
    from fabric_dw.sql import SqlTarget  # noqa: PLC0415

    ep: Warehouse = ephemeral_sql_endpoint
    if ep.connection_string is None:
        pytest.skip("SQL analytics endpoint has no connection string")
    target = SqlTarget(
        workspace_id=str(workspace_id),
        database=ep.name,
        connection_string=ep.connection_string,
    )
    result = await functions.list_functions(target)
    assert isinstance(result, list)
