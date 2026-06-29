"""Dynamic data masking service functions for Microsoft Fabric Data Warehouses.

Reads from ``sys.masked_columns`` and issues
``ALTER TABLE ... ALTER COLUMN ... ADD MASKED WITH (FUNCTION = '...')`` and
``ALTER TABLE ... ALTER COLUMN ... DROP MASKED`` statements.

Statement-building safety
--------------------------
All statements are built from:
- Validated, bracket-quoted identifiers via
  :func:`~fabric_dw.identifiers.validate_identifier` +
  :func:`~fabric_dw.identifiers.quote_identifier`.
- Column names validated via :func:`~fabric_dw.identifiers.validate_column_name`
  + :func:`~fabric_dw.identifiers.quote_identifier`.
- Fixed allowlist for mask function type (``default``, ``email``, ``random``,
  ``partial``).
- Numeric arguments for ``random`` and ``partial`` are validated as integers.
- The ``partial`` padding string is validated and escaped by
  :func:`_validate_and_escape_padding`.

No SQL text is ever parsed or rewritten.
"""

from __future__ import annotations

import asyncio

from fabric_dw.auth import CredentialMode
from fabric_dw.identifiers import (
    quote_identifier,
    validate_column_name,
    validate_identifier,
)
from fabric_dw.models import MaskedColumn
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "VALID_MASK_FUNCTION_TYPES",
    "drop_column_mask",
    "list_masked_columns",
    "set_column_mask",
]

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

#: Valid mask function type tokens (lowercase).
VALID_MASK_FUNCTION_TYPES: frozenset[str] = frozenset({"default", "email", "random", "partial"})

_MAX_PADDING_LEN = 128

# Unicode line/paragraph separators that act as line breaks in some parsers.
_UNICODE_LINE_SEPS: frozenset[str] = frozenset({"\x85", "\u2028", "\u2029"})

# ---------------------------------------------------------------------------
# SQL templates (read)
# ---------------------------------------------------------------------------

_LIST_MASKED_COLUMNS_SQL = """\
SELECT
    s.name AS schema_name,
    o.name AS table_name,
    c.name AS column_name,
    mc.masking_function
FROM sys.masked_columns mc
JOIN sys.columns c ON mc.object_id = c.object_id AND mc.column_id = c.column_id
JOIN sys.objects o ON mc.object_id = o.object_id
JOIN sys.schemas s ON o.schema_id = s.schema_id
WHERE mc.is_masked = 1
ORDER BY s.name, o.name, c.name;
"""

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _validate_and_escape_padding(padding: str) -> str:
    """Validate and escape a padding string for use inside ``partial(...)`` mask function.

    The full SQL statement embeds the padding as:
    ``ALTER TABLE ... ADD MASKED WITH (FUNCTION = 'partial(N,"<padding>",M)')``.
    The padding is inside double quotes ``"..."`` within the outer single-quoted
    SQL string literal ``'...'``.

    Validation rules:

    - Rejects empty padding.
    - Rejects strings longer than 128 characters.
    - Rejects ``"`` (double quote): cannot safely escape inside the ``"..."``
      inner delimiters.
    - Rejects ``)`` (close-paren): would close the ``partial(...)`` call prematurely.
    - Rejects control characters (``ord(c) < 0x20`` or ``ord(c) == 0x7F``).
    - Rejects Unicode line separators U+0085, U+2028, U+2029.
    - Rejects ``--`` (SQL line comment sequence).
    - Rejects ``;`` (statement separator).

    Single quotes are escaped by doubling (``'`` -> ``''``) per T-SQL string
    literal rules, because the padding lives inside the outer ``'...'`` literal.

    Args:
        padding: The raw padding string from the caller.

    Returns:
        The escaped padding string, safe for embedding in the SQL string literal.

    Raises:
        ValueError: If the padding fails any validation rule.
    """
    if not padding:
        msg = "padding must not be empty"
        raise ValueError(msg)
    if len(padding) > _MAX_PADDING_LEN:
        msg = f"padding must not exceed {_MAX_PADDING_LEN} characters; got {len(padding)}"
        raise ValueError(msg)
    if '"' in padding:
        msg = (
            'padding must not contain a double quote (") '
            '-- it would break the inner "..." delimiter'
        )
        raise ValueError(msg)
    if ")" in padding:
        msg = "padding must not contain ')' -- it would close the partial() call prematurely"
        raise ValueError(msg)
    for ch in padding:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:  # noqa: PLR2004
            msg = f"padding must not contain control characters; found ord={ord(ch):#04x}"
            raise ValueError(msg)
        if ch in _UNICODE_LINE_SEPS:
            msg = f"padding must not contain Unicode line separators; found U+{ord(ch):04X}"
            raise ValueError(msg)
    if "--" in padding:
        msg = "padding must not contain SQL line comment sequence '--'"
        raise ValueError(msg)
    if ";" in padding:
        msg = "padding must not contain statement separator ';'"
        raise ValueError(msg)
    # T-SQL string escaping: single quote -> doubled single quote.
    return padding.replace("'", "''")


def _build_mask_function(
    fn_type: str,
    *,
    start: int | None = None,
    end: int | None = None,
    prefix: int | None = None,
    padding: str | None = None,
    suffix: int | None = None,
) -> str:
    """Build a mask function literal for use in ``ADD MASKED WITH (FUNCTION = '...')``.

    The returned string is the raw function call (without surrounding quotes),
    e.g. ``"default()"``, ``"email()"``, ``"random(1, 12)"``, or
    ``'partial(2,"XXXX",2)'``.

    Args:
        fn_type: Mask function type - one of ``"default"``, ``"email"``,
            ``"random"``, ``"partial"`` (case-insensitive).
        start: Lower bound for ``random()`` masking (required for ``random``).
        end: Upper bound for ``random()`` masking (required for ``random``).
            Must be >= *start*.
        prefix: Number of leading characters to expose for ``partial()`` masking
            (required for ``partial``).
        padding: Replacement padding string for ``partial()`` masking (required
            for ``partial``). Validated and escaped by
            :func:`_validate_and_escape_padding`.
        suffix: Number of trailing characters to expose for ``partial()`` masking
            (required for ``partial``).

    Returns:
        The mask function literal string (no outer quotes).

    Raises:
        ValueError: If *fn_type* is unknown, required arguments are missing,
            or inapplicable arguments are provided for the chosen function.
    """
    lower = fn_type.lower()
    if lower not in VALID_MASK_FUNCTION_TYPES:
        msg = (
            f"Invalid mask function type {fn_type!r}: "
            f"must be one of {', '.join(sorted(VALID_MASK_FUNCTION_TYPES))}"
        )
        raise ValueError(msg)

    if lower in {"default", "email"}:
        _extra = [
            name
            for name, val in (
                ("--start", start),
                ("--end", end),
                ("--prefix", prefix),
                ("--padding", padding),
                ("--suffix", suffix),
            )
            if val is not None
        ]
        if _extra:
            msg = f"{lower}() mask does not accept arguments; unexpected: {', '.join(_extra)}"
            raise ValueError(msg)
        return f"{lower}()"

    if lower == "random":
        _extra = [
            name
            for name, val in (
                ("--prefix", prefix),
                ("--padding", padding),
                ("--suffix", suffix),
            )
            if val is not None
        ]
        if _extra:
            msg = f"random() mask does not accept: {', '.join(_extra)}; use --start and --end"
            raise ValueError(msg)
        if start is None or end is None:
            msg = "random() mask requires both --start and --end arguments"
            raise ValueError(msg)
        if int(start) > int(end):
            msg = f"random() mask requires start <= end; got start={start}, end={end}"
            raise ValueError(msg)
        return f"random({int(start)}, {int(end)})"

    # partial
    _extra = [name for name, val in (("--start", start), ("--end", end)) if val is not None]
    if _extra:
        msg = (
            f"partial() mask does not accept: {', '.join(_extra)}; "
            "use --prefix, --padding, and --suffix"
        )
        raise ValueError(msg)
    if prefix is None or padding is None or suffix is None:
        msg = "partial() mask requires --prefix, --padding, and --suffix arguments"
        raise ValueError(msg)
    escaped = _validate_and_escape_padding(padding)
    return f'partial({int(prefix)},"{escaped}",{int(suffix)})'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_masked_columns(
    target: SqlTarget,
    *,
    table_schema: str | None = None,
    table_name: str | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> list[MaskedColumn]:
    """Return columns that have dynamic data masking applied.

    Reads from ``sys.masked_columns`` joined to ``sys.columns``, ``sys.objects``,
    and ``sys.schemas``.

    Args:
        target: SQL connection target.
        table_schema: Optional schema filter (case-insensitive match).
        table_name: Optional table name filter (case-insensitive match).
        mode: Credential mode for Entra authentication.

    Returns:
        List of :class:`~fabric_dw.models.MaskedColumn` objects, ordered by
        schema, table, then column name.
    """

    def _run() -> list[MaskedColumn]:
        cols, rows = run_query(target, _LIST_MASKED_COLUMNS_SQL, mode=mode)
        result: list[MaskedColumn] = []
        for row in rows:
            d = dict(zip(cols, row, strict=True))
            s_name = str(d["schema_name"])
            t_name = str(d["table_name"])
            c_name = str(d["column_name"])
            m_fn = str(d["masking_function"])

            if table_schema is not None and s_name.lower() != table_schema.lower():
                continue
            if table_name is not None and t_name.lower() != table_name.lower():
                continue

            result.append(
                MaskedColumn(
                    schema_name=s_name,
                    table_name=t_name,
                    column_name=c_name,
                    masking_function=m_fn,
                )
            )
        return result

    return await asyncio.to_thread(_run)


async def set_column_mask(
    target: SqlTarget,
    table_schema: str,
    table_name: str,
    column_name: str,
    fn_type: str,
    *,
    start: int | None = None,
    end: int | None = None,
    prefix: int | None = None,
    padding: str | None = None,
    suffix: int | None = None,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> str:
    """Apply or replace a dynamic data mask on a column.

    Executes
    ``ALTER TABLE [schema].[table] ALTER COLUMN [col] ADD MASKED WITH (FUNCTION = '...')``.
    ``ADD MASKED`` replaces any existing mask on the column without error.

    Args:
        target: SQL connection target.
        table_schema: Schema name of the target table.
        table_name: Name of the target table.
        column_name: Name of the column to mask.
        fn_type: Mask function type - ``"default"``, ``"email"``, ``"random"``,
            or ``"partial"`` (case-insensitive).
        start: Lower bound for ``random()`` masking (required for ``random``).
        end: Upper bound for ``random()`` masking (required for ``random``).
            Must be >= *start*.
        prefix: Leading characters to expose for ``partial()`` masking
            (required for ``partial``).
        padding: Replacement padding string for ``partial()`` masking (required
            for ``partial``). Validated and escaped internally.
        suffix: Trailing characters to expose for ``partial()`` masking
            (required for ``partial``).
        mode: Credential mode for Entra authentication.

    Returns:
        The mask function literal that was applied, e.g. ``"email()"``.

    Raises:
        ValueError: If any identifier is invalid or the mask function args are wrong.
    """
    validate_identifier(table_schema)
    validate_identifier(table_name)
    validate_column_name(column_name)
    mask_fn_literal = _build_mask_function(
        fn_type,
        start=start,
        end=end,
        prefix=prefix,
        padding=padding,
        suffix=suffix,
    )
    table_ref = f"{quote_identifier(table_schema)}.{quote_identifier(table_name)}"
    col_ref = quote_identifier(column_name)
    ddl = (
        f"ALTER TABLE {table_ref} ALTER COLUMN {col_ref} "
        f"ADD MASKED WITH (FUNCTION = '{mask_fn_literal}');"
    )

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)
    return mask_fn_literal


async def drop_column_mask(
    target: SqlTarget,
    table_schema: str,
    table_name: str,
    column_name: str,
    *,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Remove a dynamic data mask from a column.

    Executes ``ALTER TABLE [schema].[table] ALTER COLUMN [col] DROP MASKED``.

    This is a destructive operation: the masking rule is permanently removed.

    Args:
        target: SQL connection target.
        table_schema: Schema name of the target table.
        table_name: Name of the target table.
        column_name: Name of the column to unmask.
        mode: Credential mode for Entra authentication.

    Raises:
        ValueError: If any identifier is invalid.
    """
    validate_identifier(table_schema)
    validate_identifier(table_name)
    validate_column_name(column_name)
    table_ref = f"{quote_identifier(table_schema)}.{quote_identifier(table_name)}"
    col_ref = quote_identifier(column_name)
    ddl = f"ALTER TABLE {table_ref} ALTER COLUMN {col_ref} DROP MASKED;"

    def _run() -> None:
        run_query(target, ddl, mode=mode, autocommit=True, fetch="none")

    await asyncio.to_thread(_run)
