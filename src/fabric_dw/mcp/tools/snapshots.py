"""MCP tools for warehouse snapshot operations."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import (
    assert_destructive_allowed,
    assert_workspace_allowed,
    assert_writes_allowed,
)
from fabric_dw.mcp._helpers import fabric_err, make_sql_target, resolve_item
from fabric_dw.services import snapshots

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register snapshot tools against *mcp*."""

    @mcp.tool(name="list_snapshots")
    async def list_snapshots(workspace: str, warehouse: str) -> list[dict[str, Any]]:
        """Return all snapshots belonging to a warehouse."""
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("list_snapshots ws=%s item=%s", ws_id, item.id)
            result = await snapshots.list_snapshots(ctx.http, ws_id, item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return [s.model_dump(by_alias=True, mode="json") for s in result]

    @mcp.tool(name="create_snapshot")
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
        assert_writes_allowed("create_snapshot")
        assert_workspace_allowed(workspace)
        parsed_dt: datetime | None = None
        if snapshot_dt is not None:
            try:
                parsed_dt = datetime.fromisoformat(snapshot_dt)
            except ValueError as exc:
                raise ToolError(  # noqa: TRY003
                    f"invalid snapshot_dt {snapshot_dt!r}: expected ISO-8601"
                ) from exc
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("create_snapshot ws=%s item=%s name=%r", ws_id, item.id, name)
            result = await snapshots.create(
                ctx.http,
                ws_id,
                item.id,
                name,
                description=description,
                snapshot_dt=parsed_dt,
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        ctx.resolver.clear_negative_cache()
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="rename_snapshot")
    async def rename_snapshot(
        workspace: str,
        snapshot: str,
        new_name: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Rename a warehouse snapshot."""
        assert_writes_allowed("rename_snapshot")
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, snap_item = await resolve_item(ctx.resolver, workspace, snapshot)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("rename_snapshot ws=%s item=%s new=%r", ws_id, snap_item.id, new_name)
            result = await snapshots.rename(
                ctx.http,
                ws_id,
                snap_item.id,
                new_name=new_name,
                description=description,
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        ctx.resolver.clear_negative_cache()
        return result.model_dump(by_alias=True, mode="json")

    @mcp.tool(name="delete_snapshot")
    async def delete_snapshot(workspace: str, snapshot: str) -> dict[str, Any]:
        """Delete a warehouse snapshot."""
        assert_writes_allowed("delete_snapshot")
        assert_destructive_allowed()
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, snap_item = await resolve_item(ctx.resolver, workspace, snapshot)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("delete_snapshot ws=%s item=%s", ws_id, snap_item.id)
            await snapshots.delete(ctx.http, ws_id, snap_item.id)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"deleted": True, "snapshot_id": str(snap_item.id)}

    @mcp.tool(name="roll_snapshot_timestamp")
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
        assert_writes_allowed("roll_snapshot_timestamp")
        assert_workspace_allowed(workspace)
        parsed_dt: datetime | None = None
        if new_dt is not None:
            try:
                parsed_dt = datetime.fromisoformat(new_dt)
            except ValueError as exc:
                raise ToolError(  # noqa: TRY003
                    f"invalid new_dt {new_dt!r}: expected ISO-8601"
                ) from exc
        ctx = get_context()
        try:
            ws_id, item = await resolve_item(ctx.resolver, workspace, warehouse)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug(
                "roll_snapshot_timestamp ws=%s item=%s snap=%r", ws_id, item.id, snapshot_name
            )
            target = make_sql_target(ws_id, item, warehouse)
            await snapshots.roll_timestamp(target, snapshot_name, parsed_dt, mode=ctx.auth_mode)
        except FabricError as exc:
            raise fabric_err(exc) from exc
        return {"rolled": True, "snapshot_name": snapshot_name, "new_dt": new_dt}
