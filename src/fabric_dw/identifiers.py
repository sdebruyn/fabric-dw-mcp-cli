"""SQL identifier utilities: validation, bracket-quoting, and qualified-name parsing.

Public API
----------
- :func:`validate_identifier` — allowlist-regex validator for SQL identifier segments.
- :func:`quote_identifier`    — bracket-quote a validated identifier, escaping ``]``.
- :func:`parse_qualified_name` — split ``schema.object`` on the first dot.
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

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


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
    escape) before wrapping in ``[…]``.  The caller is responsible for ensuring
    *name* was validated first via :func:`validate_identifier` — since
    :func:`validate_identifier` already rejects ``]``, the escaping step is a
    belt-and-suspenders guard that allows this function to be used independently.

    Args:
        name: The raw identifier to quote.

    Returns:
        The bracket-quoted identifier string, e.g. ``"[my_table]"``.
    """
    escaped = name.replace("]", "]]")
    return f"[{escaped}]"


def parse_qualified_name(qualified: str) -> tuple[str, str]:
    """Split *qualified* into ``(schema, object_name)`` on the first dot.

    Only unquoted ``schema.object`` notation is supported.  Bracket-quoted
    names containing a literal dot (e.g. ``[a.b].[c]``) are **not** parsed
    correctly by this function — callers that need to handle such names must
    pre-split them before calling this helper.

    Args:
        qualified: A qualified name of the form ``schema.object``.

    Returns:
        A ``(schema, object_name)`` tuple.

    Raises:
        ValueError: If *qualified* does not contain a dot.
    """
    dot = qualified.index(".")  # raises ValueError when no dot
    return qualified[:dot], qualified[dot + 1 :]
