"""Integration tests for services.procedures — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_procedures.py

Fixture note: uses ``mutable_schema_target`` from conftest, which creates a
uniquely-named schema on BOTH the shared warm warehouse and the shared SQL analytics
endpoint, then cascade-drops it on teardown.  All procedures are created inside that
schema; the cascade drop handles cleanup, with additional try/finally guards for
mid-test failures.

The ``mutable_schema_target`` fixture is parametrized over two targets:
  - ``[warehouse]``     — Data Warehouse (always runs)
  - ``[sql_endpoint]``  — SQL Analytics Endpoint (``pytest.mark.sql_endpoint``, CI only)

Stored procedures are supported on **both** Fabric Data Warehouses and SQL Analytics
Endpoints.  The service has no ``_assert_not_sql_endpoint`` guard — this file proves
it on both targets.
"""

from __future__ import annotations

import contextlib

import pytest

from fabric_dw.exceptions import NotFoundError
from fabric_dw.models import StoredProcedure
from fabric_dw.services import procedures
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration


async def test_list_procedures_returns_list(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """list_procedures on the shared warehouse must return a list."""
    sql_target, _schema = mutable_schema_target
    result = await procedures.list_procedures(sql_target)
    assert isinstance(result, list)


async def test_create_procedure_returns_procedure_object(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """create_procedure must return a StoredProcedure with the correct schema/name."""
    sql_target, schema = mutable_schema_target
    proc_name = "pytest_procs_create"
    body = "BEGIN SELECT 1 AS id, 'hello' AS greeting END"

    try:
        created = await procedures.create_procedure(sql_target, schema, proc_name, body)
        assert isinstance(created, StoredProcedure)
        assert created.schema_name == schema
        assert created.name == proc_name
        assert created.qualified_name == f"{schema}.{proc_name}"
        assert created.definition is not None
        assert "SELECT" in created.definition.upper()
    finally:
        with contextlib.suppress(Exception):
            await procedures.drop_procedure(sql_target, schema, proc_name)


async def test_list_procedures_includes_created_procedure(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """A newly created procedure must appear in list_procedures results."""
    sql_target, schema = mutable_schema_target
    proc_name = "pytest_procs_list"
    body = "BEGIN SELECT 42 AS answer END"

    try:
        await procedures.create_procedure(sql_target, schema, proc_name, body)

        all_procs = await procedures.list_procedures(sql_target)
        assert any(p.name == proc_name and p.schema_name == schema for p in all_procs)

        # Also verify schema filter narrows correctly
        schema_procs = await procedures.list_procedures(sql_target, schema=schema)
        schema_names = {p.name for p in schema_procs}
        assert proc_name in schema_names

        # A schema that doesn't exist should return empty
        other_procs = await procedures.list_procedures(sql_target, schema="nonexistent_schema_x")
        assert other_procs == []
    finally:
        with contextlib.suppress(Exception):
            await procedures.drop_procedure(sql_target, schema, proc_name)


async def test_get_procedure_returns_definition(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """get_procedure must return the StoredProcedure with its definition populated."""
    sql_target, schema = mutable_schema_target
    proc_name = "pytest_procs_get"
    body = "BEGIN SELECT 99 AS magic_number END"

    try:
        await procedures.create_procedure(sql_target, schema, proc_name, body)

        fetched = await procedures.get_procedure(sql_target, schema, proc_name)
        assert isinstance(fetched, StoredProcedure)
        assert fetched.schema_name == schema
        assert fetched.name == proc_name
        assert fetched.definition is not None
        assert "magic_number" in fetched.definition.lower()
    finally:
        with contextlib.suppress(Exception):
            await procedures.drop_procedure(sql_target, schema, proc_name)


async def test_get_procedure_raises_not_found_for_missing_procedure(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """get_procedure must raise NotFoundError when the procedure does not exist."""
    sql_target, schema = mutable_schema_target
    with pytest.raises(NotFoundError):
        await procedures.get_procedure(sql_target, schema, "pytest_procs_does_not_exist")


async def test_update_procedure_changes_definition(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """update_procedure must redefine the procedure and return the updated definition."""
    sql_target, schema = mutable_schema_target
    proc_name = "pytest_procs_update"
    original_body = "BEGIN SELECT 1 AS version END"
    updated_body = "BEGIN SELECT 2 AS version, 'updated' AS status END"

    try:
        await procedures.create_procedure(sql_target, schema, proc_name, original_body)

        updated = await procedures.update_procedure(sql_target, schema, proc_name, updated_body)
        assert isinstance(updated, StoredProcedure)
        assert updated.definition is not None
        assert "status" in updated.definition.lower()
    finally:
        with contextlib.suppress(Exception):
            await procedures.drop_procedure(sql_target, schema, proc_name)


async def test_drop_procedure_removes_procedure(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """drop_procedure must remove the procedure so it no longer appears in list_procedures."""
    sql_target, schema = mutable_schema_target
    proc_name = "pytest_procs_drop"
    body = "BEGIN SELECT 0 AS placeholder END"

    await procedures.create_procedure(sql_target, schema, proc_name, body)

    # Confirm it exists before dropping
    before = await procedures.list_procedures(sql_target)
    assert any(p.name == proc_name and p.schema_name == schema for p in before)

    await procedures.drop_procedure(sql_target, schema, proc_name)

    # Must not appear in listing after drop
    after = await procedures.list_procedures(sql_target)
    assert not any(p.name == proc_name and p.schema_name == schema for p in after)

    # get_procedure must raise NotFoundError
    with pytest.raises(NotFoundError):
        await procedures.get_procedure(sql_target, schema, proc_name)


async def test_create_procedure_full_roundtrip(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """End-to-end: create -> list -> get (definition contains body) -> update (definition
    changes) -> drop.
    """
    sql_target, schema = mutable_schema_target
    proc_name = "pytest_procs_roundtrip"
    v1_body = "BEGIN SELECT 1 AS n END"
    v2_body = "BEGIN SELECT 2 AS n, 'v2' AS label END"

    try:
        # --- create ---
        created = await procedures.create_procedure(sql_target, schema, proc_name, v1_body)
        assert created.name == proc_name

        # --- list ---
        all_procs = await procedures.list_procedures(sql_target)
        assert any(p.name == proc_name and p.schema_name == schema for p in all_procs)

        # --- get (definition contains body) ---
        fetched = await procedures.get_procedure(sql_target, schema, proc_name)
        assert fetched.definition is not None
        assert "SELECT" in fetched.definition.upper()

        # --- update (definition changes) ---
        updated = await procedures.update_procedure(sql_target, schema, proc_name, v2_body)
        assert updated.definition is not None
        assert "label" in updated.definition.lower()

    finally:
        with contextlib.suppress(Exception):
            await procedures.drop_procedure(sql_target, schema, proc_name)
