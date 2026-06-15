"""Shared helpers used across MCP tool modules.

This module provides utilities imported by every domain tool module:

- :func:`fabric_err` — convert a :class:`~fabric_dw.exceptions.FabricError`
  to a :class:`~mcp.server.fastmcp.exceptions.ToolError` with structured data.
- :func:`tool_err` — uniform error funnel mapping FabricError / ValueError /
  Exception to ToolError without inline ternaries.
- :func:`mutating_tool` — decorator factory that registers a tool with FastMCP
  **and** injects :func:`~fabric_dw.mcp._guards.assert_writes_allowed` using
  the same name string, eliminating the duplication flagged by M21.
- :func:`parse_iso8601` — parse an ISO-8601 string to :class:`~datetime.datetime`,
  raising :class:`~mcp.server.fastmcp.exceptions.ToolError` on bad input.
- :func:`parse_qualified_name` — split ``"schema.object"`` strings, raising
  :class:`~mcp.server.fastmcp.exceptions.ToolError` on bad input.
- :func:`make_sql_target` — build a :class:`~fabric_dw.sql.SqlTarget` from a
  resolved item entry and workspace ID, including the connection-string guard.
- :func:`resolve_item` — return ``(workspace_id, ItemEntry)`` in one resolver
  round-trip so tools avoid the double workspace-lookup pattern.
- :func:`safe_rows` — apply ``json_safe`` to every cell in a row-set in one
  place, removing duplicated list-comprehensions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from datetime import datetime
from functools import wraps
from typing import Any, ParamSpec, TypeVar
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.cache import ItemEntry as _ItemEntry
from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._guards import assert_writes_allowed as _assert_writes_allowed
from fabric_dw.resolver import Resolver
from fabric_dw.sql import SqlTarget
from fabric_dw.sql_io import json_safe as _json_safe

__all__ = [
    "fabric_err",
    "make_sql_target",
    "mutating_tool",
    "parse_iso8601",
    "parse_qualified_name",
    "resolve_item",
    "safe_rows",
    "tool_err",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")

_log = logging.getLogger(__name__)

# Note: CREATE/DROP SCHEMA, views, procedures, and functions are all supported
# on SQL Analytics Endpoints — see T-SQL Applies-to reference for Fabric.
# No DDL guard is needed for these operations; only table DML/DDL is blocked.


def mutating_tool(
    mcp: FastMCP,
    name: str,
) -> Callable[
    [Callable[_P, Coroutine[None, None, _R]]],
    Callable[_P, Coroutine[None, None, _R]],
]:
    """Decorator factory that registers a mutating MCP tool using a single name.

    Combines ``@mcp.tool(name=name)`` with an injected
    :func:`~fabric_dw.mcp._guards.assert_writes_allowed` call so that the tool
    name is written exactly once.  Without this helper the name was duplicated
    in the decorator and in the ``assert_writes_allowed("…")`` call (M21).

    Usage::

        @mutating_tool(mcp, "create_view")
        async def create_view(workspace: str, ...) -> dict[str, Any]:
            # assert_writes_allowed is called automatically with "create_view"
            ...

    Args:
        mcp: The :class:`~mcp.server.fastmcp.FastMCP` server instance.
        name: The tool name string, used for both registration and the write guard.

    Returns:
        A decorator that wraps the tool function and registers it with *mcp*.
    """

    def decorator(
        fn: Callable[_P, Coroutine[None, None, _R]],
    ) -> Callable[_P, Coroutine[None, None, _R]]:
        @wraps(fn)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            _assert_writes_allowed(name)
            return await fn(*args, **kwargs)

        # Register the wrapper (which includes the guard) under the given name.
        registered: Callable[_P, Coroutine[None, None, _R]] = mcp.tool(name=name)(wrapper)
        return registered

    return decorator


def fabric_err(exc: Exception) -> ToolError:
    """Convert a :class:`~fabric_dw.exceptions.FabricError` to a :class:`ToolError`.

    For :class:`FabricError` instances the message is enriched with structured
    metadata (HTTP status code, request ID, hint) so MCP clients receive all
    the information they need for diagnostics.  The metadata is appended as a
    JSON-encoded suffix on a second line so free-text parsers still see a
    clean first line.

    Args:
        exc: The exception to convert.  When *exc* is not a
            :class:`~fabric_dw.exceptions.FabricError`, only the message
            is included.

    Returns:
        A :class:`ToolError` whose message includes the exception type, the
        formatted exception string (which already contains hint / request_id
        via :meth:`FabricError.__str__`), and — for :class:`FabricError`
        instances — a ``|meta|``-prefixed JSON block with ``error_type``,
        ``status``, and ``request_id`` for machine parsing.
    """
    import json  # noqa: PLC0415

    err_type = type(exc).__name__
    msg = f"{err_type}: {exc}"
    if isinstance(exc, FabricError):
        meta: dict[str, Any] = {"error_type": err_type}
        if exc.status is not None:
            meta["status"] = exc.status
        if exc.request_id is not None:
            meta["request_id"] = exc.request_id
        if exc.hint is not None:
            meta["hint"] = exc.hint
        if len(meta) > 1:
            msg = f"{msg}\n|meta| {json.dumps(meta)}"
    return ToolError(msg)


def tool_err(exc: Exception) -> ToolError:
    """Uniform error funnel: FabricError → structured ToolError, other → ToolError(str).

    Use this in ``except (ValueError, FabricError)`` blocks to replace the
    verbose inline ternary pattern::

        raise tool_err(exc) from exc

    Args:
        exc: Any exception from a service call.

    Returns:
        A :class:`ToolError` appropriate for the exception type.
    """
    if isinstance(exc, FabricError):
        return fabric_err(exc)
    return ToolError(str(exc))


def parse_iso8601(value: str | None, param: str) -> datetime | None:
    """Parse an ISO-8601 string to :class:`~datetime.datetime`.

    Returns ``None`` when *value* is ``None`` (optional timestamp parameters
    default to server-side semantics such as ``CURRENT_TIMESTAMP``).

    This is the single shared ISO-8601 parser for all MCP tools.  Previously
    this logic was duplicated in ``queries.py``, ``sql_pools.py``,
    ``snapshots.py`` (twice), and ``tables.py``.

    Args:
        value: An ISO-8601 datetime string, or ``None``.
        param: The parameter name used in the error message.

    Returns:
        A :class:`~datetime.datetime` parsed from *value*, or ``None`` when
        *value* is ``None``.

    Raises:
        ToolError: When *value* is not a valid ISO-8601 string.
    """
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"invalid {param} {value!r}: expected ISO-8601") from exc


def parse_qualified_name(qualified_name: str, kind: str = "object") -> tuple[str, str]:
    """Split a ``"schema.name"`` qualified identifier.

    Args:
        qualified_name: Dot-separated qualified name, e.g. ``dbo.vw_sales``.
        kind: Human-readable label for the object type used in the error
            message (e.g. ``"view"`` or ``"table"``).

    Returns:
        A ``(schema, name)`` tuple.

    Raises:
        ToolError: When *qualified_name* does not contain exactly one ``.``
            with non-empty parts on both sides.
    """
    schema, _, name = qualified_name.partition(".")
    if not schema or not name:
        raise ToolError(f"qualified_name must be <schema>.<{kind}>, got {qualified_name!r}")
    return schema, name


def make_sql_target(ws_id: UUID, entry: _ItemEntry, item: str) -> SqlTarget:
    """Build a :class:`~fabric_dw.sql.SqlTarget` from a resolved item entry.

    Raises :class:`~mcp.server.fastmcp.exceptions.ToolError` directly when
    *entry* has no connection string, eliminating the ``try: raise FabricError
    # noqa: TRY301`` anti-pattern in every caller.

    Args:
        ws_id: The workspace UUID.
        entry: The resolved item entry.
        item: The warehouse/item name as supplied by the caller (used in the
            error message when the connection string is absent).

    Returns:
        A fully populated :class:`~fabric_dw.sql.SqlTarget`.

    Raises:
        ToolError: When *entry* has no connection string.
    """
    if entry.connection_string is None:
        raise ToolError(f"item {item!r} has no connection string; cannot execute SQL")
    return SqlTarget(
        workspace_id=str(ws_id),
        database=entry.display_name,
        connection_string=entry.connection_string,
    )


async def resolve_item(resolver: Resolver, workspace: str, item: str) -> tuple[UUID, _ItemEntry]:
    """Resolve *workspace* + *item* to a ``(workspace_id, ItemEntry)`` pair.

    The resolver's ``item()`` method internally looks up the workspace ID
    again.  This helper avoids the double workspace-lookup pattern that appears
    throughout the tool handlers::

        ws_id = await resolver.workspace_id(workspace)  # lookup 1
        entry = await resolver.item(workspace, item)  # lookup 2 (internal)

    By calling ``workspace_id`` once and exposing the result alongside the
    entry, callers can pass ``ws_id`` to ``assert_workspace_allowed`` and
    ``make_sql_target`` without redundant network round-trips.

    Args:
        resolver: The active :class:`~fabric_dw.resolver.Resolver`.
        workspace: Workspace name or GUID.
        item: Item (warehouse, endpoint, …) name or GUID.

    Returns:
        ``(workspace_id, entry)`` — both values the tool normally needs.
    """
    ws_id = await resolver.workspace_id(workspace)
    entry = await resolver.item(str(ws_id), item)
    return ws_id, entry


def safe_rows(rows: list[Any]) -> list[list[Any]]:
    """Apply :func:`~fabric_dw.sql_io.json_safe` to every cell in *rows*.

    Centralises the ``[[json_safe(v) for v in row] for row in rows]``
    pattern that previously appeared in multiple tool modules.

    Args:
        rows: Raw row data as returned by the SQL execution layer (a list of
            sequences — lists or tuples depending on the driver).

    Returns:
        A new list-of-lists with all values converted to JSON-safe types
        (strings for datetime/Decimal, base64 strings for bytes with
        ``__base64`` column-name suffix handled by the caller).
    """
    return [[_json_safe(v) for v in row] for row in rows]
