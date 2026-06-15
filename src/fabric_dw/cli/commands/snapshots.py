"""Snapshots sub-commands for the fabric-dw CLI."""

from __future__ import annotations

from datetime import datetime

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    confirm_destructive,
    coro,
    parse_iso_datetime,
    resolve_item,
    resolve_item_with_cache,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import snapshots as _snapshots_svc


@click.group("snapshots")
def snapshots_group() -> None:
    """Manage Microsoft Fabric Data Warehouse snapshots."""


@snapshots_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@coro
async def list_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """List all snapshots for ITEM (warehouse) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            items = await _snapshots_svc.list_snapshots(http, ws_id, entry.id)
            render(
                [s.model_dump(by_alias=True, mode="json") for s in items],
                json_output=ctx.json_output,
                table_title="Snapshots",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("name")
@click.option("--description", default=None, help="Optional description.")
@click.option(
    "--snapshot-dt",
    default=None,
    help="Optional snapshot datetime (ISO 8601, UTC).",
)
@click.pass_obj
@coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    name: str,
    description: str | None,
    snapshot_dt: str | None,
) -> None:
    """Create a new snapshot named NAME for ITEM (warehouse) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    parsed_dt: datetime | None = None
    if snapshot_dt is not None:
        parsed_dt = parse_iso_datetime(snapshot_dt, "--snapshot-dt")

    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
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
@click.argument("snapshot")
@click.argument("new_name")
@click.argument("workspace", required=False, default=None)
@click.option("--description", default=None, help="Optional new description.")
@click.pass_obj
@coro
async def rename_cmd(
    ctx: CliContext,
    snapshot: str,
    new_name: str,
    workspace: str | None,
    description: str | None,
) -> None:
    """Rename SNAPSHOT to NEW_NAME in WORKSPACE (workspace and snapshot accept name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry, cache = await resolve_item_with_cache(http, ws, snapshot)
            obj = await _snapshots_svc.rename(
                http,
                ws_id,
                entry.id,
                new_name=new_name,
                description=description,
                cache=cache,
                old_name=entry.display_name or None,
            )
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("delete")
@click.argument("snapshot")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@coro
async def delete_cmd(ctx: CliContext, snapshot: str, workspace: str | None) -> None:
    """Delete SNAPSHOT from WORKSPACE (both accept name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry, cache = await resolve_item_with_cache(http, ws, snapshot)
            if not confirm_destructive(
                f"Delete snapshot {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _snapshots_svc.delete(
                http,
                ws_id,
                entry.id,
                cache=cache,
                name=entry.display_name or None,
            )
            if ctx.json_output:
                render(
                    {"status": "deleted", "name": entry.display_name, "id": str(entry.id)},
                    json_output=True,
                )
            else:
                click.echo(f"Snapshot {entry.display_name!r} ({entry.id}) deleted.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("roll")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("snapshot_name")
@click.option(
    "--at",
    "new_dt",
    default=None,
    help="Target datetime (ISO 8601, UTC). Defaults to CURRENT_TIMESTAMP.",
)
@click.pass_obj
@coro
async def roll_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    snapshot_name: str,
    new_dt: str | None,
) -> None:
    """Roll SNAPSHOT_NAME on ITEM (warehouse) in WORKSPACE to a new timestamp.

    WORKSPACE and ITEM accept name or GUID.
    SNAPSHOT_NAME must be the display name of the snapshot database.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    parsed_dt: datetime | None = None
    if new_dt is not None:
        parsed_dt = parse_iso_datetime(new_dt, "--at")

    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            if not confirm_destructive(
                f"Roll snapshot {snapshot_name!r} on warehouse "
                f"{entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _snapshots_svc.roll_timestamp(target, snapshot_name, parsed_dt, mode=ctx.auth)
            if ctx.json_output:
                render({"status": "rolled", "name": snapshot_name}, json_output=True)
            else:
                click.echo(f"Snapshot {snapshot_name!r} rolled.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
