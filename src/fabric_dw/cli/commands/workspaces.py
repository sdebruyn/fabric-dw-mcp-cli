"""Workspace sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    _coro,
    build_http_client,
    confirm_destructive,
    resolve_workspace_arg,
    resolve_workspace_id,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import workspaces as _workspaces_svc

_log = logging.getLogger(__name__)


@click.group("workspaces")
def workspaces_group() -> None:
    """Manage Microsoft Fabric workspaces."""


@workspaces_group.command("list")
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext) -> None:
    """List all workspaces the authenticated principal has access to."""
    try:
        async with build_http_client(ctx) as http:
            items = await _workspaces_svc.list_all(http)
            render(
                [w.model_dump(by_alias=True, mode="json") for w in items],
                json_output=ctx.json_output,
                table_title="Workspaces",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@workspaces_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str | None) -> None:
    """Get details for WORKSPACE (name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            obj = await _workspaces_svc.get(http, ws_id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@workspaces_group.command("set-collation")
@click.argument("workspace", required=False, default=None)
@click.argument("collation")
@click.pass_obj
@_coro
async def set_collation_cmd(ctx: CliContext, workspace: str | None, collation: str) -> None:
    """Set the default Data Warehouse COLLATION for WORKSPACE (name or GUID).

    COLLATION must be one of the supported Fabric collations.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
    if not confirm_destructive(
        f"Set collation to {collation!r} for workspace {ws_id}?",
        yes=ctx.yes,
    ):
        click.echo("Aborted.")
        return
    try:
        async with build_http_client(ctx) as http:
            await _workspaces_svc.set_collation(http, ws_id, collation)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Collation set to {collation!r}.")
