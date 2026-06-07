"""Snapshots sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
from fabric_dw.services import snapshots as _snapshots_svc
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
    """Build and yield HTTP and SQL clients for snapshot commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        sql = FabricSqlClient(mode=ctx.auth)
        yield http, sql


async def _resolve_item(
    http: FabricHttpClient,
    workspace: str,
    item: str,
) -> tuple[UUID, ItemEntry]:
    """Resolve workspace and item names/GUIDs to UUIDs + item entry."""
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    ws_id = await resolver.workspace_id(workspace)
    entry = await resolver.item(workspace, item)
    return ws_id, entry


@click.group("snapshots")
def snapshots_group() -> None:
    """Manage Microsoft Fabric Data Warehouse snapshots."""


@snapshots_group.command("list")
@click.argument("workspace")
@click.argument("warehouse")
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str, warehouse: str) -> None:
    """List all snapshots for WAREHOUSE in WORKSPACE."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            items = await _snapshots_svc.list_snapshots(http, ws_id, entry.id)
            render(
                [s.model_dump(by_alias=True, mode="json") for s in items],
                json_output=ctx.json_output,
                table_title="Snapshots",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("create")
@click.argument("workspace")
@click.argument("warehouse")
@click.argument("name")
@click.option("--description", default=None, help="Optional description.")
@click.option(
    "--snapshot-dt",
    default=None,
    help="Optional snapshot datetime (ISO 8601, UTC).",
)
@click.pass_obj
@_coro
async def create_cmd(  # noqa: PLR0913
    ctx: CliContext,
    workspace: str,
    warehouse: str,
    name: str,
    description: str | None,
    snapshot_dt: str | None,
) -> None:
    """Create a new snapshot named NAME for WAREHOUSE in WORKSPACE."""
    parsed_dt: datetime | None = None
    if snapshot_dt is not None:
        try:
            parsed_dt = datetime.fromisoformat(snapshot_dt)
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=UTC)
        except ValueError as exc:
            raise click.ClickException(  # noqa: TRY003
                f"Invalid --snapshot-dt value {snapshot_dt!r}: {exc}"
            ) from exc

    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            obj = await _snapshots_svc.create(
                http,
                ws_id,
                entry.id,
                name,
                description=description,
                snapshot_dt=parsed_dt,
            )
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("rename")
@click.argument("workspace")
@click.argument("snapshot")
@click.argument("new_name")
@click.option("--description", default=None, help="Optional new description.")
@click.pass_obj
@_coro
async def rename_cmd(
    ctx: CliContext,
    workspace: str,
    snapshot: str,
    new_name: str,
    description: str | None,
) -> None:
    """Rename SNAPSHOT in WORKSPACE to NEW_NAME (workspace and snapshot accept name or GUID)."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, snapshot)
            obj = await _snapshots_svc.rename(
                http,
                ws_id,
                entry.id,
                new_name=new_name,
                description=description,
            )
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("delete")
@click.argument("workspace")
@click.argument("snapshot")
@click.pass_obj
@_coro
async def delete_cmd(ctx: CliContext, workspace: str, snapshot: str) -> None:
    """Delete SNAPSHOT from WORKSPACE (both accept name or GUID)."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, snapshot)
            if not confirm(
                f"Delete snapshot {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _snapshots_svc.delete(http, ws_id, entry.id)
            click.echo(f"Snapshot {entry.display_name!r} ({entry.id}) deleted.")
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@snapshots_group.command("roll")
@click.argument("workspace")
@click.argument("warehouse")
@click.argument("snapshot_name")
@click.option(
    "--to",
    "new_dt",
    default=None,
    help="Target datetime (ISO 8601, UTC). Defaults to CURRENT_TIMESTAMP.",
)
@click.pass_obj
@_coro
async def roll_cmd(
    ctx: CliContext,
    workspace: str,
    warehouse: str,
    snapshot_name: str,
    new_dt: str | None,
) -> None:
    """Roll SNAPSHOT_NAME on WAREHOUSE in WORKSPACE to a new timestamp.

    WORKSPACE and WAREHOUSE accept name or GUID.
    SNAPSHOT_NAME must be the display name of the snapshot database.
    """
    parsed_dt: datetime | None = None
    if new_dt is not None:
        try:
            parsed_dt = datetime.fromisoformat(new_dt)
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=UTC)
        except ValueError as exc:
            raise click.ClickException(  # noqa: TRY003
                f"Invalid --to value {new_dt!r}: {exc}"
            ) from exc

    try:
        async with _build_clients(ctx) as (http, sql):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            if not confirm(
                f"Roll snapshot {snapshot_name!r} on warehouse "
                f"{entry.display_name!r} ({entry.id})?",
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
            await _snapshots_svc.roll_timestamp(sql, target, snapshot_name, parsed_dt)
            click.echo(f"Snapshot {snapshot_name!r} rolled.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
