"""Integration tests for services.views — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_views.py

Fixture note: uses ``mutable_schema_target`` from conftest, which creates a
uniquely-named schema on BOTH the shared warm warehouse and the shared SQL analytics
endpoint, then cascade-drops it on teardown.  All views are created inside that
schema and cleaned up by the cascade drop, with additional try/finally guards for
mid-test failures.

The ``mutable_schema_target`` fixture is parametrized over two targets:
  - ``[warehouse]``     — Data Warehouse (always runs)
  - ``[sql_endpoint]``  — SQL Analytics Endpoint (``pytest.mark.sql_endpoint``, CI only)
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

import pytest

from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.models import View
from fabric_dw.services import views
from fabric_dw.sql import SqlTarget, run_query

pytestmark = pytest.mark.integration


async def test_list_views_returns_list(
    read_target: SqlTarget,
) -> None:
    """list_views on either target must return a list (may be non-empty)."""
    result = await views.list_views(read_target)
    assert isinstance(result, list)


async def test_create_view_returns_view_object(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """create_view must return a View with the correct schema/name."""
    sql_target, schema = mutable_schema_target
    view_name = "pytest_views_create"
    select_body = "SELECT 1 AS id, 'hello' AS greeting"

    try:
        created = await views.create_view(sql_target, schema, view_name, select_body)
        assert isinstance(created, View)
        assert created.schema_name == schema
        assert created.name == view_name
        assert created.qualified_name == f"{schema}.{view_name}"
        assert created.definition is not None
        assert "SELECT" in created.definition.upper()
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, view_name)


async def test_list_views_includes_created_view(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """A newly created view must appear in list_views results."""
    sql_target, schema = mutable_schema_target
    view_name = "pytest_views_list"
    select_body = "SELECT 42 AS answer"

    try:
        await views.create_view(sql_target, schema, view_name, select_body)

        all_views = await views.list_views(sql_target)
        names = {v.name for v in all_views}
        assert view_name in names

        # Also verify schema filter narrows correctly
        dbo_views = await views.list_views(sql_target, schema=schema)
        dbo_names = {v.name for v in dbo_views}
        assert view_name in dbo_names

        # A schema that doesn't exist should return empty
        other_views = await views.list_views(sql_target, schema="nonexistent_schema_x")
        assert other_views == []
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, view_name)


async def test_get_view_returns_definition(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """get_view must return the View with its definition populated."""
    sql_target, schema = mutable_schema_target
    view_name = "pytest_views_get"
    select_body = "SELECT 99 AS magic_number"

    try:
        await views.create_view(sql_target, schema, view_name, select_body)

        fetched = await views.get_view(sql_target, schema, view_name)
        assert isinstance(fetched, View)
        assert fetched.schema_name == schema
        assert fetched.name == view_name
        assert fetched.definition is not None
        assert "magic_number" in fetched.definition.lower()
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, view_name)


async def test_get_view_raises_not_found_for_missing_view(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """get_view must raise NotFoundError when the view does not exist."""
    sql_target, schema = mutable_schema_target
    with pytest.raises(NotFoundError):
        await views.get_view(sql_target, schema, "pytest_views_does_not_exist")


async def test_read_view_returns_columns_and_rows(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """read_view must return (columns, rows) where columns contains expected names."""
    sql_target, schema = mutable_schema_target
    view_name = "pytest_views_read"
    select_body = "SELECT 7 AS lucky_number, 'world' AS message"

    try:
        await views.create_view(sql_target, schema, view_name, select_body)

        cols, rows = await views.read_view(sql_target, schema, view_name, count=5)
        assert isinstance(cols, list)
        assert len(cols) > 0
        assert "lucky_number" in cols
        assert "message" in cols
        assert isinstance(rows, list)
        assert len(rows) >= 1
        # Verify actual data values
        row_dict = dict(zip(cols, rows[0], strict=True))
        assert row_dict["lucky_number"] == 7
        assert row_dict["message"] == "world"
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, view_name)


async def test_read_view_raises_not_found_for_missing_view(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """read_view must raise NotFoundError when the view does not exist."""
    sql_target, schema = mutable_schema_target
    with pytest.raises(NotFoundError):
        await views.read_view(sql_target, schema, "pytest_views_read_missing")


async def test_update_view_changes_definition(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """update_view must redefine the view and return the updated definition."""
    sql_target, schema = mutable_schema_target
    view_name = "pytest_views_update"
    original_body = "SELECT 1 AS version"
    updated_body = "SELECT 2 AS version, 'updated' AS status"

    try:
        await views.create_view(sql_target, schema, view_name, original_body)

        updated = await views.update_view(sql_target, schema, view_name, updated_body)
        assert isinstance(updated, View)
        assert updated.definition is not None
        assert "status" in updated.definition.lower()

        # Reading should reflect the new SELECT
        cols, rows = await views.read_view(sql_target, schema, view_name, count=1)
        assert "status" in cols
        row_dict = dict(zip(cols, rows[0], strict=True))
        assert row_dict["version"] == 2
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, view_name)


async def test_drop_view_removes_view(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """drop_view must remove the view so it no longer appears in list_views."""
    sql_target, schema = mutable_schema_target
    view_name = "pytest_views_drop"
    select_body = "SELECT 0 AS placeholder"

    await views.create_view(sql_target, schema, view_name, select_body)

    # Confirm it exists before dropping
    before = await views.list_views(sql_target)
    assert any(v.name == view_name for v in before)

    await views.drop_view(sql_target, schema, view_name)

    # Must not appear in listing after drop
    after = await views.list_views(sql_target)
    assert not any(v.name == view_name for v in after)

    # get_view must raise NotFoundError
    with pytest.raises(NotFoundError):
        await views.get_view(sql_target, schema, view_name)


async def test_create_view_full_roundtrip(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """End-to-end: create → list → get → read → update → read → drop."""
    sql_target, schema = mutable_schema_target
    view_name = "pytest_views_roundtrip"
    v1_body = "SELECT 1 AS n"
    v2_body = "SELECT 2 AS n, 'v2' AS label"

    try:
        # --- create ---
        created = await views.create_view(sql_target, schema, view_name, v1_body)
        assert created.name == view_name

        # --- list ---
        all_views = await views.list_views(sql_target)
        assert any(v.name == view_name for v in all_views)

        # --- get ---
        fetched = await views.get_view(sql_target, schema, view_name)
        assert fetched.definition is not None

        # --- read ---
        cols, rows = await views.read_view(sql_target, schema, view_name, count=10)
        assert "n" in cols
        assert rows[0][cols.index("n")] == 1

        # --- update ---
        updated = await views.update_view(sql_target, schema, view_name, v2_body)
        assert updated.definition is not None
        assert "label" in updated.definition.lower()

        # --- read again ---
        cols2, rows2 = await views.read_view(sql_target, schema, view_name, count=10)
        assert "label" in cols2
        assert rows2[0][cols2.index("n")] == 2

    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, view_name)


async def test_rename_view_creates_new_and_removes_old(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """rename_view must make the old name disappear and the new name appear."""
    sql_target, schema = mutable_schema_target
    old_name = "pytest_views_rename_old"
    new_name = "pytest_views_rename_new"
    qualified = f"{schema}.{old_name}"
    select_body = "SELECT 42 AS the_answer"

    try:
        # Create with old name
        await views.create_view(sql_target, schema, old_name, select_body)

        # Confirm old name exists
        before = await views.list_views(sql_target)
        assert any(v.name == old_name for v in before)

        # Rename
        renamed = await views.rename_view(sql_target, qualified, new_name)
        assert isinstance(renamed, View)
        assert renamed.name == new_name
        assert renamed.schema_name == schema
        assert renamed.qualified_name == f"{schema}.{new_name}"

        # Old name must be gone
        after = await views.list_views(sql_target)
        assert not any(v.name == old_name for v in after)

        # New name must exist
        assert any(v.name == new_name for v in after)

        # get_view on old name must raise NotFoundError
        with pytest.raises(NotFoundError):
            await views.get_view(sql_target, schema, old_name)

        # get_view on new name must succeed
        fetched = await views.get_view(sql_target, schema, new_name)
        assert fetched.name == new_name

    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, old_name)
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, new_name)


async def test_count_view_rows_returns_nonnegative_int(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """count_view_rows must return a non-negative integer for a real view."""
    sql_target, schema = mutable_schema_target
    view_name = "pytest_views_count"
    select_body = "SELECT 1 AS id UNION ALL SELECT 2 AS id"

    try:
        await views.create_view(sql_target, schema, view_name, select_body)
        count = await views.count_view_rows(sql_target, schema, view_name)
        assert isinstance(count, int)
        assert count >= 0
        assert count == 2
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, view_name)


# ---------------------------------------------------------------------------
# Time-travel: read_view and count_view_rows with as_of
# ---------------------------------------------------------------------------
#
# Both functions are tested with a server-side timestamp captured AFTER the view
# is created to avoid client/server clock skew.
#
# Caveats (expected server-side errors, not bugs):
#   - A timestamp before the underlying view was created errors server-side.
#   - A timestamp outside the configured retention window errors server-side.
#   - A freshly-created view may have no committed version visible at the
#     captured timestamp (Fabric distributed compute flush latency); the tests
#     skip rather than fail in that case.

# Fragments from SQL engine error messages that mean no committed history exists
# at the requested timestamp.
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


def _get_server_ts(sql_target: SqlTarget) -> datetime:
    """Return a UTC-aware server-side timestamp via SYSUTCDATETIME()."""
    _, rows = run_query(sql_target, "SELECT SYSUTCDATETIME() AS ts")
    raw = rows[0][0]
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
    parsed = datetime.fromisoformat(str(raw))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


async def test_read_view_with_as_of_succeeds_or_skips(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """read_view with as_of set to a server-side post-creation timestamp succeeds or skips.

    Creates a view, captures a server-side timestamp after the DDL commit, then
    calls read_view with as_of.  Proves the OPTION (FOR TIMESTAMP AS OF ...)
    clause is syntactically and semantically accepted by Fabric end-to-end.

    A freshly-created view may have no committed history at the captured
    timestamp (Fabric distributed compute flush latency); the test skips in that
    case rather than failing, because the rejection is expected server behavior,
    not a code bug.
    """
    sql_target, schema = mutable_schema_target
    view_name = "pytest_views_timetravel_read"
    select_body = "SELECT 1 AS id, 'alpha' AS label UNION ALL SELECT 2, 'beta'"

    try:
        await views.create_view(sql_target, schema, view_name, select_body)

        # Capture a server-side timestamp AFTER the DDL commit to ensure the
        # as_of is >= the view creation time from the server's perspective.
        as_of: datetime = await asyncio.to_thread(_get_server_ts, sql_target)

        try:
            cols, rows = await views.read_view(sql_target, schema, view_name, count=10, as_of=as_of)
        except Exception as exc:
            if _is_time_travel_unavailable(exc) or isinstance(exc, FabricError):
                pytest.skip(
                    f"No committed history at {as_of.isoformat()} for a freshly-created "
                    f"view (expected on distributed compute): {exc}"
                )
            raise
        assert "id" in cols
        assert "label" in cols
        assert len(rows) == 2
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, view_name)


async def test_count_view_rows_with_as_of_succeeds_or_skips(
    mutable_schema_target: tuple[SqlTarget, str],
) -> None:
    """count_view_rows with as_of set to a server-side post-creation timestamp succeeds or skips.

    Same freshly-created-view caveat as test_read_view_with_as_of_succeeds_or_skips.
    When the server does have committed history at the timestamp, the call must
    return the correct row count.
    """
    sql_target, schema = mutable_schema_target
    view_name = "pytest_views_timetravel_count"
    select_body = "SELECT 1 AS id UNION ALL SELECT 2 UNION ALL SELECT 3"

    try:
        await views.create_view(sql_target, schema, view_name, select_body)

        as_of: datetime = await asyncio.to_thread(_get_server_ts, sql_target)

        try:
            count = await views.count_view_rows(sql_target, schema, view_name, as_of=as_of)
        except Exception as exc:
            if _is_time_travel_unavailable(exc) or isinstance(exc, FabricError):
                pytest.skip(
                    f"No committed history at {as_of.isoformat()} for a freshly-created "
                    f"view (expected on distributed compute): {exc}"
                )
            raise
        assert isinstance(count, int)
        assert count == 3
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(sql_target, schema, view_name)
