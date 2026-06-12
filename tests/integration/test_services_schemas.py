"""Integration tests for services.schemas — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_schemas.py

Fixture note: uses ``ephemeral_sql_target`` from conftest.  The target points at
a freshly-created warehouse for each test session; all schemas created here are
cleaned up in the respective test's ``finally`` block.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

from fabric_dw.models import Schema
from fabric_dw.services import schemas, tables
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_schema_name() -> str:
    """Return a short, collision-resistant schema name safe for Fabric DDL."""
    return f"pytest_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_list_schemas_returns_list(ephemeral_sql_target: SqlTarget) -> None:
    """list_schemas returns a list (dbo is always present on a fresh warehouse)."""
    result = await schemas.list_schemas(ephemeral_sql_target)
    assert isinstance(result, list)
    # dbo is user-writable and must appear on every fresh Fabric DW.
    schema_names = {s.name for s in result}
    assert "dbo" in schema_names


async def test_list_schemas_excludes_system_schemas(ephemeral_sql_target: SqlTarget) -> None:
    """System schemas must not appear in list_schemas output."""
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
    result = await schemas.list_schemas(ephemeral_sql_target)
    schema_names = {s.name for s in result}
    overlap = schema_names & system_schemas
    assert not overlap, f"System schemas leaked into list_schemas output: {overlap}"


async def test_create_schema_returns_schema_model(ephemeral_sql_target: SqlTarget) -> None:
    """create_schema returns a Schema with the correct name."""
    name = _unique_schema_name()
    try:
        created = await schemas.create_schema(ephemeral_sql_target, name)
        assert isinstance(created, Schema)
        assert created.name == name
        assert created.principal_id is not None
    finally:
        with contextlib.suppress(Exception):
            await schemas.delete_schema(ephemeral_sql_target, name)


async def test_create_list_delete_roundtrip(ephemeral_sql_target: SqlTarget) -> None:
    """Create a schema, verify it appears in list_schemas, then delete it."""
    name = _unique_schema_name()
    try:
        await schemas.create_schema(ephemeral_sql_target, name)

        listed = await schemas.list_schemas(ephemeral_sql_target)
        listed_names = {s.name for s in listed}
        assert name in listed_names, f"Newly-created schema {name!r} missing from list_schemas"

        await schemas.delete_schema(ephemeral_sql_target, name)

        after_delete = await schemas.list_schemas(ephemeral_sql_target)
        after_delete_names = {s.name for s in after_delete}
        assert name not in after_delete_names, f"Schema {name!r} still present after delete_schema"
    finally:
        # Guard: ensure the schema is gone even if the test assertion failed
        # before the explicit delete above.
        with contextlib.suppress(Exception):
            await schemas.delete_schema(ephemeral_sql_target, name)


async def test_delete_schema_cascade_drops_table(ephemeral_sql_target: SqlTarget) -> None:
    """delete_schema(cascade=True) drops contained tables before dropping the schema.

    Steps:
    1. Create a user schema.
    2. Create a table inside that schema.
    3. Call delete_schema(cascade=True) — must succeed without a "schema not empty" error.
    4. Verify the schema is gone from list_schemas.
    """
    schema_name = _unique_schema_name()
    table_name = "pytest_cascade_tbl"

    try:
        await schemas.create_schema(ephemeral_sql_target, schema_name)

        # Create a table inside the new schema so the schema is non-empty.
        await tables.create_table(
            ephemeral_sql_target,
            schema_name,
            table_name,
            "SELECT 1 AS id",
        )

        # cascade=True must drop the table then the schema without raising.
        await schemas.delete_schema(ephemeral_sql_target, schema_name, cascade=True)

        after_delete = await schemas.list_schemas(ephemeral_sql_target)
        after_names = {s.name for s in after_delete}
        assert schema_name not in after_names, (
            f"Schema {schema_name!r} still present after cascade delete"
        )
    finally:
        # Belt-and-suspenders cleanup: suppress errors if already cleaned up.
        with contextlib.suppress(Exception):
            await tables.delete_table(ephemeral_sql_target, schema_name, table_name)
        with contextlib.suppress(Exception):
            await schemas.delete_schema(ephemeral_sql_target, schema_name)


async def test_delete_plain_schema_no_cascade(ephemeral_sql_target: SqlTarget) -> None:
    """delete_schema without cascade succeeds on an empty schema."""
    name = _unique_schema_name()
    try:
        await schemas.create_schema(ephemeral_sql_target, name)
        # Plain delete (no cascade) on an empty schema must succeed.
        await schemas.delete_schema(ephemeral_sql_target, name)

        after = await schemas.list_schemas(ephemeral_sql_target)
        assert name not in {s.name for s in after}
    finally:
        with contextlib.suppress(Exception):
            await schemas.delete_schema(ephemeral_sql_target, name)
