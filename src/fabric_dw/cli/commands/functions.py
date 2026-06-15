"""Functions sub-commands for the fabric-dw CLI.

Note: Scalar UDFs and inline TVFs are preview features on Microsoft Fabric DW as of mid-2026.
Function DDL is supported on both Data Warehouses and SQL Analytics Endpoints.
"""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    confirm_destructive,
    coro,
    load_sql_body,
    parse_qualified_name,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import functions as _fns_svc
from fabric_dw.services.functions import validate_kind


@click.group("functions")
def functions_group() -> None:
    """Manage T-SQL user-defined functions on Fabric warehouses and SQL Analytics Endpoints.

    Scalar UDFs (FN) and inline TVFs (IF) are preview features as of mid-2026.
    Function DDL is supported on both Data Warehouses and SQL Analytics Endpoints.
    """


@functions_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option("--schema", default=None, help="Filter by schema name.")
@click.option(
    "--kind",
    type=click.Choice(["scalar", "inline-tvf", "all"], case_sensitive=False),
    default="all",
    show_default=True,
    help="Filter by function kind: scalar (FN), inline-tvf (IF), or all.",
)
@click.pass_obj
@coro
async def list_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    schema: str | None,
    kind: str,
) -> None:
    """List T-SQL user-defined functions on ITEM (warehouse or SQL endpoint) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _fns_svc.list_functions(
                target,
                schema=schema,
                kind=validate_kind(kind),
                mode=ctx.auth,  # type: ignore[arg-type]
            )
            render(
                [f.model_dump(by_alias=True, mode="json") for f in items],
                json_output=ctx.json_output,
                table_title="Functions",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@functions_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@coro
async def get_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
) -> None:
    """Fetch the full definition of QUALIFIED_NAME (schema.fn) on ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, fn_name = parse_qualified_name(qualified_name, kind="function")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            f = await _fns_svc.get_function(target, schema, fn_name, mode=ctx.auth)
            render(f.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@functions_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option("--name", "qualified_name", required=True, help="Qualified name: schema.fn.")
@click.option("--body", "body", default=None, help="Inline function body.")
@click.option("--from-file", default=None, help="Path to a .sql file containing the function body.")
@click.pass_obj
@coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
    body: str | None,
    from_file: str | None,
) -> None:
    """Create a new T-SQL user-defined function QUALIFIED_NAME on ITEM in WORKSPACE.

    The body should include the parameter list, RETURNS clause, and function body
    (everything after CREATE FUNCTION [schema].[name]).

    Scalar UDFs and inline TVFs are preview features as of mid-2026.
    Function DDL is supported on both Data Warehouses and SQL Analytics Endpoints.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, fn_name = parse_qualified_name(qualified_name, kind="function")
    fn_body = load_sql_body(body, from_file, inline_opt="--body")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            f = await _fns_svc.create_function(target, schema, fn_name, fn_body, mode=ctx.auth)
            render(f.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@functions_group.command("update")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option("--body", "body", default=None, help="Inline function body.")
@click.option("--from-file", default=None, help="Path to a .sql file containing the function body.")
@click.pass_obj
@coro
async def update_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
    body: str | None,
    from_file: str | None,
) -> None:
    """Redefine QUALIFIED_NAME (schema.fn) on ITEM in WORKSPACE via CREATE OR ALTER FUNCTION.

    Note: ALTER FUNCTION cannot change the function kind (e.g. scalar to inline TVF).
    You will be asked to confirm unless --yes is passed.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, fn_name = parse_qualified_name(qualified_name, kind="function")
    fn_body = load_sql_body(body, from_file, inline_opt="--body")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            confirmed = confirm(
                f"Redefine function [{schema}].[{fn_name}] on {entry.display_name!r}?",
                yes=ctx.yes,
            )
            if not confirmed:
                click.echo("Aborted.")
                return
            f = await _fns_svc.update_function(target, schema, fn_name, fn_body, mode=ctx.auth)
            render(f.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@functions_group.command("drop")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option(
    "--if-exists",
    is_flag=True,
    default=False,
    help="No-op when the function does not exist (DROP FUNCTION IF EXISTS).",
)
@click.pass_obj
@coro
async def drop_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
    if_exists: bool,
) -> None:
    """Drop QUALIFIED_NAME (schema.fn) from ITEM in WORKSPACE.

    You will be asked to confirm unless --yes is passed.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, fn_name = parse_qualified_name(qualified_name, kind="function")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            if not confirm_destructive(
                f"Drop function [{schema}].[{fn_name}] from {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _fns_svc.drop_function(
                target, schema, fn_name, if_exists=if_exists, mode=ctx.auth
            )
            if ctx.json_output:
                render({"status": "dropped", "name": f"[{schema}].[{fn_name}]"}, json_output=True)
            else:
                click.echo(f"Function [{schema}].[{fn_name}] dropped.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@functions_group.command("rename")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option("--new-name", required=True, help="New bare (unqualified) function name.")
@click.pass_obj
@coro
async def rename_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
    new_name: str,
) -> None:
    """Rename QUALIFIED_NAME (schema.fn) on ITEM in WORKSPACE to --new-name.

    Uses EXEC sp_rename. The new name must be a bare (unqualified) identifier;
    sp_rename cannot move a function to a different schema.
    You will be asked to confirm unless --yes is passed.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, fn_name = parse_qualified_name(qualified_name, kind="function")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            confirmed = confirm(
                f"Rename function [{schema}].[{fn_name}] on"
                f" {entry.display_name!r} to {new_name!r}?",
                yes=ctx.yes,
            )
            if not confirmed:
                click.echo("Aborted.")
                return
            f = await _fns_svc.rename_function(target, qualified_name, new_name, mode=ctx.auth)
            render(f.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
