"""CRUD operations for SQL views on Fabric Data Warehouses and SQL Analytics Endpoints.

Public API
----------
- :func:`validate_identifier` — re-exported from :mod:`fabric_dw.identifiers`.
- :func:`list_views`          — list all views (optionally filtered by schema).
- :func:`get_view`            — fetch a single view with its definition.
- :func:`create_view`         — issue CREATE VIEW … AS <select_body>.
- :func:`update_view`         — issue CREATE OR ALTER VIEW … AS <select_body>.
- :func:`drop_view`           — issue DROP VIEW.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import cast

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFound
from fabric_dw.identifiers import quote_identifier, validate_identifier
from fabric_dw.models import View
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "create_view",
    "drop_view",
    "get_view",
    "list_views",
    "read_view",
    "update_view",
    "validate_identifier",
]

# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

# TOP count is injected as a literal int (not user input) — safe.
_READ_VIEW_SQL = "SELECT TOP ({count}) * FROM {schema_q}.{view_q};"

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
        PermissionDenied: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
    """
    if schema is not None:
        validate_identifier(schema)

    # Identifier is validated above; safe to embed via parameter binding.
    # For schema filter we use ? binding; for the "all schemas" branch we use a
    # tautology literal that is never user-controlled.
    if schema is not None:
        schema_filter = "s.name = ?"
        filter_params: list[object] = [schema]
    else:
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
        NotFound: If the view does not exist (zero rows AND zero columns).
        PermissionDenied: If the driver reports a permission error.
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
        cols, rows = run_query(target, read_sql, mode=mode)
        if not cols:
            msg = f"View [{schema}].[{view_name}] not found"
            raise NotFound(msg)
        return cols, list(rows)

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
        NotFound: If no view with that schema/name exists.
        PermissionDenied: If the driver reports a permission error.
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
            raise NotFound(msg)
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
        select_body: The free-form SELECT statement.  Not validated — the caller
            owns the SQL (same trust model as ``sql exec``).
        mode: The credential mode for Entra authentication.

    Returns:
        The newly-created :class:`~fabric_dw.models.View` (fetched after DDL).

    Raises:
        ValueError: If *schema* or *view_name* fails identifier validation.
        PermissionDenied: If the driver reports a CREATE VIEW permission error.
    """
    validate_identifier(schema)
    validate_identifier(view_name)

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
        select_body: The free-form SELECT statement.  Caller-owned.
        mode: The credential mode for Entra authentication.

    Returns:
        The updated :class:`~fabric_dw.models.View` (fetched after DDL).

    Raises:
        ValueError: If *schema* or *view_name* fails identifier validation.
        PermissionDenied: If the driver reports an ALTER VIEW permission error.
    """
    validate_identifier(schema)
    validate_identifier(view_name)

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
        PermissionDenied: If the driver reports a DROP VIEW permission error.
    """
    validate_identifier(schema)
    validate_identifier(view_name)

    ddl = f"DROP VIEW {quote_identifier(schema)}.{quote_identifier(view_name)}"

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)
