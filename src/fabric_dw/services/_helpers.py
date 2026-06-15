"""Shared utilities for Fabric DW services."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import Protocol, TypeVar
from uuid import UUID

__all__ = ["compact", "reject_non_select", "scan_all_workspaces"]

_T = TypeVar("_T")


class _HasNameAndId(Protocol):
    """Structural protocol for objects with ``name`` and ``id`` attributes."""

    @property
    def name(self) -> str: ...

    @property
    def id(self) -> UUID: ...


def compact(mapping: Mapping[str, object]) -> dict[str, object]:
    """Return a copy of *mapping* with all ``None``-valued entries removed.

    Use this to build request bodies that should omit optional fields::

        body = compact({"displayName": name, "description": description})

    Args:
        mapping: A mapping whose values may be ``None``.

    Returns:
        A new ``dict[str, object]`` with every key whose value is ``None``
        filtered out.
    """
    return {k: v for k, v in mapping.items() if v is not None}


async def scan_all_workspaces(
    workspaces: Sequence[_HasNameAndId],
    fetch: Callable[[_HasNameAndId], Coroutine[object, object, list[_T]]],
    *,
    logger: logging.Logger,
    skip_errors: tuple[type[BaseException], ...],
) -> list[_T]:
    """Fan-out *fetch* over every workspace with bounded concurrency.

    Workspaces that raise any exception in *skip_errors* are skipped with a
    per-workspace ``warning`` log entry.  Any other exception (including other
    :class:`BaseException` subclasses) propagates immediately.

    Args:
        workspaces: Sequence of workspace objects.  Each element must have a
            ``name`` attribute used in log messages.
        fetch: Async callable that receives a workspace object and returns a
            ``list[T]`` of items for that workspace.
        logger: Logger for per-workspace and summary warnings.
        skip_errors: Exception types to skip (log + continue).

    Returns:
        A flat list of all items collected from accessible workspaces.
    """
    # Import here to avoid circular imports at module level.
    from fabric_dw.services._concurrency import bounded_gather  # noqa: PLC0415

    total = len(workspaces)
    raw = await bounded_gather(
        [lambda ws=ws: fetch(ws) for ws in workspaces],  # type: ignore[misc]
        return_exceptions=True,
    )

    out: list[_T] = []
    skipped = 0
    for ws, result in zip(workspaces, raw, strict=True):
        if isinstance(result, skip_errors):
            logger.warning("skipping workspace %s: %s", ws.name, result)
            skipped += 1
        elif isinstance(result, BaseException):
            raise result
        else:
            out.extend(result)  # type: ignore[arg-type]

    if skipped:
        logger.warning("skipped %d of %d workspaces due to access errors", skipped, total)

    return out


# ---------------------------------------------------------------------------
# SELECT-lead validator (shared by tables CTAS and view DDL paths)
# ---------------------------------------------------------------------------

# Pre-compiled patterns used by reject_non_select — each anchored at the
# current scan position (used with re.match, not re.search).
#
# Block-comment pattern uses the "unrolled loop" technique to stay linear:
#   /\*          — opening delimiter
#   [^*]*        — any non-star characters (fast, no backtracking with *)
#   (?:\*+[^*/][^*]*)* — one-or-more stars NOT followed by /: consume the star
#                        run plus the next non-star character and repeat
#   \*+/         — the closing *+/
# This is equivalent to /\*.*?\*/ with re.DOTALL but avoids catastrophic
# backtracking on inputs like "/*" + "*//*" * N.
_BLOCK_COMMENT_RE = re.compile(r"/\*[^*]*(?:\*+[^*/][^*]*)*\*+/")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_WHITESPACE_RE = re.compile(r"\s+")
_SELECT_OR_WITH_RE = re.compile(r"(?:WITH|SELECT)\b", re.IGNORECASE)


def reject_non_select(body: str) -> None:
    """Raise ValueError if *body* does not start with SELECT or WITH (after comments).

    Only the first non-comment keyword is checked.  Single-line (``--``) and
    block (``/* … */``) comments are stripped before the check.

    ``WITH`` is allowed to support Common Table Expressions (CTEs) of the form
    ``WITH cte AS (...) SELECT ...``.  A ``WITH … UPDATE`` body is *not* caught
    here — the Fabric API will reject non-SELECT bodies at the server side.
    This validator is an inexpensive first-line filter only.

    Implementation note: the check is done procedurally — consuming leading
    whitespace and comments token-by-token — rather than with a single nested
    quantifier regex.  The old ``(?:\\s*(?:/\\*.*?\\*/|--[^\\n]*\\n))*``
    pattern caused catastrophic (exponential) backtracking on adversarial
    inputs such as ``"/*" + "*//*" * N`` (CodeQL py/redos, high severity).
    Each sub-pattern used here is linear and unambiguous.

    Args:
        body: The raw SQL supplied as the DDL body (CTAS or CREATE VIEW AS body).

    Raises:
        ValueError: If the first keyword is not SELECT or WITH (CTE).
    """
    pos = 0
    length = len(body)
    while pos < length:
        # Consume leading whitespace.
        m = _WHITESPACE_RE.match(body, pos)
        if m:
            pos = m.end()
            continue
        # Consume a block comment /* ... */.
        m = _BLOCK_COMMENT_RE.match(body, pos)
        if m:
            pos = m.end()
            continue
        # Consume a line comment -- ...\n (or -- ... at end of string).
        m = _LINE_COMMENT_RE.match(body, pos)
        if m:
            pos = m.end()
            continue
        # Nothing consumed — we are at the first real token.
        break

    if not _SELECT_OR_WITH_RE.match(body, pos):
        msg = "body must begin with SELECT or WITH (CTE) (leading comments are allowed)"
        raise ValueError(msg)
