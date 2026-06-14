"""Procedures sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    _coro,
    build_http_client,
    build_sql_target,
    confirm_destructive,
    load_select_body,
    parse_qualified_name,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import procedures as _procs_svc

_log = logging.getLogger(__name__)


@click.group("procedures")
def procedures_group() -> None:
    """Manage stored procedures on Fabric warehouses and SQL Analytics Endpoints."""


@procedures_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option("--schema", default=None, help="Filter by schema name.")
@click.pass_obj
@_coro
async def list_cmd(
    ctx: CliContext, workspace: str | None, item: str | None, schema: str | None
) -> None:
    """List stored procedures on ITEM (warehouse or SQL endpoint) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _procs_svc.list_procedures(target, schema=schema, mode=ctx.auth)
            render(
                [p.model_dump(by_alias=True, mode="json") for p in items],
                json_output=ctx.json_output,
                table_title="Stored Procedures",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@procedures_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@_coro
async def get_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
) -> None:
    """Fetch the full definition of QUALIFIED_NAME (schema.proc) on ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, proc_name = parse_qualified_name(qualified_name, kind="procedure")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            p = await _procs_svc.get_procedure(target, schema, proc_name, mode=ctx.auth)
            render(p.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@procedures_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option("--name", "qualified_name", required=True, help="Qualified name: schema.proc.")
@click.option("--body", "body", default=None, help="Inline procedure body.")
@click.option(
    "--from-file", default=None, help="Path to a .sql file containing the procedure body."
)
@click.pass_obj
@_coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
    body: str | None,
    from_file: str | None,
) -> None:
    """Create a new stored procedure QUALIFIED_NAME on ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, proc_name = parse_qualified_name(qualified_name, kind="procedure")
    proc_body = load_select_body(body, from_file)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            p = await _procs_svc.create_procedure(
                target, schema, proc_name, proc_body, mode=ctx.auth
            )
            render(p.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@procedures_group.command("update")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option("--body", "body", default=None, help="Inline procedure body.")
@click.option(
    "--from-file", default=None, help="Path to a .sql file containing the procedure body."
)
@click.pass_obj
@_coro
async def update_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
    body: str | None,
    from_file: str | None,
) -> None:
    """Redefine QUALIFIED_NAME (schema.proc) on ITEM in WORKSPACE via CREATE OR ALTER PROCEDURE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, proc_name = parse_qualified_name(qualified_name, kind="procedure")
    proc_body = load_select_body(body, from_file)
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            confirmed = confirm(
                f"Redefine procedure [{schema}].[{proc_name}] on {entry.display_name!r}?",
                yes=ctx.yes,
            )
            if not confirmed:
                click.echo("Aborted.")
                return
            p = await _procs_svc.update_procedure(
                target, schema, proc_name, proc_body, mode=ctx.auth
            )
            render(p.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@procedures_group.command("drop")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@_coro
async def drop_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
) -> None:
    """Drop QUALIFIED_NAME (schema.proc) from ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, proc_name = parse_qualified_name(qualified_name, kind="procedure")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            if not confirm_destructive(
                f"Drop procedure [{schema}].[{proc_name}] from"
                f" {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _procs_svc.drop_procedure(target, schema, proc_name, mode=ctx.auth)
            if ctx.json_output:
                render({"status": "dropped", "name": f"[{schema}].[{proc_name}]"}, json_output=True)
            else:
                click.echo(f"Procedure [{schema}].[{proc_name}] dropped.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
