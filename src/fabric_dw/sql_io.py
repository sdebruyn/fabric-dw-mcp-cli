"""Arrow-backed I/O helpers for SQL result sets.

Converts raw ``(columns, rows)`` pairs (as returned by DBAPI cursors) into a
:class:`pyarrow.Table` and then writes them to JSON, CSV, or Parquet.

Designed to be reusable across ``tables read`` and ``views read`` (issue #211).
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq

__all__ = [
    "OutputFormat",
    "columns_rows_to_arrow",
    "write_arrow",
]


class OutputFormat:
    """Known output format identifiers."""

    JSON = "json"
    CSV = "csv"
    PARQUET = "parquet"

    ALL = (JSON, CSV, PARQUET)


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

    col_arrays: dict[str, list[Any]] = {c: [] for c in columns}
    for row in rows:
        for col, val in zip(columns, row, strict=True):
            col_arrays[col].append(val)

    arrays: list[pa.Array] = []
    for col in columns:
        values = col_arrays[col]
        try:
            arrays.append(pa.array(values))
        except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError):
            arrays.append(pa.array([str(v) if v is not None else None for v in values]))

    return pa.table(dict(zip(columns, arrays, strict=True)))


def _arrow_to_json_records(table: pa.Table) -> list[dict[str, Any]]:
    """Serialise *table* to a list of JSON-safe dicts."""
    return [
        {col: _json_safe(table.column(col)[i].as_py()) for col in table.column_names}
        for i in range(table.num_rows)
    ]


def _json_safe(value: Any) -> Any:  # noqa: ANN401
    """Coerce *value* to a JSON-serialisable type."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def write_arrow(
    table: pa.Table,
    fmt: str,
    output: Path | None = None,
) -> None:
    """Write *table* to the requested format.

    - ``json``: writes JSON array to *stdout* (or *output* if given).
    - ``csv``: writes CSV to *output* (required).
    - ``parquet``: writes Parquet to *output* (required).

    Args:
        table: The Arrow table to write.
        fmt: One of ``"json"``, ``"csv"``, ``"parquet"``.
        output: Path to write to.  Required for ``csv`` and ``parquet``.
            When ``None`` and format is ``json``, output goes to stdout.

    Raises:
        ValueError: If *fmt* is not a known format, or if *output* is ``None``
            for a format that requires a file path.
    """
    if fmt not in OutputFormat.ALL:
        msg = f"Unknown output format {fmt!r}; expected one of {OutputFormat.ALL}"
        raise ValueError(msg)

    if fmt in (OutputFormat.CSV, OutputFormat.PARQUET) and output is None:
        msg = f"--output PATH is required for {fmt!r} format"
        raise ValueError(msg)

    if fmt == OutputFormat.JSON:
        records = _arrow_to_json_records(table)
        payload = json.dumps(records, default=str, ensure_ascii=False, indent=2)
        if output is not None:
            output.write_text(payload, encoding="utf-8")
        else:
            sys.stdout.write(payload)
            sys.stdout.write("\n")

    elif fmt == OutputFormat.CSV:
        assert output is not None  # noqa: S101 — guarded above
        buf = io.BytesIO()
        pa_csv.write_csv(table, buf)
        output.write_bytes(buf.getvalue())

    elif fmt == OutputFormat.PARQUET:
        assert output is not None  # noqa: S101 — guarded above
        pq.write_table(table, str(output))
