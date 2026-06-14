"""Restore-points sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    _coro,
    build_http_client,
    confirm_destructive,
    resolve_item,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import restore as _restore_svc

_log = logging.getLogger(__name__)


@click.group("restore-points")
def restore_points_group() -> None:
    """Manage Microsoft Fabric Warehouse restore points."""


@restore_points_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """List all restore points for ITEM (warehouse) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            wh_id = entry.id
            items = await _restore_svc.list_points(http, ws_id, wh_id)
            render(
                [rp.model_dump(by_alias=True, mode="json") for rp in items],
                json_output=ctx.json_output,
                table_title="Restore Points",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@restore_points_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("restore_point_id")
@click.pass_obj
@_coro
async def get_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    restore_point_id: str,
) -> None:
    """Get a restore point by RESTORE_POINT_ID for ITEM (warehouse) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            wh_id = entry.id
            rp = await _restore_svc.get_point(http, ws_id, wh_id, restore_point_id)
            render(rp.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@restore_points_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option("--name", default=None, help="Optional display name (max 128 chars).")
@click.option("--description", default=None, help="Optional description (max 512 chars).")
@click.pass_obj
@_coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    name: str | None,
    description: str | None,
) -> None:
    """Create a restore point for ITEM (warehouse) in WORKSPACE at the current timestamp."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            wh_id = entry.id
            rp = await _restore_svc.create_point(
                http, ws_id, wh_id, name=name, description=description
            )
            render(rp.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@restore_points_group.command("rename")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("restore_point_id")
@click.argument("new_name")
@click.option("--description", default=None, help="Optional new description.")
@click.pass_obj
@_coro
async def rename_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    restore_point_id: str,
    new_name: str,
    description: str | None,
) -> None:
    """Rename RESTORE_POINT_ID to NEW_NAME on ITEM (warehouse) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            wh_id = entry.id
            rp = await _restore_svc.update_point(
                http,
                ws_id,
                wh_id,
                restore_point_id,
                name=new_name,
                description=description,
            )
            render(rp.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@restore_points_group.command("delete")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("restore_point_id")
@click.pass_obj
@_coro
async def delete_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    restore_point_id: str,
) -> None:
    """Delete RESTORE_POINT_ID on ITEM (warehouse) in WORKSPACE.

    Only user-defined restore points can be deleted; system-created points
    are automatically managed by the service.
    You will be asked to confirm unless --yes is passed.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            wh_id = entry.id
            if not confirm_destructive(
                f"Delete restore point {restore_point_id!r} on warehouse"
                f" {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _restore_svc.delete_point(http, ws_id, wh_id, restore_point_id)
            if ctx.json_output:
                render({"status": "deleted", "id": restore_point_id}, json_output=True)
            else:
                click.echo(f"Restore point {restore_point_id!r} deleted.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@restore_points_group.command("restore")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("restore_point_id")
@click.pass_obj
@_coro
async def restore_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    restore_point_id: str,
) -> None:
    """Restore ITEM (warehouse) in-place to RESTORE_POINT_ID in WORKSPACE.

    WARNING: This is a destructive operation. The warehouse will be
    unavailable for approximately 10 minutes while the restore completes.
    In interactive mode you must type the warehouse name to confirm.
    Pass --yes to skip the prompt for automation.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            wh_id = entry.id
            if not ctx.yes:
                rp = await _restore_svc.get_point(http, ws_id, wh_id, restore_point_id)
                when = rp.event_date_time.isoformat() if rp.event_date_time else "unknown"
                click.echo(
                    f"\nWARNING: This will REPLACE all data in warehouse {wh!r} "
                    f"with the state at restore point {restore_point_id!r} "
                    f"(created {when}).\n"
                    f"The warehouse will be UNAVAILABLE for ~10 minutes.\n",
                    err=True,
                )
                typed = click.prompt(
                    f"To restore warehouse {wh!r} to restore point "
                    f"{restore_point_id!r} (created {when}), "
                    f"type the warehouse name to confirm"
                )
                if typed != wh:
                    click.echo("Aborted.")
                    return
            await _restore_svc.restore_in_place(http, ws_id, wh_id, restore_point_id)
            click.echo(f"Warehouse restored to restore point {restore_point_id!r} successfully.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
