"""Workspace sub-commands for the fabric-dw CLI."""

from __future__ import annotations

from uuid import UUID

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    build_http_client,
    confirm_destructive,
    coro,
    resolve_workspace_arg,
    resolve_workspace_id,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import capacities as _capacities_svc
from fabric_dw.services import workspaces as _workspaces_svc


@click.group("workspaces")
def workspaces_group() -> None:
    """Manage Microsoft Fabric workspaces."""


@workspaces_group.command("list-capacities")
@click.pass_obj
@coro
async def list_capacities_cmd(ctx: CliContext) -> None:
    """List all Fabric capacities the authenticated principal has access to."""
    try:
        async with build_http_client(ctx) as http:
            items = await _capacities_svc.list_all(http)
            render(
                [c.model_dump(by_alias=True, mode="json") for c in items],
                json_output=ctx.json_output,
                table_title="Capacities",
                prune_null_columns=True,
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@workspaces_group.command("list")
@click.pass_obj
@coro
async def list_cmd(ctx: CliContext) -> None:
    """List all workspaces the authenticated principal has access to."""
    try:
        async with build_http_client(ctx) as http:
            items = await _workspaces_svc.list_all(http)
            render(
                [w.model_dump(by_alias=True, mode="json") for w in items],
                json_output=ctx.json_output,
                table_title="Workspaces",
                prune_null_columns=True,
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@workspaces_group.command("assign-capacity")
@click.argument("workspace", required=False, default=None)
@click.option(
    "--capacity-id",
    required=True,
    type=click.UUID,
    help="UUID of the capacity to assign the workspace to.",
)
@click.pass_obj
@coro
async def assign_capacity_cmd(ctx: CliContext, workspace: str | None, capacity_id: UUID) -> None:
    """Assign WORKSPACE (name or GUID) to a capacity.

    WORKSPACE is the workspace name or GUID.  --capacity-id must be a valid UUID.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            await _workspaces_svc.assign_to_capacity(http, ws_id, capacity_id)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
    if ctx.json_output:
        render(
            {"workspace_id": str(ws_id), "capacity_id": str(capacity_id)},
            json_output=True,
        )
    else:
        click.echo(f"Workspace {ws_id} assigned to capacity {capacity_id}.")


@workspaces_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@coro
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
@coro
async def set_collation_cmd(ctx: CliContext, workspace: str | None, collation: str) -> None:
    """Set the default Data Warehouse COLLATION for WORKSPACE (name or GUID).

    COLLATION must be one of the supported Fabric collations.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            if not confirm_destructive(
                f"Set collation to {collation!r} for workspace {ws_id}?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _workspaces_svc.set_collation(http, ws_id, collation)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    if ctx.json_output:
        render({"status": "set", "collation": collation}, json_output=True)
    else:
        click.echo(f"Collation set to {collation!r}.")
