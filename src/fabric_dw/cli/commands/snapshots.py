"""Snapshots sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import click

from fabric_dw import auth as _auth
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    _coro,
    _resolve_item,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.services import snapshots as _snapshots_svc
from fabric_dw.sql import SqlTarget

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_http_client(
    ctx: CliContext,
) -> AsyncIterator[FabricHttpClient]:
    """Build and yield an HTTP client for snapshot commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http


def _parse_utc_dt(value: str, flag: str) -> datetime:
    """Parse an ISO 8601 datetime string and normalise to UTC."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise click.ClickException(  # noqa: TRY003
            f"Invalid {flag} value {value!r}: {exc}"
        ) from exc
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@click.group("snapshots")
def snapshots_group() -> None:
    """Manage Microsoft Fabric Data Warehouse snapshots."""


@snapshots_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """List all snapshots for WAREHOUSE in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            items = await _snapshots_svc.list_snapshots(http, ws_id, entry.id)
            render(
                [s.model_dump(by_alias=True, mode="json") for s in items],
                json_output=ctx.json_output,
                table_title="Snapshots",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.argument("name")
@click.option("--description", default=None, help="Optional description.")
@click.option(
    "--snapshot-dt",
    default=None,
    help="Optional snapshot datetime (ISO 8601, UTC).",
)
@click.pass_obj
@_coro
async def create_cmd(  # noqa: PLR0913
    ctx: CliContext,
    workspace: str | None,
    warehouse: str | None,
    name: str,
    description: str | None,
    snapshot_dt: str | None,
) -> None:
    """Create a new snapshot named NAME for WAREHOUSE in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    parsed_dt: datetime | None = None
    if snapshot_dt is not None:
        parsed_dt = _parse_utc_dt(snapshot_dt, "--snapshot-dt")

    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _snapshots_svc.create(
                http,
                ws_id,
                entry.id,
                name,
                description=description,
                snapshot_dt=parsed_dt,
            )
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("rename")
@click.argument("workspace")
@click.argument("snapshot")
@click.argument("new_name")
@click.option("--description", default=None, help="Optional new description.")
@click.pass_obj
@_coro
async def rename_cmd(
    ctx: CliContext,
    workspace: str,
    snapshot: str,
    new_name: str,
    description: str | None,
) -> None:
    """Rename SNAPSHOT in WORKSPACE to NEW_NAME (workspace and snapshot accept name or GUID)."""
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, workspace, snapshot)
            obj = await _snapshots_svc.rename(
                http,
                ws_id,
                entry.id,
                new_name=new_name,
                description=description,
            )
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("delete")
@click.argument("workspace")
@click.argument("snapshot")
@click.pass_obj
@_coro
async def delete_cmd(ctx: CliContext, workspace: str, snapshot: str) -> None:
    """Delete SNAPSHOT from WORKSPACE (both accept name or GUID)."""
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, workspace, snapshot)
            confirmed = confirm(
                f"Delete snapshot {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            await _snapshots_svc.delete(http, ws_id, entry.id)
            click.echo(f"Snapshot {entry.display_name!r} ({entry.id}) deleted.")
    except click.Abort:
        raise
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("roll")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.argument("snapshot_name")
@click.option(
    "--at",
    "new_dt",
    default=None,
    help="Target datetime (ISO 8601, UTC). Defaults to CURRENT_TIMESTAMP.",
)
@click.pass_obj
@_coro
async def roll_cmd(
    ctx: CliContext,
    workspace: str | None,
    warehouse: str | None,
    snapshot_name: str,
    new_dt: str | None,
) -> None:
    """Roll SNAPSHOT_NAME on WAREHOUSE in WORKSPACE to a new timestamp.

    WORKSPACE and WAREHOUSE accept name or GUID.
    SNAPSHOT_NAME must be the display name of the snapshot database.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    parsed_dt: datetime | None = None
    if new_dt is not None:
        parsed_dt = _parse_utc_dt(new_dt, "--at")

    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Warehouse {entry.display_name!r} has no connection string."
                )
            confirmed = confirm(
                f"Roll snapshot {snapshot_name!r} on warehouse "
                f"{entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            await _snapshots_svc.roll_timestamp(target, snapshot_name, parsed_dt, mode=ctx.auth)
            click.echo(f"Snapshot {snapshot_name!r} rolled.")
    except click.Abort:
        raise
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
