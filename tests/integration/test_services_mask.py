"""Integration tests for dynamic data masking service functions.

Run with: pytest -m integration tests/integration/test_services_mask.py

Covers the set -> list (assert masked) -> drop -> list (assert unmasked) round-trip
for dynamic data masking.

The test creates a minimal table, applies masks to its columns, asserts the catalog
reports them correctly, then drops the masks.  Teardown drops the table.

Data Warehouse and SQL Analytics Endpoint: ALTER TABLE ... ALTER COLUMN ADD/DROP MASKED
requires ALTER ANY MASK and ALTER on the target table, which the test service principal
has via its workspace role.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from fabric_dw.services import mask as mask_svc
from fabric_dw.sql import SqlTarget, run_query

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_table_name() -> str:
    """Return a short, collision-resistant table name safe for Fabric DDL."""
    return f"pytest_mask_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mask_table(
    warehouse_schema: tuple[SqlTarget, str],
) -> AsyncIterator[tuple[SqlTarget, str, str]]:
    """Create a minimal table for mask tests and drop it in teardown.

    Yields ``(sql_target, schema_name, table_name)``.

    The table has two columns: ``Email`` (varchar) and ``Phone`` (varchar).
    Teardown suppresses exceptions so a missing table (e.g. from a failed
    CREATE) does not mask the original test error.
    """
    sql_target, schema_name = warehouse_schema
    table_name = _unique_table_name()

    await asyncio.to_thread(
        run_query,
        sql_target,
        (
            f"CREATE TABLE [{schema_name}].[{table_name}] ("
            "    Email VARCHAR(256) NULL,"
            "    Phone VARCHAR(20) NULL,"
            "    Salary INT NULL"
            ");"
        ),
        autocommit=True,
        fetch="none",
    )
    try:
        yield sql_target, schema_name, table_name
    finally:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                run_query,
                sql_target,
                f"DROP TABLE IF EXISTS [{schema_name}].[{table_name}];",
                autocommit=True,
                fetch="none",
            )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_set_and_list_default_mask(
    mask_table: tuple[SqlTarget, str, str],
) -> None:
    """set_column_mask (default) -> list_masked_columns round-trip."""
    sql_target, schema_name, table_name = mask_table

    await mask_svc.set_column_mask(
        sql_target,
        schema_name,
        table_name,
        "Email",
        fn_type="default",
    )

    columns = await mask_svc.list_masked_columns(
        sql_target,
        table_schema=schema_name,
        table_name=table_name,
    )
    assert any(c.column_name == "Email" for c in columns), (
        f"Email not found in masked columns: {columns}"
    )
    email_col = next(c for c in columns if c.column_name == "Email")
    assert "default" in email_col.masking_function.lower()


@pytest.mark.integration
async def test_set_and_list_email_mask(
    mask_table: tuple[SqlTarget, str, str],
) -> None:
    """set_column_mask (email) -> list_masked_columns round-trip."""
    sql_target, schema_name, table_name = mask_table

    await mask_svc.set_column_mask(
        sql_target,
        schema_name,
        table_name,
        "Email",
        fn_type="email",
    )

    columns = await mask_svc.list_masked_columns(
        sql_target,
        table_schema=schema_name,
        table_name=table_name,
    )
    email_col = next((c for c in columns if c.column_name == "Email"), None)
    assert email_col is not None, "Email not found in masked columns"
    assert "email" in email_col.masking_function.lower()


@pytest.mark.integration
async def test_set_and_list_random_mask(
    mask_table: tuple[SqlTarget, str, str],
) -> None:
    """set_column_mask (random) -> list_masked_columns round-trip."""
    sql_target, schema_name, table_name = mask_table

    await mask_svc.set_column_mask(
        sql_target,
        schema_name,
        table_name,
        "Salary",
        fn_type="random",
        start=1,
        end=999999,
    )

    columns = await mask_svc.list_masked_columns(
        sql_target,
        table_schema=schema_name,
        table_name=table_name,
    )
    salary_col = next((c for c in columns if c.column_name == "Salary"), None)
    assert salary_col is not None, "Salary not found in masked columns"
    assert "random" in salary_col.masking_function.lower()


@pytest.mark.integration
async def test_set_and_list_partial_mask(
    mask_table: tuple[SqlTarget, str, str],
) -> None:
    """set_column_mask (partial) -> list_masked_columns round-trip."""
    sql_target, schema_name, table_name = mask_table

    await mask_svc.set_column_mask(
        sql_target,
        schema_name,
        table_name,
        "Phone",
        fn_type="partial",
        prefix=0,
        padding="XXX-XXX-",
        suffix=4,
    )

    columns = await mask_svc.list_masked_columns(
        sql_target,
        table_schema=schema_name,
        table_name=table_name,
    )
    phone_col = next((c for c in columns if c.column_name == "Phone"), None)
    assert phone_col is not None, "Phone not found in masked columns"
    assert "partial" in phone_col.masking_function.lower()


@pytest.mark.integration
async def test_drop_mask_removes_column_from_list(
    mask_table: tuple[SqlTarget, str, str],
) -> None:
    """set_column_mask -> drop_column_mask -> list (asserts column no longer masked)."""
    sql_target, schema_name, table_name = mask_table

    # Apply a mask
    await mask_svc.set_column_mask(
        sql_target,
        schema_name,
        table_name,
        "Email",
        fn_type="email",
    )

    # Verify it appears
    columns_before = await mask_svc.list_masked_columns(
        sql_target,
        table_schema=schema_name,
        table_name=table_name,
    )
    assert any(c.column_name == "Email" for c in columns_before)

    # Drop the mask
    await mask_svc.drop_column_mask(
        sql_target,
        schema_name,
        table_name,
        "Email",
    )

    # Verify it is gone
    columns_after = await mask_svc.list_masked_columns(
        sql_target,
        table_schema=schema_name,
        table_name=table_name,
    )
    assert not any(c.column_name == "Email" for c in columns_after), (
        "Email still appears as masked after DROP MASKED"
    )


@pytest.mark.integration
async def test_set_mask_replaces_existing_mask(
    mask_table: tuple[SqlTarget, str, str],
) -> None:
    """ADD MASKED replaces an existing mask without error."""
    sql_target, schema_name, table_name = mask_table

    await mask_svc.set_column_mask(
        sql_target,
        schema_name,
        table_name,
        "Email",
        fn_type="default",
    )
    # Replace with email()
    await mask_svc.set_column_mask(
        sql_target,
        schema_name,
        table_name,
        "Email",
        fn_type="email",
    )

    columns = await mask_svc.list_masked_columns(
        sql_target,
        table_schema=schema_name,
        table_name=table_name,
    )
    email_col = next((c for c in columns if c.column_name == "Email"), None)
    assert email_col is not None
    assert "email" in email_col.masking_function.lower()
