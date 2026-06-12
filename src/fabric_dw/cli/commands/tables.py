"""Tables sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
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
from fabric_dw.services import tables as _tables_svc
from fabric_dw.sql_io import OutputFormat, columns_rows_to_arrow, write_arrow

_log = logging.getLogger(__name__)


@click.group("tables")
def tables_group() -> None:
    """Manage SQL tables on Fabric warehouses and SQL Analytics Endpoints."""


@tables_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option("--schema", default=None, help="Filter by schema name.")
@click.pass_obj
@_coro
async def list_cmd(
    ctx: CliContext, workspace: str | None, item: str | None, schema: str | None
) -> None:
    """List tables on ITEM (warehouse or SQL endpoint) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _tables_svc.list_tables(target, schema=schema, mode=ctx.auth)
            render(
                [t.model_dump(mode="json") for t in items],
                json_output=ctx.json_output,
                table_title="Tables",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@tables_group.command("read")
@click.argument("workspace", required=False, default=None)
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
@_coro
async def read_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
    count: int,
    fmt: str,
    output: str | None,
) -> None:
    """Read up to COUNT rows from QUALIFIED_NAME (schema.table) on ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")
    output_path = Path(output) if output else None

    if fmt in (OutputFormat.CSV, OutputFormat.PARQUET) and output_path is None:
        raise click.UsageError(f"--output PATH is required for {fmt!r} format.")  # noqa: TRY003

    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            columns, rows = await _tables_svc.read_table(
                target, schema, table_name, count=count, mode=ctx.auth
            )
            arrow_table = columns_rows_to_arrow(columns, rows)
            write_arrow(arrow_table, fmt, output_path)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@tables_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option("--name", "qualified_name", required=True, help="Qualified name: schema.table.")
@click.option("--select", "select_body", default=None, help="Inline SELECT statement for CTAS.")
@click.option("--from-file", default=None, help="Path to a .sql file containing the SELECT body.")
@click.pass_obj
@_coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
    select_body: str | None,
    from_file: str | None,
) -> None:
    """Create a new table via CTAS (CREATE TABLE AS SELECT) on ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")
    body = load_select_body(select_body, from_file)
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            t = await _tables_svc.create_table(
                target, schema, table_name, body, kind=entry.kind, mode=ctx.auth
            )
            render(t.model_dump(mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@tables_group.command("delete")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@_coro
async def delete_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
) -> None:
    """Drop QUALIFIED_NAME (schema.table) from ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            confirm_destructive(
                f"Drop table [{schema}].[{table_name}] from {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            await _tables_svc.delete_table(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth
            )
            click.echo(f"Table [{schema}].[{table_name}] dropped.")
    except click.Abort:
        raise
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@tables_group.command("clear")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@_coro
async def clear_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
) -> None:
    """Truncate QUALIFIED_NAME (schema.table) on ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            confirm_destructive(
                f"Truncate table [{schema}].[{table_name}] on {entry.display_name!r}?",
                yes=ctx.yes,
            )
            await _tables_svc.clear_table(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth
            )
            click.echo(f"Table [{schema}].[{table_name}] truncated.")
    except click.Abort:
        raise
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
