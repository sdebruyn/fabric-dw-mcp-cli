"""CRUD operations for SQL schemas on Fabric Data Warehouses.

Public API
----------
- :func:`validate_identifier` — re-exported from :mod:`views` (shared validator).
- :func:`list_schemas`         — list all user-defined schemas via TDS ``sys.schemas``.
- :func:`create_schema`        — ``CREATE SCHEMA [<name>]``.
- :func:`delete_schema`        — ``DROP SCHEMA [<name>]``. Optionally cascade-drops
                                  contained tables and views first.

List-source note
----------------
No public REST endpoint exists for enumerating warehouse schemas.  This module
falls back to TDS via ``sys.schemas``, filtering out well-known system schemas.

System schema filter
--------------------
The following schemas are excluded from :func:`list_schemas` results because
they are maintained by the engine and are not user-editable:

- ``sys`` — SQL Server system catalog schema.
- ``INFORMATION_SCHEMA`` — ANSI information schema views.
- ``db_owner``, ``db_accessadmin``, ``db_securityadmin``, ``db_ddladmin``,
  ``db_backupoperator``, ``db_datareader``, ``db_datawriter``,
  ``db_denydatareader``, ``db_denydatawriter`` — fixed database role schemas
  (``db_*`` prefix).

``dbo`` is **not** filtered because it is user-writable and is the default
schema for warehouse tables.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from typing import cast

from fabric_dw import sql
from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFound
from fabric_dw.models import Schema
from fabric_dw.services.views import validate_identifier
from fabric_dw.sql import SqlTarget

__all__ = [
    "create_schema",
    "delete_schema",
    "list_schemas",
    "validate_identifier",
]

# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

#: System schemas that are never user-editable on Fabric Data Warehouses.
_SYSTEM_SCHEMAS: frozenset[str] = frozenset(
    {
        "sys",
        "INFORMATION_SCHEMA",
        "db_owner",
        "db_accessadmin",
        "db_securityadmin",
        "db_ddladmin",
        "db_backupoperator",
        "db_datareader",
        "db_datawriter",
        "db_denydatareader",
        "db_denydatawriter",
    }
)

_LIST_SCHEMAS_SQL = """\
SELECT
    s.name,
    s.principal_id
FROM sys.schemas s
WHERE s.name NOT IN ({placeholders})
  AND s.name NOT LIKE 'db[_]%'
ORDER BY s.name;
"""

_LIST_TABLES_IN_SCHEMA_SQL = """\
SELECT t.name AS obj_name, 'TABLE' AS obj_type
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE s.name = '{schema}'
UNION ALL
SELECT v.name AS obj_name, 'VIEW' AS obj_type
FROM sys.views v
JOIN sys.schemas s ON s.schema_id = v.schema_id
WHERE s.name = '{schema}';
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_schema(cols: list[str], row: tuple[object, ...]) -> Schema:
    """Build a :class:`Schema` from a column-name list and a result row tuple."""
    data = dict(zip(cols, row, strict=True))
    name = str(data["name"])
    raw_pid = data.get("principal_id")
    principal_id = int(cast("int", raw_pid)) if raw_pid is not None else None
    return Schema(name=name, principal_id=principal_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_schemas(
    target: SqlTarget,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[Schema]:
    """Return all user-defined schemas on *target*.

    System schemas (``sys``, ``INFORMATION_SCHEMA``, ``db_*`` fixed-role
    schemas) are excluded.  ``dbo`` is included because it is user-writable.

    Args:
        target: The warehouse to query.
        mode: The credential mode for Entra authentication.

    Returns:
        A (possibly empty) list of :class:`~fabric_dw.models.Schema` instances.

    Raises:
        PermissionDenied: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
    """
    placeholders = ", ".join(f"'{s}'" for s in sorted(_SYSTEM_SCHEMAS))
    list_sql = _LIST_SCHEMAS_SQL.format(placeholders=placeholders)

    def _run() -> list[Schema]:
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
            return [_row_to_schema(cols, r) for r in rows]

    return await asyncio.to_thread(_run)


async def create_schema(
    target: SqlTarget,
    name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> Schema:
    """Create a new schema via ``CREATE SCHEMA [<name>]``.

    Args:
        target: The warehouse to connect to.
        name: The schema name.  Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.Schema` reflecting the newly-created
        schema (fetched via ``sys.schemas`` after DDL).

    Raises:
        ValueError: If *name* fails identifier validation.
        PermissionDenied: If the driver reports a CREATE SCHEMA permission error.
    """
    validate_identifier(name)

    ddl = f"CREATE SCHEMA [{name}]"

    def _run_ddl() -> None:
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

    await asyncio.to_thread(_run_ddl)
    return await _fetch_schema(target, name, mode=mode)


async def delete_schema(
    target: SqlTarget,
    name: str,
    *,
    cascade: bool = False,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop a schema via ``DROP SCHEMA [<name>]``.

    If *cascade* is ``True``, all tables and views contained in the schema
    are dropped first via individual ``DROP TABLE`` / ``DROP VIEW`` statements,
    then the schema itself is dropped.

    .. caution::

        When *cascade* is ``True``, **all tables and views in the schema are
        permanently deleted along with their data**.  This operation is
        irreversible.  Confirm with the user before calling this function with
        ``cascade=True``.

    Args:
        target: The warehouse to connect to.
        name: The schema name.  Must pass :func:`validate_identifier`.
        cascade: When ``True``, drop all tables and views in the schema
            before dropping the schema itself.  Defaults to ``False``.
        mode: The credential mode for Entra authentication.

    Raises:
        ValueError: If *name* fails identifier validation.
        PermissionDenied: If the driver reports a DROP SCHEMA permission error.
    """
    validate_identifier(name)

    if cascade:
        await _drop_schema_objects(target, name, mode=mode)

    ddl = f"DROP SCHEMA [{name}]"

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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_schema(
    target: SqlTarget,
    name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> Schema:
    """Fetch a single schema record from sys.schemas to build a :class:`Schema`.

    Args:
        target: The warehouse to query.
        name: The schema name (already validated).
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.Schema` instance.

    Raises:
        NotFound: If the schema is not found after creation.
    """
    # Safe: name passes validate_identifier() before this helper is called.
    fetch_sql = (
        f"SELECT s.name, s.principal_id "  # noqa: S608  # nosec B608
        f"FROM sys.schemas s "
        f"WHERE s.name = '{name}';"
    )

    def _run() -> Schema:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(fetch_sql)
                cols = [c[0] for c in (cursor.description or [])]
                rows = cursor.fetchall()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise
            if not rows:
                msg = f"Schema [{name}] not found after creation"
                raise NotFound(msg)
            return _row_to_schema(cols, rows[0])

    return await asyncio.to_thread(_run)


async def _drop_schema_objects(
    target: SqlTarget,
    schema_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop all tables and views contained in *schema_name*.

    Enumerates objects via ``sys.tables`` and ``sys.views`` then issues
    individual ``DROP TABLE`` / ``DROP VIEW`` DDL for each.

    This is a helper for :func:`delete_schema` when ``cascade=True``.
    The schema name is assumed to have been validated by the caller.

    Args:
        target: The warehouse to connect to.
        schema_name: The schema whose objects will be dropped (already validated).
        mode: The credential mode for Entra authentication.
    """
    # Safe: schema_name passes validate_identifier() in delete_schema().
    list_sql = _LIST_TABLES_IN_SCHEMA_SQL.format(schema=schema_name)

    def _run() -> list[tuple[str, str]]:
        with closing(sql.open_connection(target, mode=mode)) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(list_sql)
                rows = cursor.fetchall()
            except Exception as exc:
                mapped = sql.map_driver_error(exc)
                if mapped:
                    raise mapped from exc
                raise
            return [(str(r[0]), str(r[1])) for r in rows]

    objects = await asyncio.to_thread(_run)

    for obj_name, obj_type in objects:
        # Object names come from sys.tables/sys.views — bracket-safe catalog names.
        # Validate anyway to be defensive.
        validate_identifier(obj_name)
        ddl_keyword = "TABLE" if obj_type == "TABLE" else "VIEW"
        ddl = f"DROP {ddl_keyword} [{schema_name}].[{obj_name}]"

        def _run_drop(stmt: str = ddl) -> None:
            with closing(sql.open_connection(target, mode=mode)) as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute(stmt)
                    conn.commit()
                except Exception as exc:
                    mapped = sql.map_driver_error(exc)
                    if mapped:
                        raise mapped from exc
                    raise

        await asyncio.to_thread(_run_drop)
