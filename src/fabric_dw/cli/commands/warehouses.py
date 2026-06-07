"""Warehouse sub-commands for the fabric-dw CLI."""

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
from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import WarehouseKind
from fabric_dw.resolver import Resolver
from fabric_dw.services import ownership as _ownership_svc
from fabric_dw.services import warehouses as _warehouses_svc

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
    """Build and yield an HTTP client for warehouse commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http, None


async def _resolve_item(
    http: FabricHttpClient,
    workspace: str,
    warehouse: str,
) -> tuple[UUID, ItemEntry]:
    """Resolve workspace and warehouse names/GUIDs to UUIDs + item entry."""
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    ws_id = await resolver.workspace_id(workspace)
    entry = await resolver.item(workspace, warehouse)
    return ws_id, entry


@click.group("warehouses")
def warehouses_group() -> None:
    """Manage Microsoft Fabric Data Warehouses and SQL Analytics Endpoints."""


@warehouses_group.command("list")
@click.argument("workspace")
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str) -> None:
    """List all warehouses in WORKSPACE (name or GUID)."""
    try:
        async with _build_clients(ctx) as (http, _):
            cache = LookupCache()
            resolver = Resolver(http=http, cache=cache)
            ws_id = await resolver.workspace_id(workspace)
            items = await _warehouses_svc.list_warehouses(http, ws_id)
            render(
                [w.model_dump(by_alias=True, mode="json") for w in items],
                json_output=ctx.json_output,
                table_title="Warehouses",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("get")
@click.argument("workspace")
@click.argument("warehouse")
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str, warehouse: str) -> None:
    """Get details for WAREHOUSE in WORKSPACE (both accept name or GUID)."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            obj = await _warehouses_svc.get_warehouse(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("create")
@click.argument("workspace")
@click.argument("name")
@click.option("--collation", default=None, help="Default collation for the warehouse.")
@click.option("--description", default=None, help="Description for the warehouse.")
@click.pass_obj
@_coro
async def create_cmd(
    ctx: CliContext,
    workspace: str,
    name: str,
    collation: str | None,
    description: str | None,
) -> None:
    """Create a new warehouse named NAME in WORKSPACE (name or GUID)."""
    try:
        async with _build_clients(ctx) as (http, _):
            cache = LookupCache()
            resolver = Resolver(http=http, cache=cache)
            ws_id = await resolver.workspace_id(workspace)
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
@click.argument("workspace")
@click.argument("warehouse")
@click.argument("new_name")
@click.option("--description", default=None, help="Optional new description.")
@click.pass_obj
@_coro
async def rename_cmd(
    ctx: CliContext,
    workspace: str,
    warehouse: str,
    new_name: str,
    description: str | None,
) -> None:
    """Rename WAREHOUSE in WORKSPACE to NEW_NAME (workspace and warehouse accept name or GUID)."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            if not confirm(
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
            )
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("delete")
@click.argument("workspace")
@click.argument("warehouse")
@click.pass_obj
@_coro
async def delete_cmd(ctx: CliContext, workspace: str, warehouse: str) -> None:
    """Delete WAREHOUSE from WORKSPACE (both accept name or GUID)."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            if not confirm(
                f"Delete warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _warehouses_svc.delete(http, ws_id, entry.id)
            click.echo(f"Warehouse {entry.display_name!r} ({entry.id}) deleted.")
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@warehouses_group.command("takeover")
@click.argument("workspace")
@click.argument("warehouse")
@click.pass_obj
@_coro
async def takeover_cmd(ctx: CliContext, workspace: str, warehouse: str) -> None:
    """Take ownership of WAREHOUSE in WORKSPACE (both accept name or GUID).

    Not supported for SQL Analytics Endpoints.
    """
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            if entry.kind == WarehouseKind.SQL_ENDPOINT:
                raise click.UsageError(  # noqa: TRY003, TRY301
                    "takeover is not supported for SQL Analytics Endpoints"
                )
            if not confirm(
                f"Take over warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _ownership_svc.takeover(http, ws_id, entry.id)
            click.echo(f"Ownership of warehouse {entry.display_name!r} ({entry.id}) taken.")
    except click.UsageError:
        raise
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
