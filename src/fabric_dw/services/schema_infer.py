"""Pure pyarrow schema-inference helpers for Fabric Data Warehouses.

Public API
----------
- :func:`infer_columns_from_parquet` -- infer columns from a Parquet file footer.
- :func:`infer_columns_from_csv`     -- infer columns from a CSV file sample.
- :func:`infer_columns_from_json`    -- infer columns from a JSONL or JSON-array file.

This module is a neutral leaf: it depends only on :mod:`fabric_dw.models`,
:mod:`fabric_dw.types`, the standard library, and :mod:`pyarrow`.  It does
**not** import :mod:`fabric_dw.sql`, :mod:`fabric_dw.services.tables`, or any
other SQL/warehouse orchestration layer, which allows both ``tables.py`` and
``load.py`` to share the inference helpers without creating an import cycle.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pyarrow as pa

from fabric_dw.models import ColumnSpec
from fabric_dw.types import arrow_type_to_tsql

__all__ = [
    "infer_columns_from_csv",
    "infer_columns_from_json",
    "infer_columns_from_parquet",
]

_log = logging.getLogger(__name__)


async def infer_columns_from_parquet(
    parquet_path: Path,
    *,
    varchar_length: int = 8000,
) -> list[ColumnSpec]:
    """Infer a :class:`~fabric_dw.models.ColumnSpec` list from a Parquet file footer.

    Reads **only the Parquet footer** (schema metadata) -- no data rows are ever
    loaded.  Arrow types are mapped to Fabric-DW-supported T-SQL types via
    :func:`~fabric_dw.types.arrow_type_to_tsql`.  Nullability is taken from the
    Arrow schema field.

    Args:
        parquet_path: Path to the Parquet file.  Only the file footer is read.
        varchar_length: Default VARCHAR/VARBINARY length for string and binary
            columns.  Defaults to 8000 (Fabric DW non-MAX maximum).

    Returns:
        A list of :class:`~fabric_dw.models.ColumnSpec` instances (one per column).

    Raises:
        FileNotFoundError: If *parquet_path* does not exist.
        ValueError: If any Parquet field maps to an unsupported T-SQL type.
    """
    import pyarrow.parquet as pq  # noqa: PLC0415

    if not parquet_path.exists():
        msg = f"Parquet file not found: {parquet_path}"
        raise FileNotFoundError(msg)

    # Read schema only -- pq.read_schema reads the footer without loading any row groups.
    arrow_schema = await asyncio.to_thread(pq.read_schema, str(parquet_path))

    columns: list[ColumnSpec] = []
    for field in arrow_schema:
        sql_type = arrow_type_to_tsql(field.type, field.name, varchar_length=varchar_length)
        nullable = field.nullable
        columns.append(ColumnSpec(name=field.name, sql_type=sql_type, nullable=nullable))

    _log.debug("infer_columns_from_parquet: %d columns from %s", len(columns), parquet_path.name)
    return columns


async def infer_columns_from_csv(
    csv_path: Path,
    *,
    all_varchar: bool = False,
    varchar_length: int = 8000,
    sample_rows: int = 1000,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> list[ColumnSpec]:
    """Infer a :class:`~fabric_dw.models.ColumnSpec` list from a CSV file.

    Reads the CSV **header row + a bounded sample** of rows for type inference.
    No data is inserted into the warehouse; this is a pure inference operation.

    When *all_varchar* is ``True``, every column becomes
    ``VARCHAR(*varchar_length*)`` regardless of observed values.

    Args:
        csv_path: Path to the CSV file.  Only the header + a bounded sample are read.
        all_varchar: Force all columns to ``VARCHAR(*varchar_length*)``, overriding
            inference.  Useful when inference produces unexpected results.
        varchar_length: Length for VARCHAR columns (default 8000).
        sample_rows: Maximum number of rows to read for type inference (default 1000).
        delimiter: CSV field delimiter (default ``,``).
        encoding: File encoding (default ``utf-8-sig`` to strip BOM if present).

    Returns:
        A list of :class:`~fabric_dw.models.ColumnSpec` instances (one per column).

    Raises:
        FileNotFoundError: If *csv_path* does not exist.
        ValueError: If the CSV file is empty (no header row), or if any inferred
            Arrow type cannot be mapped to a T-SQL type and fallback fails.
    """
    if not csv_path.exists():
        msg = f"CSV file not found: {csv_path}"
        raise FileNotFoundError(msg)

    if all_varchar:
        # Read just the header row to get column names.
        import csv  # noqa: PLC0415

        with csv_path.open(encoding=encoding, newline="") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            try:
                header = next(reader)
            except StopIteration:
                msg = f"CSV file is empty: {csv_path}"
                raise ValueError(msg) from None

        return [
            ColumnSpec(name=col_name, sql_type=f"VARCHAR({varchar_length})", nullable=True)
            for col_name in header
        ]

    # Read header + a bounded sample for type inference via pyarrow.csv.
    # Use open_csv() (streaming batches) so that only a prefix of the file
    # is ever loaded into memory -- read_csv() would buffer the entire file
    # before slice() could discard excess rows.
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.csv as pa_csv  # noqa: PLC0415

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
            # Header-only CSV -- return a zero-row table so schema is preserved.
            return inferred_schema.empty_table()
        return pa.Table.from_batches(batches)

    arrow_table = await asyncio.to_thread(_read_csv_sample)
    _log.debug(
        "infer_columns_from_csv: read %d sample rows from %s for type inference",
        arrow_table.num_rows,
        csv_path.name,
    )

    columns: list[ColumnSpec] = []
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

    _log.debug("infer_columns_from_csv: %d columns from %s", len(columns), csv_path.name)
    return columns


def _json_sample_source(json_path: Path, sample_rows: int) -> str | pa.Buffer:
    """Detect the JSON shape and return a pyarrow ``open_json`` input source.

    Peeks the first non-whitespace byte (after stripping any UTF-8 BOM):

    - ``[`` -- a **JSON array** of objects: the whole file is parsed with
      :func:`json.loads`, validated as a non-empty list of objects, and the
      first *sample_rows* records are re-emitted as JSONL into an in-memory
      buffer.  The full file is loaded into memory in this case -- for very
      large data prefer JSONL.
    - otherwise -- **JSONL** (newline-delimited objects): the file path is
      returned so :func:`pyarrow.json.open_json` streams it directly.

    Args:
        json_path: Path to the JSONL file or JSON array file.
        sample_rows: Maximum number of records to keep (array form only;
            JSONL is bounded later during batch iteration).

    Returns:
        Either the file-path string (JSONL) or a :class:`pyarrow.Buffer`
        holding a bounded JSONL sample (array form), ready for ``open_json``.

    Raises:
        ValueError: If the file is empty, or a JSON array is malformed or is
            not a non-empty list of objects.
    """
    import json  # noqa: PLC0415

    import pyarrow as pa  # noqa: PLC0415

    raw = json_path.read_bytes()
    # Strip a UTF-8 BOM if present so the first-byte peek is reliable.
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    stripped = raw.lstrip()
    if not stripped:
        msg = f"JSON file is empty: {json_path}"
        raise ValueError(msg)

    if stripped[:1] != b"[":
        # JSONL (newline-delimited objects) -- stream the file directly.
        return str(json_path)

    # JSON array of records -- parse fully, then re-emit a bounded sample as JSONL.
    try:
        records = json.loads(stripped)
    except json.JSONDecodeError as exc:
        msg = f"Invalid JSON in {json_path}: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(records, list) or not records:
        msg = (
            f"JSON array in {json_path} must be a non-empty list of objects "
            f"(got {type(records).__name__})."
        )
        raise ValueError(msg)
    if not all(isinstance(rec, dict) for rec in records):
        msg = f"JSON array in {json_path} must contain only objects (records)."
        raise ValueError(msg)
    sample = records[:sample_rows]
    buffer = "\n".join(json.dumps(rec) for rec in sample).encode("utf-8")
    return pa.py_buffer(buffer)


def _json_sample_to_arrow_schema(json_path: Path, sample_rows: int) -> pa.Schema:
    """Read a bounded sample of JSON data and return the inferred Arrow schema.

    The file shape (JSONL vs JSON array of objects) is auto-detected by
    :func:`_json_sample_source`.  pyarrow unifies types across records
    (``int64``/``double``/``bool``/``string``/``timestamp[s]``) and forms the
    union of keys across records.

    Args:
        json_path: Path to the JSONL file or JSON array file.
        sample_rows: Maximum number of records to read for type inference.

    Returns:
        A :class:`pyarrow.Schema` reflecting the inferred column types.

    Raises:
        ValueError: If the file is empty / contains zero records, if a JSON
            array is malformed or is not a non-empty list of objects, or if
            pyarrow cannot parse the data (e.g. a whole-file number-to-string
            conflict -- the message suggests ``--all-varchar``).
    """
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.json as pa_json  # noqa: PLC0415

    source = _json_sample_source(json_path, sample_rows)
    read_opts = pa_json.ReadOptions(block_size=1 << 20) if isinstance(source, str) else None

    reader = pa_json.open_json(source, read_options=read_opts)
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
        # Zero records read -- schema may still be empty (no columns).
        if not inferred_schema.names:
            msg = f"JSON file contains no records: {json_path}"
            raise ValueError(msg)
        return inferred_schema
    return pa.Table.from_batches(batches).schema


def _json_sample_keys(json_path: Path, sample_rows: int) -> list[str]:
    """Return the union of record keys from a bounded JSON sample, in first-seen order.

    Used by the ``all_varchar`` path: it only needs column names, and must not
    fail on type conflicts (e.g. a column that mixes numbers and strings) -- that
    is the very situation ``--all-varchar`` exists to rescue.  The file shape is
    auto-detected by :func:`_json_sample_source`; the JSONL sample is parsed
    line-by-line with the stdlib :mod:`json` module rather than pyarrow.

    Args:
        json_path: Path to the JSONL file or JSON array file.
        sample_rows: Maximum number of records to scan for keys.

    Returns:
        The union of keys across the sampled records, ordered by first appearance.

    Raises:
        ValueError: If the file is empty / contains zero records, or is malformed.
    """
    import json  # noqa: PLC0415

    source = _json_sample_source(json_path, sample_rows)

    # Ordered set of keys (first-seen order preserved by dict insertion order).
    keys: dict[str, None] = {}

    def _collect(record: object, line_no: int) -> None:
        if not isinstance(record, dict):
            # Invalid file data (not a programming type error); surface as ValueError
            # so the CLI maps it to a user-facing ClickException like the other checks.
            msg = f"JSON record {line_no} in {json_path} is not an object."
            raise ValueError(msg)  # noqa: TRY004
        # JSON object keys are always strings.
        for key in record:
            keys[str(key)] = None

    if isinstance(source, str):
        # JSONL -- parse each non-blank line up to *sample_rows* records.
        with json_path.open(encoding="utf-8-sig") as fh:
            seen = 0
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    msg = f"Invalid JSON on line {line_no} of {json_path}: {exc}"
                    raise ValueError(msg) from exc
                _collect(record, line_no)
                seen += 1
                if seen >= sample_rows:
                    break
    else:
        # JSON array -- the buffer already holds the bounded JSONL sample.
        text = source.to_pybytes().decode("utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            _collect(json.loads(line), line_no)

    if not keys:
        msg = f"JSON file contains no records: {json_path}"
        raise ValueError(msg)
    return list(keys)


async def infer_columns_from_json(
    json_path: Path,
    *,
    all_varchar: bool = False,
    varchar_length: int = 8000,
    sample_rows: int = 1000,
) -> list[ColumnSpec]:
    """Infer a :class:`~fabric_dw.models.ColumnSpec` list from JSON **data**.

    The file may be either **JSONL** (one JSON object per line) or a **JSON
    array of objects**; the shape is auto-detected by peeking the first
    non-whitespace byte (after stripping any BOM).  A bounded sample of
    *sample_rows* records is read and pyarrow infers the column types
    (``int64``/``double``/``bool``/``string``/``timestamp[s]`` -- ISO strings
    auto-detected); the union of keys across records becomes the column set.
    No data is inserted into the warehouse; this is a pure inference operation.

    All inferred columns are **nullable** (any key may be omitted from any
    record).  Each Arrow type is mapped via
    :func:`~fabric_dw.types.arrow_type_to_tsql`; a column whose inferred type
    has no scalar T-SQL equivalent (a nested ``struct``/``list``, or a mixed
    number-to-string column that pyarrow surfaced per-column) falls back to
    ``VARCHAR(*varchar_length*)`` with a warning.  When *all_varchar* is
    ``True``, every column becomes ``VARCHAR(*varchar_length*)`` up front,
    skipping type inference.

    Performance: JSONL is **streamed** (only a bounded prefix is loaded,
    regardless of file size); a JSON array is **fully loaded** into memory via
    :func:`json.loads` -- for very large data prefer JSONL.

    Args:
        json_path: Path to the JSONL file or JSON array file.
        all_varchar: Force all columns to ``VARCHAR(*varchar_length*)``,
            overriding inference.  Useful when inference produces unexpected
            results or a whole-file number-to-string conflict.
        varchar_length: Length for VARCHAR columns (default 8000).
        sample_rows: Maximum number of records to read for type inference
            (default 1000).

    Returns:
        A list of :class:`~fabric_dw.models.ColumnSpec` instances (one per column).

    Raises:
        FileNotFoundError: If *json_path* does not exist.
        ValueError: If the file is empty / contains zero records, is malformed,
            is not a non-empty list of objects (array form), or if pyarrow
            cannot parse the data (e.g. a whole-file number-to-string conflict --
            the message suggests ``--all-varchar``).
    """
    if not json_path.exists():
        msg = f"JSON file not found: {json_path}"
        raise FileNotFoundError(msg)

    if all_varchar:
        # Only the key union is needed.  Read keys with the stdlib (not pyarrow)
        # so this path still works when a column mixes numbers and strings -- the
        # exact case --all-varchar exists to rescue.
        names = await asyncio.to_thread(_json_sample_keys, json_path, sample_rows)
        return [
            ColumnSpec(name=name, sql_type=f"VARCHAR({varchar_length})", nullable=True)
            for name in names
        ]

    import pyarrow as pa  # noqa: PLC0415

    try:
        arrow_schema = await asyncio.to_thread(_json_sample_to_arrow_schema, json_path, sample_rows)
    except pa.ArrowInvalid as exc:
        # Whole-file parse failure (e.g. a column changes from number to string
        # across records -- pyarrow raises before per-column fallback is possible).
        msg = (
            f"Could not infer a schema from {json_path}: {exc}. "
            "If a column mixes numbers and strings, use --all-varchar."
        )
        raise ValueError(msg) from exc

    columns: list[ColumnSpec] = []
    for i, name in enumerate(arrow_schema.names):
        field = arrow_schema.field(i)
        try:
            sql_type = arrow_type_to_tsql(field.type, name, varchar_length=varchar_length)
        except ValueError:
            # Fall back to VARCHAR for non-mappable inferred types (nested
            # struct/list, or a per-column number-to-string conflict).
            _log.warning(
                "Column %r: inferred Arrow type %r has no T-SQL equivalent; "
                "falling back to VARCHAR(%d)",
                name,
                field.type,
                varchar_length,
            )
            sql_type = f"VARCHAR({varchar_length})"
        # JSON columns are always nullable (keys may be omitted from any record).
        columns.append(ColumnSpec(name=name, sql_type=sql_type, nullable=True))

    _log.debug("infer_columns_from_json: %d columns from %s", len(columns), json_path.name)
    return columns
