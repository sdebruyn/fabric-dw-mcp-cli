"""CRUD operations for SQL tables on Fabric Data Warehouses.

Public API
----------
- :func:`validate_identifier` — re-exported from :mod:`fabric_dw.identifiers`.
- :func:`list_tables`          — list all tables via TDS ``sys.tables JOIN sys.schemas``.
- :func:`read_table`           — ``SELECT TOP (N) * FROM [schema].[table]``.
- :func:`create_table`         — ``CREATE TABLE … AS <select_body>`` (CTAS).
- :func:`clone_table`          — ``CREATE TABLE … AS CLONE OF …`` (zero-copy clone).
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
from datetime import datetime
from typing import cast

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import ItemKindError, NotFoundError
from fabric_dw.identifiers import parse_qualified_name, quote_identifier, validate_identifier
from fabric_dw.models import Table, WarehouseKind
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "clear_table",
    "clone_table",
    "create_table",
    "delete_table",
    "list_tables",
    "read_table",
    "validate_identifier",
]

# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

_SQL_ENDPOINT_READONLY_MSG = "SQL Endpoints are read-only; CREATE/DROP/TRUNCATE not supported"


def _assert_not_sql_endpoint(kind: WarehouseKind) -> None:
    """Raise :class:`~fabric_dw.exceptions.ItemKindError` for SQL Endpoint items.

    Args:
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the resolved item.

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
    """
    if kind == WarehouseKind.SQL_ENDPOINT:
        raise ItemKindError(_SQL_ENDPOINT_READONLY_MSG)


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

# TOP count is an internal int (not user-supplied string), safe to embed.
_READ_TABLE_SQL = "SELECT TOP ({count}) * FROM {schema_q}.{table_q};"

_FETCH_TABLE_SQL = """\
SELECT s.name AS schema_name, t.name, t.create_date AS created,
       t.modify_date AS modified
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE s.name = ? AND t.name = ?;
"""

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
        PermissionDeniedError: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
    """
    if schema is not None:
        validate_identifier(schema)

    if schema is not None:
        schema_filter = "s.name = ?"
        filter_params: list[object] = [schema]
    else:
        schema_filter = "1=1"
        filter_params = []

    list_sql = _LIST_TABLES_SQL.format(schema_filter=schema_filter)

    def _run() -> list[Table]:
        cols, rows = run_query(
            target,
            list_sql,
            params=filter_params or None,
            mode=mode,
        )
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
        NotFoundError: If the table does not exist (zero rows AND zero columns).
        PermissionDeniedError: If the driver reports a permission error.
    """
    validate_identifier(schema)
    validate_identifier(table_name)

    read_sql = _READ_TABLE_SQL.format(
        count=int(count),
        schema_q=quote_identifier(schema),
        table_q=quote_identifier(table_name),
    )

    def _run() -> tuple[list[str], list[tuple[object, ...]]]:
        cols, rows = run_query(target, read_sql, mode=mode)
        if not cols:
            msg = f"Table [{schema}].[{table_name}] not found"
            raise NotFoundError(msg)
        return cols, list(rows)

    return await asyncio.to_thread(_run)


async def create_table(
    target: SqlTarget,
    schema: str,
    table_name: str,
    select_body: str,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
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
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.Table` reflecting the newly-created table
        (fetched via ``sys.tables`` after DDL).

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If *schema* or *table_name* fails identifier validation, or
            if *select_body* does not start with SELECT or WITH (CTE).
        PermissionDeniedError: If the driver reports a CREATE TABLE permission error.
    """
    _assert_not_sql_endpoint(kind)
    validate_identifier(schema)
    validate_identifier(table_name)
    _reject_non_select(select_body)

    ddl = f"CREATE TABLE {quote_identifier(schema)}.{quote_identifier(table_name)} AS {select_body}"

    def _run_ddl() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run_ddl)
    return await _fetch_table(target, schema, table_name, mode=mode)


async def clone_table(
    target: SqlTarget,
    source: str,
    new_table: str,
    *,
    at: datetime | None = None,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> Table:
    """Create a zero-copy clone of *source* table as *new_table*.

    Executes ``CREATE TABLE [new_schema].[new_table] AS CLONE OF
    [src_schema].[src_table]`` (with an optional ``AT '<timestamp>'`` suffix).

    Both *source* and *new_table* are dot-separated qualified names
    (``schema.table``).  Every identifier component is validated via
    :func:`validate_identifier` and bracket-quoted via :func:`quote_identifier`
    before being embedded in the DDL string.

    The ``AT`` timestamp — when provided — is a :class:`~datetime.datetime`
    that has already been parsed and validated at the CLI/MCP boundary.  It is
    formatted to a fixed safe literal (``YYYY-MM-DDTHH:MM:SS.mmm``) so no
    raw user string is ever interpolated into the DDL.

    Args:
        target: The warehouse to connect to.
        source: Qualified source table name (``schema.table``).
            Both parts must pass :func:`validate_identifier`.
        new_table: Qualified name for the new cloned table (``schema.table``).
            Both parts must pass :func:`validate_identifier`.
        at: Optional point-in-time (UTC) for a historical clone.
            When provided, the ``AT '<literal>'`` clause is appended.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.Table` reflecting the newly-cloned table
        (fetched via ``sys.tables`` after DDL).

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If any identifier component fails validation, or if *source*
            or *new_table* are not dot-separated qualified names.
        PermissionDeniedError: If the driver reports a CREATE TABLE permission error.
    """
    _assert_not_sql_endpoint(kind)

    src_schema, src_name = parse_qualified_name(source)
    new_schema, new_name = parse_qualified_name(new_table)

    validate_identifier(src_schema)
    validate_identifier(src_name)
    validate_identifier(new_schema)
    validate_identifier(new_name)

    src_q = f"{quote_identifier(src_schema)}.{quote_identifier(src_name)}"
    new_q = f"{quote_identifier(new_schema)}.{quote_identifier(new_name)}"

    ddl = f"CREATE TABLE {new_q} AS CLONE OF {src_q}"
    if at is not None:
        # Format the datetime as a millisecond-precision UTC literal.
        # The AT clause does not support bound parameters in T-SQL DDL, so we
        # embed a fixed-format literal derived from the already-validated datetime
        # object — never an arbitrary user string.
        at_literal = at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}"
        ddl = f"{ddl} AT '{at_literal}'"

    def _run_ddl() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run_ddl)
    return await _fetch_table(target, new_schema, new_name, mode=mode)


async def delete_table(
    target: SqlTarget,
    schema: str,
    table_name: str,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop a table via ``DROP TABLE [schema].[table]``.

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        table_name: The table name.  Must pass :func:`validate_identifier`.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If *schema* or *table_name* fails identifier validation.
        PermissionDeniedError: If the driver reports a DROP TABLE permission error.
    """
    _assert_not_sql_endpoint(kind)
    validate_identifier(schema)
    validate_identifier(table_name)

    ddl = f"DROP TABLE {quote_identifier(schema)}.{quote_identifier(table_name)}"

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)


async def clear_table(
    target: SqlTarget,
    schema: str,
    table_name: str,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Truncate a table via ``TRUNCATE TABLE [schema].[table]``.

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        table_name: The table name.  Must pass :func:`validate_identifier`.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If *schema* or *table_name* fails identifier validation.
        PermissionDeniedError: If the driver reports a TRUNCATE TABLE permission error.
    """
    _assert_not_sql_endpoint(kind)
    validate_identifier(schema)
    validate_identifier(table_name)

    ddl = f"TRUNCATE TABLE {quote_identifier(schema)}.{quote_identifier(table_name)}"

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

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
        NotFoundError: If the table is not found after creation.
    """

    def _run() -> Table:
        cols, rows = run_query(
            target,
            _FETCH_TABLE_SQL,
            params=[schema, table_name],
            mode=mode,
        )
        if not rows:
            msg = f"Table [{schema}].[{table_name}] not found after creation"
            raise NotFoundError(msg)
        return _row_to_table(cols, rows[0])

    return await asyncio.to_thread(_run)
