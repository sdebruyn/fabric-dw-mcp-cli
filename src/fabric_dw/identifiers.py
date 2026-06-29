"""SQL identifier utilities: validation, bracket-quoting, and qualified-name parsing.

Public API
----------
- :func:`validate_identifier` — allowlist-regex validator for SQL identifier segments.
- :func:`quote_identifier`    — bracket-quote a validated identifier, escaping ``]``.
- :func:`parse_qualified_name` — split ``schema.object`` on the first dot, raising
  :class:`ValueError` for missing dot, empty parts, or whitespace-only parts.
"""

from __future__ import annotations

import re

__all__ = [
    "parse_qualified_name",
    "quote_identifier",
    "quote_principal",
    "validate_column_name",
    "validate_identifier",
    "validate_principal_name",
]

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}\Z")

# Principal names in Fabric include Entra UPNs (user@domain.com), B2B guest
# UPNs (user_domain.com#EXT#@tenant.onmicrosoft.com), app GUIDs
# (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx), display names (possibly with spaces,
# hyphens, or apostrophes such as "O'Brien"), and database role names.  The
# pattern is therefore deliberately broader than the plain-identifier regex.
#
# Allowed characters: letters (a-z A-Z), digits (0-9), @, ., -, _, ',
# # (used in B2B guest UPN suffix), and internal spaces (trimmed at
# validation time).
# Hard-rejected: ], ;, control characters (< 0x20 or 0x7F), and the SQL
# line-comment prefix --.
# Maximum length: 128 characters (SQL Server NVARCHAR(128) for sysname).
#
# Safety note: ' and # are only injected via quote_principal(), which wraps the
# entire name in bracket-quotes ([...]).  Inside bracket-quotes, neither ' nor #
# enables SQL injection.  They are therefore safe to allow here.
_PRINCIPAL_NAME_RE = re.compile(r"^[A-Za-z0-9@.\-_'# ]{1,128}\Z")

# ASCII control character boundaries (used in validate_principal_name).
_CTRL_LOW = 0x20  # characters below this (0x00-0x1F) are control chars
_CTRL_DEL = 0x7F  # DEL character

# Maximum length for SQL Server sysname / NVARCHAR(128) identifier columns.
_MAX_NAME_LEN = 128


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def validate_identifier(name: str) -> str:
    """Validate that *name* is a safe SQL identifier segment.

    Accepted pattern: ``[A-Za-z_][A-Za-z0-9_]{0,127}`` (max 128 chars).

    Explicit fast-path rejections (belt-and-suspenders):

    - ``]`` — closes a bracket-quoted identifier; enables injection.
    - ``;`` — statement separator.
    - ``--`` — line comment.

    ASCII identifiers only (regex excludes unicode).  This is a deliberate
    conservative choice: widening the regex without switching to parameterised
    queries would reintroduce injection risk.  Use bracket-quoted names with
    permitted characters only.

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


def quote_identifier(name: str) -> str:
    """Return *name* bracket-quoted for use in a SQL statement.

    Escapes any ``]`` in *name* as ``]]`` (the standard SQL Server bracket-quote
    escape) before wrapping in ``[…]``.

    .. warning::
        Always call :func:`validate_identifier` **before** calling this
        function.  Quoting alone does not prevent injection via newlines, NUL
        bytes, or names longer than 128 characters — all of which
        :func:`validate_identifier` rejects.  The ``]``-escaping here is a
        defence-in-depth measure, not a substitute for validation.

    Args:
        name: The raw identifier to quote.  Should have already been validated
            via :func:`validate_identifier`.

    Returns:
        The bracket-quoted identifier string, e.g. ``"[my_table]"``.
    """
    escaped = name.replace("]", "]]")
    return f"[{escaped}]"


def validate_principal_name(name: str) -> str:
    """Validate that *name* is a safe Fabric database principal name.

    Accepted characters: letters, digits, ``@``, ``.``, ``-``, ``_``, ``'``,
    ``#``, and internal spaces (leading/trailing spaces are stripped before
    matching).  Maximum length: 128 characters.

    Explicit fast-path rejections (belt-and-suspenders):

    - ``]`` — closes a bracket-quoted identifier; enables injection.
    - ``;`` — statement separator.
    - ``--`` — line comment.
    - Control characters (< U+0020 or U+007F) — not valid in principal names.

    This validator handles Entra UPNs (``user@contoso.com``), B2B guest UPNs
    (``user_contoso.com#EXT#@tenant.onmicrosoft.com``), application GUIDs
    (``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``), and role/user display names that
    may contain spaces, apostrophes (e.g. ``O'Brien``), or ``#`` characters.

    Args:
        name: The raw principal name supplied by the caller.

    Returns:
        *name* unchanged (with original surrounding whitespace preserved) if valid.

    Raises:
        ValueError: If *name* contains dangerous characters, is empty, or
            exceeds 128 characters.
    """
    if not name or not name.strip():
        msg = "Principal name must not be empty"
        raise ValueError(msg)
    # Fast-path rejections before regex
    if "]" in name or ";" in name or "--" in name:
        msg = f"Invalid principal name {name!r}: contains forbidden character(s)"
        raise ValueError(msg)
    # Reject any control characters
    if any(ord(c) < _CTRL_LOW or ord(c) == _CTRL_DEL for c in name):
        msg = f"Invalid principal name {name!r}: contains control character(s)"
        raise ValueError(msg)
    stripped = name.strip()
    if not _PRINCIPAL_NAME_RE.match(stripped):
        msg = (
            f"Invalid principal name {name!r}: "
            "allowed characters are letters, digits, @, ., -, _, ', #, and spaces (max 128)"
        )
        raise ValueError(msg)
    return name


def quote_principal(name: str) -> str:
    """Return *name* bracket-quoted for use as a principal in a SQL statement.

    Escapes any ``]`` in *name* as ``]]`` (the standard SQL Server bracket-quote
    escape) before wrapping in ``[…]``.

    .. warning::
        Always call :func:`validate_principal_name` **before** calling this
        function.  The ``]``-escaping here is a defence-in-depth measure only.

    Args:
        name: The principal name to quote.  Should have been validated via
            :func:`validate_principal_name`.

    Returns:
        The bracket-quoted principal string, e.g. ``"[user@contoso.com]"``.
    """
    escaped = name.strip().replace("]", "]]")
    return f"[{escaped}]"


def validate_column_name(name: str) -> str:
    """Validate a column name for use inside bracket-quoted SQL identifiers.

    Column names are always wrapped in ``[...]`` by :func:`quote_identifier`
    before being embedded in SQL, so they may contain characters that the
    stricter :func:`validate_identifier` rejects (spaces, hyphens, leading
    digits, etc.).  This validator permits that broader character set while
    still blocking every pattern that enables injection:

    Explicitly rejected (belt-and-suspenders, before any escaping):

    - ``]`` -- closes the bracket-quoted identifier; enables injection even
      with escaping as the subsequent character would escape the wrong thing.
    - ``;`` -- SQL statement separator.
    - ``--`` -- SQL line-comment prefix.
    - Control characters (``< U+0020`` or ``U+007F``) -- NUL, CR, LF, etc.

    Constraints:

    - Must not be empty.
    - Length cap: 128 characters (``sysname`` / ``NVARCHAR(128)`` limit).

    Defence-in-depth note: :func:`quote_identifier` still escapes any ``]``
    characters found inside the string as ``]]``, so the hard rejection of
    ``]`` here is redundant but retained as an additional safety layer.

    Args:
        name: The raw column name supplied by the caller.

    Returns:
        *name* unchanged if valid.

    Raises:
        ValueError: If *name* contains forbidden characters, is empty, or
            exceeds 128 characters.
    """
    if not name:
        msg = "Column name must not be empty"
        raise ValueError(msg)
    # Fast-path rejections (belt-and-suspenders)
    if "]" in name or ";" in name or "--" in name:
        msg = f"Invalid column name {name!r}: contains forbidden character(s)"
        raise ValueError(msg)
    # Reject control characters (NUL, CR, LF, and all < U+0020 or U+007F)
    if any(ord(c) < _CTRL_LOW or ord(c) == _CTRL_DEL for c in name):
        msg = f"Invalid column name {name!r}: contains control character(s)"
        raise ValueError(msg)
    if len(name) > _MAX_NAME_LEN:
        msg = f"Invalid column name {name!r}: exceeds 128 characters"
        raise ValueError(msg)
    return name


def parse_qualified_name(qualified: str, kind: str = "object") -> tuple[str, str]:
    """Split *qualified* into ``(schema, object_name)`` on the **first** dot.

    Canonical semantics (all callers must conform to these):

    - Splits on the **first** dot only.  Multi-dot input ``"a.b.c"`` returns
      ``("a", "b.c")`` — the remainder after the first dot is the object name.
    - Bracket-quoted names containing a literal dot (e.g. ``[a.b].[c]``) are
      **not** handled correctly — callers that receive such names must pre-split
      them.
    - Whitespace-only schema or object parts (e.g. ``"  .name"`` or
      ``"schema.  "``) are rejected — the strip check catches them.
    - Raises :class:`ValueError` for any invalid input; upper layers
      (:func:`~fabric_dw.mcp._helpers.parse_qualified_name`,
      :func:`~fabric_dw.cli.commands._utils.parse_qualified_name`) convert
      that into their respective error types (:class:`~mcp.server.fastmcp.exceptions.ToolError`,
      :class:`click.UsageError`).

    Args:
        qualified: A qualified name of the form ``schema.<kind>``.  The caller
            is responsible for validating each part via
            :func:`validate_identifier` before embedding it in SQL.
        kind: Human-readable label for the object type used in the error
            message (e.g. ``"view"`` or ``"table"``).  Defaults to
            ``"object"``.

    Returns:
        A ``(schema, object_name)`` tuple.  **Neither part is validated** —
        call :func:`validate_identifier` on each before SQL use.

    Raises:
        ValueError: If *qualified* does not contain a dot, or if either the
            schema part or the object part is empty or whitespace-only.
    """
    if "." not in qualified:
        msg = f"Invalid qualified name {qualified!r}: expected <schema>.<{kind}> (missing dot)"
        raise ValueError(msg)
    dot = qualified.index(".")
    schema = qualified[:dot]
    obj = qualified[dot + 1 :]
    if not schema.strip():
        msg = f"Invalid qualified name {qualified!r}: schema part must not be empty"
        raise ValueError(msg)
    if not obj.strip():
        msg = f"Invalid qualified name {qualified!r}: {kind} part must not be empty"
        raise ValueError(msg)
    return schema, obj
