"""Workspace sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import click

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import _coro, resolve_workspace_arg
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver
from fabric_dw.services import workspaces as _workspaces_svc

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_clients(
    ctx: CliContext,
) -> AsyncIterator[tuple[FabricHttpClient, None]]:
    """Build and yield an HTTP client for workspace commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http, None


async def _resolve_workspace(http: FabricHttpClient, workspace: str) -> UUID:
    """Resolve a workspace name or GUID to a UUID."""
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    return await resolver.workspace_id(workspace)


@click.group("workspaces")
def workspaces_group() -> None:
    """Manage Microsoft Fabric workspaces."""


@workspaces_group.command("list")
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext) -> None:
    """List all workspaces the authenticated principal has access to."""
    try:
        async with _build_clients(ctx) as (http, _):
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
        async with _build_clients(ctx) as (http, _):
            ws_id = await _resolve_workspace(http, ws)
            obj = await _workspaces_svc.get(http, ws_id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@workspaces_group.command("get-collation")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@_coro
async def get_collation_cmd(ctx: CliContext, workspace: str | None) -> None:
    """Get the default Data Warehouse collation for WORKSPACE (name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id = await _resolve_workspace(http, ws)
            collation = await _workspaces_svc.get_collation(http, ws_id)
            if ctx.json_output:
                render({"collation": collation}, json_output=True)
            else:
                click.echo(collation if collation is not None else "(not set)")
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
        async with _build_clients(ctx) as (http, _):
            ws_id = await _resolve_workspace(http, ws)
            confirmed = confirm(
                f"Set collation to {collation!r} for workspace {ws_id}?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            await _workspaces_svc.set_collation(http, ws_id, collation)
            click.echo(f"Collation set to {collation!r}.")
    except click.Abort:
        raise
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
