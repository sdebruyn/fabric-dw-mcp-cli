"""SQL Analytics Endpoint sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render, render_permissions_table, render_refresh_table
from fabric_dw.cli.commands._utils import (
    _coro,
    _resolve_item,
    build_http_client,
    make_resolver,
    resolve_warehouse_arg,
    resolve_workspace_arg,
    validate_workspace_or_all_workspaces,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import permissions as _permissions_svc
from fabric_dw.services import sql_endpoints as _sql_endpoints_svc

_log = logging.getLogger(__name__)


@click.group("sql-endpoints")
def sql_endpoints_group() -> None:
    """Manage Microsoft Fabric SQL Analytics Endpoints."""


@sql_endpoints_group.command("list")
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
    """List all SQL analytics endpoints in WORKSPACE (name or GUID).

    Pass -A / --all-workspaces to scan every visible workspace instead.
    WORKSPACE and --all-workspaces are mutually exclusive; exactly one is required.
    """
    validate_workspace_or_all_workspaces(workspace, all_workspaces)
    try:
        async with build_http_client(ctx) as http:
            if all_workspaces:
                items = await _sql_endpoints_svc.list_all_workspaces(http)
            else:
                resolver, _ = make_resolver(http)
                assert workspace is not None  # noqa: S101 - guarded above
                ws_id = await resolver.workspace_id(workspace)
                items = await _sql_endpoints_svc.list_endpoints(http, ws_id)
            render(
                [ep.model_dump(by_alias=True, mode="json") for ep in items],
                json_output=ctx.json_output,
                table_title="SQL Analytics Endpoints",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_endpoints_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """Get details for ITEM (SQL analytics endpoint) in WORKSPACE (both accept name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    ep = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, ep)
            obj = await _sql_endpoints_svc.get_endpoint(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_endpoints_group.command("refresh")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option(
    "--recreate-tables",
    "recreate_tables",
    is_flag=True,
    default=False,
    help=(
        "Drop and recreate all tables during the refresh. "
        "Use to resolve inconsistencies or force a clean rebuild. "
        "DESTRUCTIVE — use with caution."
    ),
)
@click.pass_obj
@_coro
async def refresh_cmd(
    ctx: CliContext, workspace: str | None, item: str | None, recreate_tables: bool
) -> None:
    """Refresh metadata for ITEM (SQL endpoint) in WORKSPACE (both accept name or GUID).

    Triggers a metadata sync from the underlying Lakehouse delta tables.
    This is a long-running operation (LRO) that is polled to completion.

    By default, results are shown as a Rich table.  Pass --json (on the root
    command) to emit raw JSON instead.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    ep = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, ep)
            statuses = await _sql_endpoints_svc.refresh_metadata(
                http, ws_id, entry.id, recreate_tables=recreate_tables
            )
            if ctx.json_output:
                render(
                    [s.model_dump(by_alias=True, mode="json") for s in statuses],
                    json_output=True,
                )
            else:
                render_refresh_table(statuses)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_endpoints_group.command("permissions")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@_coro
async def permissions_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """List principals with access to ITEM (SQL endpoint) in WORKSPACE (both accept name or GUID).

    Requires Fabric Administrator role.
    See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    ep = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, ep)
            items = await _permissions_svc.list_item_access(http, ws_id, entry.id)
            render_permissions_table(
                items,
                title="SQL Analytics Endpoint Permissions",
                json_output=ctx.json_output,
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
