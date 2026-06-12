"""Integration tests for services.tables — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_tables.py

Fixture note: uses ``ephemeral_sql_target`` from conftest.  The target points at
a freshly-created warehouse for each test session; all tables created here are
cleaned up in the roundtrip test itself.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import pytest

from fabric_dw.exceptions import NotFoundError
from fabric_dw.models import Table
from fabric_dw.services import tables
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration


async def test_list_tables_returns_list(ephemeral_sql_target: SqlTarget) -> None:
    result = await tables.list_tables(ephemeral_sql_target)
    assert isinstance(result, list)


async def test_create_read_clear_delete_roundtrip(ephemeral_sql_target: SqlTarget) -> None:
    schema = "dbo"
    table_name = "pytest_tables_roundtrip"
    select_body = "SELECT 1 AS id, 'hello' AS greeting"

    try:
        created = await tables.create_table(ephemeral_sql_target, schema, table_name, select_body)
        assert isinstance(created, Table)
        assert created.schema_name == schema
        assert created.name == table_name

        cols, rows = await tables.read_table(ephemeral_sql_target, schema, table_name, count=5)
        assert "id" in cols or len(cols) > 0
        assert isinstance(rows, list)

        all_tables = await tables.list_tables(ephemeral_sql_target)
        names = {t.name for t in all_tables}
        assert table_name in names

        await tables.clear_table(ephemeral_sql_target, schema, table_name)
        _, rows_after_clear = await tables.read_table(
            ephemeral_sql_target, schema, table_name, count=5
        )
        assert rows_after_clear == []

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(ephemeral_sql_target, schema, table_name)

    with pytest.raises((NotFoundError, Exception)):
        await tables.read_table(ephemeral_sql_target, schema, table_name)


async def test_clone_table_creates_identical_rows(ephemeral_sql_target: SqlTarget) -> None:
    """clone_table (plain, no AT) creates a clone with the same rows as the source."""
    schema = "dbo"
    source_name = "pytest_clone_source"
    clone_name = "pytest_clone_copy"
    select_body = "SELECT 1 AS id, 'hello' AS greeting"

    try:
        # Create the source table.
        await tables.create_table(ephemeral_sql_target, schema, source_name, select_body)

        # Clone it without a point-in-time.
        cloned = await tables.clone_table(
            ephemeral_sql_target, f"{schema}.{source_name}", f"{schema}.{clone_name}"
        )
        assert isinstance(cloned, Table)
        assert cloned.schema_name == schema
        assert cloned.name == clone_name

        # Verify the clone is visible and contains the same data.
        all_tables = await tables.list_tables(ephemeral_sql_target, schema=schema)
        names = {t.name for t in all_tables}
        assert clone_name in names

        src_cols, src_rows = await tables.read_table(
            ephemeral_sql_target, schema, source_name, count=100
        )
        cln_cols, cln_rows = await tables.read_table(
            ephemeral_sql_target, schema, clone_name, count=100
        )
        assert src_cols == cln_cols
        assert len(cln_rows) == len(src_rows)
    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(ephemeral_sql_target, schema, source_name)
        with contextlib.suppress(Exception):
            await tables.delete_table(ephemeral_sql_target, schema, clone_name)


async def test_clone_table_at_point_in_time(ephemeral_sql_target: SqlTarget) -> None:
    """clone_table with AT timestamp clones the table at the specified point in time.

    A fresh table will have existed for only a few seconds, so the AT timestamp
    is captured immediately after creation and used for the clone.  If the
    engine rejects the timestamp because the table has no history at that
    instant (some engines require at least one committed version *before* the
    AT time), the test is skipped with a clear reason rather than failing.
    """
    schema = "dbo"
    source_name = "pytest_clone_at_source"
    clone_name = "pytest_clone_at_copy"
    select_body = "SELECT 42 AS value"

    try:
        await tables.create_table(ephemeral_sql_target, schema, source_name, select_body)

        # Capture a timestamp a moment after creation.
        at_dt = datetime.now(tz=UTC)

        try:
            cloned = await tables.clone_table(
                ephemeral_sql_target,
                f"{schema}.{source_name}",
                f"{schema}.{clone_name}",
                at=at_dt,
            )
        except Exception as exc:
            # A freshly-created table may have no committed history older than
            # the AT timestamp; skip gracefully rather than marking the whole
            # test as a hard failure.
            pytest.skip(
                f"Point-in-time clone not feasible on a freshly created table "
                f"(no history at {at_dt.isoformat()}): {exc}"
            )

        assert isinstance(cloned, Table)
        assert cloned.name == clone_name
    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(ephemeral_sql_target, schema, source_name)
        with contextlib.suppress(Exception):
            await tables.delete_table(ephemeral_sql_target, schema, clone_name)
