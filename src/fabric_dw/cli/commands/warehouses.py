"""Warehouse sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render, render_permissions_table
from fabric_dw.cli.commands._utils import (
    _coro,
    _resolve_item,
    _resolve_item_with_cache,
    build_http_client,
    make_resolver,
    resolve_warehouse_arg,
    resolve_workspace_arg,
    validate_workspace_or_all_workspaces,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.models import WarehouseKind
from fabric_dw.services import ownership as _ownership_svc
from fabric_dw.services import permissions as _permissions_svc
from fabric_dw.services import warehouses as _warehouses_svc

_log = logging.getLogger(__name__)


@click.group("warehouses")
def warehouses_group() -> None:
    """Manage Microsoft Fabric Data Warehouses and SQL Analytics Endpoints."""
    # NOTE: positional argument is named 'warehouse' throughout this group (not 'item') because
    # every command here is warehouse-specific; this is a deliberate exception to the item-rename.


@warehouses_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.option(
    "-A",
    "--all-workspaces",
    "all_workspaces",
    is_flag=True,
    default=False,
    help="Scan all visible workspaces and aggregate results.",
)
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str | None, all_workspaces: bool) -> None:
    """List all warehouses in WORKSPACE (name or GUID).

    Pass -A / --all-workspaces to scan every visible workspace instead.
    WORKSPACE and --all-workspaces are mutually exclusive; exactly one is required.
    """
    validate_workspace_or_all_workspaces(workspace, all_workspaces)
    try:
        async with build_http_client(ctx) as http:
            if all_workspaces:
                items = await _warehouses_svc.list_all_workspaces(http)
            else:
                ws = resolve_workspace_arg(ctx, workspace)
                resolver, _ = make_resolver(http)
                ws_id = await resolver.workspace_id(ws)
                items = await _warehouses_svc.list_warehouses(http, ws_id)
            render(
                [w.model_dump(by_alias=True, mode="json") for w in items],
                json_output=ctx.json_output,
                table_title="Warehouses",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """Get details for WAREHOUSE in WORKSPACE (both accept name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _warehouses_svc.get_warehouse(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("name")
@click.option("--collation", default=None, help="Default collation for the warehouse.")
@click.option("--description", default=None, help="Description for the warehouse.")
@click.pass_obj
@_coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    name: str,
    collation: str | None,
    description: str | None,
) -> None:
    """Create a new warehouse named NAME in WORKSPACE (name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            resolver, _ = make_resolver(http)
            ws_id = await resolver.workspace_id(ws)
            obj = await _warehouses_svc.create(
                http,
                ws_id,
                name,
                collation=collation,
                description=description,
            )
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("rename")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.argument("new_name")
@click.option("--description", default=None, help="Optional new description.")
@click.pass_obj
@_coro
async def rename_cmd(
    ctx: CliContext,
    workspace: str | None,
    warehouse: str | None,
    new_name: str,
    description: str | None,
) -> None:
    """Rename WAREHOUSE in WORKSPACE to NEW_NAME (workspace and warehouse accept name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry, cache = await _resolve_item_with_cache(http, ws, wh)
            confirmed = confirm(
                f"Rename warehouse {entry.display_name!r} ({entry.id}) to {new_name!r}?",
                yes=ctx.yes,
            )
            if not confirmed:
                click.echo("Aborted.")
                return
            obj = await _warehouses_svc.rename(
                http,
                ws_id,
                entry.id,
                new_name,
                description=description,
                cache=cache,
                old_name=entry.display_name or None,
            )
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("delete")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@_coro
async def delete_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """Delete WAREHOUSE from WORKSPACE (both accept name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry, cache = await _resolve_item_with_cache(http, ws, wh)
            confirmed = confirm(
                f"Delete warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                click.echo("Aborted.")
                return
            await _warehouses_svc.delete(
                http,
                ws_id,
                entry.id,
                cache=cache,
                name=entry.display_name or None,
            )
            click.echo(f"Warehouse {entry.display_name!r} ({entry.id}) deleted.")
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("takeover")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@_coro
async def takeover_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """Take ownership of WAREHOUSE in WORKSPACE (both accept name or GUID).

    Not supported for SQL Analytics Endpoints.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
    if entry.kind == WarehouseKind.SQL_ENDPOINT:
        raise click.UsageError("takeover is not supported for SQL Analytics Endpoints")
    confirmed = confirm(
        f"Take over warehouse {entry.display_name!r} ({entry.id})?",
        yes=ctx.yes,
    )
    if not confirmed:
        click.echo("Aborted.")
        return
    try:
        async with build_http_client(ctx) as http:
            await _ownership_svc.takeover(http, ws_id, entry.id)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Ownership of warehouse {entry.display_name!r} ({entry.id}) taken.")


@warehouses_group.command("permissions")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@_coro
async def permissions_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """List principals with access to WAREHOUSE in WORKSPACE (both accept name or GUID).

    Requires Fabric Administrator role.
    See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            items = await _permissions_svc.list_item_access(http, ws_id, entry.id)
            render_permissions_table(
                items, title="Warehouse Permissions", json_output=ctx.json_output
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
