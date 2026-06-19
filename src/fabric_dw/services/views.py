"""CRUD operations for SQL views on Fabric Data Warehouses and SQL Analytics Endpoints.

Public API
----------
- :func:`validate_identifier` — re-exported from :mod:`fabric_dw.identifiers`.
- :func:`list_views`          — list all views (optionally filtered by schema).
- :func:`read_view`           — ``SELECT TOP (N) * FROM [schema].[view]``.
- :func:`count_view_rows`     — ``SELECT COUNT_BIG(*) FROM [schema].[view]``.
- :func:`get_view`            — fetch a single view with its definition.
- :func:`create_view`         — issue CREATE VIEW … AS <select_body>.
- :func:`update_view`         — issue CREATE OR ALTER VIEW … AS <select_body>.
- :func:`drop_view`           — issue DROP VIEW.
- :func:`rename_view`         — rename a view via sp_rename (both DW and SQL endpoint).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import cast

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFoundError
from fabric_dw.identifiers import parse_qualified_name, quote_identifier, validate_identifier
from fabric_dw.models import View
from fabric_dw.services._helpers import reject_non_select
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "count_view_rows",
    "create_view",
    "drop_view",
    "get_view",
    "list_views",
    "read_view",
    "rename_view",
    "update_view",
    "validate_identifier",
]

# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

# TOP count is injected as a literal int (not user input) — safe.
_READ_VIEW_SQL = "SELECT TOP ({count}) * FROM {schema_q}.{view_q};"

# COUNT_BIG(*) is bigint-safe (avoids INT overflow on wide views).
_COUNT_VIEW_SQL = "SELECT COUNT_BIG(*) AS row_count FROM {schema_q}.{view_q};"

_LIST_VIEWS_SQL = """\
SELECT
    s.name AS schema_name,
    v.name,
    v.create_date AS created,
    v.modify_date AS modified
FROM sys.views v
JOIN sys.schemas s ON s.schema_id = v.schema_id
WHERE ({schema_filter})
ORDER BY s.name, v.name;
"""

_GET_VIEW_SQL = """\
SELECT
    s.name AS schema_name,
    v.name,
    v.create_date AS created,
    v.modify_date AS modified,
    m.definition
FROM sys.views v
JOIN sys.schemas s ON s.schema_id = v.schema_id
JOIN sys.sql_modules m ON m.object_id = v.object_id
WHERE s.name = ? AND v.name = ?;
"""

# sp_rename takes string arguments (not identifiers) → bind as ? parameters.
# @objname = qualified old name ('schema.old_view'), @newname = bare new name.
# sp_rename cannot move across schemas, so @newname must be unqualified.
_SP_RENAME_SQL = "EXEC sp_rename ?, ?, 'OBJECT'"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_view(cols: list[str], row: tuple[object, ...]) -> View:
    """Build a :class:`View` from a column-name list and a result row tuple."""
    data = dict(zip(cols, row, strict=True))
    schema_name = str(data["schema_name"])
    name = str(data["name"])
    raw_def = data.get("definition") if "definition" in data else None
    definition = cast("str | None", raw_def)
    return View(
        schema_name=schema_name,
        name=name,
        qualified_name=f"{schema_name}.{name}",
        definition=definition,
        created=cast(datetime, data["created"]),
        modified=cast(datetime, data["modified"]),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_views(
    target: SqlTarget,
    *,
    schema: str | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[View]:
    """Return all views on *target*, optionally filtered to a single *schema*.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: When provided, only views in this schema are returned.
            Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Returns:
        A (possibly empty) list of :class:`~fabric_dw.models.View` instances.

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
        # "all schemas" branch uses a tautology literal that is never user-controlled.
        schema_filter = "1=1"
        filter_params = []

    list_sql = _LIST_VIEWS_SQL.format(schema_filter=schema_filter)

    def _run() -> list[View]:
        cols, rows = run_query(
            target,
            list_sql,
            params=filter_params or None,
            mode=mode,
        )
        return [_row_to_view(cols, r) for r in rows]

    return await asyncio.to_thread(_run)


async def read_view(
    target: SqlTarget,
    schema: str,
    view_name: str,
    *,
    count: int = 10,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> tuple[list[str], list[tuple[object, ...]]]:
    """Return up to *count* rows from *schema*.*view_name*.

    The result is a ``(columns, rows)`` pair suitable for passing to
    :mod:`fabric_dw.sql_io` for materialisation via Arrow.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        view_name: The view name.  Must pass :func:`validate_identifier`.
        count: Maximum number of rows to return (default 10).
        mode: The credential mode for Entra authentication.

    Returns:
        A ``(columns, rows)`` tuple where *columns* is a list of column name
        strings and *rows* is a list of row tuples.

    Raises:
        ValueError: If *schema* or *view_name* fails identifier validation.
        NotFoundError: If the view does not exist (SQL Server error 208 mapped
            upstream by :func:`~fabric_dw.sql.run_query`).
        PermissionDeniedError: If the driver reports a permission error.
    """
    validate_identifier(schema)
    validate_identifier(view_name)

    # Identifiers are validated; bracket-quote them for the FROM clause.
    # TOP count is an internal int (not user-supplied string), safe to embed.
    read_sql = _READ_VIEW_SQL.format(
        count=int(count),
        schema_q=quote_identifier(schema),
        view_q=quote_identifier(view_name),
    )

    def _run() -> tuple[list[str], list[tuple[object, ...]]]:
        # run_query raises NotFoundError (via map_driver_error) for SQL error 208
        # (invalid object name) before returning.  The empty-cols guard below is
        # a secondary check that mirrors read_table for consistency: if the driver
        # returns no column metadata (description is None), treat it as not found.
        cols, rows = run_query(target, read_sql, mode=mode)
        if not cols:
            msg = f"View [{schema}].[{view_name}] not found"
            raise NotFoundError(msg)
        return cols, list(rows)

    return await asyncio.to_thread(_run)


async def count_view_rows(
    target: SqlTarget,
    schema: str,
    view_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> int:
    """Return the total row count of *schema*.*view_name* via ``COUNT_BIG(*)``.

    Uses ``COUNT_BIG(*)`` rather than ``COUNT(*)`` to avoid integer overflow on
    views that return more than 2 147 483 647 rows.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        view_name: The view name.  Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Returns:
        The number of rows in the view as a Python :class:`int`.

    Raises:
        ValueError: If *schema* or *view_name* fails identifier validation.
        FabricError: If the view does not exist or the engine reports an error.
        PermissionDeniedError: If the driver reports a permission error.
    """
    validate_identifier(schema)
    validate_identifier(view_name)

    count_sql = _COUNT_VIEW_SQL.format(
        schema_q=quote_identifier(schema),
        view_q=quote_identifier(view_name),
    )

    def _run() -> int:
        _cols, rows = run_query(target, count_sql, mode=mode)
        return int(rows[0][0])

    return await asyncio.to_thread(_run)


async def get_view(
    target: SqlTarget,
    schema: str,
    view_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> View:
    """Fetch a single view with its ``sys.sql_modules`` definition.

    Args:
        target: The warehouse to query.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        view_name: The view name.  Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.View` with ``definition`` populated.

    Raises:
        ValueError: If *schema* or *view_name* fails identifier validation.
        NotFoundError: If no view with that schema/name exists.
        PermissionDeniedError: If the driver reports a permission error.
    """
    validate_identifier(schema)
    validate_identifier(view_name)

    def _run() -> View:
        cols, rows = run_query(
            target,
            _GET_VIEW_SQL,
            params=[schema, view_name],
            mode=mode,
        )
        if not rows:
            msg = f"View [{schema}].[{view_name}] not found"
            raise NotFoundError(msg)
        return _row_to_view(cols, rows[0])

    return await asyncio.to_thread(_run)


async def create_view(
    target: SqlTarget,
    schema: str,
    view_name: str,
    select_body: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> View:
    """Create a new view via ``CREATE VIEW [<schema>].[<view>] AS <select_body>``.

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        view_name: The view name.  Must pass :func:`validate_identifier`.
        select_body: The SELECT statement (or CTE) used as the view body.
            The first non-comment keyword **must** be ``SELECT`` or ``WITH``
            (for CTE-based queries); anything else raises :class:`ValueError`.
        mode: The credential mode for Entra authentication.

    Returns:
        The newly-created :class:`~fabric_dw.models.View` (fetched after DDL).

    Raises:
        ValueError: If *schema* or *view_name* fails identifier validation, or
            if *select_body* does not start with SELECT or WITH (CTE).
        PermissionDeniedError: If the driver reports a CREATE VIEW permission error.
    """
    validate_identifier(schema)
    validate_identifier(view_name)
    reject_non_select(select_body)

    ddl = f"CREATE VIEW {quote_identifier(schema)}.{quote_identifier(view_name)} AS {select_body}"

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)
    return await get_view(target, schema, view_name, mode=mode)


async def update_view(
    target: SqlTarget,
    schema: str,
    view_name: str,
    select_body: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> View:
    """Redefine a view via ``CREATE OR ALTER VIEW [<schema>].[<view>] AS <select_body>``.

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        view_name: The view name.  Must pass :func:`validate_identifier`.
        select_body: The SELECT statement (or CTE) used as the view body.
            The first non-comment keyword **must** be ``SELECT`` or ``WITH``
            (for CTE-based queries); anything else raises :class:`ValueError`.
        mode: The credential mode for Entra authentication.

    Returns:
        The updated :class:`~fabric_dw.models.View` (fetched after DDL).

    Raises:
        ValueError: If *schema* or *view_name* fails identifier validation, or
            if *select_body* does not start with SELECT or WITH (CTE).
        PermissionDeniedError: If the driver reports an ALTER VIEW permission error.
    """
    validate_identifier(schema)
    validate_identifier(view_name)
    reject_non_select(select_body)

    ddl = (
        f"CREATE OR ALTER VIEW {quote_identifier(schema)}.{quote_identifier(view_name)}"
        f" AS {select_body}"
    )

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)
    return await get_view(target, schema, view_name, mode=mode)


async def drop_view(
    target: SqlTarget,
    schema: str,
    view_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop a view via ``DROP VIEW [<schema>].[<view>]``.

    Args:
        target: The warehouse to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        view_name: The view name.  Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Raises:
        ValueError: If *schema* or *view_name* fails identifier validation.
        PermissionDeniedError: If the driver reports a DROP VIEW permission error.
    """
    validate_identifier(schema)
    validate_identifier(view_name)

    ddl = f"DROP VIEW {quote_identifier(schema)}.{quote_identifier(view_name)}"

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)


async def rename_view(
    target: SqlTarget,
    qualified: str,
    new_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> View:
    """Rename a view via ``EXEC sp_rename @objname, @newname, 'OBJECT'``.

    Works on both Data Warehouses and SQL Analytics Endpoints — no DW-only guard
    is applied.

    ``sp_rename`` takes names as STRING ARGUMENTS (not SQL identifiers), so both
    the old qualified name and the new bare name are bound as ``?`` parameters.
    The new name must be unqualified (no dot) because ``sp_rename`` cannot move
    a view across schemas.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        qualified: Current qualified name of the view, e.g. ``dbo.vw_sales``.
            Parsed via :func:`~fabric_dw.identifiers.parse_qualified_name`.
        new_name: New bare (unqualified) view name.  Must pass
            :func:`validate_identifier` and must not contain a dot.
        mode: The credential mode for Entra authentication.

    Returns:
        The renamed :class:`~fabric_dw.models.View` (fetched after rename using
        the original schema and the new name).

    Raises:
        ValueError: If *qualified* cannot be parsed, if either identifier part
            fails validation, or if *new_name* is schema-qualified (contains a
            dot).
        NotFoundError: If the renamed view cannot be found after the rename.
        PermissionDeniedError: If the driver reports a permission error.
    """
    schema, old_view = parse_qualified_name(qualified)
    validate_identifier(schema)
    validate_identifier(old_view)

    if "." in new_name:
        msg = (
            f"New name {new_name!r} must not be schema-qualified; "
            "sp_rename cannot move a view to a different schema"
        )
        raise ValueError(msg)
    validate_identifier(new_name)

    # @objname = 'schema.oldview', @newname = 'newview' — bound as ? params.
    old_qualified = f"{schema}.{old_view}"

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
        return await get_view(target, schema, new_name, mode=mode)
    except NotFoundError:
        msg = f"View [{schema}].[{new_name}] not found after rename"
        raise NotFoundError(msg) from None
