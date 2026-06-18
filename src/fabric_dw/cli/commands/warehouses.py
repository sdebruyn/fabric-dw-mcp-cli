"""Warehouse sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import (
    render,
    render_permissions_table,
    with_default_collation_for_display,
)
from fabric_dw.cli.commands._utils import (
    build_http_client,
    confirm_destructive,
    coro,
    make_resolver,
    resolve_item,
    resolve_item_with_cache,
    resolve_warehouse_arg,
    resolve_workspace,
    validate_workspace_option_or_all_workspaces,
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
@click.option(
    "-A",
    "--all-workspaces",
    "all_workspaces",
    is_flag=True,
    default=False,
    help="Scan all visible workspaces and aggregate results.",
)
@click.option(
    "--warehouses-only",
    "warehouses_only",
    is_flag=True,
    default=False,
    help="List only Warehouses; exclude SQL Analytics Endpoints (skips an API call).",
)
@click.pass_obj
@coro
async def list_cmd(
    ctx: CliContext,
    all_workspaces: bool,
    warehouses_only: bool,
) -> None:
    """List all warehouses in the target workspace.

    The workspace comes from -w/--workspace (or the configured default).

    Lists both Warehouses and SQL Analytics Endpoints by default; pass
    --warehouses-only to exclude SQL Analytics Endpoints.

    Pass -A / --all-workspaces to scan every visible workspace instead.
    -w/--workspace and --all-workspaces are mutually exclusive.

    The human-readable table omits the redundant Workspace ID column when a
    single workspace is targeted (every row shares it); -A keeps it because
    rows then span workspaces.  --json output always includes workspace_id.
    """
    # An explicit -w clashes with -A; a configured default does not (so -A
    # always wins over a default and only the explicit flag is a conflict).
    validate_workspace_option_or_all_workspaces(ctx.workspace, all_workspaces)
    try:
        async with build_http_client(ctx) as http:
            if all_workspaces:
                items = await _warehouses_svc.list_all_workspaces(
                    http, warehouses_only=warehouses_only
                )
            else:
                resolver, _ = make_resolver(http)
                ws_id = await resolver.workspace_id(resolve_workspace(ctx))
                items = await _warehouses_svc.list_warehouses(
                    http, ws_id, warehouses_only=warehouses_only
                )
            # Single-workspace listings share one workspace per row, so the
            # Workspace ID column is redundant noise in the human table — drop
            # it (table only).  -A spans workspaces, so the column is kept.
            # --json is never pruned (render ignores drop_columns for JSON).
            drop_columns = None if all_workspaces else ("workspaceId",)
            rows = [w.model_dump(by_alias=True, mode="json") for w in items]
            if not ctx.json_output:
                rows = [with_default_collation_for_display(r) for r in rows]
            render(
                rows,
                json_output=ctx.json_output,
                table_title="Warehouses",
                drop_columns=drop_columns,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("get")
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@coro
async def get_cmd(ctx: CliContext, warehouse: str | None) -> None:
    """Get details for WAREHOUSE (name or GUID) in the target workspace."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            obj = await _warehouses_svc.get_warehouse(http, ws_id, entry.id)
            dump = obj.model_dump(by_alias=True, mode="json")
            # Human output substitutes Fabric's effective default collation when
            # the API returns null; --json keeps the raw API value.
            if not ctx.json_output:
                dump = with_default_collation_for_display(dump)
            render(dump, json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("create")
@click.argument("name")
@click.option("--collation", default=None, help="Default collation for the warehouse.")
@click.option("--description", default=None, help="Description for the warehouse.")
@click.pass_obj
@coro
async def create_cmd(
    ctx: CliContext,
    name: str,
    collation: str | None,
    description: str | None,
) -> None:
    """Create a new warehouse named NAME in the target workspace."""
    ws = resolve_workspace(ctx)
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
            dump = obj.model_dump(by_alias=True, mode="json")
            if not ctx.json_output:
                dump = with_default_collation_for_display(dump)
            render(dump, json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("rename")
@click.argument("warehouse", required=False, default=None)
@click.argument("new_name")
@click.option("--description", default=None, help="Optional new description.")
@click.pass_obj
@coro
async def rename_cmd(
    ctx: CliContext,
    warehouse: str | None,
    new_name: str,
    description: str | None,
) -> None:
    """Rename WAREHOUSE (name or GUID) to NEW_NAME in the target workspace."""
    ws = resolve_workspace(ctx)
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
            dump = obj.model_dump(by_alias=True, mode="json")
            if not ctx.json_output:
                dump = with_default_collation_for_display(dump)
            render(dump, json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("delete")
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@coro
async def delete_cmd(ctx: CliContext, warehouse: str | None) -> None:
    """Delete WAREHOUSE (name or GUID) from the target workspace."""
    ws = resolve_workspace(ctx)
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
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@coro
async def takeover_cmd(ctx: CliContext, warehouse: str | None) -> None:
    """Take ownership of WAREHOUSE (name or GUID) in the target workspace.

    Not supported for SQL Analytics Endpoints.
    """
    ws = resolve_workspace(ctx)
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
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@coro
async def permissions_cmd(ctx: CliContext, warehouse: str | None) -> None:
    """List principals with access to WAREHOUSE (name or GUID) in the target workspace.

    Requires Fabric Administrator role.
    See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
    """
    ws = resolve_workspace(ctx)
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
