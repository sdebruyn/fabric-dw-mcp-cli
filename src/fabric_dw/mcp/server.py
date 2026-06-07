"""FastMCP server exposing all fabric-dw service functions as MCP tools.

Architecture
------------
- One ``FastMCP`` instance (``mcp``) is created at module-import time.
- All Fabric dependencies (HTTP client, SQL client, cache, resolver) are
  constructed **lazily** on first use via module-level singletons so that
  importing the module does not open any network connections.
- Each tool catches :class:`~fabric_dw.exceptions.FabricError` and its
  subclasses and re-raises as :class:`~mcp.server.fastmcp.exceptions.ToolError`
  so callers receive a structured MCP error rather than a raw traceback.
- ``workspace`` and ``warehouse`` parameters are always ``str`` (name OR GUID);
  the :class:`~fabric_dw.resolver.Resolver` translates them to UUIDs
  internally.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver
from fabric_dw.services import audit, queries, snapshots, warehouses, workspaces
from fabric_dw.services import ownership as ownership_svc
from fabric_dw.sql_client import FabricSqlClient, SqlTarget

__all__ = ["mcp", "run"]

# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

mcp: FastMCP = FastMCP("fabric-dw")

# ---------------------------------------------------------------------------
# Lazy singleton helpers
# ---------------------------------------------------------------------------

_http_client: FabricHttpClient | None = None
_sql_client: FabricSqlClient | None = None
_cache_singleton: LookupCache | None = None
_resolver_singleton: Resolver | None = None


def _get_http() -> FabricHttpClient:
    """Return the shared :class:`FabricHttpClient`, creating it on first call."""
    global _http_client  # noqa: PLW0603
    if _http_client is None:
        raw_mode = os.environ.get("FABRIC_AUTH", "default")
        mode = _auth.CredentialMode(raw_mode)
        credential = _auth.get_credential(mode)
        _http_client = FabricHttpClient(credential=credential)
    return _http_client


def _get_sql() -> FabricSqlClient:
    """Return the shared :class:`FabricSqlClient`, creating it on first call."""
    global _sql_client  # noqa: PLW0603
    if _sql_client is None:
        raw_mode = os.environ.get("FABRIC_AUTH", "default")
        mode = _auth.CredentialMode(raw_mode)
        _sql_client = FabricSqlClient(mode=mode)
    return _sql_client


def _get_cache() -> LookupCache:
    """Return the shared :class:`LookupCache`, creating it on first call."""
    global _cache_singleton  # noqa: PLW0603
    if _cache_singleton is None:
        _cache_singleton = LookupCache()
    return _cache_singleton


def _get_resolver() -> Resolver:
    """Return the shared :class:`Resolver`, creating it on first call."""
    global _resolver_singleton  # noqa: PLW0603
    if _resolver_singleton is None:
        _resolver_singleton = Resolver(http=_get_http(), cache=_get_cache())
    return _resolver_singleton


# ---------------------------------------------------------------------------
# Helper: wrap FabricError → ToolError
# ---------------------------------------------------------------------------


def _fabric_err(exc: FabricError) -> ToolError:
    """Convert a :class:`FabricError` to a :class:`ToolError`."""
    err_type = type(exc).__name__
    return ToolError(f"{err_type}: {exc}")


# ---------------------------------------------------------------------------
# Workspace tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_workspaces() -> list[dict[str, Any]]:
    """List all Fabric workspaces the caller has access to."""
    try:
        result = await workspaces.list_all(_get_http())
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [ws.model_dump(by_alias=True, mode="json") for ws in result]


@mcp.tool()
async def get_workspace(workspace: str) -> dict[str, Any]:
    """Return details for a single workspace (name or GUID)."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        result = await workspaces.get(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def get_workspace_collation(workspace: str) -> dict[str, Any]:
    """Return the default Data Warehouse collation for a workspace."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        collation = await workspaces.get_collation(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"collation": collation}


@mcp.tool()
async def set_workspace_collation(workspace: str, collation: str) -> dict[str, Any]:
    """Set the default Data Warehouse collation for a workspace."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        await workspaces.set_collation(_get_http(), ws_id, collation)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"workspace_id": str(ws_id), "collation": collation}


# ---------------------------------------------------------------------------
# Warehouse tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_warehouses(workspace: str) -> list[dict[str, Any]]:
    """List all warehouses and SQL analytics endpoints in a workspace."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        result = await warehouses.list_warehouses(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [wh.model_dump(by_alias=True, mode="json") for wh in result]


@mcp.tool()
async def get_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
    """Return details for a single warehouse (name or GUID)."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        result = await warehouses.get_warehouse(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def create_warehouse(
    workspace: str,
    name: str,
    collation: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a new Warehouse in a workspace."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        result = await warehouses.create(
            _get_http(), ws_id, name, collation=collation, description=description
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def rename_warehouse(
    workspace: str,
    warehouse: str,
    new_name: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Rename a Warehouse (and optionally update its description)."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        result = await warehouses.rename(
            _get_http(), ws_id, item.id, new_name, description=description
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def delete_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
    """Delete a Warehouse."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        await warehouses.delete(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"deleted": True, "warehouse_id": str(item.id)}


@mcp.tool()
async def takeover_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
    """Take ownership of a Warehouse."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        await ownership_svc.takeover(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"taken_over": True, "warehouse_id": str(item.id)}


# ---------------------------------------------------------------------------
# Audit tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_audit_settings(workspace: str, warehouse: str) -> dict[str, Any]:
    """Fetch the current SQL audit settings for a warehouse."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.get_settings(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def enable_audit(workspace: str, warehouse: str, retention_days: int = 0) -> dict[str, Any]:
    """Enable SQL auditing on a warehouse."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.enable(_get_http(), ws_id, item.id, retention_days=retention_days)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def disable_audit(workspace: str, warehouse: str) -> dict[str, Any]:
    """Disable SQL auditing on a warehouse."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.disable(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def set_audit_action_groups(
    workspace: str, warehouse: str, action_groups: list[str]
) -> dict[str, Any]:
    """Replace the audited action groups for a warehouse."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.set_action_groups(_get_http(), ws_id, item.id, action_groups)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


# ---------------------------------------------------------------------------
# Query tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_running_queries(workspace: str, warehouse: str) -> list[dict[str, Any]]:
    """Return all currently-executing queries on a warehouse."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        if item.connection_string is None:
            msg = f"warehouse {warehouse!r} has no connection string; cannot query DMVs"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=warehouse,
            connection_string=item.connection_string,
        )
        result = await queries.list_running(_get_sql(), target)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [q.model_dump(by_alias=True, mode="json") for q in result]


@mcp.tool()
async def kill_session(workspace: str, warehouse: str, session_id: int) -> dict[str, Any]:
    """Terminate a session on a warehouse by session_id."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        if item.connection_string is None:
            msg = f"warehouse {warehouse!r} has no connection string; cannot kill sessions"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=warehouse,
            connection_string=item.connection_string,
        )
        await queries.kill(_get_sql(), target, session_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"killed": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# Snapshot tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_snapshots(workspace: str, warehouse: str) -> list[dict[str, Any]]:
    """Return all snapshots belonging to a warehouse."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        result = await snapshots.list_snapshots(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [s.model_dump(by_alias=True, mode="json") for s in result]


@mcp.tool()
async def create_snapshot(
    workspace: str,
    warehouse: str,
    name: str,
    description: str | None = None,
    snapshot_dt: str | None = None,
) -> dict[str, Any]:
    """Create a new warehouse snapshot.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse name or GUID.
        name: Display name for the new snapshot.
        description: Optional description.
        snapshot_dt: Optional ISO-8601 datetime string for the snapshot point-in-time.
    """
    parsed_dt: datetime | None = None
    if snapshot_dt is not None:
        try:
            parsed_dt = datetime.fromisoformat(snapshot_dt)
        except ValueError as exc:
            raise ToolError(  # noqa: TRY003
                f"invalid snapshot_dt {snapshot_dt!r}: expected ISO-8601"
            ) from exc
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        result = await snapshots.create(
            _get_http(),
            ws_id,
            item.id,
            name,
            description=description,
            snapshot_dt=parsed_dt,
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def rename_snapshot(
    workspace: str,
    snapshot: str,
    new_name: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Rename a warehouse snapshot."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        snap_item = await _get_resolver().item(workspace, snapshot)
        result = await snapshots.rename(
            _get_http(),
            ws_id,
            snap_item.id,
            new_name=new_name,
            description=description,
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def delete_snapshot(workspace: str, snapshot: str) -> dict[str, Any]:
    """Delete a warehouse snapshot."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        snap_item = await _get_resolver().item(workspace, snapshot)
        await snapshots.delete(_get_http(), ws_id, snap_item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"deleted": True, "snapshot_id": str(snap_item.id)}


@mcp.tool()
async def roll_snapshot_timestamp(
    workspace: str,
    warehouse: str,
    snapshot_name: str,
    new_dt: str | None = None,
) -> dict[str, Any]:
    """Roll a snapshot's timestamp forward (or reset to current).

    Args:
        workspace: Workspace name or GUID.
        warehouse: Parent warehouse name or GUID (used for the SQL connection).
        snapshot_name: The snapshot database name to roll.
        new_dt: Optional ISO-8601 datetime string; defaults to CURRENT_TIMESTAMP.
    """
    parsed_dt: datetime | None = None
    if new_dt is not None:
        try:
            parsed_dt = datetime.fromisoformat(new_dt)
        except ValueError as exc:
            raise ToolError(  # noqa: TRY003
                f"invalid new_dt {new_dt!r}: expected ISO-8601"
            ) from exc
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        item = await _get_resolver().item(workspace, warehouse)
        if item.connection_string is None:
            msg = f"warehouse {warehouse!r} has no connection string"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=warehouse,
            connection_string=item.connection_string,
        )
        await snapshots.roll_timestamp(_get_sql(), target, snapshot_name, new_dt=parsed_dt)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"rolled": True, "snapshot_name": snapshot_name, "new_dt": new_dt}


# ---------------------------------------------------------------------------
# Cache tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def clear_cache() -> dict[str, Any]:
    """Erase all cached workspace and item name to UUID mappings."""
    _get_cache().clear()
    return {"cleared": True}


@mcp.tool()
async def invalidate_workspace_cache(workspace: str) -> dict[str, Any]:
    """Remove cache entries for a specific workspace (name or GUID)."""
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    _get_cache().invalidate_workspace(ws_id)
    return {"invalidated": True, "workspace_id": str(ws_id)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(argv: Sequence[str] | None = None) -> None:
    """Parse CLI arguments and start the FastMCP server.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when ``None``).

    Transport options
    -----------------
    ``--transport stdio`` (default)
        Communicate over stdin/stdout — standard for Claude Desktop and similar.
    ``--transport http``
        Expose a streamable-HTTP endpoint.
    """
    parser = argparse.ArgumentParser(
        prog="fabric-dw-mcp",
        description="Microsoft Fabric Data Warehouse MCP server",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport to use: 'stdio' (default) or 'http' (streamable-HTTP).",
    )
    args = parser.parse_args(argv)

    transport: Literal["stdio", "streamable-http"] = (
        "streamable-http" if args.transport == "http" else "stdio"
    )
    mcp.run(transport=transport)
