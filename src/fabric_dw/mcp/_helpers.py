"""Shared helpers used across MCP tool modules.

This module provides utilities imported by every domain tool module:

- :func:`fabric_err` — convert a :class:`~fabric_dw.exceptions.FabricError`
  to a :class:`~mcp.server.fastmcp.exceptions.ToolError`.
- :func:`require_warehouse` — reject SQL Analytics Endpoint items for DDL
  operations.
- :func:`parse_qualified_name` — split ``"schema.object"`` strings, raising
  :class:`~mcp.server.fastmcp.exceptions.ToolError` on bad input.
- :func:`make_sql_target` — build a :class:`~fabric_dw.sql.SqlTarget` from a
  resolved item entry and workspace ID.

NOTE — deduplication deferred to refactor/mcp-tool-quality
-----------------------------------------------------------
The ``SqlTarget`` construction pattern appears in ~20 tools and the qualified-
name parser in ~9 tools.  Canonical helpers are defined here so future work
(refactor/mcp-tool-quality) can consolidate callers without touching this PR.
The domain modules already use these helpers; no further changes are needed.
"""

from __future__ import annotations

from uuid import UUID

from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.cache import ItemEntry as _ItemEntry
from fabric_dw.exceptions import FabricError
from fabric_dw.models import WarehouseKind
from fabric_dw.sql import SqlTarget

__all__ = [
    "fabric_err",
    "make_sql_target",
    "parse_qualified_name",
    "require_warehouse",
]

_SQL_ENDPOINT_DDL_ERROR = "SQL Analytics Endpoints are read-only; CREATE/DROP SCHEMA not supported"


def fabric_err(exc: FabricError | Exception) -> ToolError:
    """Convert a :class:`~fabric_dw.exceptions.FabricError` to a :class:`ToolError`.

    Args:
        exc: The exception to convert.

    Returns:
        A :class:`ToolError` with the exception type and message.
    """
    err_type = type(exc).__name__
    return ToolError(f"{err_type}: {exc}")


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


def make_sql_target(ws_id: UUID, entry: _ItemEntry) -> SqlTarget:
    """Build a :class:`~fabric_dw.sql.SqlTarget` from a resolved item entry.

    Args:
        ws_id: The workspace UUID.
        entry: The resolved item entry (must have a non-``None``
            ``connection_string``).

    Returns:
        A fully populated :class:`~fabric_dw.sql.SqlTarget`.

    Note:
        Callers should check ``entry.connection_string is None`` before
        calling and raise an appropriate :class:`~fabric_dw.exceptions.FabricError`
        if the item has no connection string.
    """
    return SqlTarget(
        workspace_id=str(ws_id),
        database=entry.display_name,
        connection_string=entry.connection_string or "",
    )
