"""CRUD operations for SQL schemas on Fabric Data Warehouses.

Public API
----------
- :func:`validate_identifier` — re-exported from :mod:`fabric_dw.identifiers`.
- :func:`list_schemas`         — list all user-defined schemas via TDS ``sys.schemas``.
- :func:`create_schema`        — ``CREATE SCHEMA [<name>]``.
- :func:`delete_schema`        — ``DROP SCHEMA [<name>]``. Optionally cascade-drops
                                  contained objects (tables, views, procedures, functions)
                                  first, with target-kind-aware filtering.

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

Cascade behaviour per target kind
----------------------------------
When ``cascade=True``:

- **Fabric Data Warehouse** (``WarehouseKind.WAREHOUSE``): drops all contained
  objects — tables (``U``), views (``V``), stored procedures (``P``), and
  functions (``FN``/``IF``/``TF``) — before dropping the schema.
- **SQL Analytics Endpoint** (``WarehouseKind.SQL_ENDPOINT``): drops views,
  stored procedures, and functions, but **skips tables** because ``DROP TABLE``
  is a Warehouse-only operation on Fabric.  The schema must therefore contain
  no tables for the final ``DROP SCHEMA`` to succeed; if it does, the engine
  will reject the ``DROP SCHEMA`` with a clear error.
"""

from __future__ import annotations

import asyncio

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFoundError
from fabric_dw.identifiers import quote_identifier, validate_identifier
from fabric_dw.models import Schema, WarehouseKind
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
# Both the placeholder string and the ordered param tuple are derived from the
# same sorted sequence so that "len(placeholders) == len(params)" is a
# structural guarantee, not an implicit invariant between two separate constants.
_SYSTEM_SCHEMA_PARAMS: tuple[str, ...] = tuple(sorted(_SYSTEM_SCHEMAS))
_SYSTEM_SCHEMA_PLACEHOLDERS = ", ".join("?" for _ in _SYSTEM_SCHEMA_PARAMS)

_LIST_SCHEMAS_SQL = f"""\
SELECT
    s.name,
    s.principal_id
FROM sys.schemas s
WHERE s.name NOT IN ({_SYSTEM_SCHEMA_PLACEHOLDERS})
  AND s.name NOT LIKE 'db[_]%'
ORDER BY s.name;
"""  # noqa: S608  # nosec B608 - placeholders are ? params; schema names in _SYSTEM_SCHEMA_PARAMS are hardcoded constants.

# Enumerate all droppable user objects in a schema via sys.objects.
# Object type codes:
#   U  = USER_TABLE          (DROP TABLE)
#   V  = VIEW                (DROP VIEW)
#   P  = SQL_STORED_PROCEDURE (DROP PROCEDURE)
#   FN = SQL_SCALAR_FUNCTION  (DROP FUNCTION)
#   IF = SQL_INLINE_TABLE_VALUED_FUNCTION (DROP FUNCTION)
#   TF = SQL_TABLE_VALUED_FUNCTION        (DROP FUNCTION)
_LIST_OBJECTS_IN_SCHEMA_SQL = """\
SELECT o.name AS obj_name, o.type AS obj_type
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE s.name = ?
  AND o.type IN ('U', 'V', 'P', 'FN', 'IF', 'TF')
ORDER BY o.type, o.name;
"""  # nosec B608 - schema name is a ? param; type codes are hardcoded constants.

_FETCH_SCHEMA_SQL = """\
SELECT s.name, s.principal_id
FROM sys.schemas s
WHERE s.name = ?;
"""

# Mapping from sys.objects type code to the DDL keyword used in DROP <keyword>.
_TYPE_TO_DDL_KEYWORD: dict[str, str] = {
    "U": "TABLE",
    "V": "VIEW",
    "P": "PROCEDURE",
    "FN": "FUNCTION",
    "IF": "FUNCTION",
    "TF": "FUNCTION",
}

# Object types that are NOT droppable on a SQL Analytics Endpoint.
# DROP TABLE is a Warehouse-only operation on Fabric; tables on an endpoint
# are read-only projections of the underlying Lakehouse/Warehouse data.
_ENDPOINT_EXCLUDED_TYPES: frozenset[str] = frozenset({"U"})


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
        PermissionDeniedError: If the driver reports a permission error.
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
        PermissionDeniedError: If the driver reports a CREATE SCHEMA permission error.
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
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop a schema via ``DROP SCHEMA [<name>]``.

    If *cascade* is ``True``, all droppable objects contained in the schema
    are dropped first, then the schema itself is dropped.  The set of objects
    dropped depends on *kind*:

    - **Warehouse**: drops tables (``U``), views (``V``), stored procedures
      (``P``), and functions (``FN``/``IF``/``TF``).
    - **SQL Analytics Endpoint**: drops views, stored procedures, and
      functions, but **skips tables** because ``DROP TABLE`` is a
      Warehouse-only operation on Fabric.  If the schema contains tables, the
      final ``DROP SCHEMA`` will be rejected by the engine.

    .. caution::

        When *cascade* is ``True``, **all droppable objects in the schema are
        permanently deleted**.  This operation is irreversible.  Confirm with
        the user before calling this function with ``cascade=True``.

    Args:
        target: The warehouse to connect to.
        name: The schema name.  Must pass :func:`validate_identifier`.
        cascade: When ``True``, drop all droppable objects in the schema
            before dropping the schema itself.  Defaults to ``False``.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            Controls which object types are dropped during cascade; tables
            (type ``U``) are excluded on SQL Analytics Endpoints because
            ``DROP TABLE`` is Warehouse-only.  Defaults to
            :attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE`.
        mode: The credential mode for Entra authentication.

    Raises:
        ValueError: If *name* fails identifier validation.
        PermissionDeniedError: If the driver reports a DROP SCHEMA permission error.
    """
    validate_identifier(name)

    if cascade:
        await _drop_schema_objects(target, name, kind=kind, mode=mode)

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
        NotFoundError: If the schema is not found after creation.
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
            raise NotFoundError(msg)
        return _row_to_schema(cols, rows[0])

    return await asyncio.to_thread(_run)


async def _drop_schema_objects(
    target: SqlTarget,
    schema_name: str,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop droppable objects contained in *schema_name*, respecting *kind*.

    Enumerates objects via ``sys.objects`` (types ``U``, ``V``, ``P``,
    ``FN``, ``IF``, ``TF``) joined to ``sys.schemas``, then issues the
    appropriate ``DROP`` statement for each object on a **single** connection.

    Target-kind filtering
    ~~~~~~~~~~~~~~~~~~~~~
    - **Warehouse** (``WarehouseKind.WAREHOUSE``): all enumerated types are
      dropped (``DROP TABLE``, ``DROP VIEW``, ``DROP PROCEDURE``,
      ``DROP FUNCTION``).
    - **SQL Analytics Endpoint** (``WarehouseKind.SQL_ENDPOINT``): objects of
      type ``U`` (tables) are **excluded** because ``DROP TABLE`` is a
      Warehouse-only operation on Fabric.  Views, stored procedures, and
      functions are still dropped.  If the schema still contains tables after
      this pass, the subsequent ``DROP SCHEMA`` issued by
      :func:`delete_schema` will be rejected by the engine with a clear error
      about remaining objects — this is intentional and acceptable.

    .. note::

        **Object-dependency caveat**: objects are dropped in the order
        returned by ``sys.objects`` (by type then name), without analysing
        inter-object dependencies.  If the schema contains views or functions
        that reference other views/functions in the same schema, the drop may
        fail with a dependency error.  In that case, re-run the operation or
        drop the dependent objects manually first.

    Args:
        target: The warehouse to connect to.
        schema_name: The schema whose objects will be dropped (already validated).
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            Controls which object types are eligible for dropping.
        mode: The credential mode for Entra authentication.
    """

    def _run() -> list[tuple[str, str]]:
        _cols, rows = run_query(
            target,
            _LIST_OBJECTS_IN_SCHEMA_SQL,
            params=[schema_name],
            mode=mode,
        )
        return [(str(r[0]), str(r[1]).strip()) for r in rows]

    objects = await asyncio.to_thread(_run)

    if not objects:
        return

    # Determine which object types to exclude based on the target kind.
    excluded_types: frozenset[str] = (
        _ENDPOINT_EXCLUDED_TYPES if kind == WarehouseKind.SQL_ENDPOINT else frozenset()
    )

    # Build all DROP statements then execute them on ONE connection.
    ddl_statements: list[str] = []
    for obj_name, obj_type in objects:
        if obj_type in excluded_types:
            # Skip objects that cannot be dropped on this target kind.
            # e.g. tables (U) on SQL Analytics Endpoints.
            continue
        ddl_keyword = _TYPE_TO_DDL_KEYWORD.get(obj_type)
        if ddl_keyword is None:
            # Unknown type — skip rather than generate invalid SQL.
            continue
        # Object names come from sys.objects — catalog names.
        # Validate anyway to be defensive (belt-and-suspenders).
        validate_identifier(obj_name)
        ddl_statements.append(
            f"DROP {ddl_keyword} {quote_identifier(schema_name)}.{quote_identifier(obj_name)}"
        )

    if not ddl_statements:
        return

    await asyncio.to_thread(run_statements, target, ddl_statements, mode=mode)
