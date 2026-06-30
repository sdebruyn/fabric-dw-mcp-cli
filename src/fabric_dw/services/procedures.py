"""CRUD operations for stored procedures on Fabric Data Warehouses and SQL Analytics Endpoints.

Public API
----------
- :func:`validate_identifier` — re-exported from :mod:`fabric_dw.identifiers`.
- :func:`list_procedures`     — list all stored procedures (optionally filtered by schema).
- :func:`get_procedure`       — fetch a single procedure with its definition.
- :func:`create_procedure`    — issue CREATE PROCEDURE … AS <body>.
- :func:`update_procedure`    — issue CREATE OR ALTER PROCEDURE … AS <body>.
- :func:`drop_procedure`      — issue DROP PROCEDURE.
- :func:`transfer_procedure`  — ``ALTER SCHEMA ... TRANSFER OBJECT::...``.

Note: Stored procedures are supported on **both** Fabric Data Warehouses and
SQL Analytics Endpoints — no endpoint guard is applied here.  See Microsoft
documentation: DROP PROCEDURE and ALTER PROCEDURE both list
"SQL analytics endpoint in Microsoft Fabric" and "Warehouse in Microsoft Fabric"
in their "Applies to" lists.  This also applies to :func:`transfer_procedure`,
which is not subject to the table-only OneLake-sync restriction documented on
:func:`~fabric_dw.services.tables.transfer_table`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import cast

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFoundError
from fabric_dw.identifiers import parse_qualified_name, quote_identifier, validate_identifier
from fabric_dw.models import StoredProcedure
from fabric_dw.services._helpers import _transfer_object
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "create_procedure",
    "drop_procedure",
    "get_procedure",
    "list_procedures",
    "transfer_procedure",
    "update_procedure",
    "validate_identifier",
]

# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

_LIST_PROCEDURES_SQL = """\
SELECT
    s.name AS schema_name,
    p.name,
    p.create_date AS created,
    p.modify_date AS modified
FROM sys.procedures p
JOIN sys.schemas s ON s.schema_id = p.schema_id
WHERE ({schema_filter})
ORDER BY s.name, p.name;
"""

_GET_PROCEDURE_SQL = """\
SELECT
    s.name AS schema_name,
    p.name,
    p.create_date AS created,
    p.modify_date AS modified,
    m.definition
FROM sys.procedures p
JOIN sys.schemas s ON s.schema_id = p.schema_id
JOIN sys.sql_modules m ON m.object_id = p.object_id
WHERE s.name = ? AND p.name = ?;
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_procedure(cols: list[str], row: tuple[object, ...]) -> StoredProcedure:
    """Build a :class:`StoredProcedure` from a column-name list and a result row tuple."""
    data = dict(zip(cols, row, strict=True))
    schema_name = str(data["schema_name"])
    name = str(data["name"])
    raw_def = data.get("definition") if "definition" in data else None
    definition: str | None = cast("str | None", raw_def)
    return StoredProcedure(
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


async def list_procedures(
    target: SqlTarget,
    *,
    schema: str | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[StoredProcedure]:
    """Return all stored procedures on *target*, optionally filtered to a single *schema*.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: When provided, only procedures in this schema are returned.
            Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Returns:
        A (possibly empty) list of :class:`~fabric_dw.models.StoredProcedure` instances.

    Raises:
        ValueError: If *schema* fails identifier validation.
        PermissionDeniedError: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
    """
    if schema is not None:
        validate_identifier(schema)

    if schema is not None:
        schema_filter = "s.name = ?"
        filter_params: list[object] = [schema]
    else:
        schema_filter = "1=1"
        filter_params = []

    list_sql = _LIST_PROCEDURES_SQL.format(schema_filter=schema_filter)

    def _run() -> list[StoredProcedure]:
        cols, rows = run_query(
            target,
            list_sql,
            params=filter_params or None,
            mode=mode,
        )
        return [_row_to_procedure(cols, r) for r in rows]

    return await asyncio.to_thread(_run)


async def get_procedure(
    target: SqlTarget,
    schema: str,
    procedure_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> StoredProcedure:
    """Fetch a single stored procedure with its ``sys.sql_modules`` definition.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        procedure_name: The procedure name.  Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.StoredProcedure` with ``definition`` populated.

    Raises:
        ValueError: If *schema* or *procedure_name* fails identifier validation.
        NotFoundError: If no procedure with that schema/name exists.
        PermissionDeniedError: If the driver reports a permission error.
    """
    validate_identifier(schema)
    validate_identifier(procedure_name)

    def _run() -> StoredProcedure:
        cols, rows = run_query(
            target,
            _GET_PROCEDURE_SQL,
            params=[schema, procedure_name],
            mode=mode,
        )
        if not rows:
            msg = f"Procedure [{schema}].[{procedure_name}] not found"
            raise NotFoundError(msg)
        return _row_to_procedure(cols, rows[0])

    return await asyncio.to_thread(_run)


async def create_procedure(
    target: SqlTarget,
    schema: str,
    procedure_name: str,
    body: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> StoredProcedure:
    """Create a new stored procedure via ``CREATE PROCEDURE [<schema>].[<proc>] AS <body>``.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        procedure_name: The procedure name.  Must pass :func:`validate_identifier`.
        body: The free-form procedure body.  Not validated — the caller owns
            the SQL (same trust model as ``sql``).
        mode: The credential mode for Entra authentication.

    Returns:
        The newly-created :class:`~fabric_dw.models.StoredProcedure` (fetched after DDL).

    Raises:
        ValueError: If *schema* or *procedure_name* fails identifier validation.
        PermissionDeniedError: If the driver reports a CREATE PROCEDURE permission error.
    """
    validate_identifier(schema)
    validate_identifier(procedure_name)

    ddl = (
        f"CREATE PROCEDURE {quote_identifier(schema)}.{quote_identifier(procedure_name)} AS {body}"
    )

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)
    return await get_procedure(target, schema, procedure_name, mode=mode)


async def update_procedure(
    target: SqlTarget,
    schema: str,
    procedure_name: str,
    body: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> StoredProcedure:
    """Redefine a stored procedure via ``CREATE OR ALTER PROCEDURE … AS <body>``.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        procedure_name: The procedure name.  Must pass :func:`validate_identifier`.
        body: The free-form procedure body.  Caller-owned.
        mode: The credential mode for Entra authentication.

    Returns:
        The updated :class:`~fabric_dw.models.StoredProcedure` (fetched after DDL).

    Raises:
        ValueError: If *schema* or *procedure_name* fails identifier validation.
        PermissionDeniedError: If the driver reports an ALTER PROCEDURE permission error.
    """
    validate_identifier(schema)
    validate_identifier(procedure_name)

    ddl = (
        f"CREATE OR ALTER PROCEDURE"
        f" {quote_identifier(schema)}.{quote_identifier(procedure_name)}"
        f" AS {body}"
    )

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)
    return await get_procedure(target, schema, procedure_name, mode=mode)


async def drop_procedure(
    target: SqlTarget,
    schema: str,
    procedure_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop a stored procedure via ``DROP PROCEDURE [<schema>].[<proc>]``.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        procedure_name: The procedure name.  Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Raises:
        ValueError: If *schema* or *procedure_name* fails identifier validation.
        PermissionDeniedError: If the driver reports a DROP PROCEDURE permission error.
    """
    validate_identifier(schema)
    validate_identifier(procedure_name)

    ddl = f"DROP PROCEDURE {quote_identifier(schema)}.{quote_identifier(procedure_name)}"

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)


async def transfer_procedure(
    target: SqlTarget,
    qualified: str,
    target_schema: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> StoredProcedure:
    """Move a stored procedure to another schema via ``ALTER SCHEMA ... TRANSFER OBJECT::...``.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints —
    unlike :func:`~fabric_dw.services.tables.transfer_table`, procedure
    transfer is not subject to the table-only OneLake-sync restriction, so no
    :func:`~fabric_dw.services._helpers._assert_not_sql_endpoint` guard is
    applied here.

    .. warning::

        ``OBJECT::[schema].[name]`` matches *any* schema-scoped object with
        that name, not only stored procedures.  If a table, view, or function
        happens to share the qualified name, the engine transfers that
        object instead.  When the post-transfer re-fetch then finds no
        procedure named *procedure_name* in *target_schema*,
        :class:`~fabric_dw.exceptions.NotFoundError` is raised with a message
        that calls this out explicitly.

    .. warning::

        ``ALTER SCHEMA ... TRANSFER`` moves the procedure but does **not**
        rewrite the schema name inside its stored definition
        (``sys.sql_modules.definition``).  After a transfer,
        :func:`get_procedure` may still show the *old* schema name in the
        ``CREATE ... AS`` header even though the procedure now lives in the
        new schema.  This is not rewritten here -- doing so would require
        parsing and regenerating SQL, which this project deliberately avoids.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        qualified: The current fully-qualified name of the form ``schema.proc``.
            Parsed with :func:`~fabric_dw.identifiers.parse_qualified_name`.
        target_schema: The schema to move the procedure into.  Must pass
            :func:`validate_identifier`.  System schemas (``sys``,
            ``INFORMATION_SCHEMA``, ``guest``, fixed ``db_*`` role schemas)
            are rejected by
            :func:`~fabric_dw.services._helpers._alter_schema_transfer`.
        mode: The credential mode for Entra authentication.

    Returns:
        The moved :class:`~fabric_dw.models.StoredProcedure` (fetched via
        :func:`get_procedure` from *target_schema* after the transfer).

    Raises:
        ValueError: If *qualified* cannot be parsed, if any identifier component
            fails identifier validation, or if *target_schema* is a system schema.
        NotFoundError: If no procedure named *procedure_name* is found in
            *target_schema* after the transfer -- see the warning above about
            non-procedure objects.
        PermissionDeniedError: If the driver reports a permission error.
    """
    schema, procedure_name = parse_qualified_name(qualified)

    return await _transfer_object(
        target,
        source_schema=schema,
        object_name=procedure_name,
        target_schema=target_schema,
        object_label="procedure",
        fetch=lambda: get_procedure(target, target_schema, procedure_name, mode=mode),
        mode=mode,
    )
