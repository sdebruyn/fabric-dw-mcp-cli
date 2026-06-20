"""Integration tests for services.schemas — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_schemas.py

Fixture notes:
- ``read_target`` (parametrized): runs read-only listing tests against both the shared
  warm warehouse and the shared SQL analytics endpoint.
- ``mutable_schema_target``: creates a uniquely-named schema on BOTH the shared warm
  warehouse and the shared SQL analytics endpoint, then cascade-drops it on teardown.
  Used for schema-CRUD tests that create additional schemas on the same target.
- ``warehouse_schema``: creates a uniquely-named schema in the shared warm warehouse
  and cascade-drops it on teardown.  Used only for the cascade-drops-table test,
  which relies on ``tables.create_table`` (Data Warehouse only).

The ``mutable_schema_target`` fixture is parametrized over two targets:
  - ``[warehouse]``     — Data Warehouse (always runs)
  - ``[sql_endpoint]``  — SQL Analytics Endpoint (``pytest.mark.sql_endpoint``, CI only)

Design: The schema-CRUD tests below create *additional* schemas inside the target
(not inside ``mutable_schema_target``'s isolation schema, because schemas cannot be
nested on Fabric).  Each test is responsible for deleting its own schema in a finally
block.  The shared warehouse/endpoint teardown at session end provides a backstop.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

from fabric_dw.models import Schema
from fabric_dw.services import schemas, tables, views
from fabric_dw.sql import SqlTarget

from .conftest import SEED_SCHEMA_NAME

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_schema_name() -> str:
    """Return a short, collision-resistant schema name safe for Fabric DDL."""
    return f"pytest_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Dual-target read test — runs against both warehouse and SQL analytics endpoint
# via the parametrized ``read_target`` fixture.
# ---------------------------------------------------------------------------


async def test_list_schemas_includes_seed_schema(
    read_target: SqlTarget,
) -> None:
    """list_schemas must include the pre-seeded ``sample`` schema on both targets."""
    result = await schemas.list_schemas(read_target)
    assert isinstance(result, list)
    schema_names = {s.name for s in result}
    assert SEED_SCHEMA_NAME in schema_names


# ---------------------------------------------------------------------------
# Read-only tests (use the shared warehouse SQL target directly)
# ---------------------------------------------------------------------------


async def test_list_schemas_returns_list(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """list_schemas returns a list (dbo is always present on a fresh warehouse)."""
    sql_target, _schema = warehouse_schema
    result = await schemas.list_schemas(sql_target)
    assert isinstance(result, list)
    # dbo is user-writable and must appear on every fresh Fabric DW.
    schema_names = {s.name for s in result}
    assert "dbo" in schema_names


async def test_list_schemas_excludes_system_schemas(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """System schemas must not appear in list_schemas output."""
    sql_target, _schema = warehouse_schema
    system_schemas = {
        "sys",
        "INFORMATION_SCHEMA",
        "guest",
        "db_owner",
        "db_accessadmin",
        "db_securityadmin",
        "db_ddladmin",
        "db_backupoperator",
        "db_datareader",
        "db_datawriter",
        "db_denydatareader",
        "db_denydatawriter",
    }
    result = await schemas.list_schemas(sql_target)
    schema_names = {s.name for s in result}
    overlap = schema_names & system_schemas
    assert not overlap, f"System schemas leaked into list_schemas output: {overlap}"


# ---------------------------------------------------------------------------
# Mutating tests — dual-target via ``mutable_schema_target``.
#
# Each test creates an *additional* uniquely-named schema on the same target
# that ``mutable_schema_target`` already points at, then cleans it up in a
# finally block.
# ---------------------------------------------------------------------------


async def test_create_schema_returns_schema_model(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """create_schema returns a Schema with the correct name on both targets."""
    sql_target, _parent_schema = mutable_schema_target
    name = _unique_schema_name()
    try:
        created = await schemas.create_schema(sql_target, name)
        assert isinstance(created, Schema)
        assert created.name == name
        assert created.principal_id is not None
    finally:
        with contextlib.suppress(Exception):
            await schemas.delete_schema(sql_target, name)


async def test_create_list_delete_roundtrip(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """Create a schema, verify it appears in list_schemas, then delete it on both targets."""
    sql_target, _parent_schema = mutable_schema_target
    name = _unique_schema_name()
    try:
        await schemas.create_schema(sql_target, name)

        listed = await schemas.list_schemas(sql_target)
        listed_names = {s.name for s in listed}
        assert name in listed_names, f"Newly-created schema {name!r} missing from list_schemas"

        await schemas.delete_schema(sql_target, name)

        after_delete = await schemas.list_schemas(sql_target)
        after_delete_names = {s.name for s in after_delete}
        assert name not in after_delete_names, f"Schema {name!r} still present after delete_schema"
    finally:
        # Guard: ensure the schema is gone even if the test assertion failed
        # before the explicit delete above.
        with contextlib.suppress(Exception):
            await schemas.delete_schema(sql_target, name)


async def test_delete_schema_cascade_drops_table(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """delete_schema(cascade=True) drops contained tables before dropping the schema.

    Steps:
    1. Create a user schema.
    2. Create a table inside that schema.
    3. Call delete_schema(cascade=True) — must succeed without a "schema not empty" error.
    4. Verify the schema is gone from list_schemas.

    Note: kept on ``warehouse_schema`` (Data Warehouse only) because
    ``tables.create_table`` is rejected on SQL Analytics Endpoints.  The
    endpoint variant of cascade is covered by
    ``test_delete_schema_cascade_drops_view_on_endpoint``.
    """
    sql_target, _parent_schema = warehouse_schema
    schema_name = _unique_schema_name()
    table_name = "pytest_cascade_tbl"

    try:
        await schemas.create_schema(sql_target, schema_name)

        # Create a table inside the new schema so the schema is non-empty.
        await tables.create_table(
            sql_target,
            schema_name,
            table_name,
            "SELECT 1 AS id",
        )

        # cascade=True must drop the table then the schema without raising.
        await schemas.delete_schema(sql_target, schema_name, cascade=True)

        after_delete = await schemas.list_schemas(sql_target)
        after_names = {s.name for s in after_delete}
        assert schema_name not in after_names, (
            f"Schema {schema_name!r} still present after cascade delete"
        )
    finally:
        # Belt-and-suspenders cleanup: suppress errors if already cleaned up.
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema_name, table_name)
        with contextlib.suppress(Exception):
            await schemas.delete_schema(sql_target, schema_name, cascade=True)


async def test_delete_schema_cascade_drops_view_on_endpoint(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """delete_schema(cascade=True) drops contained views before dropping the schema.

    Mirrors ``test_delete_schema_cascade_drops_table`` but uses a view (dual-target)
    instead of a table (DWH-only), so this test runs on both the warehouse and the
    SQL analytics endpoint via ``mutable_schema_target``.
    """
    sql_target, _parent_schema = mutable_schema_target
    schema_name = _unique_schema_name()
    view_name = "pytest_cascade_view"

    try:
        await schemas.create_schema(sql_target, schema_name)

        # Create a view inside the new schema so the schema is non-empty.
        await views.create_view(sql_target, schema_name, view_name, "SELECT 1 AS id")

        # cascade=True must drop the view then the schema without raising.
        await schemas.delete_schema(sql_target, schema_name, cascade=True)

        after_delete = await schemas.list_schemas(sql_target)
        after_names = {s.name for s in after_delete}
        assert schema_name not in after_names, (
            f"Schema {schema_name!r} still present after cascade delete"
        )
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema_name, view_name)
        with contextlib.suppress(Exception):
            await schemas.delete_schema(sql_target, schema_name, cascade=True)


async def test_delete_plain_schema_no_cascade(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """delete_schema without cascade succeeds on an empty schema on both targets."""
    sql_target, _parent_schema = mutable_schema_target
    name = _unique_schema_name()
    try:
        await schemas.create_schema(sql_target, name)
        # Plain delete (no cascade) on an empty schema must succeed.
        await schemas.delete_schema(sql_target, name)

        after = await schemas.list_schemas(sql_target)
        assert name not in {s.name for s in after}
    finally:
        with contextlib.suppress(Exception):
            await schemas.delete_schema(sql_target, name)
