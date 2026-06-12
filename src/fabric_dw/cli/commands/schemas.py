"""Schemas sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    _coro,
    _guard_not_sql_endpoint,
    build_http_client,
    build_sql_target,
    confirm_destructive,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import schemas as _schemas_svc

_log = logging.getLogger(__name__)


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
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _schemas_svc.list_schemas(target, mode=ctx.auth)
            render(
                [s.model_dump(by_alias=True, mode="json") for s in items],
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
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            _guard_not_sql_endpoint(entry)
            s = await _schemas_svc.create_schema(target, name, mode=ctx.auth)
            render(s.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
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
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            _guard_not_sql_endpoint(entry)
            prompt = f"Drop schema [{name}] from {entry.display_name!r} ({entry.id})?"
            if cascade:
                prompt = (
                    f"--cascade will permanently drop all tables and views "
                    f"in schema [{name}] on {entry.display_name!r}. " + prompt
                )
            if not confirm_destructive(prompt, yes=ctx.yes):
                click.echo("Aborted.")
                return
            await _schemas_svc.delete_schema(target, name, cascade=cascade, mode=ctx.auth)
            click.echo(f"Schema [{name}] dropped.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
