"""Shared column-metadata service for SQL tables and views.

Public API
----------
- :func:`format_data_type`          — format a ``sys.columns`` type row into a T-SQL type string.
- :func:`get_object_columns`        — return column metadata for any named SQL object
  (table or view) via ``sys.columns JOIN sys.types``, ordered by ``column_id``.
- :func:`get_object_columns_or_raise` — same, but raises
  :class:`~fabric_dw.exceptions.NotFoundError` when the object does not exist.
"""

from __future__ import annotations

import asyncio
import logging

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFoundError
from fabric_dw.identifiers import validate_identifier
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "format_data_type",
    "get_object_columns",
    "get_object_columns_or_raise",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL template
# ---------------------------------------------------------------------------

# Schema and object name are passed as bound parameters (``?``) — no string
# concatenation. The query resolves the object via sys.objects so it works
# for both tables (type='U') and views (type='V') alike.
_GET_COLUMNS_SQL = """\
SELECT
    c.column_id      AS ordinal,
    c.name,
    t.name           AS type_name,
    c.max_length,
    c.precision,
    c.scale,
    c.is_nullable    AS nullable,
    c.collation_name,
    c.is_identity,
    c.is_computed
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
JOIN sys.objects o ON o.object_id = c.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE s.name = ? AND o.name = ?
ORDER BY c.column_id;
"""

# ---------------------------------------------------------------------------
# Type formatting
# ---------------------------------------------------------------------------

# Types that carry a character/byte length in max_length.
_LENGTH_TYPES: frozenset[str] = frozenset({"char", "varchar", "binary", "varbinary"})

# Types that carry a character length but store it in BYTES (÷2 for char len).
_NCHAR_TYPES: frozenset[str] = frozenset({"nchar", "nvarchar"})

# Types whose precision/scale are meaningful.
_PRECISION_SCALE_TYPES: frozenset[str] = frozenset({"decimal", "numeric"})

# Types whose scale alone (fractional-seconds precision) is meaningful.
_SCALE_TYPES: frozenset[str] = frozenset({"time", "datetime2", "datetimeoffset"})


def format_data_type(
    type_name: str,
    max_length: int,
    precision: int,
    scale: int,
) -> str:
    """Build a formatted T-SQL type string from ``sys.columns`` / ``sys.types`` fields.

    Handles the pitfalls documented in ``sys.columns``:

    - ``nchar`` / ``nvarchar``: ``max_length`` is in **bytes** (÷2 for char length).
    - ``max_length = -1`` means ``MAX`` (applies to ``varchar``, ``nvarchar``,
      ``varbinary``).
    - ``decimal`` / ``numeric``: ``(precision, scale)`` suffix.
    - ``time`` / ``datetime2`` / ``datetimeoffset``: ``(scale)`` fractional-seconds suffix.
    - All other types: no suffix.

    Args:
        type_name: Base type name from ``sys.types.name``, e.g. ``"varchar"``.
        max_length: ``sys.columns.max_length`` value.
        precision: ``sys.columns.precision`` value.
        scale: ``sys.columns.scale`` value.

    Returns:
        A formatted type string, e.g. ``"VARCHAR(50)"``, ``"NVARCHAR(MAX)"``,
        ``"DECIMAL(18,2)"``, ``"DATETIME2(7)"``, ``"INT"``.
    """
    upper = type_name.upper()

    if type_name in _NCHAR_TYPES:
        # max_length is in bytes; divide by 2 for character length.
        suffix = "MAX" if max_length == -1 else str(max_length // 2)
        return f"{upper}({suffix})"

    if type_name in _LENGTH_TYPES:
        suffix = "MAX" if max_length == -1 else str(max_length)
        return f"{upper}({suffix})"

    if type_name in _PRECISION_SCALE_TYPES:
        return f"{upper}({precision},{scale})"

    if type_name in _SCALE_TYPES:
        return f"{upper}({scale})"

    return upper


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_object_columns(
    target: SqlTarget,
    schema: str,
    object_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[dict[str, object]]:
    """Return column metadata for a SQL table or view via ``sys.columns``.

    Queries ``sys.columns JOIN sys.types JOIN sys.objects JOIN sys.schemas``
    using *schema* and *object_name* as bound parameters (no SQL string
    concatenation).  Works on both Data Warehouses and SQL Analytics Endpoints
    (column metadata is readable on both) — no ``_assert_not_sql_endpoint``
    guard is applied.

    The formatted ``data_type`` field is built by :func:`format_data_type` and
    handles ``nchar``/``nvarchar`` byte-to-char conversion, ``-1`` → ``MAX``,
    ``decimal``/``numeric`` precision/scale, and ``datetime2``/``time``/
    ``datetimeoffset`` scale suffixes.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: The schema name.  Must pass :func:`~fabric_dw.identifiers.validate_identifier`.
        object_name: The table or view name.  Must pass
            :func:`~fabric_dw.identifiers.validate_identifier`.
        mode: The credential mode for Entra authentication.

    Returns:
        A list of column dicts (one per column), each containing:

        - ``ordinal`` (:class:`int`) — ``column_id`` (1-based position).
        - ``name`` (:class:`str`) — column name.
        - ``data_type`` (:class:`str`) — formatted type string, e.g. ``VARCHAR(50)``.
        - ``nullable`` (:class:`bool`) — whether the column is nullable.
        - ``collation_name`` (:class:`str` | ``None``) — collation, if applicable.
        - ``is_identity`` (:class:`bool`) — whether the column is an identity column.
        - ``is_computed`` (:class:`bool`) — whether the column is computed.

        The list is ordered by ``column_id`` (ordinal position).  Returns an
        empty list when no object with that schema/name exists.

    Raises:
        ValueError: If *schema* or *object_name* fails identifier validation.
        PermissionDeniedError: If the driver reports a permission error.
    """
    validate_identifier(schema)
    validate_identifier(object_name)

    def _run() -> list[dict[str, object]]:
        cols, rows = run_query(
            target,
            _GET_COLUMNS_SQL,
            params=[schema, object_name],
            mode=mode,
        )
        if not rows:
            return []
        results: list[dict[str, object]] = []
        for row in rows:
            data = dict(zip(cols, row, strict=True))
            type_name = str(data["type_name"])
            max_length = int(data["max_length"])  # type: ignore[arg-type]
            precision = int(data["precision"])  # type: ignore[arg-type]
            scale = int(data["scale"])  # type: ignore[arg-type]
            results.append(
                {
                    "ordinal": int(data["ordinal"]),  # type: ignore[arg-type]
                    "name": str(data["name"]),
                    "data_type": format_data_type(type_name, max_length, precision, scale),
                    "nullable": bool(data["nullable"]),
                    "collation_name": (
                        str(data["collation_name"]) if data["collation_name"] is not None else None
                    ),
                    "is_identity": bool(data["is_identity"]),
                    "is_computed": bool(data["is_computed"]),
                }
            )
        return results

    result = await asyncio.to_thread(_run)
    _log.debug("get_object_columns: %s.%s → %d columns", schema, object_name, len(result))
    return result


async def get_object_columns_or_raise(
    target: SqlTarget,
    schema: str,
    object_name: str,
    *,
    kind_label: str = "object",
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[dict[str, object]]:
    """Like :func:`get_object_columns` but raises :class:`~fabric_dw.exceptions.NotFoundError`
    when the object does not exist (empty result).

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: The schema name.
        object_name: The table or view name.
        kind_label: Human-readable object kind for the error message (e.g. ``"table"``
            or ``"view"``).  Defaults to ``"object"``.
        mode: The credential mode for Entra authentication.

    Returns:
        A non-empty list of column dicts ordered by ordinal position.

    Raises:
        NotFoundError: If no object with that schema/name exists.
        ValueError: If *schema* or *object_name* fails identifier validation.
        PermissionDeniedError: If the driver reports a permission error.
    """
    columns = await get_object_columns(target, schema, object_name, mode=mode)
    if not columns:
        msg = f"{kind_label.capitalize()} [{schema}].[{object_name}] not found"
        raise NotFoundError(msg)
    return columns
