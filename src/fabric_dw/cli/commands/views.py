"""Views sub-commands for the fabric-dw CLI."""

from __future__ import annotations

from pathlib import Path

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
    resolve_workspace,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import views as _views_svc
from fabric_dw.services.columns import get_object_columns_or_raise as _get_columns
from fabric_dw.sql_io import OutputFormat, columns_rows_to_arrow, write_arrow


@click.group("views")
def views_group() -> None:
    """Manage SQL views on Fabric warehouses and SQL Analytics Endpoints."""


@views_group.command("list")
@click.argument("item", required=False, default=None)
@click.option("--schema", default=None, help="Filter by schema name.")
@click.pass_obj
@coro
async def list_cmd(ctx: CliContext, item: str | None, schema: str | None) -> None:
    """List views on ITEM (warehouse or SQL endpoint)."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _views_svc.list_views(target, schema=schema, mode=ctx.auth)
            render(
                [v.model_dump(by_alias=True, mode="json") for v in items],
                json_output=ctx.json_output,
                table_title="Views",
                prune_null_columns=True,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("read")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option("--count", default=10, show_default=True, help="Max rows to return.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice([f.value for f in OutputFormat], case_sensitive=False),
    default=OutputFormat.JSON,
    show_default=True,
    help="Output format.",
)
@click.option("--output", default=None, help="Write to this file instead of stdout.")
@click.pass_obj
@coro
async def read_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
    count: int,
    fmt: str,
    output: str | None,
) -> None:
    """Read up to COUNT rows from QUALIFIED_NAME (schema.view) on ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = parse_qualified_name(qualified_name, kind="view")
    output_path = Path(output) if output else None

    # --format takes precedence when explicitly supplied (i.e. differs from the default
    # JSON value); if --format is omitted (or is the default "json"), the global --json
    # flag selects JSON output.  This means --json --format csv produces CSV.
    _json_fallback = OutputFormat.JSON.value if ctx.json_output else fmt
    effective_fmt = fmt if fmt != OutputFormat.JSON else _json_fallback

    if effective_fmt in (OutputFormat.CSV, OutputFormat.PARQUET) and output_path is None:
        raise click.UsageError(f"--output PATH is required for {effective_fmt!r} format.")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            columns, rows = await _views_svc.read_view(
                target, schema, view_name, count=count, mode=ctx.auth
            )
            arrow_table = columns_rows_to_arrow(columns, rows)
            write_arrow(arrow_table, effective_fmt, output_path)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("columns")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@coro
async def columns_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
) -> None:
    """List columns of QUALIFIED_NAME (schema.view) on ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = parse_qualified_name(qualified_name, kind="view")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            cols = await _get_columns(target, schema, view_name, kind_label="view", mode=ctx.auth)
            render(
                cols,
                json_output=ctx.json_output,
                table_title="Columns",
                prune_null_columns=True,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("count")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@coro
async def count_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
) -> None:
    """Count rows in QUALIFIED_NAME (schema.view) on ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = parse_qualified_name(qualified_name, kind="view")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            row_count = await _views_svc.count_view_rows(target, schema, view_name, mode=ctx.auth)
            render(
                {"schema": schema, "name": view_name, "row_count": row_count},
                json_output=ctx.json_output,
                table_title="Row Count",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("get")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@coro
async def get_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
) -> None:
    """Fetch the full definition of QUALIFIED_NAME (schema.view) on ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = parse_qualified_name(qualified_name, kind="view")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            v = await _views_svc.get_view(target, schema, view_name, mode=ctx.auth)
            render(v.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("create")
@click.argument("item", required=False, default=None)
@click.option("--name", "qualified_name", required=True, help="Qualified name: schema.view.")
@click.option(
    "--select",
    "select_body",
    default=None,
    help=(
        "Inline SELECT or WITH (CTE) statement for the view body.  Must be a"
        " single read-only statement; write keywords and semicolons are rejected"
        " fail-closed, even inside string literals or quoted identifiers."
    ),
)
@click.option("--from-file", default=None, help="Path to a .sql file containing the SELECT body.")
@click.pass_obj
@coro
async def create_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
    select_body: str | None,
    from_file: str | None,
) -> None:
    """Create a new view QUALIFIED_NAME on ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = parse_qualified_name(qualified_name, kind="view")
    body = load_sql_body(select_body, from_file)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            v = await _views_svc.create_view(target, schema, view_name, body, mode=ctx.auth)
            render(v.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("update")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option(
    "--select",
    "select_body",
    default=None,
    help=(
        "Inline SELECT or WITH (CTE) statement for the new view body.  Must be a"
        " single read-only statement; write keywords and semicolons are rejected"
        " fail-closed, even inside string literals or quoted identifiers."
    ),
)
@click.option("--from-file", default=None, help="Path to a .sql file containing the SELECT body.")
@click.pass_obj
@coro
async def update_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
    select_body: str | None,
    from_file: str | None,
) -> None:
    """Redefine QUALIFIED_NAME (schema.view) on ITEM via CREATE OR ALTER VIEW."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = parse_qualified_name(qualified_name, kind="view")
    body = load_sql_body(select_body, from_file)
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            confirmed = confirm(
                f"Redefine view [{schema}].[{view_name}] on {entry.display_name!r}?",
                yes=ctx.yes,
            )
            if not confirmed:
                click.echo("Aborted.")
                return
            v = await _views_svc.update_view(target, schema, view_name, body, mode=ctx.auth)
            render(v.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("drop")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@coro
async def drop_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
) -> None:
    """Drop QUALIFIED_NAME (schema.view) from ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = parse_qualified_name(qualified_name, kind="view")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            if not confirm_destructive(
                f"Drop view [{schema}].[{view_name}] from {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _views_svc.drop_view(target, schema, view_name, mode=ctx.auth)
            if ctx.json_output:
                render({"status": "dropped", "name": f"[{schema}].[{view_name}]"}, json_output=True)
            else:
                click.echo(f"View [{schema}].[{view_name}] dropped.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("rename")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option("--new-name", required=True, help="New bare (unqualified) view name.")
@click.pass_obj
@coro
async def rename_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
    new_name: str,
) -> None:
    """Rename QUALIFIED_NAME (schema.view) on ITEM to --new-name."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = parse_qualified_name(qualified_name, kind="view")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            confirmed = confirm(
                f"Rename view [{schema}].[{view_name}] on {entry.display_name!r} to {new_name!r}?",
                yes=ctx.yes,
            )
            if not confirmed:
                click.echo("Aborted.")
                return
            v = await _views_svc.rename_view(target, qualified_name, new_name, mode=ctx.auth)
            render(v.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
