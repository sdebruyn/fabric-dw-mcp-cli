"""Generic SQL execution service for Fabric Data Warehouses and SQL Endpoints.

Public API
----------
- :func:`execute` — run an arbitrary SQL batch and return the last result set.
- :func:`get_plan` — capture the estimated SHOWPLAN_XML for a query without executing it.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import closing
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from fabric_dw import sql
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import FabricError, PermissionDeniedError
from fabric_dw.models import SqlResult
from fabric_dw.sql import SqlTarget

__all__ = ["execute", "get_plan"]

# Column-name suffix applied to varbinary columns so callers can detect them.
_BINARY_SUFFIX = "__base64"

# Shared hint appended to PermissionDeniedError raised from SQL execution.
_SQL_EXEC_PERMISSION_HINT = (
    "The caller must have at least READ permission on the "
    "warehouse/SQL endpoint. See "
    "https://learn.microsoft.com/fabric/data-warehouse/sql-permissions"
)


def _serialize_value(value: object) -> object:
    """Convert a raw driver value to a JSON-serialisable scalar.

    - ``datetime`` → ISO-8601 string (checked before ``date``/``time`` because
      ``datetime`` is a subclass of ``date``).
    - ``date`` → ISO-8601 date string (``YYYY-MM-DD``).
    - ``time`` → ISO-8601 time string (``HH:MM:SS[.ffffff]``).
    - ``Decimal`` → string representation.
    - ``bytes`` / ``bytearray`` → base64-encoded string (column name also tagged).
    - ``UUID`` → canonical hyphenated string representation.
    - Everything else is returned unchanged (driver already returns str/int/float/bool/None).
    """
    if isinstance(value, datetime):  # Must precede date — datetime subclasses date.
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, UUID):
        return str(value)
    return value


def _detect_binary_indices(
    description: list[tuple[str, object]] | None,
    rows: list[tuple[object, ...]],
) -> set[int]:
    """Return the set of column indices whose values are binary (bytes/bytearray).

    Detection strategy (in priority order):

    1. **cursor.description type code** — if the driver populates ``type_code``
       (``description[i][1]``) as the ``Binary`` type object, use it.  This is
       reliable and does not require scanning rows.
    2. **All-row scan fallback** — scan every row for any ``bytes``/``bytearray``
       value.  This catches columns where the first row is ``NULL`` but later
       rows carry binary data — the first-row-only heuristic would miss these.

    Args:
        description: The ``cursor.description`` list, or ``None`` if unavailable.
        rows: The fetched rows.

    Returns:
        A set of column indices that contain binary data.
    """
    # Strategy 1: type code from cursor.description.
    # When the driver populates type_code for every column, the description is
    # authoritative — if no binary columns are found, there are none.  We only
    # fall through to Strategy 2 if the driver leaves type_code as None for ALL
    # columns (i.e. the description carries no useful type information at all).
    if description:
        binary_indices: set[int] = set()
        has_any_type_code = False
        for i, col_desc in enumerate(description):
            # DB-API 2.0: col_desc[1] is the type_code.
            # mssql_python exposes a Binary type object; we check for it by
            # comparing against both the canonical name and a bytes-based sentinel.
            type_code = col_desc[1] if len(col_desc) > 1 else None
            if type_code is not None:
                has_any_type_code = True
                type_name = getattr(type_code, "__name__", None) or str(type_code)
                if "binary" in type_name.lower() or "bytes" in type_name.lower():
                    binary_indices.add(i)
        if has_any_type_code:
            # Description was populated — trust it even if no binary columns found.
            return binary_indices

    # Strategy 2: all-row scan fallback (only reached when description is absent
    # or every type_code is None, i.e. the driver provides no type information).
    all_row_binary: set[int] = set()
    for row in rows:
        for i, val in enumerate(row):
            if isinstance(val, (bytes, bytearray)):
                all_row_binary.add(i)
    return all_row_binary


def _tag_binary_columns(
    raw_columns: list[str],
    rows: list[tuple[object, ...]],
    *,
    description: list[tuple[str, object]] | None = None,
) -> tuple[list[str], list[list[object]]]:
    """Return tagged column names and serialised rows.

    Columns that contain binary (``bytes``/``bytearray``) data are renamed
    with the ``__base64`` suffix so callers can identify binary columns without
    inspecting every cell.

    Binary detection uses :func:`_detect_binary_indices`, which first tries
    ``cursor.description`` type codes (locale-independent, no row scan) and
    falls back to scanning *all* rows (not just the first, so ``NULL``-prefixed
    binary columns are correctly detected).

    Args:
        raw_columns: The raw column-name list.
        rows: The fetched rows.
        description: Optional ``cursor.description`` for type-code detection.

    Returns:
        A ``(tagged_cols, serialised_rows)`` tuple.
    """
    binary_indices = _detect_binary_indices(description, rows)

    tagged_cols = [
        f"{col}{_BINARY_SUFFIX}" if i in binary_indices else col
        for i, col in enumerate(raw_columns)
    ]
    serialised_rows: list[list[object]] = [[_serialize_value(v) for v in row] for row in rows]
    return tagged_cols, serialised_rows


async def execute(
    target: SqlTarget,
    query: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
    row_limit: int | None = None,
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
        row_limit: When set, fetch at most ``row_limit + 1`` rows from the
            driver so the caller can detect truncation without loading the
            entire result set into memory.  Pass ``None`` (default) to
            fetch all rows (``cursor.fetchall()``).

    Returns:
        A :class:`~fabric_dw.models.SqlResult` with ``columns``, ``rows``,
        and ``rowcount``.

    Raises:
        AuthError: If the driver raises an authentication failure (expired or
            missing token).
        PermissionDeniedError: If the driver raises a SQL permission-denial error.
            The exception message contains a hint pointing to the
            documentation for the required permissions.
        Exception: Any other driver error is propagated unchanged.
    """

    def _run() -> SqlResult:
        conn, _, _, _ = sql._with_connect_retry(target, mode, autocommit=False)
        with closing(conn):
            cursor = conn.cursor()
            with closing(cursor):
                try:
                    cursor.execute(query)
                except Exception as exc:
                    mapped = sql.map_driver_error(exc)
                    if mapped is not None:
                        if isinstance(mapped, PermissionDeniedError):
                            raise PermissionDeniedError(
                                str(mapped),
                                hint=_SQL_EXEC_PERMISSION_HINT,
                            ) from exc
                        raise mapped from exc
                    raise

                # Capture result sets in order, keeping only the last one that has
                # a description.  DB-API 2.0 cursors position on the *first* result
                # set after execute(); calling nextset() advances to the next one.
                # The old pattern (advance-until-False then read description) was
                # wrong: for a single-result-set SELECT, nextset() returns False
                # immediately AND leaves the cursor past the only result set, so
                # cursor.description becomes None and fetchall() returns [].
                # The correct approach is capture-then-advance: read the current
                # result set (if it has a description) before calling nextset().
                last: tuple[list[str], list[list[object]], int] | None = None
                while True:
                    if cursor.description is not None:
                        raw_cols = [col[0] for col in cursor.description]
                        if row_limit is not None:
                            raw_rows: list[tuple[object, ...]] = [
                                tuple(r) for r in cursor.fetchmany(row_limit + 1)
                            ]
                        else:
                            raw_rows = [tuple(r) for r in cursor.fetchall()]
                        tagged_cols, serialised_rows = _tag_binary_columns(
                            raw_cols,
                            raw_rows,
                            description=list(cursor.description),
                        )
                        rc: int = getattr(cursor, "rowcount", -1)
                        if rc is None or rc == -1:
                            rc = len(serialised_rows)
                        last = (tagged_cols, serialised_rows, rc)
                    if not cursor.nextset():
                        break

                if last is None:
                    # DDL / DML — no result set produced a description.
                    rowcount: int = getattr(cursor, "rowcount", -1)
                    if rowcount is None:
                        rowcount = -1
                    return SqlResult(columns=[], rows=[], rowcount=rowcount)

                cols, rows, rowcount = last
                return SqlResult(columns=cols, rows=rows, rowcount=rowcount)

    return await asyncio.to_thread(_run)


async def get_plan(
    target: SqlTarget,
    query: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> str:
    """Capture the estimated SHOWPLAN_XML for *query* without executing it.

    Issues ``SET SHOWPLAN_XML ON`` on the connection before running *query*,
    then concatenates the first column of every returned row (the standard SQL
    Server Showplan column, read positionally to avoid locale/version sensitivity).
    ``SET SHOWPLAN_XML OFF`` is guaranteed to run in a ``finally`` block before
    the connection is closed or returned to the pool.

    **Pool-safety guarantee:** if ``SET SHOWPLAN_XML OFF`` itself raises, the
    connection is marked for physical discard (via
    :meth:`~fabric_dw.sql._PooledConnection.mark_discard`) before ``close()``
    is called.  This prevents a poisoned connection with
    ``SHOWPLAN_XML`` still ``ON`` from being checked back into the pool and
    corrupting the next query on that connection.

    The query is **not** executed under ``SHOWPLAN_XML`` — only the estimated
    plan is returned.  This means DDL/DML query text is safe to plan without
    modifying any data.

    Args:
        target: The warehouse or SQL analytics endpoint to connect to.
        query: The SQL statement to plan.
        mode: The credential mode for Entra authentication.

    Returns:
        The SHOWPLAN_XML string (one or more XML fragments concatenated).

    Raises:
        AuthError: If the driver raises an authentication failure.
        PermissionDeniedError: If the driver raises a SQL permission-denial error.
            The exception message contains a hint pointing to the documentation
            for the required permissions.
        Exception: Any other driver error is propagated unchanged.
    """

    def _run() -> str:
        conn, _, _, _ = sql._with_connect_retry(target, mode, autocommit=False)
        with closing(conn):
            cursor = conn.cursor()
            with closing(cursor):
                _exc_in_flight: bool = False
                plan_parts: list[str] = []
                try:
                    cursor.execute("SET SHOWPLAN_XML ON")
                    cursor.execute(query)
                    # Normalise to real tuples so the list[tuple[...]] annotation
                    # is honest regardless of which driver Row type is returned
                    # (mssql_python.Row is iterable but not a tuple subclass).
                    rows: list[tuple[object, ...]] = [tuple(r) for r in cursor.fetchall()]
                    # Read the first column positionally (row[0]) — the standard
                    # Showplan column name varies by SQL Server version/locale.
                    plan_parts = [str(row[0]) for row in rows if row[0] is not None]
                except BaseException as exc:
                    _exc_in_flight = True
                    if isinstance(exc, Exception):
                        mapped = sql.map_driver_error(exc)
                        if mapped is not None:
                            if isinstance(mapped, PermissionDeniedError):
                                raise PermissionDeniedError(
                                    str(mapped),
                                    hint=_SQL_EXEC_PERMISSION_HINT,
                                ) from exc
                            raise mapped from exc
                    raise
                finally:
                    # Always attempt to restore SHOWPLAN_XML to OFF before the
                    # connection is closed or returned to the pool.  If the OFF
                    # command itself fails, mark the connection for physical discard
                    # so it is never returned to the pool with SHOWPLAN_XML still ON
                    # (which would poison the next query on that connection).
                    #
                    # When the main body already raised an exception (_exc_in_flight),
                    # we suppress the OFF exception so the original error propagates
                    # unmodified.  When the main body succeeded, we let the OFF
                    # exception propagate (which also ensures mark_discard is called).
                    #
                    # BaseException (KeyboardInterrupt, SystemExit) that fires inside
                    # the try body also sets _exc_in_flight via the `except BaseException`
                    # block above, so the OFF failure is suppressed and mark_discard is
                    # still called in that case too.
                    try:
                        cursor.execute("SET SHOWPLAN_XML OFF")
                    except Exception:
                        # Discard the connection: it must not re-enter the pool.
                        if isinstance(conn, sql._PooledConnection):
                            conn.mark_discard()
                        if not _exc_in_flight:
                            # No original exception — propagate the OFF failure.
                            raise
                        # Original exception already in flight — suppress OFF failure
                        # so the original propagates cleanly.

                # Raise after the finally block so the empty-plan check does not
                # interfere with the _exc_in_flight / OFF-failure suppression logic.
                if not plan_parts:
                    raise FabricError(
                        "No execution plan was returned — the statement type may not "
                        "support SHOWPLAN_XML (e.g. SET, PRINT, or comment-only batches)."
                    )
                return "".join(plan_parts)

    return await asyncio.to_thread(_run)
