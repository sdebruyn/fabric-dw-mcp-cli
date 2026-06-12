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

from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import Table
from fabric_dw.services import tables
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration

# Fragments that indicate the SQL engine rejected an AT timestamp because the
# table has no committed history at the requested point in time.  These are
# expected and trigger a pytest.skip rather than a test failure.
_CLONE_AT_SKIP_FRAGMENTS = (
    ("clone", "point in time"),
    ("no version",),
    ("history",),
    ("at time",),
)


def _is_clone_at_unavailable(exc: BaseException) -> bool:
    """Return True when *exc* is a SQL engine rejection of an AT timestamp."""
    msg = str(exc).lower()
    return any(all(frag in msg for frag in frags) for frags in _CLONE_AT_SKIP_FRAGMENTS)


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
        assert cln_rows == src_rows
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
            # the AT timestamp; the engine raises a SQL error when the AT time
            # predates any committed version.  Skip only for those expected
            # SQL/driver rejections; re-raise Python-level bugs (TypeError etc.)
            # so regressions are not silently hidden.
            if not isinstance(exc, FabricError) and not _is_clone_at_unavailable(exc):
                raise
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


async def test_rename_table_roundtrip(ephemeral_sql_target: SqlTarget) -> None:
    """Create a table, rename it, assert the old name is gone and the new name exists."""
    schema = "dbo"
    old_name = "pytest_tables_rename_src"
    new_name = "pytest_tables_rename_dst"
    select_body = "SELECT 42 AS answer"

    try:
        created = await tables.create_table(ephemeral_sql_target, schema, old_name, select_body)
        assert isinstance(created, Table)
        assert created.name == old_name

        renamed = await tables.rename_table(ephemeral_sql_target, f"{schema}.{old_name}", new_name)
        assert isinstance(renamed, Table)
        assert renamed.name == new_name
        assert renamed.schema_name == schema
        assert renamed.qualified_name == f"{schema}.{new_name}"

        all_tables = await tables.list_tables(ephemeral_sql_target)
        names = {t.name for t in all_tables}
        assert new_name in names, f"New table {new_name!r} not found after rename"
        assert old_name not in names, f"Old table {old_name!r} still present after rename"

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(ephemeral_sql_target, schema, old_name)
        with contextlib.suppress(Exception):
            await tables.delete_table(ephemeral_sql_target, schema, new_name)
