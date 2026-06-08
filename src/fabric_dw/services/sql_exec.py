"""Generic SQL execution service for Fabric Data Warehouses and SQL Endpoints.

Public API
----------
- :func:`execute` — run an arbitrary SQL batch and return the last result set.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import closing
from datetime import datetime
from decimal import Decimal

from fabric_dw import sql
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import PermissionDenied
from fabric_dw.models import SqlResult
from fabric_dw.sql import SqlTarget

__all__ = ["execute"]

# Column-name suffix applied to varbinary columns so callers can detect them.
_BINARY_SUFFIX = "__base64"


def _serialize_value(value: object) -> object:
    """Convert a raw driver value to a JSON-serialisable scalar.

    - ``datetime`` → ISO-8601 string.
    - ``Decimal`` → string representation.
    - ``bytes`` → base64-encoded string (column name will also be tagged).
    - Everything else is returned unchanged (driver already returns str/int/float/bool/None).
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(value).decode("ascii")
    return value


def _tag_binary_columns(
    raw_columns: list[str],
    rows: list[tuple[object, ...]],
) -> tuple[list[str], list[list[object]]]:
    """Return tagged column names and serialised rows.

    Columns whose corresponding values are ``bytes``/``bytearray`` in the
    *first non-None row* are renamed with the ``__base64`` suffix so callers
    can identify binary columns without inspecting every cell.

    If *rows* is empty the column names are returned unchanged (we have no
    type information without a result set).
    """
    if not rows:
        return list(raw_columns), []

    # Determine which column indices are binary by inspecting the first row.
    binary_indices: set[int] = set()
    for row in rows:
        for i, val in enumerate(row):
            if isinstance(val, (bytes, bytearray)):
                binary_indices.add(i)
        break  # Only inspect first row for type discovery

    tagged_cols = [
        f"{col}{_BINARY_SUFFIX}" if i in binary_indices else col
        for i, col in enumerate(raw_columns)
    ]
    serialised_rows: list[list[object]] = [
        [_serialize_value(v) for v in row] for row in rows
    ]
    return tagged_cols, serialised_rows


async def execute(
    target: SqlTarget,
    query: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> SqlResult:
    """Execute *query* against *target* and return the last result set.

    The function executes the SQL batch as-is (no read-only enforcement).
    Multi-statement batches are supported; only the **last** result set is
    returned.  DDL/DML statements that produce no result set return an empty
    ``SqlResult`` (``columns=[]``, ``rows=[]``).

    ``datetime`` and ``Decimal`` values are serialised to strings before being
    placed in ``SqlResult.rows``.  ``bytes`` / ``varbinary`` values are
    base64-encoded; the corresponding column name receives a ``__base64``
    suffix so callers can identify binary columns.

    Args:
        target: The warehouse or SQL analytics endpoint to connect to.
        query: The SQL batch to execute.  May be a single statement or
            multiple statements separated by semicolons or ``GO`` batches.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.SqlResult` with ``columns``, ``rows``,
        and ``rowcount``.

    Raises:
        PermissionDenied: If the driver raises a permission or auth error.
            The exception message contains a hint pointing to the
            documentation for the required permissions.
        Exception: Any other driver error is propagated unchanged.
    """

    def _run() -> SqlResult:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(query)
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    msg = (
                        f"{mapped}  "
                        "Hint: the caller must have at least READ permission on the "
                        "warehouse/SQL endpoint. See "
                        "https://learn.microsoft.com/fabric/data-warehouse/sql-permissions"
                    )
                    raise PermissionDenied(msg) from exc
                raise

            rowcount: int = getattr(cursor, "rowcount", -1)
            if rowcount is None:
                rowcount = -1

            if cursor.description is None:
                # DDL / DML — no result set.
                return SqlResult(columns=[], rows=[], rowcount=rowcount)

            raw_cols = [col[0] for col in cursor.description]
            raw_rows: list[tuple[object, ...]] = cursor.fetchall()
            tagged_cols, serialised_rows = _tag_binary_columns(raw_cols, raw_rows)
            if rowcount == -1:
                rowcount = len(serialised_rows)
            return SqlResult(columns=tagged_cols, rows=serialised_rows, rowcount=rowcount)

    return await asyncio.to_thread(_run)
