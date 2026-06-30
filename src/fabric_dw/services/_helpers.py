"""Shared utilities for Fabric DW services."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar
from uuid import UUID

from fabric_dw.auth import CredentialMode
from fabric_dw.exceptions import ItemKindError
from fabric_dw.identifiers import quote_identifier, validate_identifier
from fabric_dw.models import WarehouseKind
from fabric_dw.services.capacities import ACTIVE_STATE
from fabric_dw.services.schemas import _SYSTEM_SCHEMAS
from fabric_dw.sql import SqlTarget, run_query

__all__ = [
    "SelectBodyError",
    "build_time_travel_option",
    "coerce_to_utc",
    "compact",
    "reject_non_select",
    "scan_all_workspaces",
]

_log = logging.getLogger(__name__)

_T = TypeVar("_T")

# ---------------------------------------------------------------------------
# SQL Analytics Endpoint guard
# ---------------------------------------------------------------------------

# Single canonical message used by _assert_not_sql_endpoint across all service
# modules.  Previously each module carried its own copy with a different
# operation-specific wording; this one message is clear, accurate, and
# generic enough to apply to every write operation.
_SQL_ENDPOINT_READONLY_MSG = (
    "SQL Analytics Endpoints are read-only; use a Fabric Data Warehouse for this operation"
)


def _assert_not_sql_endpoint(kind: WarehouseKind) -> None:
    """Raise :class:`~fabric_dw.exceptions.ItemKindError` for SQL Analytics Endpoint items.

    All service-layer write operations that are unsupported on SQL Analytics
    Endpoints call this guard before issuing any network I/O.

    Args:
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the resolved item.

    Raises:
        ItemKindError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT`.
    """
    if kind == WarehouseKind.SQL_ENDPOINT:
        raise ItemKindError(_SQL_ENDPOINT_READONLY_MSG)


# ---------------------------------------------------------------------------
# ALTER SCHEMA TRANSFER helper
#
# Shared by table/view/function/procedure "transfer to another schema"
# operations.  Builds and runs a single parameterised DDL statement from
# validated, bracket-quoted identifiers -- no SQL parsing is involved.
# ---------------------------------------------------------------------------

# Reuse the single canonical system-schema list from services.schemas (sys,
# INFORMATION_SCHEMA, guest, and the fixed db_* role schemas) rather than a
# second, narrower copy -- these schemas are never valid ALTER SCHEMA TRANSFER
# targets either.  Pre-casefolded once at import time.
#
# Case-insensitive comparison is a deliberate, conservative client-side
# choice, not a claim about the warehouse's actual collation: Fabric
# Warehouses use a fixed case-sensitive binary collation
# (Latin1_General_100_BIN2_UTF8) for user data, so a schema literally named
# e.g. "Sys" (distinct from the engine's "sys") is technically possible on
# the server and would be wrongly rejected here.  We accept that asymmetry
# because (a) creating a schema that differs from a system schema only by
# case is already confusing and best avoided, and (b) it is consistent with
# how this codebase treats every other identifier comparison in this guard
# family (e.g. validate_identifier's own forbidden-character checks) as a
# fail-closed, "when in doubt, reject" client-side safety net rather than an
# authoritative mirror of server-side semantics.
_SYSTEM_SCHEMAS_CASEFOLDED: frozenset[str] = frozenset(s.casefold() for s in _SYSTEM_SCHEMAS)


async def _alter_schema_transfer(
    target: SqlTarget,
    *,
    source_schema: str,
    object_name: str,
    target_schema: str,
    mode: CredentialMode = CredentialMode.DEFAULT,
) -> None:
    """Move an object to another schema via ``ALTER SCHEMA ... TRANSFER OBJECT::...``.

    Emits exactly::

        ALTER SCHEMA [target_schema] TRANSFER OBJECT::[source_schema].[object_name]

    Fabric's T-SQL syntax only allows the ``OBJECT::`` entity class -- the
    canonical Microsoft example is ``ALTER SCHEMA Sales TRANSFER
    OBJECT::dbo.Region;`` -- so the prefix is always emitted explicitly.  All
    three identifiers are validated via :func:`~fabric_dw.identifiers.validate_identifier`
    and bracket-quoted via :func:`~fabric_dw.identifiers.quote_identifier`
    before being embedded in the DDL string.  This is a parameterised DDL
    statement built from validated identifiers, not SQL parsing or rewriting.

    *target_schema* is rejected up front when it is a system schema (``sys``,
    ``INFORMATION_SCHEMA``, ``guest``, or a fixed ``db_*`` role schema --
    see :data:`~fabric_dw.services.schemas._SYSTEM_SCHEMAS`).  Those names
    pass :func:`validate_identifier` (they are well-formed identifiers) but
    Microsoft documents that they are never valid ALTER SCHEMA TRANSFER
    targets, so this check catches them before any network I/O.  All four
    transfer operations (table/view/function/procedure) inherit this check
    for free since they all call this shared helper.

    This helper does **not** re-fetch the moved object -- callers re-fetch it
    from *target_schema* using their own object-specific lookup (e.g.
    ``_fetch_table``).

    Args:
        target: The warehouse or SQL Analytics Endpoint to connect to.
        source_schema: The object's current schema.  Must pass
            :func:`validate_identifier`.
        object_name: The (unqualified) object name.  Must pass
            :func:`validate_identifier`.
        target_schema: The schema to move the object into.  Must pass
            :func:`validate_identifier` and must not be a system schema.
        mode: The credential mode for Entra authentication.

    Raises:
        ValueError: If *source_schema*, *object_name*, or *target_schema*
            fails identifier validation, or if *target_schema* is a system
            schema.
        PermissionDeniedError: If the driver reports an ALTER SCHEMA permission error.
        FabricError: If the engine reports an error executing the DDL -- e.g.
            a missing source object or missing target schema.  Such errors may
            surface as a generic :class:`~fabric_dw.exceptions.FabricServerError`
            rather than :class:`~fabric_dw.exceptions.NotFoundError`, because
            the engine's "cannot find the object" message is not in
            ``_NOT_FOUND_FRAGMENTS`` (:mod:`fabric_dw.sql_errors`).
    """
    validate_identifier(source_schema)
    validate_identifier(object_name)
    validate_identifier(target_schema)

    if target_schema.casefold() in _SYSTEM_SCHEMAS_CASEFOLDED:
        msg = (
            f"Target schema {target_schema!r} is a reserved system schema and "
            "cannot be an ALTER SCHEMA TRANSFER target"
        )
        raise ValueError(msg)

    ddl = (
        f"ALTER SCHEMA {quote_identifier(target_schema)} "
        f"TRANSFER OBJECT::{quote_identifier(source_schema)}.{quote_identifier(object_name)}"
    )

    def _run_ddl() -> None:
        run_query(target, ddl, mode=mode, commit=True, fetch="none")

    await asyncio.to_thread(_run_ddl)


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


def _format_ms_literal(dt: datetime) -> str:
    """Return a ``yyyy-MM-ddTHH:mm:ss.fff`` literal for *dt*, rounded to the nearest millisecond.

    Uses :class:`~datetime.timedelta` carry so that microsecond values
    >= 999_500 roll cleanly into the next second rather than producing an
    invalid ``.1000`` fragment.

    Args:
        dt: A UTC-aware :class:`~datetime.datetime`.  The caller is responsible
            for ensuring the value is already in UTC.

    Returns:
        A string such as ``"2024-03-15T10:30:45.123"``.
    """
    # Round to the nearest millisecond (half-to-even via Python round()) rather
    # than truncating, so e.g. 123_750 us -> 124 ms instead of silently losing
    # 0.75 ms.  round() can return 1000 for microsecond values >= 999_500 us;
    # use timedelta to carry the extra millisecond into the seconds field correctly.
    dt_rounded = dt.replace(microsecond=0) + timedelta(milliseconds=round(dt.microsecond / 1000))
    ms_str = f"{dt_rounded.microsecond // 1000:03d}"
    return dt_rounded.strftime("%Y-%m-%dT%H:%M:%S.") + ms_str


def build_time_travel_option(as_of: datetime | None) -> str:
    """Build the Fabric ``OPTION (FOR TIMESTAMP AS OF ...)`` SQL fragment.

    Converts *as_of* to UTC, rounds to the nearest millisecond (half-to-even),
    and formats the literal as ``yyyy-MM-ddTHH:mm:ss.fff``.

    Args:
        as_of: The point-in-time datetime, or *None* to return an empty string.
            Naive datetimes are assumed UTC (via :func:`coerce_to_utc`); tz-aware
            datetimes are converted to UTC.

    Returns:
        A string like ``" OPTION (FOR TIMESTAMP AS OF '2024-03-15T10:30:45.123')"``
        when *as_of* is set, or ``""`` when *as_of* is *None*.

    Note:
        Callers that assemble SQL from a fixed-format template ending in ``";"``
        should insert this fragment before re-adding the semicolon::

            as_of_clause = build_time_travel_option(as_of)
            sql = template[:-1] + as_of_clause + ";"

        When *as_of* is *None* the result is byte-for-byte identical to the
        original template (no semicolon stripping occurs effectively).
    """
    if as_of is None:
        return ""
    at = coerce_to_utc(as_of)
    return f" OPTION (FOR TIMESTAMP AS OF '{_format_ms_literal(at)}')"


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
# Single-read-only-statement validator (shared core)
#
# Used by:
#   - reject_non_select()  (services layer, raises ValueError)
#   - mcp/_guards.py assert_readonly_sql()  (mcp layer, wraps as ToolError)
#
# Design: fully-raw, fail-closed scan.  No comment stripping, no string-literal
# masking, no SQL parsing.  All three checks run on the completely raw body text
# so that a forbidden keyword or a `;`-separated rider is always physically
# present in the scanned text and is always caught.
#
# Fail-closed tradeoffs (by design, documented in tool descriptions):
#   - A body with a leading comment is rejected because the first raw word token
#     comes from inside the comment, not from SELECT or WITH.
#   - A forbidden keyword embedded in a string literal or quoted identifier
#     (e.g. WHERE op = 'DELETE', column [delete]) is also rejected.
#   - A semicolon inside a string literal trips the multi-statement guard.
# ---------------------------------------------------------------------------

# Tokens that must never appear in a CTAS or view body (case-insensitive).
# Mirrors the denylist in mcp/_guards.py assert_readonly_sql exactly; both
# import from this module so they cannot drift.
_FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "INTO",
        "EXEC",
        "EXECUTE",
        "DROP",
        "ALTER",
        "CREATE",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "DENY",
        "KILL",
        "BACKUP",
        "RESTORE",
        "OPENROWSET",
        "OPENQUERY",
        "WRITETEXT",
        "UPDATETEXT",
        "SP_EXECUTESQL",
        "XP_CMDSHELL",
        "WAITFOR",
        "USE",
        "SHUTDOWN",
        "RECONFIGURE",
        "DBCC",
    }
)

_ALLOWED_FIRST_TOKENS: frozenset[str] = frozenset({"SELECT", "WITH"})

# Extracts all word-character sequences from the raw body text.
_TOKEN_RE = re.compile(r"\w+")

# Detects a ';' followed (after optional whitespace) by any non-whitespace
# character — i.e. a second statement following the first.
_MULTI_STMT_RE = re.compile(r";\s*\S")


class SelectBodyError(ValueError):
    """Raised when a SQL body fails single-read-only-statement validation.

    Subclasses :class:`ValueError` so callers that catch ``ValueError`` still
    work.  The ``kind`` attribute lets the MCP layer translate this to a
    :class:`~mcp.server.fastmcp.exceptions.ToolError` with a context-specific
    message without re-parsing the string.

    Attributes:
        kind: One of ``"multi_statement"``, ``"non_select"``, or
            ``"forbidden_token"``.
        token: The problematic token string (first token for ``"non_select"``,
            forbidden keyword for ``"forbidden_token"``, empty string for
            ``"multi_statement"``).
    """

    def __init__(self, kind: str, message: str, token: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.token = token


def _validate_select_body(body: str) -> None:
    """Validate that *body* is a single read-only SELECT/WITH statement.

    Performs a fully-raw, fail-closed scan: no comment stripping, no string-
    literal masking, no SQL parsing.  Checks (in order):

    1. No ``;`` followed by a non-whitespace character (multi-statement batch).
    2. First ``\\w+`` token must be ``SELECT`` or ``WITH``.
    3. No ``\\w+`` token (case-insensitive) from the forbidden-keyword denylist
       (INSERT, UPDATE, DELETE, MERGE, INTO, EXEC, EXECUTE, DROP, ALTER, CREATE,
       TRUNCATE, GRANT, REVOKE, DENY, KILL, BACKUP, RESTORE, OPENROWSET,
       OPENQUERY, WRITETEXT, UPDATETEXT, SP_EXECUTESQL, XP_CMDSHELL, WAITFOR,
       USE, SHUTDOWN, RECONFIGURE, DBCC).

    This is fail-closed by design: a body that embeds a write keyword or a
    ``;`` inside a string literal or quoted identifier, or that starts with a
    comment, is also rejected.  This is intentional.

    Args:
        body: The raw SQL body string to validate.

    Raises:
        SelectBodyError: When any check fails.  The ``kind`` attribute
            identifies which check fired.
    """
    # Check 1: multi-statement batch (';' followed by more tokens).
    if _MULTI_STMT_RE.search(body):
        raise SelectBodyError(
            "multi_statement",
            "body must not contain a multi-statement batch (';' followed by more tokens)",
        )

    tokens = _TOKEN_RE.findall(body)
    first_token = tokens[0].upper() if tokens else ""

    # Check 2: first token must be SELECT or WITH.
    if first_token not in _ALLOWED_FIRST_TOKENS:
        raise SelectBodyError(
            "non_select",
            f"body must begin with SELECT or WITH (got {first_token!r})",
            token=first_token,
        )

    # Check 3: no forbidden keyword anywhere in the body.
    for tok in tokens:
        upper = tok.upper()
        if upper in _FORBIDDEN_TOKENS:
            raise SelectBodyError(
                "forbidden_token",
                f"body must not contain forbidden keyword {upper!r}",
                token=upper,
            )


def reject_non_select(body: str) -> None:
    """Raise :class:`ValueError` when *body* is not a single read-only SELECT statement.

    Applies a fully-raw, fail-closed scan: no comment stripping, no string-
    literal masking, no SQL parsing.  All checks run on the completely raw text.

    ``WITH`` is allowed to support Common Table Expressions (CTEs).  Three
    checks are applied in order:

    1. No ``;`` followed by a non-whitespace character (multi-statement batch).
    2. First raw ``\\w+`` token must be ``SELECT`` or ``WITH``.
    3. No token from the forbidden-keyword denylist (same denylist used by the
       ``FABRIC_MCP_READONLY`` read-only mode guard).

    Fail-closed tradeoffs (by design): a body with a leading comment, a
    forbidden keyword in a string literal (``WHERE op = 'DELETE'``), a
    forbidden keyword as a quoted identifier (``[delete]``, ``"drop"``), or a
    ``;`` inside a string literal is also rejected.  To run such a body,
    reformulate it to avoid the literal keyword or semicolon.

    Args:
        body: The raw SQL supplied as the DDL body (CTAS or CREATE VIEW AS body).

    Raises:
        ValueError: When any check fails.
    """
    _validate_select_body(body)
