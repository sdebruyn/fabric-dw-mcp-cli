"""Workspace sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from functools import wraps
from typing import ParamSpec, TypeVar
from uuid import UUID

import anyio
import click

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver
from fabric_dw.services import workspaces as _workspaces_svc

_P = ParamSpec("_P")
_R = TypeVar("_R")

_log = logging.getLogger(__name__)


def _coro(f: Callable[_P, Coroutine[None, None, _R]]) -> Callable[_P, _R]:
    """Wrap an async Click command so it runs via anyio.run."""

    @wraps(f)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        async def _inner() -> _R:
            return await f(*args, **kwargs)

        return anyio.run(_inner)

    return wrapper


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
@click.argument("workspace")
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str) -> None:
    """Get details for WORKSPACE (name or GUID)."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id = await _resolve_workspace(http, workspace)
            obj = await _workspaces_svc.get(http, ws_id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@workspaces_group.command("get-collation")
@click.argument("workspace")
@click.pass_obj
@_coro
async def get_collation_cmd(ctx: CliContext, workspace: str) -> None:
    """Get the default Data Warehouse collation for WORKSPACE (name or GUID)."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id = await _resolve_workspace(http, workspace)
            collation = await _workspaces_svc.get_collation(http, ws_id)
            if ctx.json_output:
                render({"collation": collation}, json_output=True)
            else:
                click.echo(collation if collation is not None else "(not set)")
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@workspaces_group.command("set-collation")
@click.argument("workspace")
@click.argument("collation")
@click.pass_obj
@_coro
async def set_collation_cmd(ctx: CliContext, workspace: str, collation: str) -> None:
    """Set the default Data Warehouse COLLATION for WORKSPACE (name or GUID).

    COLLATION must be one of the supported Fabric collations.
    """
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id = await _resolve_workspace(http, workspace)
            if not confirm(
                f"Set collation to {collation!r} for workspace {ws_id}?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _workspaces_svc.set_collation(http, ws_id, collation)
            click.echo(f"Collation set to {collation!r}.")
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
