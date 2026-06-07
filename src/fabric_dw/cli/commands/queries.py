"""Queries sub-commands for the fabric-dw CLI."""

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
from fabric_dw.resolver import Resolver
from fabric_dw.services import queries as _queries_svc
from fabric_dw.sql_client import FabricSqlClient, SqlTarget

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
) -> AsyncIterator[tuple[FabricHttpClient, FabricSqlClient]]:
    """Build and yield HTTP and SQL clients for query commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        sql = FabricSqlClient(mode=ctx.auth)
        yield http, sql


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


@click.group("queries")
def queries_group() -> None:
    """Inspect and manage running queries on Microsoft Fabric Data Warehouses."""


@queries_group.command("list")
@click.argument("workspace")
@click.argument("warehouse")
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str, warehouse: str) -> None:
    """List currently running queries on WAREHOUSE in WORKSPACE."""
    try:
        async with _build_clients(ctx) as (http, sql):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Warehouse {entry.display_name!r} has no connection string."
                )
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            items = await _queries_svc.list_running(sql, target)
            render(
                [q.model_dump(by_alias=True, mode="json") for q in items],
                json_output=ctx.json_output,
                table_title="Running Queries",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@queries_group.command("kill")
@click.argument("workspace")
@click.argument("warehouse")
@click.argument("session_id", type=int)
@click.pass_obj
@_coro
async def kill_cmd(ctx: CliContext, workspace: str, warehouse: str, session_id: int) -> None:
    """Kill the session SESSION_ID on WAREHOUSE in WORKSPACE."""
    try:
        async with _build_clients(ctx) as (http, sql):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            if not confirm(
                f"Kill session {session_id} on warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Warehouse {entry.display_name!r} has no connection string."
                )
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            await _queries_svc.kill(sql, target, session_id)
            click.echo(f"Session {session_id} killed.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
