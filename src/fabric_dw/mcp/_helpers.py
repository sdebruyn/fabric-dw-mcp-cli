"""Shared helpers used across MCP tool modules.

This module provides utilities imported by every domain tool module:

- :func:`fabric_err` — convert a :class:`~fabric_dw.exceptions.FabricError`
  to a :class:`~mcp.server.fastmcp.exceptions.ToolError` with structured data.
- :func:`tool_err` — uniform error funnel mapping FabricError / ValueError /
  Exception to ToolError without inline ternaries.
- :func:`require_warehouse` — reject SQL Analytics Endpoint items for DDL
  operations.
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
from typing import Any
from uuid import UUID

from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.cache import ItemEntry as _ItemEntry
from fabric_dw.exceptions import FabricError
from fabric_dw.models import WarehouseKind
from fabric_dw.resolver import Resolver
from fabric_dw.sql import SqlTarget
from fabric_dw.sql_io import json_safe as _json_safe

__all__ = [
    "fabric_err",
    "make_sql_target",
    "parse_qualified_name",
    "require_warehouse",
    "resolve_item",
    "safe_rows",
    "tool_err",
]

_log = logging.getLogger(__name__)

_SQL_ENDPOINT_DDL_ERROR = "SQL Analytics Endpoints are read-only; CREATE/DROP SCHEMA not supported"


def fabric_err(exc: FabricError | Exception) -> ToolError:
    """Convert a :class:`~fabric_dw.exceptions.FabricError` to a :class:`ToolError`.

    For :class:`FabricError` instances the message is enriched with structured
    metadata (HTTP status code, request ID, hint) so MCP clients receive all
    the information they need for diagnostics.  The metadata is appended as a
    JSON-encoded suffix on a second line so free-text parsers still see a
    clean first line.

    Args:
        exc: The exception to convert.  When *exc* is a plain
            :class:`Exception` (not a :class:`FabricError`), only the message
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


def tool_err(exc: FabricError | Exception) -> ToolError:
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


def require_warehouse(entry: _ItemEntry, item: str) -> None:
    """Raise :class:`ToolError` if *entry* is a SQL Analytics Endpoint.

    DDL operations (CREATE SCHEMA, DROP SCHEMA) are not supported on SQL
    Analytics Endpoints, which are read-only views over Lakehouse data.

    Args:
        entry: The resolved item entry.
        item: The item name/GUID as supplied by the caller (used in the error
            message).

    Raises:
        ToolError: If the resolved item is a SQL Analytics Endpoint.
    """
    if entry.kind == WarehouseKind.SQL_ENDPOINT:
        raise ToolError(f"{item!r}: {_SQL_ENDPOINT_DDL_ERROR}")  # noqa: TRY003


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
        raise ToolError(  # noqa: TRY003
            f"qualified_name must be <schema>.<{kind}>, got {qualified_name!r}"
        )
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
        raise ToolError(  # noqa: TRY003
            f"item {item!r} has no connection string; cannot execute SQL"
        )
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
        entry = await resolver.item(workspace, item)     # lookup 2 (internal)

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
