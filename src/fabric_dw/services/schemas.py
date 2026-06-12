"""CRUD operations for SQL schemas on Fabric Data Warehouses.

Public API
----------
- :func:`validate_identifier` — re-exported from :mod:`fabric_dw.identifiers`.
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
- ``guest`` — system guest principal schema, not user-editable.
- ``db_owner``, ``db_accessadmin``, ``db_securityadmin``, ``db_ddladmin``,
  ``db_backupoperator``, ``db_datareader``, ``db_datawriter``,
  ``db_denydatareader``, ``db_denydatawriter`` — fixed database role schemas
  (``db_*`` prefix).

``dbo`` is **not** filtered because it is user-writable and is the default
schema for warehouse tables.
"""

from __future__ import annotations

import asyncio

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFound
from fabric_dw.identifiers import quote_identifier, validate_identifier
from fabric_dw.models import Schema
from fabric_dw.sql import SqlTarget, run_query, run_statements

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
#:
#: The explicit ``db_*`` names are kept alongside the ``db[_]%`` LIKE clause in
#: the query for clarity — they document which fixed-role schemas exist even
#: though the LIKE pattern already covers them.
_SYSTEM_SCHEMAS: frozenset[str] = frozenset(
    {
        "sys",
        "INFORMATION_SCHEMA",
        "guest",  # system guest principal schema
        # Fixed database-role schemas — also matched by the db[_]% LIKE clause.
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

# Build the IN-clause using parameter binding (? placeholders).
# The placeholders string is constructed from the frozenset length; the actual
# values are passed as params so the driver handles quoting/escaping.
_SYSTEM_SCHEMA_PLACEHOLDERS = ", ".join("?" for _ in _SYSTEM_SCHEMAS)
_SYSTEM_SCHEMA_PARAMS: tuple[str, ...] = tuple(sorted(_SYSTEM_SCHEMAS))

_LIST_SCHEMAS_SQL = f"""\
SELECT
    s.name,
    s.principal_id
FROM sys.schemas s
WHERE s.name NOT IN ({_SYSTEM_SCHEMA_PLACEHOLDERS})
  AND s.name NOT LIKE 'db[_]%'
ORDER BY s.name;
"""  # noqa: S608  # nosec B608 - placeholders are ? params; schema names in _SYSTEM_SCHEMA_PARAMS are hardcoded constants.

_LIST_TABLES_IN_SCHEMA_SQL = """\
SELECT t.name AS obj_name, 'TABLE' AS obj_type
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE s.name = ?
UNION ALL
SELECT v.name AS obj_name, 'VIEW' AS obj_type
FROM sys.views v
JOIN sys.schemas s ON s.schema_id = v.schema_id
WHERE s.name = ?;
"""

_FETCH_SCHEMA_SQL = """\
SELECT s.name, s.principal_id
FROM sys.schemas s
WHERE s.name = ?;
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_schema(cols: list[str], row: tuple[object, ...]) -> Schema:
    """Build a :class:`Schema` from a column-name list and a result row tuple."""
    data = dict(zip(cols, row, strict=True))
    name = str(data["name"])
    raw_pid = data.get("principal_id")
    principal_id = int(str(raw_pid)) if raw_pid is not None else None
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

    def _run() -> list[Schema]:
        cols, rows = run_query(
            target,
            _LIST_SCHEMAS_SQL,
            params=list(_SYSTEM_SCHEMA_PARAMS),
            mode=mode,
        )
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
    ddl = f"CREATE SCHEMA {quote_identifier(name)}"

    def _run_ddl() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

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

    ddl = f"DROP SCHEMA {quote_identifier(name)}"

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

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
        ValueError: If *name* fails identifier validation (belt-and-suspenders).
        NotFound: If the schema is not found after creation.
    """
    # Belt-and-suspenders: validate even though callers should have validated already.
    validate_identifier(name)

    def _run() -> Schema:
        cols, rows = run_query(
            target,
            _FETCH_SCHEMA_SQL,
            params=[name],
            mode=mode,
        )
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

    Enumerates objects via ``sys.tables`` and ``sys.views`` then issues all
    ``DROP TABLE`` / ``DROP VIEW`` DDL statements on a **single** connection,
    avoiding the N x TCP+TLS handshake overhead of the previous approach.

    This is a helper for :func:`delete_schema` when ``cascade=True``.
    The schema name is assumed to have been validated by the caller.

    .. note::

        **View-on-view dependency caveat**: objects are dropped in catalog order
        (the order returned by ``sys.tables``/``sys.views``), without analysing
        inter-object dependencies.  If the schema contains views that reference
        other views in the same schema, the drop may fail with a dependency
        error depending on the order in which the engine returns them.  In that
        case, re-run the operation or drop the dependent views manually first.

    Args:
        target: The warehouse to connect to.
        schema_name: The schema whose objects will be dropped (already validated).
        mode: The credential mode for Entra authentication.
    """

    def _run() -> list[tuple[str, str]]:
        # Use schema_name twice: once for sys.tables, once for sys.views (UNION ALL).
        _cols, rows = run_query(
            target,
            _LIST_TABLES_IN_SCHEMA_SQL,
            params=[schema_name, schema_name],
            mode=mode,
        )
        return [(str(r[0]), str(r[1])) for r in rows]

    objects = await asyncio.to_thread(_run)

    if not objects:
        return

    # Build all DROP statements then execute them on ONE connection.
    ddl_statements: list[str] = []
    for obj_name, obj_type in objects:
        # Object names come from sys.tables/sys.views — catalog names.
        # Validate anyway to be defensive (belt-and-suspenders).
        validate_identifier(obj_name)
        ddl_keyword = "TABLE" if obj_type == "TABLE" else "VIEW"
        ddl_statements.append(
            f"DROP {ddl_keyword} {quote_identifier(schema_name)}.{quote_identifier(obj_name)}"
        )

    await asyncio.to_thread(run_statements, target, ddl_statements, mode=mode)
