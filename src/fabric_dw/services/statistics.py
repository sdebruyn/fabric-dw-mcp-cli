"""CRUD operations for user-defined statistics on Fabric Data Warehouses.

Public API
----------
- :func:`list_statistics`   — list statistics via sys.stats (both DW and SQL Endpoint).
- :func:`show_statistics`   — ``DBCC SHOW_STATISTICS`` (both DW and SQL Endpoint).
- :func:`create_statistics` — ``CREATE STATISTICS … ON <table>(<column>)`` (DW only).
- :func:`update_statistics` — ``UPDATE STATISTICS <table> (<stat>)`` (DW only).
- :func:`drop_statistics`   — ``DROP STATISTICS <table>.<stat>`` (DW only).

Identifier safety
-----------------
All table, column, schema, and stat names are identifiers that CANNOT be bound
as SQL parameters.  Every such name is validated via
:func:`~fabric_dw.identifiers.validate_identifier` (allowlist regex
``^[A-Za-z_][A-Za-z0-9_]{0,127}$`` — single-quotes and brackets are outside
this charset and therefore always rejected).

**CREATE / UPDATE / DROP** statements then bracket-quote identifiers via
:func:`~fabric_dw.identifiers.quote_identifier` (which additionally escapes
``]`` as ``]]`` for defence-in-depth) before embedding them in SQL.

**DBCC SHOW_STATISTICS** is different: Fabric DW does not accept bracket-quoted
identifiers in either argument position.  The official Fabric DW documentation
examples use single-quoted string literals for both arguments:
``DBCC SHOW_STATISTICS ('schema.table', 'stat_name')``.  Using bracket-quoted
identifiers for the table argument causes ``Incorrect syntax near '.'``; using
them for the stat-name argument causes ``Could not locate statistics``.
Both arguments are therefore embedded as **single-quoted string literals**.
Because ``validate_identifier`` has already accepted all name parts, none can
contain a single-quote, so no ``'``→``''`` escaping is needed.

The ``sample_percent`` argument is a numeric value; it is range-validated as an
:class:`int` (1-100) and embedded as a literal integer — never an arbitrary
user string.

SQL Endpoint guard
------------------
:func:`create_statistics`, :func:`update_statistics`, and :func:`drop_statistics`
reject SQL Analytics Endpoint items client-side with a clear
:class:`~fabric_dw.exceptions.ItemKindError` before any network I/O.
:func:`list_statistics` and :func:`show_statistics` work on both item kinds.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import cast

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFoundError
from fabric_dw.identifiers import parse_qualified_name, quote_identifier, validate_identifier
from fabric_dw.models import (
    Statistic,
    StatisticDensityRow,
    StatisticDetails,
    StatisticHeaderRow,
    StatisticHistogramStep,
    WarehouseKind,
)
from fabric_dw.services._helpers import _assert_not_sql_endpoint, coerce_to_utc
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "create_statistics",
    "drop_statistics",
    "list_statistics",
    "show_statistics",
    "update_statistics",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAMPLE_PERCENT_MIN = 1
_SAMPLE_PERCENT_MAX = 100

# Bounded retry for Fabric DW eventual-consistency: DBCC SHOW_STATISTICS may
# return "Could not locate statistics" for a short window after CREATE STATISTICS
# even though sys.stats already reflects the new statistic.  Poll until the
# statistic becomes visible to DBCC, up to _DBCC_STAT_TIMEOUT seconds total.
_DBCC_STAT_POLL_INTERVAL: float = 3.0
_DBCC_STAT_TIMEOUT: float = 60.0

# Substring matched (case-insensitive) against the exception message to
# distinguish Fabric's eventual-consistency transient error from other errors.
_DBCC_NOT_FOUND_MSG = "could not locate statistics"

# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

_LIST_STATISTICS_SQL = """\
SELECT
    st.name AS stat_name,
    s.name AS schema_name,
    t.name AS table_name,
    c.name AS column_name,
    st.auto_created,
    st.user_created,
    STATS_DATE(st.object_id, st.stats_id) AS last_updated,
    st.filter_definition AS generation_method
FROM sys.stats st
JOIN sys.stats_columns sc ON sc.object_id = st.object_id AND sc.stats_id = st.stats_id
JOIN sys.columns c ON c.object_id = sc.object_id AND c.column_id = sc.column_id
JOIN sys.tables t ON t.object_id = st.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE ({schema_filter})
  AND ({table_filter})
  AND ({kind_filter})
ORDER BY s.name, t.name, st.name;
"""

# DBCC SHOW_STATISTICS on Fabric DW requires BOTH arguments as string literals.
# The official Fabric DW docs show: DBCC SHOW_STATISTICS ('schema.table', 'stat_name')
#
# First argument — table: bracket-quoted [schema].[table] causes
# "Incorrect syntax near '.'" (fixed in #371).
#
# Second argument — stat name: bracket-quoted [stat_name] causes
# "Could not locate statistics '<stat_name>'" because Fabric DW does not
# resolve bracket-quoted tokens as statistics names in this position (fixed
# in #403).
#
# Both schema, table, and stat_name are validated via validate_identifier
# (allowlist [A-Za-z_][A-Za-z0-9_]*) before embedding, so none can contain
# a single-quote — no escaping is required.
# The format key {stat_literal} is intentionally named to signal that the
# stat name is embedded as a string literal (not a bracket-quoted identifier).
_DBCC_STAT_HEADER_SQL = "DBCC SHOW_STATISTICS ('{table_s}', '{stat_literal}') WITH STAT_HEADER;"
_DBCC_DENSITY_SQL = "DBCC SHOW_STATISTICS ('{table_s}', '{stat_literal}') WITH DENSITY_VECTOR;"
_DBCC_HISTOGRAM_SQL = "DBCC SHOW_STATISTICS ('{table_s}', '{stat_literal}') WITH HISTOGRAM;"

# CREATE STATISTICS: identifiers are bracket-quoted; FULLSCAN/SAMPLE are keywords.
_CREATE_STAT_FULLSCAN_SQL = "CREATE STATISTICS {stat_q} ON {table_q} ({col_q}) WITH FULLSCAN;"
_CREATE_STAT_SAMPLE_SQL = (
    "CREATE STATISTICS {stat_q} ON {table_q} ({col_q}) WITH SAMPLE {pct} PERCENT;"
)

# UPDATE STATISTICS: identifiers are bracket-quoted.
_UPDATE_STAT_FULLSCAN_SQL = "UPDATE STATISTICS {table_q} ({stat_q}) WITH FULLSCAN;"
_UPDATE_STAT_SAMPLE_SQL = "UPDATE STATISTICS {table_q} ({stat_q}) WITH SAMPLE {pct} PERCENT;"
_UPDATE_STAT_DEFAULT_SQL = "UPDATE STATISTICS {table_q} ({stat_q});"

# DROP STATISTICS: table.stat notation (both bracket-quoted).
_DROP_STAT_SQL = "DROP STATISTICS {table_q}.{stat_q};"

# Fetch a single statistic after create (to return a Statistic model).
_FETCH_STAT_SQL = """\
SELECT
    st.name AS stat_name,
    s.name AS schema_name,
    t.name AS table_name,
    c.name AS column_name,
    st.auto_created,
    st.user_created,
    STATS_DATE(st.object_id, st.stats_id) AS last_updated,
    st.filter_definition AS generation_method
FROM sys.stats st
JOIN sys.stats_columns sc ON sc.object_id = st.object_id AND sc.stats_id = st.stats_id
JOIN sys.columns c ON c.object_id = sc.object_id AND c.column_id = sc.column_id
JOIN sys.tables t ON t.object_id = st.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE s.name = ? AND t.name = ? AND st.name = ?;
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coalesce(data: dict[str, object], *keys: str) -> object:
    """Return the value of the first key in *data* whose value is not ``None``.

    This is used to handle DBCC output where column names may arrive in
    mixed-case (e.g. ``"Rows"``) or lower-case (e.g. ``"rows"`` in test
    fixtures) form.  Unlike ``data.get(k1) or data.get(k2)``, this helper
    correctly returns ``0`` / ``0.0`` instead of falling through to the next
    key when the DB value is zero-valued (falsy but present).

    Args:
        data: The row dict to look up keys in.
        *keys: Keys to try in order; the value of the first non-``None`` hit is
            returned.

    Returns:
        The first non-``None`` value found, or ``None`` if all keys are absent
        or map to ``None``.
    """
    for k in keys:
        if (v := data.get(k)) is not None:
            return v
    return None


def _row_to_statistic(cols: list[str], row: tuple[object, ...]) -> Statistic:
    """Build a :class:`Statistic` from a column-name list and a result row."""
    data = dict(zip(cols, row, strict=True))
    schema_name = str(data["schema_name"])
    table_name = str(data["table_name"])
    last_updated = data.get("last_updated")
    return Statistic(
        name=str(data["stat_name"]),
        qualified_table=f"{schema_name}.{table_name}",
        column=str(data["column_name"]),
        auto_created=bool(data["auto_created"]),
        user_created=bool(data["user_created"]),
        last_updated=coerce_to_utc(last_updated) if isinstance(last_updated, datetime) else None,
        generation_method=(
            str(data["generation_method"]) if data.get("generation_method") is not None else None
        ),
    )


def _row_to_header(cols: list[str], row: tuple[object, ...]) -> StatisticHeaderRow:
    """Build a :class:`StatisticHeaderRow` from STAT_HEADER output.

    Column names are normalised to lower-case with spaces collapsed to
    underscores so that mixed-case DBCC output and lower-case test fixtures
    are handled by a single lookup key.
    """
    # Normalise: lower-case, spaces → underscores.  E.g. "Rows Sampled" → "rows_sampled".
    data: dict[str, object] = {
        k.lower().replace(" ", "_"): v for k, v in zip(cols, row, strict=True)
    }
    updated = data.get("updated")
    return StatisticHeaderRow(
        name=str(data.get("name") or ""),
        updated=coerce_to_utc(updated) if isinstance(updated, datetime) else None,
        rows=cast("int | None", _coalesce(data, "rows")),
        rows_sampled=cast("int | None", _coalesce(data, "rows_sampled")),
        steps=cast("int | None", _coalesce(data, "steps")),
        density=cast("float | None", _coalesce(data, "density")),
        average_key_length=cast("float | None", _coalesce(data, "average_key_length")),
        string_index=cast("str | None", _coalesce(data, "string_index")),
        filter_expression=cast("str | None", _coalesce(data, "filter_expression")),
        unfiltered_rows=cast("int | None", _coalesce(data, "unfiltered_rows")),
    )


def _row_to_density(cols: list[str], row: tuple[object, ...]) -> StatisticDensityRow:
    """Build a :class:`StatisticDensityRow` from DENSITY_VECTOR output.

    Column names are normalised to lower-case with spaces collapsed to
    underscores so that mixed-case DBCC output and lower-case test fixtures
    are handled by a single lookup key.
    """
    data: dict[str, object] = {
        k.lower().replace(" ", "_"): v for k, v in zip(cols, row, strict=True)
    }
    return StatisticDensityRow(
        all_density=cast("float | None", _coalesce(data, "all_density")),
        average_length=cast("float | None", _coalesce(data, "average_length")),
        columns=cast("str | None", _coalesce(data, "columns")),
    )


def _row_to_histogram(cols: list[str], row: tuple[object, ...]) -> StatisticHistogramStep:
    """Build a :class:`StatisticHistogramStep` from HISTOGRAM output.

    Column names are normalised to lower-case so that mixed-case DBCC output
    (``RANGE_HI_KEY``) and lower-case test fixtures (``range_hi_key``) are
    handled by a single lookup key.
    """
    data: dict[str, object] = {k.lower(): v for k, v in zip(cols, row, strict=True)}
    range_hi = _coalesce(data, "range_hi_key")
    return StatisticHistogramStep(
        range_hi_key=str(range_hi) if range_hi is not None else None,
        range_rows=cast("float | None", _coalesce(data, "range_rows")),
        eq_rows=cast("float | None", _coalesce(data, "eq_rows")),
        distinct_range_rows=cast("float | None", _coalesce(data, "distinct_range_rows")),
        avg_range_rows=cast("float | None", _coalesce(data, "avg_range_rows")),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_statistics(
    target: SqlTarget,
    *,
    schema: str | None = None,
    table: str | None = None,
    user_only: bool = False,
    auto_only: bool = False,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[Statistic]:
    """List statistics on *target*, optionally filtered by schema, table, or kind.

    Works on both Data Warehouses and SQL Analytics Endpoints.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: When provided, only statistics on tables in this schema are
            returned.  Must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        table: When provided, only statistics on this table name are returned.
            Must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        user_only: When ``True``, only user-created statistics are returned.
            Mutually exclusive with *auto_only*.
        auto_only: When ``True``, only auto-created statistics are returned.
            Mutually exclusive with *user_only*.
        mode: The credential mode for Entra authentication.

    Returns:
        A (possibly empty) list of :class:`~fabric_dw.models.Statistic` instances.

    Raises:
        ValueError: If *schema* or *table* fail identifier validation, or if
            both *user_only* and *auto_only* are ``True``.
        PermissionDeniedError: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
    """
    if user_only and auto_only:
        msg = "user_only and auto_only are mutually exclusive"
        raise ValueError(msg)

    filter_params: list[object] = []

    if schema is not None:
        validate_identifier(schema)
        schema_filter = "s.name = ?"
        filter_params.append(schema)
    else:
        schema_filter = "1=1"

    if table is not None:
        validate_identifier(table)
        table_filter = "t.name = ?"
        filter_params.append(table)
    else:
        table_filter = "1=1"

    if user_only:
        kind_filter = "st.user_created = 1"
    elif auto_only:
        kind_filter = "st.auto_created = 1"
    else:
        kind_filter = "1=1"

    list_sql = _LIST_STATISTICS_SQL.format(
        schema_filter=schema_filter,
        table_filter=table_filter,
        kind_filter=kind_filter,
    )

    def _run() -> list[Statistic]:
        cols, rows = run_query(
            target,
            list_sql,
            params=filter_params or None,
            mode=mode,
        )
        return [_row_to_statistic(cols, r) for r in rows]

    return await asyncio.to_thread(_run)


async def show_statistics(
    target: SqlTarget,
    qualified_table: str,
    stat_name: str,
    *,
    histogram_only: bool = False,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> StatisticDetails:
    """Return details from ``DBCC SHOW_STATISTICS`` for a named statistic.

    Uses the ``WITH STAT_HEADER``, ``WITH DENSITY_VECTOR``, and ``WITH HISTOGRAM``
    variants (three separate queries) to get clean, typed single result sets.

    Works on both Data Warehouses and SQL Analytics Endpoints.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        qualified_table: Qualified table name of the form ``schema.table``.
            Both parts must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        stat_name: The name of the statistic to show.
            Must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        histogram_only: When ``True``, skip the stat header and density vector
            queries and return only the histogram steps.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.StatisticDetails` instance.

    Raises:
        ValueError: If any identifier fails validation.
        NotFoundError: If no rows are returned for the stat header query.
        PermissionDeniedError: If the driver reports a permission error.
    """
    schema, table = parse_qualified_name(qualified_table)
    validate_identifier(schema)
    validate_identifier(table)
    validate_identifier(stat_name)

    # DBCC SHOW_STATISTICS on Fabric DW requires BOTH arguments as string literals.
    # The Fabric DW documentation examples use single-quoted string literals for
    # both the table and the stat name:
    #   DBCC SHOW_STATISTICS ('schema.table', 'stat_name')
    #
    # Using bracket-quoted identifiers for the table causes "Incorrect syntax
    # near '.'" (fixed in #371).  Using bracket-quoted identifiers for the stat
    # name causes "Could not locate statistics '<name>'" because Fabric DW does
    # not resolve bracket tokens as statistics names in that position (#403).
    #
    # All parts have already been validated via validate_identifier (allowlist
    # [A-Za-z_][A-Za-z0-9_]*), so none can contain single-quotes — no escaping
    # is required.
    table_s = f"{schema}.{table}"
    stat_literal = stat_name  # already validated; embedded as a single-quoted string literal

    header_sql = _DBCC_STAT_HEADER_SQL.format(table_s=table_s, stat_literal=stat_literal)
    density_sql = _DBCC_DENSITY_SQL.format(table_s=table_s, stat_literal=stat_literal)
    histogram_sql = _DBCC_HISTOGRAM_SQL.format(table_s=table_s, stat_literal=stat_literal)

    def _run_once() -> StatisticDetails:
        """Execute DBCC SHOW_STATISTICS queries once (no retry)."""
        if histogram_only:
            h_cols, h_rows = run_query(target, histogram_sql, mode=mode)
            return StatisticDetails(
                stat_header=None,
                density_vector=[],
                histogram=[_row_to_histogram(h_cols, r) for r in h_rows],
            )

        sh_cols, sh_rows = run_query(target, header_sql, mode=mode)
        if not sh_rows:
            msg = f"Statistic [{stat_name}] on [{schema}].[{table}] not found"
            raise NotFoundError(msg)
        header = _row_to_header(sh_cols, sh_rows[0])

        dv_cols, dv_rows = run_query(target, density_sql, mode=mode)
        h_cols, h_rows = run_query(target, histogram_sql, mode=mode)

        return StatisticDetails(
            stat_header=header,
            density_vector=[_row_to_density(dv_cols, r) for r in dv_rows],
            histogram=[_row_to_histogram(h_cols, r) for r in h_rows],
        )

    # Fabric DW eventual-consistency retry: DBCC SHOW_STATISTICS may raise
    # "Could not locate statistics" for a short window after CREATE STATISTICS
    # (the statistic is visible in sys.stats but not yet to DBCC).  Retry
    # until visible or until the timeout expires, then raise NotFoundError.
    # NOTE: the effective timeout can exceed _DBCC_STAT_TIMEOUT by up to one
    # TDS round-trip (the time asyncio.to_thread(_run_once) takes to return).
    deadline = time.monotonic() + _DBCC_STAT_TIMEOUT
    while True:
        try:
            return await asyncio.to_thread(_run_once)
        except Exception as exc:
            # map_driver_error() inside run_query may promote the raw DBCC
            # driver error to NotFoundError before it reaches us.  Check the
            # message on ANY exception so that promoted NotFoundErrors are
            # still retried.  The empty-rows NotFoundError has the message
            # "Statistic [..] on [..] not found" (no "could not locate
            # statistics"), so it correctly falls through to the re-raise below.
            if _DBCC_NOT_FOUND_MSG not in str(exc).lower():
                # Not the transient DBCC error — propagate immediately.
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                msg = f"Statistic [{stat_name}] on [{schema}].[{table}] not found"
                raise NotFoundError(msg) from exc
            await asyncio.sleep(min(_DBCC_STAT_POLL_INTERVAL, remaining))


async def create_statistics(
    target: SqlTarget,
    qualified_table: str,
    column: str,
    *,
    name: str | None = None,
    fullscan: bool = True,
    sample_percent: int | None = None,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> Statistic:
    """Create a new single-column statistic on *qualified_table*.

    Only single-column statistics are supported (Fabric limitation).

    Args:
        target: The warehouse to connect to.
        qualified_table: Qualified table name of the form ``schema.table``.
            Both parts must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        column: Column name to build the statistic on.
            Must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        name: Statistic name.  Fabric requires an explicit statistic name;
            omitting this argument raises :class:`ValueError`.  Must pass
            :func:`~fabric_dw.identifiers.validate_identifier`.
        fullscan: When ``True`` (default), uses ``WITH FULLSCAN``.
            Ignored when *sample_percent* is provided.
        sample_percent: Sample percentage (1-100).  When provided, overrides
            *fullscan* and uses ``WITH SAMPLE n PERCENT``.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with
            :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.Statistic` reflecting the newly-created
        statistic (fetched via ``sys.stats`` after DDL).

    Raises:
        ItemKindError: If *kind* is
            :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If any identifier fails validation or *sample_percent* is
            outside the range 1-100.
        PermissionDeniedError: If the driver reports a permission error.
    """
    _assert_not_sql_endpoint(kind)

    if name is None:
        msg = "stat name is required: Fabric requires an explicit statistic name"
        raise ValueError(msg)

    schema, table = parse_qualified_name(qualified_table)
    validate_identifier(schema)
    validate_identifier(table)
    validate_identifier(column)
    validate_identifier(name)

    if sample_percent is not None:
        pct = int(sample_percent)
        if not _SAMPLE_PERCENT_MIN <= pct <= _SAMPLE_PERCENT_MAX:
            msg = f"sample_percent must be between 1 and 100, got {pct}"
            raise ValueError(msg)

    table_q = f"{quote_identifier(schema)}.{quote_identifier(table)}"
    col_q = quote_identifier(column)
    stat_q = quote_identifier(name)

    if sample_percent is not None:
        ddl = _CREATE_STAT_SAMPLE_SQL.format(
            stat_q=stat_q, table_q=table_q, col_q=col_q, pct=int(sample_percent)
        )
    elif fullscan:
        ddl = _CREATE_STAT_FULLSCAN_SQL.format(stat_q=stat_q, table_q=table_q, col_q=col_q)
    else:
        # No scan option specified — let the engine use its default sampling.
        ddl = f"CREATE STATISTICS {stat_q} ON {table_q} ({col_q});"

    def _run_ddl() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run_ddl)
    return await _fetch_statistic(target, schema, table, name, mode=mode)


async def update_statistics(
    target: SqlTarget,
    qualified_table: str,
    stat_name: str,
    *,
    fullscan: bool = True,
    sample_percent: int | None = None,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Update an existing statistic via ``UPDATE STATISTICS``.

    Args:
        target: The warehouse to connect to.
        qualified_table: Qualified table name of the form ``schema.table``.
            Both parts must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        stat_name: The name of the statistic to update.
            Must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        fullscan: When ``True`` (default), uses ``WITH FULLSCAN``.
            Ignored when *sample_percent* is provided.
        sample_percent: Sample percentage (1-100).  When provided, overrides
            *fullscan* and uses ``WITH SAMPLE n PERCENT``.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with
            :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.

    Raises:
        ItemKindError: If *kind* is
            :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If any identifier fails validation or *sample_percent* is
            outside the range 1-100.
        PermissionDeniedError: If the driver reports a permission error.
    """
    _assert_not_sql_endpoint(kind)

    schema, table = parse_qualified_name(qualified_table)
    validate_identifier(schema)
    validate_identifier(table)
    validate_identifier(stat_name)

    if sample_percent is not None:
        pct = int(sample_percent)
        if not _SAMPLE_PERCENT_MIN <= pct <= _SAMPLE_PERCENT_MAX:
            msg = f"sample_percent must be between 1 and 100, got {pct}"
            raise ValueError(msg)

    table_q = f"{quote_identifier(schema)}.{quote_identifier(table)}"
    stat_q = quote_identifier(stat_name)

    if sample_percent is not None:
        ddl = _UPDATE_STAT_SAMPLE_SQL.format(
            table_q=table_q, stat_q=stat_q, pct=int(sample_percent)
        )
    elif fullscan:
        ddl = _UPDATE_STAT_FULLSCAN_SQL.format(table_q=table_q, stat_q=stat_q)
    else:
        ddl = _UPDATE_STAT_DEFAULT_SQL.format(table_q=table_q, stat_q=stat_q)

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)


async def drop_statistics(
    target: SqlTarget,
    qualified_table: str,
    stat_name: str,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop a statistic via ``DROP STATISTICS <table>.<stat>``.

    Args:
        target: The warehouse to connect to.
        qualified_table: Qualified table name of the form ``schema.table``.
            Both parts must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        stat_name: The name of the statistic to drop.
            Must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            SQL Endpoint items are rejected with
            :class:`~fabric_dw.exceptions.ItemKindError`.
        mode: The credential mode for Entra authentication.

    Raises:
        ItemKindError: If *kind* is
            :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
        ValueError: If any identifier fails validation.
        PermissionDeniedError: If the driver reports a permission error.
    """
    _assert_not_sql_endpoint(kind)

    schema, table = parse_qualified_name(qualified_table)
    validate_identifier(schema)
    validate_identifier(table)
    validate_identifier(stat_name)

    # DROP STATISTICS uses schema.table.stat notation (dot-separated identifiers).
    # We bracket-quote each of the three parts.
    table_q = f"{quote_identifier(schema)}.{quote_identifier(table)}"
    stat_q = quote_identifier(stat_name)
    ddl = _DROP_STAT_SQL.format(table_q=table_q, stat_q=stat_q)

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_statistic(
    target: SqlTarget,
    schema: str,
    table: str,
    stat_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> Statistic:
    """Fetch a single statistic from sys.stats after DDL.

    Args:
        target: The warehouse to query.
        schema: Schema name (already validated).
        table: Table name (already validated).
        stat_name: Statistic name (already validated).
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.Statistic` instance.

    Raises:
        NotFoundError: If the statistic is not found after creation.
    """

    def _run() -> Statistic:
        cols, rows = run_query(
            target,
            _FETCH_STAT_SQL,
            params=[schema, table, stat_name],
            mode=mode,
        )
        if not rows:
            msg = f"Statistic [{stat_name}] on [{schema}].[{table}] not found after creation"
            raise NotFoundError(msg)
        return _row_to_statistic(cols, rows[0])

    return await asyncio.to_thread(_run)
