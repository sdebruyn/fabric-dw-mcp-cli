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

Telemetry
---------
Both :func:`mutating_tool` and the :class:`InstrumentedFastMCP` subclass
wrap every registered tool with a timing + ``command_invoked`` telemetry
call (fire-and-forget, never raises).
"""

from __future__ import annotations

import inspect
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
from fabric_dw.mcp import _guards
from fabric_dw.resolver import Resolver
from fabric_dw.sql import SqlTarget
from fabric_dw.sql_io import json_safe as _json_safe
from fabric_dw.telemetry_commands import (
    emit_command_invoked,
    map_status,
    now_ms,
)

__all__ = [
    "InstrumentedFastMCP",
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


_TELEMETRY_WRAPPED_ATTR = "__fabric_telemetry_wrapped__"


def _wrap_mcp_tool_with_telemetry(
    fn: Callable[_P, Coroutine[None, None, _R]],
    name: str,
    *,
    destructive: bool = False,
) -> Callable[_P, Coroutine[None, None, _R]]:
    """Return *fn* wrapped with a fire-and-forget ``command_invoked`` telemetry call.

    The wrapper records wall-clock duration and maps the outcome to a status
    string before calling :func:`~fabric_dw.telemetry_commands.emit_command_invoked`.
    Telemetry failures are swallowed inside ``emit_command_invoked``; they
    never propagate to the caller.

    The wrapper sets ``__fabric_telemetry_wrapped__ = True`` on the returned
    callable so that :class:`InstrumentedFastMCP` can detect already-wrapped
    functions and skip the second wrapping (preventing double-emission).

    Args:
        fn: The async tool function to wrap.
        name: The MCP tool name string (used as the ``name`` attribute).
        destructive: When ``True``, the ``destructive_op`` attribute is set.

    Returns:
        A new coroutine function with the same signature that emits telemetry.
    """

    @wraps(fn)
    async def telemetry_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        start = now_ms()
        exc_seen: BaseException | None = None
        try:
            return await fn(*args, **kwargs)
        except BaseException as exc:
            exc_seen = exc
            raise
        finally:
            duration = now_ms() - start
            status = map_status(exc_seen)
            emit_command_invoked(
                name=name,
                surface="mcp",
                status=status,
                duration_ms=duration,
                destructive=destructive,
            )

    # Mark as already instrumented so InstrumentedFastMCP.tool() skips re-wrapping.
    setattr(telemetry_wrapper, _TELEMETRY_WRAPPED_ATTR, True)
    return telemetry_wrapper


def mutating_tool(
    mcp: FastMCP,
    name: str,
    *,
    destructive: bool = False,
) -> Callable[
    [Callable[_P, Coroutine[None, None, _R]]],
    Callable[_P, Coroutine[None, None, _R]],
]:
    """Decorator factory that registers a mutating MCP tool using a single name.

    Combines ``@mcp.tool(name=name)`` with an injected
    :func:`~fabric_dw.mcp._guards.assert_writes_allowed` call so that the tool
    name is written exactly once.  Without this helper the name was duplicated
    in the decorator and in the ``assert_writes_allowed("…")`` call (M21).

    When *destructive* is ``True``, an additional
    :func:`~fabric_dw.mcp._guards.assert_destructive_allowed` call is injected
    after the write guard, eliminating the second duplicate for permanently-destructive
    tools (drop, delete, clear, restore-in-place, etc.).

    A fire-and-forget ``command_invoked`` telemetry event is emitted after the
    wrapped function returns or raises, regardless of outcome.

    Usage::

        @mutating_tool(mcp, "create_view")
        async def create_view(workspace: str, ...) -> dict[str, Any]:
            # assert_writes_allowed is called automatically with "create_view"
            ...

        @mutating_tool(mcp, "drop_view", destructive=True)
        async def drop_view(workspace: str, ...) -> dict[str, Any]:
            # assert_writes_allowed + assert_destructive_allowed both called automatically
            ...

    Args:
        mcp: The :class:`~mcp.server.fastmcp.FastMCP` server instance.
        name: The tool name string, used for both registration and the write guard.
        destructive: When ``True``, also call
            :func:`~fabric_dw.mcp._guards.assert_destructive_allowed` before the
            wrapped function.  Defaults to ``False``.

    Returns:
        A decorator that wraps the tool function and registers it with *mcp*.
    """

    def decorator(
        fn: Callable[_P, Coroutine[None, None, _R]],
    ) -> Callable[_P, Coroutine[None, None, _R]]:
        @wraps(fn)
        async def guard_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            _guards.assert_writes_allowed(name)
            if destructive:
                _guards.assert_destructive_allowed()
            return await fn(*args, **kwargs)

        # Add telemetry around the guard wrapper.
        tel_wrapped = _wrap_mcp_tool_with_telemetry(guard_wrapper, name, destructive=destructive)

        # Register the wrapper (which includes the guard) under the given name.
        registered: Callable[_P, Coroutine[None, None, _R]] = mcp.tool(name=name)(tel_wrapped)
        return registered

    return decorator


class InstrumentedFastMCP(FastMCP):
    """A :class:`~mcp.server.fastmcp.FastMCP` subclass that wraps every
    ``@mcp.tool(name=...)`` call with a fire-and-forget ``command_invoked``
    telemetry event.

    This class is used as the single choke-point for MCP telemetry
    instrumentation for *read-only* tools (those registered with
    ``@mcp.tool(name=...)``).  Mutating tools use :func:`mutating_tool`
    which wraps telemetry explicitly.

    No changes to any tool registration code are required: all existing
    ``@mcp.tool(name=...)`` calls automatically gain instrumentation.
    """

    def tool(  # type: ignore[override]  # noqa: PLR0913
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: Any = None,  # noqa: ANN401
        icons: Any = None,  # noqa: ANN401
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,  # noqa: FBT001
    ) -> Callable[[Any], Any]:
        """Override :meth:`FastMCP.tool` to inject per-call telemetry."""
        parent_decorator = super().tool(
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )

        def instrumented_decorator(fn: Any) -> Any:  # noqa: ANN401
            tool_name = name if name is not None else fn.__name__
            # Skip wrapping when the callable is already instrumented (e.g. from
            # mutating_tool) to guarantee exactly ONE command_invoked event per call.
            already_wrapped = getattr(fn, _TELEMETRY_WRAPPED_ATTR, False)
            # Wrap only coroutine functions; passthrough for sync (safety net).
            # Use inspect.iscoroutinefunction (asyncio variant is deprecated in 3.12+).
            if not already_wrapped and inspect.iscoroutinefunction(fn):
                tel_fn = _wrap_mcp_tool_with_telemetry(fn, tool_name)
            else:
                tel_fn = fn
            return parent_decorator(tel_fn)

        return instrumented_decorator


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
