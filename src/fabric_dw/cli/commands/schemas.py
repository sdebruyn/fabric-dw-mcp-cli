"""Schemas sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import click

from fabric_dw import auth as _auth
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    _coro,
    _guard_not_sql_endpoint,
    _resolve_item,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.services import schemas as _schemas_svc
from fabric_dw.sql import SqlTarget

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_http_client(ctx: CliContext) -> AsyncIterator[FabricHttpClient]:
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http


@click.group("schemas")
def schemas_group() -> None:
    """Manage SQL schemas on Fabric warehouses."""


@schemas_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """List user-defined schemas on ITEM (warehouse) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Item {entry.display_name!r} has no connection string."
                )
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            items = await _schemas_svc.list_schemas(target, mode=ctx.auth)
            render(
                [s.model_dump(mode="json") for s in items],
                json_output=ctx.json_output,
                table_title="Schemas",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@schemas_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("name")
@click.pass_obj
@_coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    name: str,
) -> None:
    """Create a new SQL schema NAME on ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            _guard_not_sql_endpoint(entry)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Item {entry.display_name!r} has no connection string."
                )
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            s = await _schemas_svc.create_schema(target, name, mode=ctx.auth)
            render(s.model_dump(mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@schemas_group.command("delete")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("name")
@click.option(
    "--cascade",
    is_flag=True,
    default=False,
    help=(
        "Drop all tables and views in the schema before dropping the schema itself. "
        "WARNING: This permanently deletes all contained objects and their data."
    ),
)
@click.pass_obj
@_coro
async def delete_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    name: str,
    cascade: bool,
) -> None:
    """Drop schema NAME from ITEM in WORKSPACE.

    Pass --cascade to also drop all tables and views inside the schema first.
    This is a destructive, irreversible operation.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            _guard_not_sql_endpoint(entry)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Item {entry.display_name!r} has no connection string."
                )
            if cascade:
                click.echo(
                    f"WARNING: --cascade will permanently drop all tables and views "
                    f"in schema [{name}] on {entry.display_name!r}.",
                    err=True,
                )
            confirmed = confirm(
                f"Drop schema [{name}] from {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            await _schemas_svc.delete_schema(target, name, cascade=cascade, mode=ctx.auth)
            click.echo(f"Schema [{name}] dropped.")
    except click.Abort:
        raise
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
