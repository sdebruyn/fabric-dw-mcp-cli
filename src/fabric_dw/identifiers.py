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
    "validate_identifier",
]

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


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
