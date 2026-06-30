"""CRUD operations for T-SQL user-defined functions on Fabric Data Warehouses and SQL Endpoints.

Public API
----------
- :func:`validate_identifier` — re-exported from :mod:`fabric_dw.identifiers`.
- :func:`list_functions`      — list all user-defined functions (filtered by schema/kind).
- :func:`get_function`        — fetch a single function with its definition and parameters.
- :func:`create_function`     — issue CREATE FUNCTION [<schema>].[<name>] AS <body>.
- :func:`update_function`     — issue CREATE OR ALTER FUNCTION [<schema>].[<name>] AS <body>.
- :func:`transfer_function`   — issue ALTER SCHEMA ... TRANSFER OBJECT::... (both targets).
- :func:`drop_function`       — issue DROP FUNCTION; no-op on missing when if_exists=True.

Note: User-defined functions are supported on **both** Fabric Data Warehouses and
SQL Analytics Endpoints — no endpoint guard is applied here.  The CREATE FUNCTION,
ALTER FUNCTION, and DROP FUNCTION "Applies to" lists include both
"SQL analytics endpoint in Microsoft Fabric" and "Warehouse in Microsoft Fabric".

Preview note: Scalar UDFs (FN) and inline TVFs (IF) are preview features as of
mid-2026.  Multi-statement TVFs (TF) are not supported for creation but may
appear in catalog listings on migrated warehouses.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal, cast

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFoundError
from fabric_dw.identifiers import parse_qualified_name, quote_identifier, validate_identifier
from fabric_dw.models import Function, FunctionDetails, FunctionKind, FunctionParameter
from fabric_dw.services._helpers import _alter_schema_transfer
from fabric_dw.sql import SqlTarget, run_query

# Valid values for the ``kind`` parameter of :func:`list_functions`.
VALID_KINDS: frozenset[str] = frozenset({"scalar", "inline-tvf", "all"})

__all__ = [
    "VALID_KINDS",
    "create_function",
    "drop_function",
    "get_function",
    "list_functions",
    "transfer_function",
    "update_function",
    "validate_identifier",
    "validate_kind",
]

# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

_LIST_FUNCTIONS_SQL = """\
SELECT
    s.name  AS schema_name,
    o.name,
    o.type,
    o.type_desc,
    o.create_date AS created,
    o.modify_date AS modified,
    m.is_inlineable
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE ({schema_filter}) AND ({kind_filter})
ORDER BY s.name, o.name;
"""

_GET_FUNCTION_SQL = """\
SELECT
    s.name  AS schema_name,
    o.name,
    o.type,
    o.type_desc,
    o.create_date AS created,
    o.modify_date AS modified,
    m.definition,
    m.is_inlineable
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE s.name = ? AND o.name = ? AND o.type IN ('FN', 'IF', 'TF');
"""

_GET_PARAMS_SQL = """\
SELECT
    p.parameter_id,
    p.name,
    TYPE_NAME(p.user_type_id) AS data_type,
    p.max_length,
    p.is_output
FROM sys.parameters p
JOIN sys.objects o ON o.object_id = p.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE s.name = ? AND o.name = ?
ORDER BY p.parameter_id;
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_KIND_MAP: dict[str, FunctionKind] = {
    "FN": FunctionKind.SCALAR,
    "IF": FunctionKind.INLINE_TVF,
    "TF": FunctionKind.MSTVF,
}


def _type_to_kind_filter(kind: Literal["scalar", "inline-tvf", "all"]) -> tuple[str, list[object]]:
    """Return a (sql_fragment, params) pair for the kind filter clause."""
    if kind == "scalar":
        return "o.type = ?", ["FN"]
    if kind == "inline-tvf":
        return "o.type = ?", ["IF"]
    # "all" — include FN, IF, and TF
    return "o.type IN ('FN', 'IF', 'TF')", []


def _row_to_function(cols: list[str], row: tuple[object, ...]) -> Function:
    """Build a :class:`Function` from a column-name list and a result row tuple."""
    data = dict(zip(cols, row, strict=True))
    schema_name = str(data["schema_name"])
    name = str(data["name"])
    type_code = str(data["type"]).strip()
    kind = _KIND_MAP.get(type_code, FunctionKind.SCALAR)
    raw_inlineable = data.get("is_inlineable")
    is_inlineable: bool | None = bool(raw_inlineable) if raw_inlineable is not None else None
    return Function(
        schema_name=schema_name,
        name=name,
        qualified_name=f"{schema_name}.{name}",
        kind=kind,
        is_inlineable=is_inlineable,
        created=cast(datetime, data["created"]),
        modified=cast(datetime, data["modified"]),
    )


def _row_to_function_details(
    cols: list[str],
    row: tuple[object, ...],
    parameters: list[FunctionParameter],
) -> FunctionDetails:
    """Build a :class:`FunctionDetails` from a column-name list, row tuple, and parameter list."""
    data = dict(zip(cols, row, strict=True))
    schema_name = str(data["schema_name"])
    name = str(data["name"])
    type_code = str(data["type"]).strip()
    kind = _KIND_MAP.get(type_code, FunctionKind.SCALAR)
    raw_def = data.get("definition")
    definition: str | None = cast("str | None", raw_def)
    raw_inlineable = data.get("is_inlineable")
    is_inlineable: bool | None = bool(raw_inlineable) if raw_inlineable is not None else None
    return FunctionDetails(
        schema_name=schema_name,
        name=name,
        qualified_name=f"{schema_name}.{name}",
        kind=kind,
        is_inlineable=is_inlineable,
        definition=definition,
        parameters=parameters,
        created=cast(datetime, data["created"]),
        modified=cast(datetime, data["modified"]),
    )


def _as_int(value: object) -> int:
    """Coerce a DB-driver value (int, Decimal, or str) to a Python ``int``.

    The TDS driver returns numeric columns as ``int`` or ``Decimal`` depending
    on the column type and driver version.  Both satisfy ``int(x)`` at runtime;
    this helper makes the conversion explicit so the type-checker sees a
    narrowed ``int`` result rather than an opaque ``object``.
    """
    if isinstance(value, int):
        return value
    return int(str(value))


def _row_to_param(cols: list[str], row: tuple[object, ...]) -> FunctionParameter:
    """Build a :class:`FunctionParameter` from a column-name list and a result row tuple."""
    data = dict(zip(cols, row, strict=True))
    raw_is_output = data.get("is_output")
    is_output = bool(raw_is_output) if raw_is_output is not None else False
    return FunctionParameter(
        parameter_id=_as_int(data["parameter_id"]),
        name=str(data["name"]),
        data_type=str(data["data_type"]),
        max_length=_as_int(data["max_length"]),
        is_output=is_output,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_kind(kind: str) -> Literal["scalar", "inline-tvf", "all"]:
    """Validate *kind* and return it narrowed to the expected Literal type.

    Args:
        kind: The raw kind string from a CLI or MCP caller.

    Returns:
        The same string narrowed to ``Literal["scalar", "inline-tvf", "all"]``.

    Raises:
        ValueError: If *kind* is not one of the recognised values.
    """
    if kind not in VALID_KINDS:
        msg = f"Invalid kind {kind!r}. Must be one of: {', '.join(sorted(VALID_KINDS))}"
        raise ValueError(msg)
    # The membership check above narrows the type — cast is safe.
    return cast(Literal["scalar", "inline-tvf", "all"], kind)


async def list_functions(
    target: SqlTarget,
    *,
    schema: str | None = None,
    kind: Literal["scalar", "inline-tvf", "all"] = "all",
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[Function]:
    """Return all user-defined functions on *target*, optionally filtered by *schema* and *kind*.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    Preview note: Scalar UDFs and inline TVFs are preview features on Fabric DW as of mid-2026.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: When provided, only functions in this schema are returned.
            Must pass :func:`validate_identifier`.
        kind: Filter by function kind — ``"scalar"`` (FN), ``"inline-tvf"`` (IF),
            or ``"all"`` (FN + IF + TF).  Defaults to ``"all"``.  Validated by
            :func:`validate_kind`.
        mode: The credential mode for Entra authentication.

    Returns:
        A (possibly empty) list of :class:`~fabric_dw.models.Function` instances.

    Raises:
        ValueError: If *schema* fails identifier validation or *kind* is invalid.
        PermissionDeniedError: If the driver reports a permission error.
        AuthError: If the driver reports an authentication failure.
    """
    if schema is not None:
        validate_identifier(schema)
        schema_filter = "s.name = ?"
        schema_params: list[object] = [schema]
    else:
        schema_filter = "1=1"
        schema_params = []

    kind_filter_sql, kind_params = _type_to_kind_filter(kind)
    all_params = schema_params + kind_params

    list_sql = _LIST_FUNCTIONS_SQL.format(schema_filter=schema_filter, kind_filter=kind_filter_sql)

    def _run() -> list[Function]:
        cols, rows = run_query(
            target,
            list_sql,
            params=all_params or None,
            mode=mode,
        )
        return [_row_to_function(cols, r) for r in rows]

    return await asyncio.to_thread(_run)


async def get_function(
    target: SqlTarget,
    schema: str,
    function_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> FunctionDetails:
    """Fetch a single user-defined function with its definition and parameters.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    Args:
        target: The warehouse or SQL Analytics Endpoint to query.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        function_name: The function name.  Must pass :func:`validate_identifier`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.FunctionDetails` with ``definition`` and
        ``parameters`` populated.

    Raises:
        ValueError: If *schema* or *function_name* fails identifier validation.
        NotFoundError: If no function with that schema/name exists.
        PermissionDeniedError: If the driver reports a permission error.
    """
    validate_identifier(schema)
    validate_identifier(function_name)

    def _run() -> FunctionDetails:
        # Fetch function metadata
        cols, rows = run_query(
            target,
            _GET_FUNCTION_SQL,
            params=[schema, function_name],
            mode=mode,
        )
        if not rows:
            msg = f"Function [{schema}].[{function_name}] not found"
            raise NotFoundError(msg)

        # Fetch parameters
        param_cols, param_rows = run_query(
            target,
            _GET_PARAMS_SQL,
            params=[schema, function_name],
            mode=mode,
        )
        params = [_row_to_param(param_cols, pr) for pr in param_rows]

        return _row_to_function_details(cols, rows[0], params)

    return await asyncio.to_thread(_run)


async def create_function(
    target: SqlTarget,
    schema: str,
    function_name: str,
    body: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> FunctionDetails:
    """Create a new user-defined function via ``CREATE FUNCTION [<schema>].[<name>] AS <body>``.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    Preview note: Scalar UDFs and inline TVFs are preview features on Fabric DW as of mid-2026.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        function_name: The function name.  Must pass :func:`validate_identifier`.
        body: The free-form function body (parameter list, RETURNS clause, and body).
            Not validated — the caller owns the SQL (same trust model as ``sql``).
        mode: The credential mode for Entra authentication.

    Returns:
        The newly-created :class:`~fabric_dw.models.FunctionDetails` (fetched after DDL).

    Raises:
        ValueError: If *schema* or *function_name* fails identifier validation.
        PermissionDeniedError: If the driver reports a CREATE FUNCTION permission error.
    """
    validate_identifier(schema)
    validate_identifier(function_name)

    ddl = f"CREATE FUNCTION {quote_identifier(schema)}.{quote_identifier(function_name)} {body}"

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)
    return await get_function(target, schema, function_name, mode=mode)


async def update_function(
    target: SqlTarget,
    schema: str,
    function_name: str,
    body: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> FunctionDetails:
    """Redefine a user-defined function via ``CREATE OR ALTER FUNCTION … <body>``.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    Note: ``ALTER FUNCTION`` cannot change the function kind (e.g. scalar to inline TVF).
    The body must be compatible with the original function's kind.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        function_name: The function name.  Must pass :func:`validate_identifier`.
        body: The free-form function body.  Caller-owned.
        mode: The credential mode for Entra authentication.

    Returns:
        The updated :class:`~fabric_dw.models.FunctionDetails` (fetched after DDL).

    Raises:
        ValueError: If *schema* or *function_name* fails identifier validation.
        PermissionDeniedError: If the driver reports an ALTER FUNCTION permission error.
    """
    validate_identifier(schema)
    validate_identifier(function_name)

    ddl = (
        f"CREATE OR ALTER FUNCTION"
        f" {quote_identifier(schema)}.{quote_identifier(function_name)}"
        f" {body}"
    )

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)
    return await get_function(target, schema, function_name, mode=mode)


async def transfer_function(
    target: SqlTarget,
    qualified: str,
    target_schema: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> FunctionDetails:
    """Move a function to another schema via ``ALTER SCHEMA ... TRANSFER OBJECT::...``.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints —
    unlike :func:`~fabric_dw.services.tables.transfer_table`, no endpoint
    guard applies here, mirroring the rest of this module's function DDL.

    .. warning::

        ``OBJECT::[schema].[name]`` matches *any* schema-scoped object with
        that name, not only functions.  If a table, view, or procedure
        happens to share the qualified name, the engine transfers that
        object instead.  When the post-transfer re-fetch then finds no
        function named *function_name* in *target_schema*,
        :class:`~fabric_dw.exceptions.NotFoundError` is raised with a message
        that calls this out explicitly.

    .. warning::

        ``ALTER SCHEMA ... TRANSFER`` does not rewrite the schema name inside
        the function's stored definition (``sys.sql_modules.definition``).
        After a transfer, the re-fetched :class:`~fabric_dw.models.FunctionDetails`
        may still show the *old* schema name in the ``CREATE ... AS`` header.
        This is a stale-text display issue only; the function itself has moved.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        qualified: The current fully-qualified name of the form ``schema.fn``.
            Parsed with :func:`~fabric_dw.identifiers.parse_qualified_name`.
        target_schema: The schema to move the function into.  Must pass
            :func:`validate_identifier`.  System schemas (``sys``,
            ``INFORMATION_SCHEMA``, ``guest``, fixed ``db_*`` role schemas)
            are rejected by
            :func:`~fabric_dw.services._helpers._alter_schema_transfer`.
        mode: The credential mode for Entra authentication.

    Returns:
        A :class:`~fabric_dw.models.FunctionDetails` reflecting the moved
        function (fetched via :func:`get_function` from *target_schema* after
        the transfer).

    Raises:
        ValueError: If *qualified* cannot be parsed, if any identifier component
            fails identifier validation, or if *target_schema* is a system schema.
        NotFoundError: If no function named *function_name* is found in
            *target_schema* after the transfer -- see the warning above about
            non-function objects.
        PermissionDeniedError: If the driver reports a permission error.
    """
    schema, fn_name = parse_qualified_name(qualified, kind="function")
    validate_identifier(schema)
    validate_identifier(fn_name)
    validate_identifier(target_schema)

    await _alter_schema_transfer(
        target,
        source_schema=schema,
        object_name=fn_name,
        target_schema=target_schema,
        mode=mode,
    )

    try:
        return await get_function(target, target_schema, fn_name, mode=mode)
    except NotFoundError:
        msg = (
            f"No function named [{target_schema}].[{fn_name}] was found after "
            "the transfer. ALTER SCHEMA TRANSFER moves any schema-scoped "
            "object with that name, not only functions -- if a table, view, "
            "or procedure shared this name, check whether it was moved instead."
        )
        raise NotFoundError(msg) from None


async def drop_function(
    target: SqlTarget,
    schema: str,
    function_name: str,
    *,
    if_exists: bool = False,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> bool:
    """Drop a user-defined function via ``DROP FUNCTION [<schema>].[<name>]``.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    The implementation issues ``DROP FUNCTION`` directly (no ``IF EXISTS`` clause
    and no catalog pre-check).  SQL Server error 3701 ("Cannot drop the function
    '<name>' because it does not exist …") is mapped to
    :class:`~fabric_dw.exceptions.NotFoundError` by :func:`~fabric_dw.sql.run_query`.
    When *if_exists* is ``True`` that ``NotFoundError`` is caught here and treated
    as a silent no-op; otherwise it propagates to the caller.

    This design requires only ``DROP FUNCTION`` permission — no catalog-read
    (``VIEW DEFINITION``) permission is needed, which keeps the behaviour
    identical to the pre-fix ``DROP FUNCTION IF EXISTS`` with respect to
    required privileges.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        function_name: The function name.  Must pass :func:`validate_identifier`.
        if_exists: When ``True``, a missing function is treated as a no-op and
            ``False`` is returned.  When ``False`` (the default), a missing
            function surfaces as :class:`~fabric_dw.exceptions.NotFoundError`.
        mode: The credential mode for Entra authentication.

    Returns:
        ``True`` when the function was dropped, ``False`` when *if_exists* is
        ``True`` and the function did not exist (no-op).

    Raises:
        ValueError: If *schema* or *function_name* fails identifier validation.
        NotFoundError: If the function does not exist and *if_exists* is ``False``.
        PermissionDeniedError: If the driver reports a DROP FUNCTION permission error.
    """
    validate_identifier(schema)
    validate_identifier(function_name)

    ddl = f"DROP FUNCTION {quote_identifier(schema)}.{quote_identifier(function_name)}"

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    try:
        await asyncio.to_thread(_run)
    except NotFoundError:
        if if_exists:
            return False
        raise
    return True
