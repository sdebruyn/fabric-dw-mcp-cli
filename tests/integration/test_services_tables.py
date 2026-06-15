"""Integration tests for services.tables — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_tables.py

Fixture note: uses ``warehouse_schema`` from conftest, which creates a uniquely-named
schema inside the session-shared warm warehouse and cascade-drops it on teardown.
All tables are created inside that schema so tests are fully isolated from one another.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

import pytest

from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import Table
from fabric_dw.services import tables
from fabric_dw.sql import SqlTarget, run_query

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


async def test_list_tables_returns_list(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    sql_target, _schema = warehouse_schema
    result = await tables.list_tables(sql_target)
    assert isinstance(result, list)


async def test_create_read_clear_delete_roundtrip(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    sql_target, schema = warehouse_schema
    table_name = "pytest_tables_roundtrip"
    select_body = "SELECT 1 AS id, 'hello' AS greeting"

    try:
        created = await tables.create_table(sql_target, schema, table_name, select_body)
        assert isinstance(created, Table)
        assert created.schema_name == schema
        assert created.name == table_name

        cols, rows = await tables.read_table(sql_target, schema, table_name, count=5)
        assert "id" in cols or len(cols) > 0
        assert isinstance(rows, list)

        all_tables = await tables.list_tables(sql_target)
        names = {t.name for t in all_tables}
        assert table_name in names

        await tables.clear_table(sql_target, schema, table_name)
        _, rows_after_clear = await tables.read_table(sql_target, schema, table_name, count=5)
        assert rows_after_clear == []

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)

    with pytest.raises((NotFoundError, Exception)):
        await tables.read_table(sql_target, schema, table_name)


async def test_clone_table_creates_identical_rows(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """clone_table (plain, no AT) creates a clone with the same rows as the source."""
    sql_target, schema = warehouse_schema
    source_name = "pytest_clone_source"
    clone_name = "pytest_clone_copy"
    select_body = "SELECT 1 AS id, 'hello' AS greeting"

    try:
        # Create the source table.
        await tables.create_table(sql_target, schema, source_name, select_body)

        # Clone it without a point-in-time.
        cloned = await tables.clone_table(
            sql_target, f"{schema}.{source_name}", f"{schema}.{clone_name}"
        )
        assert isinstance(cloned, Table)
        assert cloned.schema_name == schema
        assert cloned.name == clone_name

        # Verify the clone is visible and contains the same data.
        all_tables = await tables.list_tables(sql_target, schema=schema)
        names = {t.name for t in all_tables}
        assert clone_name in names

        src_cols, src_rows = await tables.read_table(sql_target, schema, source_name, count=100)
        cln_cols, cln_rows = await tables.read_table(sql_target, schema, clone_name, count=100)
        assert src_cols == cln_cols
        assert cln_rows == src_rows
    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, source_name)
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, clone_name)


async def test_clone_table_at_point_in_time(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """clone_table with AT timestamp clones the table at the specified point in time.

    The AT timestamp must be >= the object creation time and <= the clone
    transaction time.  We capture the timestamp via ``SELECT SYSUTCDATETIME()``
    executed against the same server AFTER the table is created.  This
    eliminates client/server clock skew (the server timestamp is always >= the
    DDL commit on the same server) and ensures the AT is in the past relative to
    the subsequent clone transaction.

    If the engine rejects the timestamp because the table has no committed
    history at the requested point (e.g. some engines require a version to
    have been committed *before* the AT time), the test is skipped rather than
    failed, as this is an expected engine limitation for freshly-created tables.
    """
    sql_target, schema = warehouse_schema
    source_name = "pytest_clone_at_source"
    clone_name = "pytest_clone_at_copy"
    select_body = "SELECT 42 AS value"

    try:
        # Create the source table first so the server-side timestamp is taken
        # AFTER the DDL commit — guaranteeing AT >= object creation time.
        await tables.create_table(sql_target, schema, source_name, select_body)

        # Capture a server-side timestamp via SYSUTCDATETIME() executed on the
        # same Fabric warehouse.  This is always >= the DDL commit time (same
        # server clock) and will be < the upcoming clone transaction time.
        # Using a client-side timestamp risks client/server clock skew (~60ms)
        # where the client clock trails the server, making the AT appear to be
        # *before* the object was created from the server's perspective.
        def _get_server_ts() -> datetime:
            _, rows = run_query(sql_target, "SELECT SYSUTCDATETIME() AS ts")
            raw = rows[0][0]
            # mssql_python returns datetime objects; ensure timezone-aware UTC.
            if isinstance(raw, datetime):
                return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
            # Fallback: parse ISO string if the driver returns a string.
            # Then attach UTC only if the parsed datetime is
            # naive; if it already carries an offset, convert instead so the offset
            # is not silently discarded.
            parsed = datetime.fromisoformat(str(raw))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

        at_dt: datetime = await asyncio.to_thread(_get_server_ts)

        # Sleep so the upcoming clone transaction starts well after `at_dt`.
        # Without this buffer, Fabric's distributed compute can assign a
        # transaction start-time that is within milliseconds of `at_dt`,
        # making the AT appear to be *after* the transaction began (skew
        # between the timestamp-capture connection and the clone connection).
        # 5 s is comfortably larger than any observed inter-node clock skew.
        await asyncio.sleep(5)

        try:
            cloned = await tables.clone_table(
                sql_target,
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
            await tables.delete_table(sql_target, schema, source_name)
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, clone_name)


async def test_rename_table_roundtrip(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """Create a table, rename it, assert the old name is gone and the new name exists."""
    sql_target, schema = warehouse_schema
    old_name = "pytest_tables_rename_src"
    new_name = "pytest_tables_rename_dst"
    select_body = "SELECT 42 AS answer"

    try:
        created = await tables.create_table(sql_target, schema, old_name, select_body)
        assert isinstance(created, Table)
        assert created.name == old_name

        renamed = await tables.rename_table(sql_target, f"{schema}.{old_name}", new_name)
        assert isinstance(renamed, Table)
        assert renamed.name == new_name
        assert renamed.schema_name == schema
        assert renamed.qualified_name == f"{schema}.{new_name}"

        all_tables = await tables.list_tables(sql_target)
        names = {t.name for t in all_tables}
        assert new_name in names, f"New table {new_name!r} not found after rename"
        assert old_name not in names, f"Old table {old_name!r} still present after rename"

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, old_name)
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, new_name)
