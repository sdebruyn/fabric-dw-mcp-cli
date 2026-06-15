"""CRUD operations for SQL tables on Fabric Data Warehouses.

Public API
----------
- :func:`validate_identifier`     — re-exported from :mod:`fabric_dw.identifiers`.
- :func:`list_tables`             — list all tables via TDS ``sys.tables JOIN sys.schemas``.
- :func:`read_table`              — ``SELECT TOP (N) * FROM [schema].[table]``.
- :func:`create_table`            — ``CREATE TABLE … AS <select_body>`` (CTAS).
- :func:`create_empty_table`      — ``CREATE TABLE … (col TYPE [NULL|NOT NULL], …)`` (DDL only).
- :func:`create_table_from_parquet` — infer schema from Parquet file → :func:`create_empty_table`.
- :func:`create_table_from_csv`   — infer schema from CSV file → :func:`create_empty_table`.
- :func:`clone_table`             — ``CREATE TABLE … AS CLONE OF …`` (zero-copy clone).
- :func:`delete_table`            — ``DROP TABLE [schema].[table]``.
- :func:`clear_table`             — ``TRUNCATE TABLE [schema].[table]``.
- :func:`rename_table`            — ``EXEC sp_rename`` (Data-Warehouse-only).

List-source note
----------------
No public REST endpoint exists for enumerating warehouse tables (the OneLake
Tables REST API covers Lakehouses only, not Data Warehouses).  This module
falls back to TDS via ``sys.tables JOIN sys.schemas``, mirroring the
``views list`` approach.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import ItemKindError, NotFoundError
from fabric_dw.identifiers import parse_qualified_name, quote_identifier, validate_identifier
from fabric_dw.models import ColumnSpec, Table, WarehouseKind
from fabric_dw.services._helpers import reject_non_select
from fabric_dw.sql import SqlTarget, run_query
from fabric_dw.types import arrow_type_to_tsql, validate_tsql_type

__all__ = [
    "clear_table",
    "clone_table",
    "create_empty_table",
    "create_table",
    "create_table_from_csv",
    "create_table_from_parquet",
    "delete_table",
    "list_tables",
    "read_table",
    "rename_table",
    "validate_identifier",
]

_log = logging.getLogger(__name__)

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

# sp_rename: @objname = 'schema.oldtable', @newname = 'newtable', @objtype = 'OBJECT'
# Names are bound as ? parameters (string args to the proc, not SQL identifiers).
_SP_RENAME_SQL = "EXEC sp_rename ?, ?, 'OBJECT'"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        # Schema name is bound as a ? parameter — never interpolated into SQL.
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
    reject_non_select(select_body)

    ddl = f"CREATE TABLE {quote_identifier(schema)}.{quote_identifier(table_name)} AS {select_body}"

    def _run_ddl() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run_ddl)
    return await _fetch_table(target, schema, table_name, mode=mode)


async def create_empty_table(
    target: SqlTarget,
    schema: str,
    table_name: str,
    columns: list[ColumnSpec],
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> Table:
    """Create an empty table from an explicit column spec (DDL only, no data).

    Builds ``CREATE TABLE [schema].[table] (col TYPE [NULL|NOT NULL], …)`` from
    the validated :class:`~fabric_dw.models.ColumnSpec` list.  No data is ever
    read or inserted; this is a pure DDL operation.

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        table_name: The table name.  Must pass :func:`validate_identifier`.
        columns: Non-empty list of :class:`~fabric_dw.models.ColumnSpec` instances
            describing each column.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.Table` reflecting the newly-created table
        (fetched via ``sys.tables`` after DDL).

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If *schema* or *table_name* fails identifier validation,
            if *columns* is empty, if any column name fails identifier validation,
            or if any column ``sql_type`` is not on the Fabric DW type allowlist.
        PermissionDeniedError: If the driver reports a CREATE TABLE permission error.
    """
    _assert_not_sql_endpoint(kind)
    validate_identifier(schema)
    validate_identifier(table_name)

    if not columns:
        msg = "columns must not be empty; provide at least one ColumnSpec"
        raise ValueError(msg)

    col_defs: list[str] = []
    for col in columns:
        validate_identifier(col.name)
        validated_type = validate_tsql_type(col.sql_type)
        null_clause = "NULL" if col.nullable else "NOT NULL"
        col_defs.append(f"    {quote_identifier(col.name)} {validated_type} {null_clause}")

    col_block = ",\n".join(col_defs)
    ddl = (
        f"CREATE TABLE {quote_identifier(schema)}.{quote_identifier(table_name)} (\n{col_block}\n)"
    )

    def _run_ddl() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run_ddl)
    return await _fetch_table(target, schema, table_name, mode=mode)


async def create_table_from_parquet(
    target: SqlTarget,
    schema: str,
    table_name: str,
    parquet_path: Path,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
    varchar_length: int = 8000,
) -> Table:
    """Create an empty table whose schema is derived from a Parquet file.

    Reads **only the Parquet footer** (schema metadata) — no data rows are ever
    read or inserted.  Arrow types are mapped to Fabric-DW-supported T-SQL types
    via :func:`~fabric_dw.types.arrow_type_to_tsql`.  Nullability is taken from
    the Arrow schema field.

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        table_name: The table name.  Must pass :func:`validate_identifier`.
        parquet_path: Path to the Parquet file.  Only the file footer (schema)
            is accessed — no data rows are read.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.
        varchar_length: Default VARCHAR/VARBINARY length for string and binary
            columns.  Defaults to 8000 (Fabric DW non-MAX maximum).

    Returns:
        A :class:`~fabric_dw.models.Table` reflecting the newly-created table.

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If any Parquet field maps to an unsupported T-SQL type,
            or if any derived identifier fails validation.
        FileNotFoundError: If *parquet_path* does not exist.
    """
    import pyarrow.parquet as pq  # noqa: PLC0415

    _assert_not_sql_endpoint(kind)
    if not parquet_path.exists():
        msg = f"Parquet file not found: {parquet_path}"
        raise FileNotFoundError(msg)

    # Read schema only — pq.read_schema reads the footer without loading any row groups.
    # Wrapped in asyncio.to_thread for consistency with other blocking I/O in this module.
    arrow_schema = await asyncio.to_thread(pq.read_schema, str(parquet_path))

    columns: list[ColumnSpec] = []
    for field in arrow_schema:
        sql_type = arrow_type_to_tsql(field.type, field.name, varchar_length=varchar_length)
        nullable = field.nullable
        columns.append(ColumnSpec(name=field.name, sql_type=sql_type, nullable=nullable))

    _log.debug("create_table_from_parquet: %d columns from %s", len(columns), parquet_path.name)
    return await create_empty_table(target, schema, table_name, columns, kind=kind, mode=mode)


async def create_table_from_csv(
    target: SqlTarget,
    schema: str,
    table_name: str,
    csv_path: Path,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
    infer_types: bool = True,
    all_varchar: bool = False,
    varchar_length: int = 8000,
    sample_rows: int = 1000,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> Table:
    """Create an empty table whose schema is derived from a CSV file header.

    Reads the CSV **header row + a bounded sample** of rows for type inference.
    No data is inserted into the warehouse.

    When *all_varchar* is ``True``, every column becomes ``VARCHAR(*varchar_length*)``
    regardless of observed values — useful as an escape hatch when inference
    produces unexpected types.

    When *infer_types* is ``True`` (the default), :mod:`pyarrow.csv` is used to
    read up to *sample_rows* rows and infer types; the mapping to T-SQL types
    follows :func:`~fabric_dw.types.arrow_type_to_tsql`.

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        table_name: The table name.  Must pass :func:`validate_identifier`.
        csv_path: Path to the CSV file.  Only the header + a bounded sample of
            rows are read — this is schema inference, not data loading.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.
        infer_types: When ``True`` (default), types are inferred from sampled rows.
            When ``False``, every column becomes ``VARCHAR(*varchar_length*)``.
        all_varchar: Force all columns to ``VARCHAR(*varchar_length*)``, overriding
            inference.  Useful when inference produces unexpected results.
        varchar_length: Length for VARCHAR columns (default 8000).
        sample_rows: Maximum number of rows to read for type inference (default 1000).
        delimiter: CSV field delimiter (default ``,``).
        encoding: File encoding (default ``utf-8-sig`` to strip BOM if present).

    Returns:
        A :class:`~fabric_dw.models.Table` reflecting the newly-created table.

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If any inferred type maps to an unsupported T-SQL type,
            or if any column name fails identifier validation.
        FileNotFoundError: If *csv_path* does not exist.
    """
    import pyarrow.csv as pa_csv  # noqa: PLC0415

    _assert_not_sql_endpoint(kind)
    if not csv_path.exists():
        msg = f"CSV file not found: {csv_path}"
        raise FileNotFoundError(msg)

    use_varchar = all_varchar or not infer_types

    if use_varchar:
        # Read just the header row to get column names.
        import csv  # noqa: PLC0415

        with csv_path.open(encoding=encoding, newline="") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            try:
                header = next(reader)
            except StopIteration:
                msg = f"CSV file is empty: {csv_path}"
                raise ValueError(msg) from None

        columns = [
            ColumnSpec(name=col_name, sql_type=f"VARCHAR({varchar_length})", nullable=True)
            for col_name in header
        ]
    else:
        # Read header + a bounded sample for type inference via pyarrow.csv.
        # Use open_csv() (streaming batches) so that only a prefix of the file
        # is ever loaded into memory — read_csv() would buffer the entire file
        # before slice() could discard excess rows.
        import pyarrow as pa  # noqa: PLC0415

        read_opts = pa_csv.ReadOptions(encoding=encoding)
        parse_opts = pa_csv.ParseOptions(delimiter=delimiter)
        # ConvertOptions: auto-convert types, treat empty fields as null
        convert_opts = pa_csv.ConvertOptions(null_values=["", "NULL", "null", "NA", "N/A"])

        def _read_csv_sample() -> pa.Table:
            """Read at most *sample_rows* rows via the streaming CSV reader.

            Uses :func:`pyarrow.csv.open_csv` (batch streaming) so that the
            file is read lazily and iteration stops as soon as *sample_rows*
            rows have been accumulated.  Only the prefix of the file is ever
            loaded into memory, regardless of total file size.
            """
            reader = pa_csv.open_csv(
                str(csv_path),
                read_options=read_opts,
                parse_options=parse_opts,
                convert_options=convert_opts,
            )
            # reader.schema is available before iteration and reflects inferred types.
            inferred_schema = reader.schema
            batches: list[pa.RecordBatch] = []
            rows_seen = 0
            for batch in reader:
                remaining = sample_rows - rows_seen
                chunk = batch.slice(0, remaining) if batch.num_rows > remaining else batch
                batches.append(chunk)
                rows_seen += chunk.num_rows
                if rows_seen >= sample_rows:
                    break
            if not batches:
                # Header-only CSV — return a zero-row table so schema is preserved.
                return inferred_schema.empty_table()
            return pa.Table.from_batches(batches)

        arrow_table = await asyncio.to_thread(_read_csv_sample)
        _log.debug(
            "create_table_from_csv: read %d sample rows from %s for type inference",
            arrow_table.num_rows,
            csv_path.name,
        )

        columns = []
        for i, name in enumerate(arrow_table.schema.names):
            field = arrow_table.schema.field(i)
            try:
                sql_type = arrow_type_to_tsql(field.type, name, varchar_length=varchar_length)
            except ValueError:
                # Fall back to VARCHAR for non-mappable inferred types.
                _log.warning(
                    "Column %r: inferred Arrow type %r has no T-SQL equivalent; "
                    "falling back to VARCHAR(%d)",
                    name,
                    field.type,
                    varchar_length,
                )
                sql_type = f"VARCHAR({varchar_length})"
            # CSV columns are always nullable (empty cells).
            columns.append(ColumnSpec(name=name, sql_type=sql_type, nullable=True))

    _log.debug("create_table_from_csv: %d columns from %s", len(columns), csv_path.name)
    return await create_empty_table(target, schema, table_name, columns, kind=kind, mode=mode)


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
            Sub-millisecond precision is rounded to the nearest millisecond
            (ties to even, i.e. Python banker's rounding via :func:`round`).
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
        #
        # Round to the nearest millisecond (half-to-even via Python round())
        # rather than truncating, so that e.g. 123_750 µs → 124 ms instead
        # of silently shifting the point-in-time 0.75 ms earlier.
        # round() can return 1000 for microsecond values ≥ 999_500 µs;
        # use timedelta to roll the carry into the seconds field correctly.
        at_rounded = at.replace(microsecond=0) + timedelta(
            milliseconds=round(at.microsecond / 1000)
        )
        ms_part = f"{at_rounded.microsecond // 1000:03d}"
        at_literal = at_rounded.strftime("%Y-%m-%dT%H:%M:%S.") + ms_part
        ddl = f"{ddl} AT '{at_literal}'"

    def _run_ddl() -> None:
        # Clone DDL runs on an autocommit connection so the implicit transaction
        # starts exactly at statement-execute time — after the captured AT
        # timestamp.  With autocommit=False the ODBC driver issues BEGIN
        # TRANSACTION before the first statement; a pooled connection can have
        # its transaction start time predate the requested AT point, causing
        # "TIMESTAMP is after the current transaction started" on Fabric.
        # Autocommit connections bypass the pool (always opened fresh) and
        # eliminate the implicit transaction, matching the pattern used for
        # ALTER DATABASE snapshot DDL.  Both the AT and non-AT paths share
        # this single code path for consistency.
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

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


async def rename_table(
    target: SqlTarget,
    qualified: str,
    new_name: str,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> Table:
    """Rename a table via ``EXEC sp_rename``.

    ``sp_rename`` takes names as string arguments, so both the current
    qualified name and the new bare name are passed as bound ``?`` parameters
    — no identifier interpolation is required or performed.

    Args:
        target: The warehouse to connect to.
        qualified: The current fully-qualified name of the form ``schema.table``.
            Parsed with :func:`~fabric_dw.identifiers.parse_qualified_name`.
        new_name: The new **unqualified** table name.  Must pass
            :func:`validate_identifier`.  Schema-qualified values (containing a
            dot) are rejected — ``sp_rename`` cannot move a table to a different
            schema.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.Table` reflecting the renamed table
        (fetched via ``sys.tables`` after the rename).

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If *qualified* cannot be parsed, if *new_name* fails
            identifier validation, or if *new_name* is schema-qualified (contains
            a dot).
        NotFoundError: If the renamed table cannot be found in ``sys.tables``
            after the rename.
        PermissionDeniedError: If the driver reports a permission error.
    """
    _assert_not_sql_endpoint(kind)

    schema, _old_name = parse_qualified_name(qualified)
    validate_identifier(schema)
    validate_identifier(_old_name)

    if "." in new_name:
        msg = (
            f"New name {new_name!r} must not be schema-qualified; "
            "sp_rename cannot move a table to a different schema"
        )
        raise ValueError(msg)
    validate_identifier(new_name)

    # @objname = 'schema.oldtable', @newname = 'newtable' — bound as ? params.
    old_qualified = f"{schema}.{_old_name}"

    def _run() -> None:
        run_query(
            target,
            _SP_RENAME_SQL,
            params=[old_qualified, new_name],
            mode=mode,
            commit=True,
            fetch="none",
        )

    await asyncio.to_thread(_run)
    try:
        return await _fetch_table(target, schema, new_name, mode=mode)
    except NotFoundError:
        msg = f"Table [{schema}].[{new_name}] not found after rename"
        raise NotFoundError(msg) from None


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
