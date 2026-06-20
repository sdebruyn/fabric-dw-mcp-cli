"""Schemas sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    confirm_destructive,
    coro,
    resolve_warehouse_arg,
    resolve_workspace,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import schemas as _schemas_svc


@click.group("schemas")
def schemas_group() -> None:
    """Manage SQL schemas on Fabric warehouses."""


@schemas_group.command("list")
@click.argument("item", required=False, default=None)
@click.pass_obj
@coro
async def list_cmd(ctx: CliContext, item: str | None) -> None:
    """List user-defined schemas on ITEM (warehouse)."""
    ws = resolve_workspace(ctx)
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
@click.argument("item", required=False, default=None)
@click.argument("name")
@click.pass_obj
@coro
async def create_cmd(
    ctx: CliContext,
    item: str | None,
    name: str,
) -> None:
    """Create a new SQL schema NAME on ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            s = await _schemas_svc.create_schema(target, name, mode=ctx.auth)
            render(s.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@schemas_group.command("delete")
@click.argument("item", required=False, default=None)
@click.argument("name")
@click.option(
    "--cascade",
    is_flag=True,
    default=False,
    help=(
        "Drop all tables, views, functions, and stored procedures in the schema before "
        "dropping the schema itself. "
        "WARNING: This permanently deletes all contained objects and their data."
    ),
)
@click.pass_obj
@coro
async def delete_cmd(
    ctx: CliContext,
    item: str | None,
    name: str,
    cascade: bool,
) -> None:
    """Drop schema NAME from ITEM.

    Pass --cascade to also drop all tables, views, functions, and stored
    procedures inside the schema first.
    This is a destructive, irreversible operation.
    """
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            prompt = f"Drop schema [{name}] from {entry.display_name!r} ({entry.id})?"
            if cascade:
                prompt = (
                    f"--cascade will permanently drop all tables, views, functions, "
                    f"and stored procedures in schema [{name}] on {entry.display_name!r}. "
                    + prompt
                )
            if not confirm_destructive(prompt, yes=ctx.yes):
                click.echo("Aborted.")
                return
            await _schemas_svc.delete_schema(
                target, name, cascade=cascade, kind=entry.kind, mode=ctx.auth
            )
            if ctx.json_output:
                render({"status": "dropped", "name": f"[{name}]"}, json_output=True)
            else:
                click.echo(f"Schema [{name}] dropped.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
