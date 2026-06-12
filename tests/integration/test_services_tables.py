"""Integration tests for services.tables — requires real Fabric credentials.

Run with: pytest -m integration tests/integration/test_services_tables.py

Fixture note: uses ``ephemeral_sql_target`` from conftest.  The target points at
a freshly-created warehouse for each test session; all tables created here are
cleaned up in the roundtrip test itself.
"""

from __future__ import annotations

import contextlib

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
