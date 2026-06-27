"""Shared utilities for Fabric DW services."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Coroutine, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, TypeVar
from uuid import UUID

from fabric_dw.services.capacities import ACTIVE_STATE

__all__ = [
    "coerce_to_utc",
    "compact",
    "find_statement_start",
    "normalize_object_definition",
    "reject_non_select",
    "scan_all_workspaces",
]

_log = logging.getLogger(__name__)

_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# Datetime coercion
# ---------------------------------------------------------------------------


def coerce_to_utc(dt: datetime) -> datetime:
    """Return *dt* as a UTC-aware datetime.

    Naive datetimes (no tzinfo) are assumed to be UTC and returned with
    ``tzinfo=UTC``.  Already-aware datetimes are converted to UTC.

    Use this at service-layer boundaries where callers may pass either a naive
    or tz-aware datetime; the convention is that naive means UTC.

    Args:
        dt: A :class:`~datetime.datetime` object, naive or tz-aware.

    Returns:
        A UTC-aware :class:`~datetime.datetime`.
    """
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class _HasNameAndId(Protocol):
    """Structural protocol for objects with ``name`` and ``id`` attributes."""

    @property
    def name(self) -> str: ...

    @property
    def id(self) -> UUID: ...


class _HasNameIdAndCapacity(_HasNameAndId, Protocol):
    """Structural protocol for workspace objects that also carry a capacity ID.

    The ``capacity_id`` attribute is ``None`` when the workspace is not
    attached to a capacity (e.g. a Trial or personal workspace).
    """

    @property
    def capacity_id(self) -> UUID | None: ...


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


def _is_capacity_active(
    ws: _HasNameIdAndCapacity,
    capacity_states: dict[str, str] | None,
) -> bool:
    """Return ``True`` when *ws* should be included in a scan.

    Returns ``False`` (skip the workspace) when:

    * *capacity_states* is not ``None`` (proactive filtering is available) AND
    * the workspace has no ``capacity_id`` attribute, or ``capacity_id`` is
      ``None`` or empty, OR the mapped capacity state is not ``"Active"``.

    When *capacity_states* is ``None`` (the caller lacks ``Capacity.Read.All``
    permission and the proactive filter fell back), this function always
    returns ``True`` so every workspace is attempted and the defensive
    per-workspace error handling takes over.

    Args:
        ws: Workspace object — must implement :class:`_HasNameIdAndCapacity`
            (i.e. exposes ``name``, ``id``, and ``capacity_id``).
        capacity_states: Lower-cased ``{capacity_id: state}`` map as returned
            by :func:`~fabric_dw.services.capacities.get_capacity_states`, or
            ``None`` when proactive filtering is unavailable.

    Returns:
        ``True`` if the workspace should be scanned, ``False`` if it should be
        silently skipped.
    """
    if capacity_states is None:
        # Proactive filtering unavailable — let the defensive path handle it.
        return True

    cap_id: UUID | None = ws.capacity_id
    if cap_id is None:
        return False

    state = capacity_states.get(str(cap_id).lower())
    if state is None:
        # Capacity ID present but absent from the capacity map — treat as
        # unavailable (conservative skip).
        return False

    return state == ACTIVE_STATE


async def scan_all_workspaces(
    workspaces: Sequence[_HasNameIdAndCapacity],
    fetch: Callable[[_HasNameIdAndCapacity], Coroutine[object, object, list[_T]]],
    *,
    logger: logging.Logger,
    skip_errors: tuple[type[BaseException], ...],
    capacity_states: dict[str, str] | None = None,
) -> list[_T]:
    """Fan-out *fetch* over every workspace with bounded concurrency.

    Workspaces that raise any exception in *skip_errors* are skipped with a
    per-workspace ``warning`` log entry.  Any other exception (including other
    :class:`BaseException` subclasses) propagates immediately.

    Proactive capacity filtering
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    When *capacity_states* is provided (a ``{capacity_id_lower: state}`` dict
    from ``GET /v1/capacities``), workspaces whose capacity is not ``"Active"``
    (or whose ``capacity_id`` is ``None``) are skipped **before** the fan-out,
    avoiding the ~22s hang that paused-capacity data-plane calls incur.  The
    skip is logged at ``DEBUG`` level only (silent to the user).

    Defensive fallback
    ~~~~~~~~~~~~~~~~~~
    When *capacity_states* is ``None`` (the caller lacks the capacity-read
    permission), all workspaces are attempted.  A non-retriable
    :class:`~fabric_dw.exceptions.FabricServerError` (``is_retriable=False``)
    on a per-workspace call is treated as a silent skip (``DEBUG`` log), not a
    fatal error.

    Result-classification precedence
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    For each per-workspace result the checks are applied in this order:

    1. ``FabricServerError`` with ``is_retriable=False`` → silent ``DEBUG``
       skip (defensive capacity path).  Checked first so that a
       ``FabricServerError`` subclass that also happens to appear in
       *skip_errors* still gets the quieter treatment.
    2. ``isinstance(result, skip_errors)`` → ``WARNING``-level skip (access
       errors: 403, 404, …).
    3. Any other ``BaseException`` → propagate (unexpected error).
    4. Otherwise → aggregate into the output list.

    Args:
        workspaces: Sequence of workspace objects.  Each element must implement
            :class:`_HasNameIdAndCapacity` (``name``, ``id``, ``capacity_id``).
        fetch: Async callable that receives a workspace object and returns a
            ``list[T]`` of items for that workspace.
        logger: Logger for per-workspace and summary warnings.
        skip_errors: Exception types to skip with a WARNING log (e.g.
            PermissionDeniedError, NotFoundError).
        capacity_states: Optional ``{capacity_id_lower: state}`` map for
            proactive capacity filtering.  Pass ``None`` to disable proactive
            filtering and rely on the defensive fallback only.

    Returns:
        A flat list of all items collected from accessible workspaces.
    """
    # Import here to avoid circular imports at module level.
    from fabric_dw.exceptions import FabricServerError  # noqa: PLC0415
    from fabric_dw.services._concurrency import bounded_gather  # noqa: PLC0415

    # Proactive capacity filter: skip paused/no-capacity workspaces before
    # issuing any data-plane call.  Only active when capacity_states is known.
    capacity_skipped = 0
    active_workspaces: list[_HasNameIdAndCapacity] = []
    for ws in workspaces:
        if _is_capacity_active(ws, capacity_states):
            active_workspaces.append(ws)
        else:
            logger.debug(
                "skipping workspace %s: capacity is not Active (proactive capacity filter)",
                ws.name,
            )
            capacity_skipped += 1

    if capacity_skipped:
        logger.debug(
            "proactively skipped %d workspace(s) with inactive/missing capacity",
            capacity_skipped,
        )

    # The denominator for the access-error summary is the number of workspaces
    # that actually entered the fan-out (after the proactive capacity filter),
    # not the total across all workspaces.
    fan_out_total = len(active_workspaces)
    raw = await bounded_gather(
        [lambda ws=ws: fetch(ws) for ws in active_workspaces],
        return_exceptions=True,
    )

    out: list[_T] = []
    access_skipped = 0
    capacity_defensive_skipped = 0
    for ws, result in zip(active_workspaces, raw, strict=True):
        # Precedence matters — check non-retriable FabricServerError FIRST so
        # that a FabricServerError subclass that also satisfies skip_errors
        # still gets the silent DEBUG treatment (defensive capacity path).
        if isinstance(result, FabricServerError) and not result.is_retriable:
            # Non-retriable 5xx: most likely a paused capacity (defensive path
            # when proactive capacity filter was unavailable).  Skip silently.
            logger.debug(
                "skipping workspace %s: non-retriable server error (capacity likely unavailable)",
                ws.name,
            )
            capacity_defensive_skipped += 1
        elif isinstance(result, skip_errors):
            logger.warning("skipping workspace %s: %s", ws.name, result)
            access_skipped += 1
        elif isinstance(result, BaseException):
            raise result
        else:
            out.extend(result)

    if access_skipped:
        logger.warning(
            "skipped %d of %d workspaces due to access errors",
            access_skipped,
            fan_out_total,
        )
    if capacity_defensive_skipped:
        logger.debug(
            "defensively skipped %d workspace(s) with non-retriable server errors "
            "(capacity likely unavailable)",
            capacity_defensive_skipped,
        )

    return out


# ---------------------------------------------------------------------------
# Definition normaliser (shared by views, functions, and procedures)
# ---------------------------------------------------------------------------

# Fabric Data Warehouse can return a definition from sys.sql_modules where the
# schema and/or object name in the CREATE <TYPE> header are empty (e.g.
# "CREATE VIEW . AS ...", "CREATE FUNCTION . (...)" etc.).  The pattern
# matches CREATE VIEW, CREATE FUNCTION, CREATE PROCEDURE (and their
# CREATE OR ALTER variants), allowing either or both of the schema/name parts
# to be empty or whitespace-only.
#
# The name-token alternation uses a bracket-first, plain-second strategy:
#   bracket form: `\[([^\]]*)\]`              — matches `[anything]`
#   plain form:   `([^\[.\s\(]*)`             — stops at `[`, `.`, whitespace, or `(`
# For the bracket form, optional whitespace (`\s*`) before `[` handles the rare
# case of extra spaces between the dot and the opening bracket
# (e.g. `[dbo].  [vw_sales]`).  Plain-form names must not consume whitespace
# to avoid greedily matching SQL keywords like `AS` in `CREATE PROCEDURE . AS`.
_CREATE_OBJECT_HEADER_RE = re.compile(
    r"(?i)^(\s*CREATE\s+(?:OR\s+ALTER\s+)?(?:VIEW|FUNCTION|PROCEDURE)\s+)"
    r"(?:\[([^\]]*)\]|([^\[.\s]*))"  # schema: [schema] or plain
    r"\."  # dot separator
    r"(?:\s*\[([^\]]*)\]|([^\[.\s\(]*))",  # name: optional-ws [name] or plain
)


def _bracket_escape(s: str) -> str:
    """Escape a T-SQL identifier for use in bracket-quoted form.

    Replaces every ``]`` with ``]]`` so the result is safe to embed inside
    ``[…]`` delimiters.
    """
    return s.replace("]", "]]")


def normalize_object_definition(definition: str, schema_name: str, name: str) -> str:
    """Replace an empty or missing schema/name in a CREATE … header.

    Fabric's ``sys.sql_modules`` can store a definition where the object name
    in the CREATE header is blank (e.g. ``CREATE VIEW . AS ...``).  This helper
    detects that pattern and replaces the header with the correct bracket-quoted
    ``[schema].[name]`` taken from the catalog columns (which are always
    populated correctly via ``sys.views``/``sys.objects``/``sys.procedures``
    JOINed with ``sys.schemas``).

    Covers ``CREATE VIEW``, ``CREATE FUNCTION``, and ``CREATE PROCEDURE`` (and
    their ``CREATE OR ALTER`` variants).  When both parts are already present
    the definition is returned unchanged.

    Returns the definition unchanged (with a ``DEBUG`` log) when:

    * the CREATE header cannot be matched (e.g. leading comment block), or
    * *schema_name* or *name* is empty/blank (would produce ``[].[x]``).

    Args:
        definition: The raw ``sys.sql_modules.definition`` string.
        schema_name: The catalog schema name (from ``sys.schemas.name``).
        name: The catalog object name (from ``sys.views/objects/procedures.name``).

    Returns:
        The definition with the CREATE header corrected, or the original string
        when no correction is needed or is safe to apply.
    """
    # Guard: if the catalog values themselves are blank we cannot produce valid
    # DDL — emit a debug log and return unchanged rather than ``[].[]``.
    if not schema_name.strip() or not name.strip():
        _log.debug(
            "normalize_object_definition: catalog schema/name is blank — skipping normalisation"
        )
        return definition

    m = _CREATE_OBJECT_HEADER_RE.match(definition)
    if m is None:
        # The header could not be matched — most likely a leading comment block
        # (e.g. "-- ...") precedes CREATE.  Log and pass through unchanged.
        _log.debug(
            "normalize_object_definition: CREATE header not matched at position 0 "
            "(possible leading comment) — returning definition unchanged"
        )
        return definition

    prefix = m.group(1)  # "CREATE VIEW " (with leading whitespace)
    # Bracket-quoted and plain alternations are mutually exclusive within each
    # capture group pair; use `or ""` to collapse the None from the inactive branch.
    stored_schema = (m.group(2) or m.group(3) or "").strip()
    stored_name = (m.group(4) or m.group(5) or "").strip()

    if stored_schema and stored_name:
        # Both parts present — nothing to fix.
        return definition

    # At least one part is empty: substitute with the catalog values, escaping
    # any `]` characters so the bracket-quoted form is valid T-SQL DDL.
    effective_schema = stored_schema or _bracket_escape(schema_name)
    effective_name = stored_name or _bracket_escape(name)
    new_header = f"{prefix}[{effective_schema}].[{effective_name}]"
    return new_header + definition[m.end() :]


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


def find_statement_start(definition: str) -> int:
    """Return the index of the first non-comment, non-whitespace character.

    Skips leading whitespace, single-line comments (``--``), and block comments
    (``/* ... */``) so that callers can anchor searches to the real first SQL
    token rather than to text inside a comment.

    Used by rename operations to locate the real ``CREATE ...`` header even when
    the stored definition begins with a documentation comment that contains object
    keywords (e.g. ``-- CREATE FUNCTION helper``).

    Args:
        definition: A raw ``sys.sql_modules.definition`` string (or any SQL text).

    Returns:
        The index of the first character that is neither whitespace nor part of
        a leading comment.  Returns ``len(definition)`` when the entire string
        consists of whitespace and comments.
    """
    pos = 0
    length = len(definition)
    while pos < length:
        m = _WHITESPACE_RE.match(definition, pos)
        if m:
            pos = m.end()
            continue
        m = _BLOCK_COMMENT_RE.match(definition, pos)
        if m:
            pos = m.end()
            continue
        m = _LINE_COMMENT_RE.match(definition, pos)
        if m:
            pos = m.end()
            continue
        break
    return pos


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
