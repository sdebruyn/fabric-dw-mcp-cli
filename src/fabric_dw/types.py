"""Arrow/Parquet → Fabric DW T-SQL type mapping.

Fabric Data Warehouse supports a subset of SQL Server T-SQL types.  This module
provides a single authoritative function for converting a :class:`pyarrow.DataType`
(as returned from a Parquet schema or CSV inference) to a supported T-SQL type
string.

References
----------
- Fabric DW data types:
  https://learn.microsoft.com/en-us/fabric/data-warehouse/data-types
- CREATE TABLE (Fabric DW):
  https://learn.microsoft.com/en-us/sql/t-sql/statements/create-table-azure-sql-data-warehouse?view=fabric
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pyarrow as pa

__all__ = ["arrow_type_to_tsql", "validate_tsql_type"]

# ---------------------------------------------------------------------------
# Allowlist: T-SQL base type keywords supported by Fabric DW.
# ---------------------------------------------------------------------------

_TSQL_ALLOWLIST_RE = re.compile(
    r"""
    ^
    (?:
        SMALLINT | TINYINT | INT | BIGINT
        | FLOAT | REAL
        | BIT
        | DECIMAL | NUMERIC
        | DATE | DATETIME2 | TIME | DATETIMEOFFSET
        | VARCHAR | NVARCHAR | CHAR | NCHAR
        | VARBINARY
        | UNIQUEIDENTIFIER
    )
    (?:\s*\(\s*\d+\s*(?:,\s*\d+\s*)?\))?
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Default VARCHAR length used when an exact length is not available.
_DEFAULT_VARCHAR_LEN = 8000


def validate_tsql_type(sql_type: str) -> str:
    """Validate that *sql_type* is an allowlisted Fabric-DW T-SQL type.

    This prevents injection via a caller-supplied type string.  Only the type
    token itself (name + optional numeric parameters) is accepted — no keywords
    like ``NULL``, ``NOT NULL``, ``IDENTITY``, etc.

    Args:
        sql_type: A T-SQL type string, e.g. ``"INT"``, ``"VARCHAR(255)"``.

    Returns:
        *sql_type* unchanged if valid.

    Raises:
        ValueError: If *sql_type* does not match the allowlist.
    """
    cleaned = sql_type.strip()
    if not _TSQL_ALLOWLIST_RE.match(cleaned):
        msg = (
            f"Unsupported or unsafe T-SQL type {sql_type!r}. "
            "Must be a Fabric-DW-supported type such as INT, VARCHAR(n), DECIMAL(p,s), "
            "DATETIME2, DATE, BIT, BIGINT, REAL, FLOAT, VARBINARY(n)."
        )
        raise ValueError(msg)
    return cleaned


def _arrow_primitive_to_tsql(  # noqa: PLR0911,PLR0912
    at: pa.DataType,
    varchar_length: int,
) -> str | None:
    """Map a primitive (non-nested) Arrow type to a T-SQL type string.

    Returns ``None`` for types that are not directly mappable (nested, union, etc.)
    so the caller can apply fall-through logic.
    """
    import pyarrow as pa  # noqa: PLC0415

    # Integers — ordered from smallest to largest to minimise False branches.
    if pa.types.is_int8(at) or pa.types.is_int16(at) or pa.types.is_uint8(at):
        return "SMALLINT"
    if pa.types.is_int32(at) or pa.types.is_uint16(at):
        return "INT"
    if pa.types.is_int64(at) or pa.types.is_uint32(at) or pa.types.is_uint64(at):
        return "BIGINT"
    # Floats
    if pa.types.is_float16(at) or pa.types.is_float32(at):
        return "REAL"
    if pa.types.is_float64(at):
        return "FLOAT"
    # Boolean
    if pa.types.is_boolean(at):
        return "BIT"
    # Decimal
    if pa.types.is_decimal(at):
        dec = at  # type: ignore[assignment]
        return f"DECIMAL({dec.precision},{dec.scale})"
    # Dates / times
    if pa.types.is_date(at):
        return "DATE"
    if pa.types.is_time(at):
        return "TIME(7)"
    if pa.types.is_timestamp(at):
        return "DATETIME2(7)"
    if pa.types.is_duration(at):
        return "BIGINT"
    # Strings
    if pa.types.is_string(at) or pa.types.is_large_string(at):
        return f"VARCHAR({varchar_length})"
    # UTF-8 view (Arrow 12+)
    try:
        if pa.types.is_string_view(at):  # type: ignore[attr-defined]
            return f"VARCHAR({varchar_length})"
    except AttributeError:  # pragma: no cover — older pyarrow
        pass
    # Binary
    if pa.types.is_binary(at) or pa.types.is_large_binary(at):
        return f"VARBINARY({varchar_length})"
    # Null type — map to the smallest nullable type
    if pa.types.is_null(at):
        return "VARCHAR(1)"
    return None


def arrow_type_to_tsql(
    arrow_type: pa.DataType,
    field_name: str = "<unknown>",
    *,
    varchar_length: int = _DEFAULT_VARCHAR_LEN,
) -> str:
    """Convert a :class:`pyarrow.DataType` to a Fabric-DW-supported T-SQL type string.

    The mapping covers all common Arrow/Parquet primitive types.  Nested,
    list, struct, and map types are not supported (and not representable in
    Fabric DW's CREATE TABLE syntax).

    Args:
        arrow_type: The Arrow data type from a Parquet field or CSV-inferred schema.
        field_name: The column name — used only in error messages so the caller
            can identify which column triggered the error.
        varchar_length: Default length for ``VARCHAR``/``NVARCHAR`` / ``VARBINARY``
            columns when the type carries no length information.  Defaults to 8000
            (Fabric DW maximum for non-MAX varchar).

    Returns:
        A Fabric-DW-compatible T-SQL type string, e.g. ``"INT"``, ``"VARCHAR(8000)"``.

    Raises:
        ValueError: When the Arrow type has no supported T-SQL equivalent
            (e.g. list, struct, map, union, or dictionary with an unsupported key).
    """
    import pyarrow as pa  # noqa: PLC0415

    at = arrow_type

    # Try primitive mapping first.
    result = _arrow_primitive_to_tsql(at, varchar_length)
    if result is not None:
        return result

    # Dictionary (categorical) — expand to value type.
    if pa.types.is_dictionary(at):
        dict_type = at  # type: ignore[assignment]
        return arrow_type_to_tsql(dict_type.value_type, field_name, varchar_length=varchar_length)

    # Everything else (list, struct, map, union, fixed_size_list, …) is unsupported.
    msg = (
        f"Column {field_name!r}: Arrow type {at!r} has no supported Fabric DW T-SQL equivalent. "
        "Nested types (list, struct, map, union) are not representable as a scalar column. "
        "Use --all-varchar to fall back to VARCHAR for all columns."
    )
    raise ValueError(msg)
