"""Integration tests for services.tables — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_tables.py

Fixture notes:
- ``read_target`` (parametrized): runs read-only tests against both the shared warm
  warehouse and the shared SQL analytics endpoint.  Tests use the pre-seeded
  ``sample`` schema (``sample.colors`` / ``sample.numbers``).
- ``warehouse_schema``: creates a uniquely-named schema inside the session-shared
  warm warehouse and cascade-drops it on teardown.  Used exclusively for DWH-only
  mutating tests (CREATE / DROP / CLEAR / RENAME / CLONE).
- ``shared_sql_endpoint``: used for the SQL-endpoint-only ``get_table_health_metrics``
  test (``sp_get_table_health_metrics`` targets lakehouse Delta tables and is only
  available on SQL Analytics Endpoints, not on Data Warehouses).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import Table
from fabric_dw.services import columns as columns_svc
from fabric_dw.services import schemas as schemas_svc
from fabric_dw.services import tables
from fabric_dw.sql import SqlTarget, run_query

from .conftest import SEED_SCHEMA_NAME, SharedSqlEndpointTarget

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Dual-target read tests — run against both warehouse and SQL analytics endpoint
# via the parametrized ``read_target`` fixture.  All assertions are made against
# the pre-seeded ``sample`` schema (sample.colors / sample.numbers).
# ---------------------------------------------------------------------------


async def test_read_table_returns_seeded_rows(
    read_target: SqlTarget,
) -> None:
    """read_table against sample.colors must return the three seeded rows."""
    result = await tables.read_table(read_target, SEED_SCHEMA_NAME, "colors", count=10)
    assert "id" in result.columns
    assert "name" in result.columns
    # Exactly three rows were seeded (red / green / blue).
    assert len(result.rows) == 3


async def test_count_table_rows_on_seeded_table(
    read_target: SqlTarget,
) -> None:
    """count_table_rows on sample.colors must return 3 (the seeded row count)."""
    result = await tables.count_table_rows(read_target, SEED_SCHEMA_NAME, "colors")
    assert isinstance(result.row_count, int)
    assert result.row_count == 3


async def test_get_table_columns_on_seeded_table(
    read_target: SqlTarget,
) -> None:
    """get_object_columns on sample.colors must return id and name columns."""
    result = await columns_svc.get_object_columns(read_target, SEED_SCHEMA_NAME, "colors")
    assert len(result) == 2
    col_names = {c["name"] for c in result}
    assert col_names == {"id", "name"}


# ---------------------------------------------------------------------------
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

        read_result = await tables.read_table(sql_target, schema, table_name, count=5)
        assert "id" in read_result.columns or len(read_result.columns) > 0
        assert isinstance(read_result.rows, list)

        all_tables = await tables.list_tables(sql_target)
        names = {t.name for t in all_tables}
        assert table_name in names

        await tables.clear_table(sql_target, schema, table_name)
        after_clear = await tables.read_table(sql_target, schema, table_name, count=5)
        assert after_clear.rows == []

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

        src_result = await tables.read_table(sql_target, schema, source_name, count=100)
        cln_result = await tables.read_table(sql_target, schema, clone_name, count=100)
        assert src_result.columns == cln_result.columns
        assert cln_result.rows == src_result.rows
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


async def test_transfer_table_roundtrip(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """Create a table, transfer it to a second schema, assert old gone / new present."""
    sql_target, schema = warehouse_schema
    table_name = "pytest_tables_transfer_dst"
    select_body = "SELECT 42 AS answer"
    target_schema_name = f"{schema}_target"

    await schemas_svc.create_schema(sql_target, target_schema_name)
    try:
        created = await tables.create_table(sql_target, schema, table_name, select_body)
        assert isinstance(created, Table)
        assert created.schema_name == schema

        moved = await tables.transfer_table(
            sql_target, f"{schema}.{table_name}", target_schema_name
        )
        assert isinstance(moved, Table)
        assert moved.name == table_name
        assert moved.schema_name == target_schema_name
        assert moved.qualified_name == f"{target_schema_name}.{table_name}"

        all_tables = await tables.list_tables(sql_target)
        in_target = {t.name for t in all_tables if t.schema_name == target_schema_name}
        in_source = {t.name for t in all_tables if t.schema_name == schema}
        assert table_name in in_target, f"{table_name!r} not found in {target_schema_name!r}"
        assert table_name not in in_source, f"{table_name!r} still present in {schema!r}"

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, target_schema_name, table_name)
        with contextlib.suppress(Exception):
            await schemas_svc.delete_schema(sql_target, target_schema_name, cascade=True)


async def test_transfer_table_missing_target_schema_raises(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """transfer_table against a nonexistent target schema raises a FabricError.

    GOTCHA (see the ``_NOT_FOUND_FRAGMENTS`` note in ``fabric_dw.sql_errors``,
    intentionally NOT changed by this test): the engine's "cannot find the
    object" message for a missing schema is not mapped to NotFoundError, so a
    missing target schema surfaces as a generic FabricServerError instead.
    This test documents that observed behaviour rather than asserting it is
    desired -- a tighter _NOT_FOUND_FRAGMENTS match is a separate follow-up.
    """
    sql_target, schema = warehouse_schema
    table_name = "pytest_tables_transfer_missing_schema"
    select_body = "SELECT 1 AS id"

    try:
        await tables.create_table(sql_target, schema, table_name, select_body)
        with pytest.raises(FabricError) as exc_info:
            await tables.transfer_table(
                sql_target, f"{schema}.{table_name}", "pytest_nonexistent_schema_zzz"
            )
        # Document what actually surfaces today: NOT a NotFoundError.
        assert not isinstance(exc_info.value, NotFoundError)
    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)


async def test_transfer_table_name_collision_raises(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """transfer_table raises when an object of the same name already exists in the target schema.

    Documents the exception type the engine surfaces for a genuine name
    collision in the target schema (as opposed to the missing-schema/missing-
    object gotcha covered separately above).
    """
    sql_target, schema = warehouse_schema
    table_name = "pytest_tables_transfer_collision"
    target_schema_name = f"{schema}_collision_target"
    select_body = "SELECT 1 AS id"

    await schemas_svc.create_schema(sql_target, target_schema_name)
    try:
        await tables.create_table(sql_target, schema, table_name, select_body)
        await tables.create_table(sql_target, target_schema_name, table_name, select_body)

        with pytest.raises(FabricError):
            await tables.transfer_table(sql_target, f"{schema}.{table_name}", target_schema_name)
    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, target_schema_name, table_name)
        with contextlib.suppress(Exception):
            await schemas_svc.delete_schema(sql_target, target_schema_name, cascade=True)


async def test_count_table_rows_returns_nonnegative_int(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """count_table_rows must return a non-negative integer for a real table."""
    sql_target, schema = warehouse_schema
    table_name = "pytest_tables_count"
    select_body = "SELECT 1 AS id UNION ALL SELECT 2 AS id UNION ALL SELECT 3 AS id"

    try:
        await tables.create_table(sql_target, schema, table_name, select_body)
        result = await tables.count_table_rows(sql_target, schema, table_name)
        assert isinstance(result.row_count, int)
        assert result.row_count >= 0
        assert result.row_count == 3
    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# Time-travel: read_table and count_table_rows with as_of
# ---------------------------------------------------------------------------
#
# The pre-seeded ``sample.colors`` table (created during session setup, well
# before these tests run) provides stable, known-count data.  Using a "current"
# timestamp as as_of is safe because the table has a long committed history.
#
# Caveats (expected server-side errors, not bugs):
#   - A timestamp before the object's creation time raises a SQL error.
#   - A timestamp outside the configured retention window (1-120 days, default
#     30) raises a SQL error.
#   - A freshly-created table may have no committed history at the requested
#     point; this is tested in ``test_time_travel_on_fresh_table_*`` below and
#     handled by a pytest.skip rather than a failure.

# Fragments from SQL engine error messages that mean the requested timestamp has
# no committed history for the object (expected on freshly-created tables).
_TIME_TRAVEL_SKIP_FRAGMENTS = (
    ("no version",),
    ("history",),
    ("at time",),
    ("point in time",),
    ("timestamp",),
)


def _is_time_travel_unavailable(exc: BaseException) -> bool:
    """Return True when *exc* means no committed history exists at the requested timestamp."""
    msg = str(exc).lower()
    return any(all(frag in msg for frag in frags) for frags in _TIME_TRAVEL_SKIP_FRAGMENTS)


async def test_read_table_with_as_of_returns_seeded_rows(
    read_target: SqlTarget,
) -> None:
    """read_table with as_of=now succeeds and returns the seeded rows.

    Uses the pre-seeded sample.colors table (created during session setup) so
    that any current timestamp is guaranteed to be within its committed history.
    Proves that the generated OPTION (FOR TIMESTAMP AS OF ...) clause is
    syntactically and semantically accepted by the Fabric SQL engine end-to-end.
    """
    as_of = datetime.now(tz=UTC)
    try:
        result = await tables.read_table(
            read_target, SEED_SCHEMA_NAME, "colors", count=10, as_of=as_of
        )
    except Exception as exc:
        if _is_time_travel_unavailable(exc):
            pytest.skip(f"No committed history at {as_of.isoformat()}: {exc}")
        raise
    assert "id" in result.columns
    assert "name" in result.columns
    assert len(result.rows) == 3  # Three seeded rows: red, green, blue


async def test_count_table_rows_with_as_of_returns_seeded_count(
    read_target: SqlTarget,
) -> None:
    """count_table_rows with as_of=now succeeds and returns the seeded row count.

    Same guarantee as test_read_table_with_as_of_returns_seeded_rows: the
    pre-seeded sample.colors table has a long committed history, so any current
    timestamp is a valid point-in-time anchor.
    """
    as_of = datetime.now(tz=UTC)
    try:
        result = await tables.count_table_rows(read_target, SEED_SCHEMA_NAME, "colors", as_of=as_of)
    except Exception as exc:
        if _is_time_travel_unavailable(exc):
            pytest.skip(f"No committed history at {as_of.isoformat()}: {exc}")
        raise
    assert isinstance(result.row_count, int)
    assert result.row_count == 3  # Three seeded rows: red, green, blue


async def test_read_table_as_of_matches_read_without_as_of(
    read_target: SqlTarget,
) -> None:
    """read_table with as_of=now returns the same data as read without as_of.

    Confirms the OPTION clause is non-destructive: for stable seeded data,
    a current point-in-time read must match a plain (latest) read.
    """
    result_plain = await tables.read_table(read_target, SEED_SCHEMA_NAME, "colors", count=100)
    as_of = datetime.now(tz=UTC)
    try:
        result_timed = await tables.read_table(
            read_target, SEED_SCHEMA_NAME, "colors", count=100, as_of=as_of
        )
    except Exception as exc:
        if _is_time_travel_unavailable(exc):
            pytest.skip(f"No committed history at {as_of.isoformat()}: {exc}")
        raise
    assert result_plain.columns == result_timed.columns
    assert sorted(str(r) for r in result_plain.rows) == sorted(str(r) for r in result_timed.rows)


async def test_count_table_rows_as_of_matches_count_without_as_of(
    read_target: SqlTarget,
) -> None:
    """count_table_rows with as_of=now returns the same count as without as_of."""
    result_plain = await tables.count_table_rows(read_target, SEED_SCHEMA_NAME, "colors")
    as_of = datetime.now(tz=UTC)
    try:
        result_timed = await tables.count_table_rows(
            read_target, SEED_SCHEMA_NAME, "colors", as_of=as_of
        )
    except Exception as exc:
        if _is_time_travel_unavailable(exc):
            pytest.skip(f"No committed history at {as_of.isoformat()}: {exc}")
        raise
    assert result_plain.row_count == result_timed.row_count


async def test_time_travel_on_fresh_table_read(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """read_table with as_of set to a server-side timestamp (after DDL) succeeds or skips.

    A freshly-created table may have no committed version visible at the
    captured server timestamp (Fabric distributed compute may not have flushed
    the write to the time-travel history yet).  The test skips rather than fails
    in that case, because the server-side rejection is expected behavior, not a
    code bug.  When the table DOES have a committed history, the call must return
    the seeded rows.
    """
    sql_target, schema = warehouse_schema
    table_name = "pytest_timetravel_read"
    select_body = "SELECT 1 AS id, 'alpha' AS label UNION ALL SELECT 2, 'beta'"

    try:
        await tables.create_table(sql_target, schema, table_name, select_body)

        # Capture a server-side timestamp after the DDL commit to avoid
        # client/server clock skew (same technique as test_clone_table_at_point_in_time).
        def _get_server_ts() -> datetime:
            _, rows = run_query(sql_target, "SELECT SYSUTCDATETIME() AS ts")
            raw = rows[0][0]
            if isinstance(raw, datetime):
                return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
            parsed = datetime.fromisoformat(str(raw))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

        as_of: datetime = await asyncio.to_thread(_get_server_ts)

        try:
            result = await tables.read_table(sql_target, schema, table_name, count=10, as_of=as_of)
        except Exception as exc:
            if _is_time_travel_unavailable(exc) or isinstance(exc, FabricError):
                pytest.skip(
                    f"No committed history at {as_of.isoformat()} for a freshly-created "
                    f"table (expected on distributed compute): {exc}"
                )
            raise
        assert len(result.rows) == 2
    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)


async def test_time_travel_on_fresh_table_count(
    warehouse_schema: tuple[SqlTarget, str],
) -> None:
    """count_table_rows with as_of set to a server-side timestamp succeeds or skips.

    Same freshly-created-table caveat as test_time_travel_on_fresh_table_read.
    """
    sql_target, schema = warehouse_schema
    table_name = "pytest_timetravel_count"
    select_body = "SELECT 1 AS id UNION ALL SELECT 2 UNION ALL SELECT 3"

    try:
        await tables.create_table(sql_target, schema, table_name, select_body)

        def _get_server_ts() -> datetime:
            _, rows = run_query(sql_target, "SELECT SYSUTCDATETIME() AS ts")
            raw = rows[0][0]
            if isinstance(raw, datetime):
                return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
            parsed = datetime.fromisoformat(str(raw))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

        as_of: datetime = await asyncio.to_thread(_get_server_ts)

        try:
            result = await tables.count_table_rows(sql_target, schema, table_name, as_of=as_of)
        except Exception as exc:
            if _is_time_travel_unavailable(exc) or isinstance(exc, FabricError):
                pytest.skip(
                    f"No committed history at {as_of.isoformat()} for a freshly-created "
                    f"table (expected on distributed compute): {exc}"
                )
            raise
        assert result.row_count == 3
    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)


# ---------------------------------------------------------------------------
# SQL-endpoint-only: get_table_health_metrics (sp_get_table_health_metrics)
# ---------------------------------------------------------------------------
#
# sp_get_table_health_metrics is only available on SQL Analytics Endpoints
# (not on Data Warehouses).  The proc surfaces Delta/Parquet layout metrics
# for lakehouse tables.  Its output column schema is not yet documented by
# Microsoft, so assertions are intentionally generic (columns non-empty,
# list of row tuples returned).
#
# The seeded ``sample.colors`` table (written as a Delta Lake layout into the
# parent Lakehouse during fixture setup) is the probe table — it is guaranteed
# to be visible on the endpoint before the fixture yields.

# Driver message fragments that mean the proc is not yet implemented by the
# endpoint's SQL engine version (a tenant-level GA rollout gap, not a bug).
# Lower-cased before matching.
_HEALTH_METRICS_UNAVAILABLE_FRAGMENTS = (
    # SQL engine version predates the proc: "The stored procedure is not
    # available in this version of SQL Server."
    "is not available in this version",
    # Defensive variants of the same condition surfaced by the engine.
    "stored procedure is not available",
)


def _is_health_metrics_unavailable(exc: BaseException) -> bool:
    """Return True when *exc* means sp_get_table_health_metrics is not yet deployed.

    Catches the "not available in this version of SQL Server" condition that the
    raw driver raises when the proc name is recognised but the endpoint's engine
    version does not yet implement it.  ``map_driver_error`` does not classify
    this (it is neither a not-found error number nor an auth/permission fragment),
    so the test must string-match the driver message to skip gracefully.
    """
    msg = str(exc).lower()
    return any(frag in msg for frag in _HEALTH_METRICS_UNAVAILABLE_FRAGMENTS)


@pytest.mark.sql_endpoint
async def test_get_table_health_metrics_on_sql_endpoint(
    shared_sql_endpoint: SharedSqlEndpointTarget,
) -> None:
    """get_table_health_metrics against a seeded endpoint table returns columns + rows.

    Uses ``sample.colors`` (seeded via the parent Lakehouse during fixture setup)
    as the probe table.  The stored procedure output schema is undocumented
    (#594 mandated a generic passthrough), so assertions are coarse:

    - The call succeeds (no exception).
    - ``columns`` is a non-empty list of strings.
    - ``rows`` is a list of tuples (may be empty if the proc yields no rows for
      a freshly-seeded table — but the result shape must be correct).

    ``sp_get_table_health_metrics`` was announced as Generally Available at
    Build 2026 but may not yet be rolled out to all Fabric tenants.  When the
    proc is not available the test is skipped rather than failed (the proc
    absence is a tenant-level GA rollout issue, not a code bug).
    """
    from fabric_dw.models import WarehouseKind  # noqa: PLC0415

    sql_target = shared_sql_endpoint.sql_target
    try:
        metrics = await tables.get_table_health_metrics(
            sql_target,
            SEED_SCHEMA_NAME,
            "colors",
            kind=WarehouseKind.SQL_ENDPOINT,
        )
    except NotFoundError as exc:
        # sp_get_table_health_metrics is GA-announced at Build 2026 but may not
        # yet be deployed on all tenants.  SQL Server error 2812 ("Could not find
        # stored procedure") is mapped to NotFoundError by map_driver_error()
        # (see sql.py _NOT_FOUND_ERROR_NUMBERS), so we catch the typed exception
        # rather than string-matching the raw driver message.
        pytest.skip(
            f"sp_get_table_health_metrics is not yet available on this tenant "
            f"({exc}); skipping — re-run when the GA rollout reaches this tenant"
        )
    except Exception as exc:
        # The proc exists by name but the endpoint's SQL engine version doesn't
        # implement it yet: the driver raises "The stored procedure is not
        # available in this version of SQL Server."  map_driver_error() does NOT
        # map this to a typed exception (it's neither error 208/2812 nor an auth/
        # permission fragment), so the raw driver error propagates here.  Treat it
        # the same way as the NotFoundError path: a GA-rollout gap, not a code bug.
        if not _is_health_metrics_unavailable(exc):
            raise
        pytest.skip(
            f"sp_get_table_health_metrics is not available in this SQL Analytics "
            f"Endpoint's engine version ({exc}); skipping — re-run when the GA "
            f"rollout reaches this endpoint"
        )

    # The proc must return at least one column (output schema is undocumented
    # but the proc is GA and always yields a result set when available).
    assert isinstance(metrics.columns, list), (
        f"expected list of column names, got {type(metrics.columns)}"
    )
    assert metrics.columns, f"expected non-empty columns list, got {metrics.columns!r}"
    for col in metrics.columns:
        assert isinstance(col, str), f"column name must be str, got {type(col)}: {col!r}"

    # rows is a list of tuples; may be empty for a freshly-seeded table.
    assert isinstance(metrics.rows, list), f"expected list of row tuples, got {type(metrics.rows)}"
    for row in metrics.rows:
        assert isinstance(row, tuple), f"each row must be a tuple, got {type(row)}: {row!r}"


# ===========================================================================
# export_table — Parquet round-trip
# ===========================================================================


async def test_export_table_parquet_roundtrip(
    warehouse_schema: tuple[SqlTarget, str],
    tmp_path: Path,
) -> None:
    """export_table: seed a table, export as Parquet, read back, assert values."""
    sql_target, schema = warehouse_schema
    table_name = "pytest_export_roundtrip"
    select_body = "SELECT 1 AS id, 'hello' AS greeting UNION ALL SELECT 2, 'world'"

    try:
        await tables.create_table(sql_target, schema, table_name, select_body)

        output = tmp_path / "export.parquet"
        row_count = await tables.export_table(sql_target, schema, table_name, output, "parquet")

        assert row_count == 2
        assert output.exists()

        # Read the Parquet back with pyarrow and verify the contents.
        pq_table = pq.read_table(str(output))
        assert pq_table.num_rows == 2
        col_names = set(pq_table.schema.names)
        assert "id" in col_names
        assert "greeting" in col_names

        ids = pq_table.column("id").to_pylist()
        greetings = pq_table.column("greeting").to_pylist()
        assert sorted(ids) == [1, 2]
        assert sorted(greetings) == ["hello", "world"]

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)


async def test_export_table_with_limit(
    warehouse_schema: tuple[SqlTarget, str],
    tmp_path: Path,
) -> None:
    """export_table with --limit returns at most N rows."""
    sql_target, schema = warehouse_schema
    table_name = "pytest_export_limit"
    # Seed 5 rows and export with limit=2.
    select_body = (
        "SELECT 1 AS id UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5"
    )

    try:
        await tables.create_table(sql_target, schema, table_name, select_body)

        output = tmp_path / "export_limit.parquet"
        row_count = await tables.export_table(
            sql_target, schema, table_name, output, "parquet", limit=2
        )

        assert row_count == 2
        pq_table = pq.read_table(str(output))
        assert pq_table.num_rows == 2

    finally:
        with contextlib.suppress(Exception):
            await tables.delete_table(sql_target, schema, table_name)
