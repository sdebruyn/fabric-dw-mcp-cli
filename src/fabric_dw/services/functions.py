"""CRUD operations for T-SQL user-defined functions on Fabric Data Warehouses and SQL Endpoints.

Public API
----------
- :func:`validate_identifier` — re-exported from :mod:`fabric_dw.identifiers`.
- :func:`list_functions`      — list all user-defined functions (filtered by schema/kind).
- :func:`get_function`        — fetch a single function with its definition and parameters.
- :func:`create_function`     — issue CREATE FUNCTION [<schema>].[<name>] AS <body>.
- :func:`update_function`     — issue CREATE OR ALTER FUNCTION [<schema>].[<name>] AS <body>.
- :func:`drop_function`       — issue DROP FUNCTION [IF EXISTS].
- :func:`rename_function`     — rename via DROP + CREATE (Fabric DW rejects sp_rename for UDFs).

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
import re
from datetime import datetime
from typing import Literal, cast

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import NotFoundError
from fabric_dw.identifiers import parse_qualified_name, quote_identifier, validate_identifier
from fabric_dw.models import Function, FunctionDetails, FunctionKind, FunctionParameter
from fabric_dw.sql import SqlTarget, run_query

# Valid values for the ``kind`` parameter of :func:`list_functions`.
VALID_KINDS: frozenset[str] = frozenset({"scalar", "inline-tvf", "all"})

__all__ = [
    "VALID_KINDS",
    "create_function",
    "drop_function",
    "get_function",
    "list_functions",
    "rename_function",
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

# Fabric DW does not support sp_rename for user-defined functions.
# Microsoft docs explicitly state: "drop the object and re-create it with the
# new name" for functions, triggers, views, and stored procedures.
# See https://learn.microsoft.com/sql/relational-databases/system-stored-procedures/sp-rename-transact-sql#remarks
#
# rename_function therefore fetches the current definition and re-creates the
# function under the new name before dropping the old one.  No sp_rename SQL
# constant is needed for functions.

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
    definition = cast("str | None", raw_def)
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


def _extract_function_body(schema: str, name: str, definition: str) -> str:
    """Extract the parameter/RETURNS/body portion from a stored ``sys.sql_modules`` definition.

    ``sys.sql_modules`` stores the full original ``CREATE FUNCTION`` statement.
    For rename-via-recreate we need only the part after the qualified object
    name so we can prepend a new ``CREATE FUNCTION [schema].[new_name]``.

    Handles both bracket-quoted (``[schema].[name]``) and unquoted
    (``schema.name``) forms, as well as single-part names without a schema.

    Uses ``re.search`` for ``CREATE FUNCTION`` to skip any leading comment
    blocks that contain the word ``FUNCTION`` before the actual DDL keyword.

    Raises:
        NotFoundError: If the ``CREATE FUNCTION`` keyword pair cannot be located
            in the definition string (indicates a corrupted or unexpected
            definition).
    """
    m = re.search(r"\bCREATE\s+FUNCTION\b", definition, re.IGNORECASE)
    if m is None:
        msg = f"Cannot parse definition for [{schema}].[{name}]: 'CREATE FUNCTION' not found"
        raise NotFoundError(msg)

    # Advance past "FUNCTION" and trailing whitespace.
    pos = m.end()
    while pos < len(definition) and definition[pos] in (" ", "\t", "\n", "\r"):
        pos += 1

    # Skip up to two name-tokens (schema + name) separated by a dot.
    for _ in range(2):
        if pos >= len(definition):
            break
        pos = _skip_name_token(definition, pos)
        if pos < len(definition) and definition[pos] == ".":
            pos += 1  # consume the dot between schema and name
        else:
            break  # single-part name or end of string

    return definition[pos:]


def _skip_name_token(definition: str, pos: int) -> int:
    """Advance *pos* past one bracket-quoted or plain identifier token.

    Returns the updated position (pointing to the first character after the
    token).
    """
    if pos >= len(definition):
        return pos

    if definition[pos] == "[":
        # Bracket-quoted token — find closing bracket, respecting escaped ']]'.
        end = pos + 1
        while end < len(definition):
            if definition[end] == "]":
                if end + 1 < len(definition) and definition[end + 1] == "]":
                    end += 2  # escaped bracket
                    continue
                end += 1
                break
            end += 1
        return end

    # Unquoted token — stop at whitespace (including \r for CRLF), '(', or '.'.
    end = pos
    while end < len(definition) and definition[end] not in (" ", "\t", "\n", "\r", "(", "."):
        end += 1
    return end


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
            Not validated — the caller owns the SQL (same trust model as ``sql exec``).
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


async def drop_function(
    target: SqlTarget,
    schema: str,
    function_name: str,
    *,
    if_exists: bool = False,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Drop a user-defined function via ``DROP FUNCTION [IF EXISTS] [<schema>].[<name>]``.

    Supported on both Fabric Data Warehouses and SQL Analytics Endpoints.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        schema: The schema name.  Must pass :func:`validate_identifier`.
        function_name: The function name.  Must pass :func:`validate_identifier`.
        if_exists: When ``True``, emits ``DROP FUNCTION IF EXISTS`` so the statement
            is a no-op when the function does not exist.
        mode: The credential mode for Entra authentication.

    Raises:
        ValueError: If *schema* or *function_name* fails identifier validation.
        PermissionDeniedError: If the driver reports a DROP FUNCTION permission error.
    """
    validate_identifier(schema)
    validate_identifier(function_name)

    if_exists_clause = "IF EXISTS " if if_exists else ""
    ddl = (
        f"DROP FUNCTION {if_exists_clause}"
        f"{quote_identifier(schema)}.{quote_identifier(function_name)}"
    )

    def _run() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run)


async def rename_function(
    target: SqlTarget,
    qualified: str,
    new_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> FunctionDetails:
    """Rename a user-defined function via DROP + CREATE under the new name.

    Works on both Data Warehouses and SQL Analytics Endpoints — no DW-only guard
    is applied.

    **Why not ``sp_rename``?**
    Fabric Warehouse rejects ``sp_rename`` for user-defined functions with
    "An invalid parameter or option was specified for procedure 'sys.sp_rename'".
    The Microsoft T-SQL reference explicitly states that ``sp_rename`` must not be
    used to rename functions; the recommended approach is to drop and re-create the
    object.  See https://learn.microsoft.com/sql/relational-databases/system-stored-procedures/sp-rename-transact-sql#remarks

    The implementation:

    1. Fetch the existing function details (definition from ``sys.sql_modules``).
    2. Create a new function with the same schema, body, and the new name.
    3. Drop the old function.

    Partial-state behaviour:

    - If step 2 fails (e.g. the new name already exists), the old function is
      untouched and the error propagates cleanly — no state change occurs.
    - If step 3 fails after step 2 has succeeded, **both** the old and the new
      function exist simultaneously and an exception is raised.  The caller must
      clean up the duplicate by manually dropping the old name.

    The ordering (create-before-drop) is intentional: a failure must never
    silently destroy the user's function.

    The new name must be unqualified (no dot) because the function stays in the
    same schema.

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        qualified: Current qualified name of the function, e.g. ``dbo.fn_clean``.
            Parsed via :func:`~fabric_dw.identifiers.parse_qualified_name`.
        new_name: New bare (unqualified) function name.  Must pass
            :func:`validate_identifier` and must not contain a dot.
        mode: The credential mode for Entra authentication.

    Returns:
        The newly-created :class:`~fabric_dw.models.FunctionDetails` under the
        new name (fetched after the DDL sequence completes).

    Raises:
        ValueError: If *qualified* cannot be parsed, if either identifier part
            fails validation, or if *new_name* is schema-qualified (contains a dot).
        NotFoundError: If the source function does not exist, or if the newly
            created function cannot be found after the DDL sequence.
        PermissionDeniedError: If the driver reports a permission error.
    """
    schema, old_name = parse_qualified_name(qualified)
    validate_identifier(schema)
    validate_identifier(old_name)

    if "." in new_name:
        msg = (
            f"New name {new_name!r} must not be schema-qualified; "
            "rename cannot move a function to a different schema"
        )
        raise ValueError(msg)
    validate_identifier(new_name)

    # Step 1: fetch the existing definition so we can re-create it.
    existing = await get_function(target, schema, old_name, mode=mode)

    if existing.definition is None:
        msg = f"Function [{schema}].[{old_name}] has no definition in sys.sql_modules"
        raise NotFoundError(msg)

    # sys.sql_modules stores the full original CREATE FUNCTION statement.
    # Strip the preamble (CREATE FUNCTION [schema].[name]) so we can re-issue
    # it under the new name.
    body = _extract_function_body(schema, old_name, existing.definition)

    # Step 2: create under the new name.  create_function() already calls
    # get_function() internally and returns the FunctionDetails — reuse it.
    new_details = await create_function(target, schema, new_name, body, mode=mode)

    # Step 3: drop the old name.
    await drop_function(target, schema, old_name, mode=mode)

    return new_details
