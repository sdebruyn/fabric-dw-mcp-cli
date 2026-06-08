"""CRUD operations for SQL views on Fabric Data Warehouses and SQL Analytics Endpoints.

Public API
----------
- :func:`validate_identifier` — reject dangerous/malformed SQL identifiers.
- :func:`list_views`          — list all views (optionally filtered by schema).
- :func:`get_view`            — fetch a single view with its definition.
- :func:`create_view`         — issue CREATE VIEW … AS <select_body>.
- :func:`update_view`         — issue CREATE OR ALTER VIEW … AS <select_body>.
- :func:`drop_view`           — issue DROP VIEW.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import closing
from datetime import datetime
from typing import cast

from fabric_dw import sql
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFound
from fabric_dw.models import View
from fabric_dw.sql import SqlTarget

__all__ = [
    "create_view",
    "drop_view",
    "get_view",
    "list_views",
    "update_view",
    "validate_identifier",
]

# ---------------------------------------------------------------------------
# Identifier validator
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def validate_identifier(name: str) -> str:
    """Validate that *name* is a safe SQL identifier segment.

    Accepted pattern: ``[A-Za-z_][A-Za-z0-9_]{0,127}`` (max 128 chars).

    Explicit fast-path rejections before the regex (belt-and-suspenders):
    - ``]`` — closes a bracket-quoted identifier; enables injection.
    - ``;`` — statement separator.
    - ``--`` — line comment.

    Args:
        name: The raw identifier string supplied by the caller.

    Returns:
        *name* unchanged if valid.

    Raises:
        ValueError: If *name* contains dangerous characters or does not match
            the allowed pattern.
    """
    if "]" in name or ";" in name or "--" in name:
        msg = f"Invalid SQL identifier {name!r}: contains forbidden character(s)"
        raise ValueError(msg)
    if not _IDENTIFIER_RE.match(name):
        msg = f"Invalid SQL identifier {name!r}: must match [A-Za-z_][A-Za-z0-9_]{{0,127}}"
        raise ValueError(msg)
    return name


# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

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
WHERE s.name = '{schema}' AND v.name = '{view}';
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

    schema_filter = f"s.name = '{schema}'" if schema is not None else "1=1"
    list_sql = _LIST_VIEWS_SQL.format(schema_filter=schema_filter)

    def _run() -> list[View]:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(list_sql)
                cols = [c[0] for c in (cursor.description or [])]
                rows = cursor.fetchall()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise
            return [_row_to_view(cols, r) for r in rows]

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

    get_sql = _GET_VIEW_SQL.format(schema=schema, view=view_name)

    def _run() -> View:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(get_sql)
                cols = [c[0] for c in (cursor.description or [])]
                rows = cursor.fetchall()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise
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

    ddl = f"CREATE VIEW [{schema}].[{view_name}] AS {select_body}"

    def _run() -> None:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(ddl)
                conn.commit()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise

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

    ddl = f"CREATE OR ALTER VIEW [{schema}].[{view_name}] AS {select_body}"

    def _run() -> None:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(ddl)
                conn.commit()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise

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

    ddl = f"DROP VIEW [{schema}].[{view_name}]"

    def _run() -> None:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(ddl)
                conn.commit()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise

    await asyncio.to_thread(_run)
