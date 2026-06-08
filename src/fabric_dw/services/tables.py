"""CRUD operations for SQL tables on Fabric Data Warehouses.

Public API
----------
- :func:`validate_identifier` — re-exported from :mod:`views` (shared validator).
- :func:`list_tables`          — list all tables via TDS ``sys.tables JOIN sys.schemas``.
- :func:`read_table`           — ``SELECT TOP (N) * FROM [schema].[table]``.
- :func:`create_table`         — ``CREATE TABLE … AS <select_body>`` (CTAS).
- :func:`delete_table`         — ``DROP TABLE [schema].[table]``.
- :func:`clear_table`          — ``TRUNCATE TABLE [schema].[table]``.

List-source note
----------------
No public REST endpoint exists for enumerating warehouse tables (the OneLake
Tables REST API covers Lakehouses only, not Data Warehouses).  This module
falls back to TDS via ``sys.tables JOIN sys.schemas``, mirroring the
``views list`` approach.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import closing
from datetime import datetime
from typing import cast

from fabric_dw import sql
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFound
from fabric_dw.models import Table
from fabric_dw.services.views import validate_identifier
from fabric_dw.sql import SqlTarget

__all__ = [
    "clear_table",
    "create_table",
    "delete_table",
    "list_tables",
    "read_table",
    "validate_identifier",
]

# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

_LIST_TABLES_SQL = """\
SELECT
    s.name AS schema_name,
    t.name,
    t.create_date AS created,
    t.modify_date AS modified
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE ({schema_filter})
ORDER BY s.name, t.name;
"""

_READ_TABLE_SQL = "SELECT TOP ({count}) * FROM [{schema}].[{table}];"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SELECT_LEAD_RE = re.compile(
    r"^(?:\s*(?:/\*.*?\*/|--[^\n]*\n))*\s*(?:WITH|SELECT)\b",
    re.IGNORECASE | re.DOTALL,
)


def _reject_non_select(body: str) -> None:
    """Raise ValueError if *body* does not start with SELECT or WITH (after comments).

    Only the first non-comment keyword is checked.  Single-line (``--``) and
    block (``/* … */``) comments are stripped before the check.

    ``WITH`` is allowed to support Common Table Expressions (CTEs) of the form
    ``WITH cte AS (...) SELECT ...``.  A ``WITH … UPDATE`` body is *not* caught
    here — the Fabric CTAS API will reject non-SELECT bodies at the server side.
    This validator is an inexpensive first-line filter only.

    Args:
        body: The raw SQL supplied as the CTAS body.

    Raises:
        ValueError: If the first keyword is not SELECT or WITH (CTE).
    """
    if not _SELECT_LEAD_RE.match(body):
        msg = "CTAS body must begin with SELECT or WITH (CTE) (leading comments are allowed)"
        raise ValueError(msg)


def _row_to_table(cols: list[str], row: tuple[object, ...]) -> Table:
    """Build a :class:`Table` from a column-name list and a result row tuple."""
    data = dict(zip(cols, row, strict=True))
    schema_name = str(data["schema_name"])
    name = str(data["name"])
    return Table(
        schema_name=schema_name,
        name=name,
        qualified_name=f"{schema_name}.{name}",
        created=cast(datetime, data["created"]),
        modified=cast(datetime, data["modified"]),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_tables(
    target: SqlTarget,
    *,
    schema: str | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[Table]:
    """Return all tables on *target*, optionally filtered to a single *schema*.

    Uses ``sys.tables JOIN sys.schemas`` (TDS) — no warehouse-table REST API
    is available for Fabric Data Warehouses.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: When provided, only tables in this schema are returned.
            Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Returns:
        A (possibly empty) list of :class:`~fabric_dw.models.Table` instances.

    Raises:
        ValueError: If *schema* fails identifier validation.
        PermissionDenied: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
    """
    if schema is not None:
        validate_identifier(schema)

    schema_filter = f"s.name = '{schema}'" if schema is not None else "1=1"
    list_sql = _LIST_TABLES_SQL.format(schema_filter=schema_filter)

    def _run() -> list[Table]:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(list_sql)
                cols = [c[0] for c in (cursor.description or [])]
                rows = cursor.fetchall()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise
            return [_row_to_table(cols, r) for r in rows]

    return await asyncio.to_thread(_run)


async def read_table(
    target: SqlTarget,
    schema: str,
    table_name: str,
    *,
    count: int = 10,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> tuple[list[str], list[tuple[object, ...]]]:
    """Return up to *count* rows from *schema*.*table_name*.

    The result is a ``(columns, rows)`` pair suitable for passing to
    :mod:`fabric_dw.sql_io` for materialisation via Arrow.

    Args:
        target: The warehouse to query.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        table_name: The table name.  Must pass :func:`validate_identifier`.
        count: Maximum number of rows to return (default 10).
        mode: The credential mode for Entra authentication.

    Returns:
        A ``(columns, rows)`` tuple where *columns* is a list of column name
        strings and *rows* is a list of row tuples.

    Raises:
        ValueError: If *schema* or *table_name* fails identifier validation.
        NotFound: If the table does not exist (zero rows AND zero columns).
        PermissionDenied: If the driver reports a permission error.
    """
    validate_identifier(schema)
    validate_identifier(table_name)

    read_sql = _READ_TABLE_SQL.format(count=int(count), schema=schema, table=table_name)

    def _run() -> tuple[list[str], list[tuple[object, ...]]]:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(read_sql)
                cols = [c[0] for c in (cursor.description or [])]
                rows = cursor.fetchall()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise
            if not cols:
                msg = f"Table [{schema}].[{table_name}] not found"
                raise NotFound(msg)
            return cols, list(rows)

    return await asyncio.to_thread(_run)


async def create_table(
    target: SqlTarget,
    schema: str,
    table_name: str,
    select_body: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> Table:
    """Create a new table via ``CREATE TABLE [schema].[table] AS <select_body>`` (CTAS).

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        table_name: The table name.  Must pass :func:`validate_identifier`.
        select_body: The SELECT statement (or CTE) used as the CTAS source.
            The first non-comment keyword **must** be ``SELECT`` or ``WITH``
            (for CTE-based queries); anything else raises :class:`ValueError`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.Table` reflecting the newly-created table
        (fetched via ``sys.tables`` after DDL).

    Raises:
        ValueError: If *schema* or *table_name* fails identifier validation, or
            if *select_body* does not start with SELECT or WITH (CTE).
        PermissionDenied: If the driver reports a CREATE TABLE permission error.
    """
    validate_identifier(schema)
    validate_identifier(table_name)
    _reject_non_select(select_body)

    ddl = f"CREATE TABLE [{schema}].[{table_name}] AS {select_body}"

    def _run_ddl() -> None:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(ddl)
                conn.commit()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise

    await asyncio.to_thread(_run_ddl)
    return await _fetch_table(target, schema, table_name, mode=mode)


async def delete_table(
    target: SqlTarget,
    schema: str,
    table_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop a table via ``DROP TABLE [schema].[table]``.

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        table_name: The table name.  Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Raises:
        ValueError: If *schema* or *table_name* fails identifier validation.
        PermissionDenied: If the driver reports a DROP TABLE permission error.
    """
    validate_identifier(schema)
    validate_identifier(table_name)

    ddl = f"DROP TABLE [{schema}].[{table_name}]"

    def _run() -> None:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(ddl)
                conn.commit()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise

    await asyncio.to_thread(_run)


async def clear_table(
    target: SqlTarget,
    schema: str,
    table_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Truncate a table via ``TRUNCATE TABLE [schema].[table]``.

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        table_name: The table name.  Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Raises:
        ValueError: If *schema* or *table_name* fails identifier validation.
        PermissionDenied: If the driver reports a TRUNCATE TABLE permission error.
    """
    validate_identifier(schema)
    validate_identifier(table_name)

    ddl = f"TRUNCATE TABLE [{schema}].[{table_name}]"

    def _run() -> None:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(ddl)
                conn.commit()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise

    await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_table(
    target: SqlTarget,
    schema: str,
    table_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> Table:
    """Fetch a single table record from sys.tables to build a :class:`Table`.

    Args:
        target: The warehouse to query.
        schema: The schema name (already validated).
        table_name: The table name (already validated).
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.Table` instance.

    Raises:
        NotFound: If the table is not found after creation.
    """
    # Safe: schema and table_name pass validate_identifier() before this helper is called.
    fetch_sql = (
        f"SELECT s.name AS schema_name, t.name, t.create_date AS created, "  # noqa: S608  # nosec B608
        f"t.modify_date AS modified "
        f"FROM sys.tables t "
        f"JOIN sys.schemas s ON s.schema_id = t.schema_id "
        f"WHERE s.name = '{schema}' AND t.name = '{table_name}';"
    )

    def _run() -> Table:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(fetch_sql)
                cols = [c[0] for c in (cursor.description or [])]
                rows = cursor.fetchall()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise
            if not rows:
                msg = f"Table [{schema}].[{table_name}] not found after creation"
                raise NotFound(msg)
            return _row_to_table(cols, rows[0])

    return await asyncio.to_thread(_run)
