"""Integration tests for services.views — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_views.py

Fixture note: uses ``ephemeral_sql_target`` from conftest.  The target points at
a freshly-created warehouse for each test session; all views created here are
cleaned up inside each test via try/finally.
"""

from __future__ import annotations

import contextlib

import pytest

from fabric_dw.exceptions import NotFoundError
from fabric_dw.models import View
from fabric_dw.services import views
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration


async def test_list_views_returns_list(ephemeral_sql_target: SqlTarget) -> None:
    """list_views on a fresh warehouse must return an empty (or non-empty) list."""
    result = await views.list_views(ephemeral_sql_target)
    assert isinstance(result, list)


async def test_create_view_returns_view_object(ephemeral_sql_target: SqlTarget) -> None:
    """create_view must return a View with the correct schema/name."""
    schema = "dbo"
    view_name = "pytest_views_create"
    select_body = "SELECT 1 AS id, 'hello' AS greeting"

    try:
        created = await views.create_view(ephemeral_sql_target, schema, view_name, select_body)
        assert isinstance(created, View)
        assert created.schema_name == schema
        assert created.name == view_name
        assert created.qualified_name == f"{schema}.{view_name}"
        assert created.definition is not None
        assert "SELECT" in created.definition.upper()
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(ephemeral_sql_target, schema, view_name)


async def test_list_views_includes_created_view(ephemeral_sql_target: SqlTarget) -> None:
    """A newly created view must appear in list_views results."""
    schema = "dbo"
    view_name = "pytest_views_list"
    select_body = "SELECT 42 AS answer"

    try:
        await views.create_view(ephemeral_sql_target, schema, view_name, select_body)

        all_views = await views.list_views(ephemeral_sql_target)
        names = {v.name for v in all_views}
        assert view_name in names

        # Also verify schema filter narrows correctly
        dbo_views = await views.list_views(ephemeral_sql_target, schema=schema)
        dbo_names = {v.name for v in dbo_views}
        assert view_name in dbo_names

        # A schema that doesn't exist should return empty
        other_views = await views.list_views(ephemeral_sql_target, schema="nonexistent_schema_x")
        assert other_views == []
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(ephemeral_sql_target, schema, view_name)


async def test_get_view_returns_definition(ephemeral_sql_target: SqlTarget) -> None:
    """get_view must return the View with its definition populated."""
    schema = "dbo"
    view_name = "pytest_views_get"
    select_body = "SELECT 99 AS magic_number"

    try:
        await views.create_view(ephemeral_sql_target, schema, view_name, select_body)

        fetched = await views.get_view(ephemeral_sql_target, schema, view_name)
        assert isinstance(fetched, View)
        assert fetched.schema_name == schema
        assert fetched.name == view_name
        assert fetched.definition is not None
        assert "magic_number" in fetched.definition.lower()
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(ephemeral_sql_target, schema, view_name)


async def test_get_view_raises_not_found_for_missing_view(
    ephemeral_sql_target: SqlTarget,
) -> None:
    """get_view must raise NotFoundError when the view does not exist."""
    with pytest.raises(NotFoundError):
        await views.get_view(ephemeral_sql_target, "dbo", "pytest_views_does_not_exist")


async def test_read_view_returns_columns_and_rows(ephemeral_sql_target: SqlTarget) -> None:
    """read_view must return (columns, rows) where columns contains expected names."""
    schema = "dbo"
    view_name = "pytest_views_read"
    select_body = "SELECT 7 AS lucky_number, 'world' AS message"

    try:
        await views.create_view(ephemeral_sql_target, schema, view_name, select_body)

        cols, rows = await views.read_view(ephemeral_sql_target, schema, view_name, count=5)
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
            await views.drop_view(ephemeral_sql_target, schema, view_name)


async def test_read_view_raises_not_found_for_missing_view(
    ephemeral_sql_target: SqlTarget,
) -> None:
    """read_view must raise NotFoundError when the view does not exist."""
    with pytest.raises(NotFoundError):
        await views.read_view(ephemeral_sql_target, "dbo", "pytest_views_read_missing")


async def test_update_view_changes_definition(ephemeral_sql_target: SqlTarget) -> None:
    """update_view must redefine the view and return the updated definition."""
    schema = "dbo"
    view_name = "pytest_views_update"
    original_body = "SELECT 1 AS version"
    updated_body = "SELECT 2 AS version, 'updated' AS status"

    try:
        await views.create_view(ephemeral_sql_target, schema, view_name, original_body)

        updated = await views.update_view(ephemeral_sql_target, schema, view_name, updated_body)
        assert isinstance(updated, View)
        assert updated.definition is not None
        assert "status" in updated.definition.lower()

        # Reading should reflect the new SELECT
        cols, rows = await views.read_view(ephemeral_sql_target, schema, view_name, count=1)
        assert "status" in cols
        row_dict = dict(zip(cols, rows[0], strict=True))
        assert row_dict["version"] == 2
    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(ephemeral_sql_target, schema, view_name)


async def test_drop_view_removes_view(ephemeral_sql_target: SqlTarget) -> None:
    """drop_view must remove the view so it no longer appears in list_views."""
    schema = "dbo"
    view_name = "pytest_views_drop"
    select_body = "SELECT 0 AS placeholder"

    await views.create_view(ephemeral_sql_target, schema, view_name, select_body)

    # Confirm it exists before dropping
    before = await views.list_views(ephemeral_sql_target)
    assert any(v.name == view_name for v in before)

    await views.drop_view(ephemeral_sql_target, schema, view_name)

    # Must not appear in listing after drop
    after = await views.list_views(ephemeral_sql_target)
    assert not any(v.name == view_name for v in after)

    # get_view must raise NotFoundError
    with pytest.raises(NotFoundError):
        await views.get_view(ephemeral_sql_target, schema, view_name)


async def test_create_view_full_roundtrip(ephemeral_sql_target: SqlTarget) -> None:
    """End-to-end: create → list → get → read → update → read → drop."""
    schema = "dbo"
    view_name = "pytest_views_roundtrip"
    v1_body = "SELECT 1 AS n"
    v2_body = "SELECT 2 AS n, 'v2' AS label"

    try:
        # --- create ---
        created = await views.create_view(ephemeral_sql_target, schema, view_name, v1_body)
        assert created.name == view_name

        # --- list ---
        all_views = await views.list_views(ephemeral_sql_target)
        assert any(v.name == view_name for v in all_views)

        # --- get ---
        fetched = await views.get_view(ephemeral_sql_target, schema, view_name)
        assert fetched.definition is not None

        # --- read ---
        cols, rows = await views.read_view(ephemeral_sql_target, schema, view_name, count=10)
        assert "n" in cols
        assert rows[0][cols.index("n")] == 1

        # --- update ---
        updated = await views.update_view(ephemeral_sql_target, schema, view_name, v2_body)
        assert updated.definition is not None
        assert "label" in updated.definition.lower()

        # --- read again ---
        cols2, rows2 = await views.read_view(ephemeral_sql_target, schema, view_name, count=10)
        assert "label" in cols2
        assert rows2[0][cols2.index("n")] == 2

    finally:
        with contextlib.suppress(Exception):
            await views.drop_view(ephemeral_sql_target, schema, view_name)
