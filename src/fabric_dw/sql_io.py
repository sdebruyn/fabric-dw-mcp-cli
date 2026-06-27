"""Arrow-backed I/O helpers for SQL result sets.

Converts raw ``(columns, rows)`` pairs (as returned by DBAPI cursors) into a
:class:`pyarrow.Table` and then writes them to JSON, CSV, or Parquet.

Designed to be reusable across ``tables read`` and ``views read`` (issue #211).

Duplicate column names
----------------------
T-SQL permits queries such as ``SELECT a.id, b.id`` that yield two columns
with the same name.  This module normalises duplicates at the earliest point —
:func:`_disambiguate_columns` — and propagates the unique names through every
output layer (Arrow schema, JSON keys, CSV headers, Parquet field names).

The disambiguation scheme is:

* The **first** occurrence keeps its original name unchanged.
* Each **subsequent** occurrence is suffixed with ``_2``, ``_3``, …
* If the suffixed name would itself collide with another column (original or
  already-suffixed), the counter is incremented until a free name is found.

Example: ``['id', 'id', 'id']`` → ``['id', 'id_2', 'id_3']``.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
from enum import StrEnum
from pathlib import Path
from typing import IO, Any

import click
import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq

__all__ = [
    "OutputFormat",
    "columns_rows_to_arrow",
    "json_safe",
    "write_arrow",
]

_log = logging.getLogger(__name__)


class OutputFormat(StrEnum):
    """Known output format identifiers."""

    JSON = "json"
    CSV = "csv"
    PARQUET = "parquet"


def _disambiguate_columns(columns: list[str]) -> list[str]:
    """Return a copy of *columns* with duplicate names made unique.

    The first occurrence of each name is kept as-is.  Later occurrences are
    suffixed ``_2``, ``_3``, … (incrementing until the candidate name is not
    already present in the output list).

    Args:
        columns: Original column name list, possibly containing duplicates.

    Returns:
        A new list of the same length as *columns* with all names unique.
    """
    seen: set[str] = set()
    result: list[str] = []
    for name in columns:
        if name not in seen:
            seen.add(name)
            result.append(name)
        else:
            counter = 2
            candidate = f"{name}_{counter}"
            while candidate in seen:
                counter += 1
                candidate = f"{name}_{counter}"
            seen.add(candidate)
            result.append(candidate)
    return result


def columns_rows_to_arrow(
    columns: list[str],
    rows: list[tuple[object, ...]],
) -> pa.Table:
    """Convert a DBAPI result set into a :class:`pyarrow.Table`.

    Each column value is coerced via ``str`` when Arrow cannot infer a native
    type; this ensures the function never raises for exotic MSSQL types such as
    ``uniqueidentifier``, ``datetimeoffset``, or ``varbinary``.

    Duplicate column names (e.g. from ``SELECT a.id, b.id``) are
    **disambiguated** before the Arrow schema is constructed: later occurrences
    receive ``_2``, ``_3``, … suffixes (see :func:`_disambiguate_columns`).
    The same unique names are used consistently across all output formats so
    that every column's data is preserved in JSON, CSV, and Parquet output.

    Args:
        columns: Ordered list of column name strings.  May contain duplicates.
        rows: List of row tuples; each tuple must have the same length as
            *columns*.  A :class:`ValueError` is raised if any row's length
            does not match ``len(columns)``.

    Returns:
        A :class:`pyarrow.Table` with one column per name in *columns*,
        using disambiguated names when duplicates were present.

    Raises:
        ValueError: If any row in *rows* has a different length than *columns*.
    """
    if not columns:
        return pa.table({})

    unique_columns = _disambiguate_columns(columns)
    n_cols = len(columns)

    # Build per-column value lists positionally so duplicate column names are
    # preserved (T-SQL allows e.g. ``SELECT a.id, b.id`` which yields two
    # columns both named "id"). Enforce row-length contract explicitly so the
    # caller receives a descriptive ValueError rather than an IndexError or a
    # cryptic ArrowInvalid later.
    col_arrays_by_idx: list[list[Any]] = [[] for _ in columns]
    for row_idx, row in enumerate(rows):
        row_len = len(row)
        if row_len != n_cols:
            msg = f"Row {row_idx} has {row_len} value(s) but {n_cols} column(s) were declared"
            raise ValueError(msg)
        for idx, val in enumerate(row):
            col_arrays_by_idx[idx].append(val)

    arrays: list[pa.Array] = []
    for idx, col in enumerate(unique_columns):
        values = col_arrays_by_idx[idx]
        try:
            arrays.append(pa.array(values))
        except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError):
            _log.warning(
                "column %r could not be represented as a uniform Arrow type; "
                "falling back to string",
                col,
            )
            arrays.append(pa.array([str(v) if v is not None else None for v in values]))

    fields = [pa.field(col, arr.type) for col, arr in zip(unique_columns, arrays, strict=True)]
    schema = pa.schema(fields)
    return pa.Table.from_arrays(arrays, schema=schema)


def _arrow_to_json_records(table: pa.Table) -> list[dict[str, Any]]:
    """Serialise *table* to a list of JSON-safe dicts.

    Column names are taken from ``table.schema.names``, which are guaranteed to
    be unique because :func:`columns_rows_to_arrow` disambiguates duplicates
    before building the schema.  Each column is accessed by positional index so
    the implementation is correct even if an external caller somehow passes a
    table with duplicate field names (the last such column would overwrite
    earlier ones in that case, but callers should use
    :func:`columns_rows_to_arrow` to avoid that).
    """
    col_names = table.schema.names
    col_data = [table.column(i).to_pylist() for i in range(table.num_columns)]
    return [
        {col_names[j]: json_safe(col_data[j][i]) for j in range(table.num_columns)}
        for i in range(table.num_rows)
    ]


def json_safe(value: Any) -> Any:  # noqa: ANN401
    """Coerce *value* to a JSON-serialisable type.

    This is the canonical implementation shared between :mod:`sql_io` and
    :mod:`fabric_dw.mcp.server`.  Binary values (``bytes``, ``bytearray``,
    ``memoryview``) are rendered as base64-encoded ASCII strings, consistent
    with the ``__base64`` column-name suffix contract described in
    :class:`~fabric_dw.models.SqlResult`.

    Args:
        value: Any Python value returned from a DBAPI cursor or Arrow column.

    Returns:
        A JSON-serialisable scalar (``None``, ``bool``, ``int``, ``float``,
        ``str``).
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float):
        # nan/inf are not valid JSON (RFC 8259 §6); map to null so downstream
        # parsers (JS JSON.parse, jq, …) can consume the output.
        return None if not math.isfinite(value) else value
    if isinstance(value, (int, str)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(value).decode("ascii")
    return str(value)


def write_arrow(
    table: pa.Table,
    fmt: str,
    output: Path | None = None,
    *,
    out: IO[str] | None = None,
) -> None:
    """Write *table* to the requested format.

    - ``json``: writes JSON array to *out* stream (or *output* file if given).
    - ``csv``: writes CSV to *output* (required).
    - ``parquet``: writes Parquet to *output* (required).

    Duplicate column names are disambiguated by :func:`columns_rows_to_arrow`
    before the table reaches this function, so all output formats receive
    unique column names and every column's data is preserved.

    Args:
        table: The Arrow table to write.
        fmt: One of ``"json"``, ``"csv"``, ``"parquet"``.
        output: Path to write to.  Required for ``csv`` and ``parquet``.
            When ``None`` and format is ``json``, output goes to *out*.
        out: Text stream for JSON stdout output.  When ``None``,
            :func:`click.get_text_stream` is used so Click's pager / redirect
            handling is respected.  Ignored when *output* is provided.

    Raises:
        ValueError: If *fmt* is not a known format, or if *output* is ``None``
            for a format that requires a file path.
        AssertionError: If an unhandled :class:`OutputFormat` member is
            encountered (indicates a programmer error, not a user error).
    """
    if fmt not in OutputFormat:
        msg = f"Unknown output format {fmt!r}; expected one of {[f.value for f in OutputFormat]}"
        raise ValueError(msg)

    if fmt in (OutputFormat.CSV, OutputFormat.PARQUET) and output is None:
        msg = f"--output PATH is required for {fmt!r} format"
        raise ValueError(msg)

    match OutputFormat(fmt):
        case OutputFormat.JSON:
            records = _arrow_to_json_records(table)
            payload = json.dumps(records, default=str, ensure_ascii=False, indent=2)
            if output is not None:
                output.write_text(payload, encoding="utf-8")
            else:
                stream = out if out is not None else click.get_text_stream("stdout")
                stream.write(payload)
                stream.write("\n")
        case OutputFormat.CSV:
            if output is None:  # guarded by ValueError above; this is an internal error
                raise RuntimeError("output path is required for CSV format")
            buf = io.BytesIO()
            pa_csv.write_csv(table, buf)
            output.write_bytes(buf.getvalue())
        case OutputFormat.PARQUET:
            if output is None:  # guarded by ValueError above; this is an internal error
                raise RuntimeError("output path is required for Parquet format")
            pq.write_table(table, str(output))
        case _:  # pragma: no cover
            msg = f"Unhandled OutputFormat member {fmt!r}; update write_arrow to handle it"
            raise AssertionError(msg)
