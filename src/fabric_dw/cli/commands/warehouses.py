"""Warehouse sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import json as _json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import click
from rich.console import Console
from rich.table import Table

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    _coro,
    _resolve_item,
    _resolve_item_with_cache,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import ItemAccess, WarehouseKind
from fabric_dw.resolver import Resolver
from fabric_dw.services import ownership as _ownership_svc
from fabric_dw.services import permissions as _permissions_svc
from fabric_dw.services import warehouses as _warehouses_svc

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_clients(
    ctx: CliContext,
) -> AsyncIterator[tuple[FabricHttpClient, None]]:
    """Build and yield an HTTP client for warehouse commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http, None


@click.group("warehouses")
def warehouses_group() -> None:
    """Manage Microsoft Fabric Data Warehouses and SQL Analytics Endpoints."""


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
    WORKSPACE and --all-workspaces are mutually exclusive.
    WORKSPACE may be omitted when a default is set via
    ``fabric-dw config set workspace`` or ``FABRIC_DW_DEFAULT_WORKSPACE``.
    """
    if workspace and all_workspaces:
        raise click.UsageError("WORKSPACE and --all-workspaces are mutually exclusive.")  # noqa: TRY003
    try:
        async with _build_clients(ctx) as (http, _):
            if all_workspaces:
                items = await _warehouses_svc.list_all_workspaces(http)
            else:
                ws = resolve_workspace_arg(ctx, workspace)
                cache = LookupCache()
                resolver = Resolver(http=http, cache=cache)
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
        async with _build_clients(ctx) as (http, _):
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
        async with _build_clients(ctx) as (http, _):
            cache = LookupCache()
            resolver = Resolver(http=http, cache=cache)
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
        async with _build_clients(ctx) as (http, _):
            ws_id, entry, cache = await _resolve_item_with_cache(http, ws, wh)
            confirmed = confirm(
                f"Rename warehouse {entry.display_name!r} ({entry.id}) to {new_name!r}?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
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
    except click.Abort:
        raise
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
        async with _build_clients(ctx) as (http, _):
            ws_id, entry, cache = await _resolve_item_with_cache(http, ws, wh)
            confirmed = confirm(
                f"Delete warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            await _warehouses_svc.delete(
                http,
                ws_id,
                entry.id,
                cache=cache,
                name=entry.display_name or None,
            )
            click.echo(f"Warehouse {entry.display_name!r} ({entry.id}) deleted.")
    except click.Abort:
        raise
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
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, ws, wh)
            if entry.kind == WarehouseKind.SQL_ENDPOINT:
                raise click.UsageError(  # noqa: TRY003, TRY301
                    "takeover is not supported for SQL Analytics Endpoints"
                )
            confirmed = confirm(
                f"Take over warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            await _ownership_svc.takeover(http, ws_id, entry.id)
            click.echo(f"Ownership of warehouse {entry.display_name!r} ({entry.id}) taken.")
    except click.Abort:
        raise
    except click.UsageError:
        raise
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


def _render_permissions_table(
    accesses: Sequence[ItemAccess], *, console: Console | None = None
) -> None:
    """Render a sequence of :class:`~fabric_dw.models.ItemAccess` as a Rich table."""
    con = console or Console()
    table = Table(title="Warehouse Permissions", show_header=True, header_style="bold")
    table.add_column("Display Name", no_wrap=True)
    table.add_column("UPN / App ID")
    table.add_column("Type")
    table.add_column("Permissions")
    table.add_column("Additional")

    for entry in accesses:
        p = entry.principal
        display = p.display_name or ""
        identity = p.user_principal_name or (str(p.aad_app_id) if p.aad_app_id else "")
        ptype = p.type
        perms = ", ".join(entry.item_access_details.permissions)
        additional = ", ".join(entry.item_access_details.additional_permissions)
        table.add_row(display, identity, ptype, perms, additional)

    con.print(table)


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
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, ws, wh)
            items = await _permissions_svc.list_item_access(http, ws_id, entry.id)
            if ctx.json_output:
                click.echo(
                    _json.dumps(
                        [a.model_dump(by_alias=True, mode="json") for a in items],
                        indent=2,
                        default=str,
                    )
                )
            else:
                _render_permissions_table(items)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
