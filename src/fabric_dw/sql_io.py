"""Arrow-backed I/O helpers for SQL result sets.

Converts raw ``(columns, rows)`` pairs (as returned by DBAPI cursors) into a
:class:`pyarrow.Table` and then writes them to JSON, CSV, or Parquet.

Designed to be reusable across ``tables read`` and ``views read`` (issue #211).
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


def columns_rows_to_arrow(
    columns: list[str],
    rows: list[tuple[object, ...]],
) -> pa.Table:
    """Convert a DBAPI result set into a :class:`pyarrow.Table`.

    Each column value is coerced via ``str`` when Arrow cannot infer a native
    type; this ensures the function never raises for exotic MSSQL types such as
    ``uniqueidentifier``, ``datetimeoffset``, or ``varbinary``.

    Args:
        columns: Ordered list of column name strings.
        rows: List of row tuples; each tuple must have the same length as
            *columns*.

    Returns:
        A :class:`pyarrow.Table` with one column per name in *columns*.
    """
    if not columns:
        return pa.table({})

    # Build per-column value lists positionally so duplicate column names are
    # preserved (T-SQL allows e.g. ``SELECT a.id, b.id`` which yields two
    # columns both named "id").
    col_arrays_by_idx: list[list[Any]] = [[] for _ in columns]
    for row in rows:
        for idx, val in enumerate(row):
            col_arrays_by_idx[idx].append(val)

    arrays: list[pa.Array] = []
    for idx, col in enumerate(columns):
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

    schema = pa.schema([pa.field(col, arr.type) for col, arr in zip(columns, arrays, strict=True)])
    return pa.Table.from_arrays(arrays, schema=schema)


def _arrow_to_json_records(table: pa.Table) -> list[dict[str, Any]]:
    """Serialise *table* to a list of JSON-safe dicts.

    Columns are accessed by positional index (not by name) so that tables with
    duplicate column names — legal in T-SQL — are serialised correctly.  When
    duplicates exist the resulting dict keys will collide; the last column with
    a given name wins, which matches the natural expectation for a row dict.
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
            if output is not None:  # guarded above — always true here
                buf = io.BytesIO()
                pa_csv.write_csv(table, buf)
                output.write_bytes(buf.getvalue())
        case OutputFormat.PARQUET:
            if output is not None:  # guarded above — always true here
                pq.write_table(table, str(output))
