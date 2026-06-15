"""Warehouse sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render, render_permissions_table
from fabric_dw.cli.commands._utils import (
    build_http_client,
    confirm_destructive,
    coro,
    make_resolver,
    resolve_item,
    resolve_item_with_cache,
    resolve_warehouse_arg,
    resolve_workspace_arg,
    validate_workspace_or_all_workspaces,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.models import WarehouseKind
from fabric_dw.services import ownership as _ownership_svc
from fabric_dw.services import permissions as _permissions_svc
from fabric_dw.services import warehouses as _warehouses_svc


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
@coro
async def list_cmd(ctx: CliContext, workspace: str | None, all_workspaces: bool) -> None:
    """List all warehouses in WORKSPACE (name or GUID).

    Pass -A / --all-workspaces to scan every visible workspace instead.
    WORKSPACE and --all-workspaces are mutually exclusive; exactly one is required.
    """
    # Resolve the workspace default before the XOR validation so that a
    # configured default-workspace (env / config file) is honoured when no
    # positional arg is passed but --all-workspaces is also absent.
    resolved_workspace = None if all_workspaces else resolve_workspace_arg(ctx, workspace)
    validate_workspace_or_all_workspaces(resolved_workspace, all_workspaces)
    try:
        async with build_http_client(ctx) as http:
            if all_workspaces:
                items = await _warehouses_svc.list_all_workspaces(http)
            else:
                # resolved_workspace is guaranteed non-None by validate_workspace_or_all_workspaces
                if resolved_workspace is None:  # pragma: no cover — defensive
                    raise click.UsageError("Provide WORKSPACE or pass --all-workspaces / -A.")
                resolver, _ = make_resolver(http)
                ws_id = await resolver.workspace_id(resolved_workspace)
                items = await _warehouses_svc.list_warehouses(http, ws_id)
            render(
                [w.model_dump(by_alias=True, mode="json") for w in items],
                json_output=ctx.json_output,
                table_title="Warehouses",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@coro
async def get_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """Get details for WAREHOUSE in WORKSPACE (both accept name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            obj = await _warehouses_svc.get_warehouse(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("name")
@click.option("--collation", default=None, help="Default collation for the warehouse.")
@click.option("--description", default=None, help="Description for the warehouse.")
@click.pass_obj
@coro
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
@coro
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
            ws_id, entry, cache = await resolve_item_with_cache(http, ws, wh)
            if not confirm_destructive(
                f"Rename warehouse {entry.display_name!r} ({entry.id}) to {new_name!r}?",
                yes=ctx.yes,
            ):
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
@coro
async def delete_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """Delete WAREHOUSE from WORKSPACE (both accept name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry, cache = await resolve_item_with_cache(http, ws, wh)
            if not confirm_destructive(
                f"Delete warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _warehouses_svc.delete(
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
                click.echo(f"Warehouse {entry.display_name!r} ({entry.id}) deleted.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("takeover")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@coro
async def takeover_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """Take ownership of WAREHOUSE in WORKSPACE (both accept name or GUID).

    Not supported for SQL Analytics Endpoints.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            if entry.kind == WarehouseKind.SQL_ENDPOINT:
                raise click.UsageError("takeover is not supported for SQL Analytics Endpoints")
            if not confirm_destructive(
                f"Take over warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _ownership_svc.takeover(http, ws_id, entry.id)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Ownership of warehouse {entry.display_name!r} ({entry.id}) taken.")


@warehouses_group.command("permissions")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@coro
async def permissions_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """List principals with access to WAREHOUSE in WORKSPACE (both accept name or GUID).

    Requires Fabric Administrator role.
    See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            items = await _permissions_svc.list_item_access(http, ws_id, entry.id)
            render_permissions_table(
                items, title="Warehouse Permissions", json_output=ctx.json_output
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
