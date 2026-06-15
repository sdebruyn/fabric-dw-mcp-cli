"""Statistics sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    confirm_destructive,
    coro,
    parse_qualified_name,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import statistics as _stats_svc


@click.group("statistics")
def statistics_group() -> None:
    """Manage user-defined statistics on Fabric Data Warehouses and SQL Analytics Endpoints.

    Only single-column statistics are supported (Fabric limitation).
    DDL operations (create/update/delete) require a Data Warehouse;
    list and show work on both Data Warehouses and SQL Analytics Endpoints.
    """


@statistics_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option("--schema", default=None, help="Filter by schema name.")
@click.option("--table", default=None, help="Filter by table name (unqualified).")
@click.option(
    "--user-only",
    is_flag=True,
    default=False,
    help="Only show user-created statistics.",
)
@click.option(
    "--auto-only",
    is_flag=True,
    default=False,
    help="Only show auto-created statistics.",
)
@click.pass_obj
@coro
async def list_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    schema: str | None,
    table: str | None,
    user_only: bool,
    auto_only: bool,
) -> None:
    """List statistics on ITEM (warehouse or SQL endpoint) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _stats_svc.list_statistics(
                target,
                schema=schema,
                table=table,
                user_only=user_only,
                auto_only=auto_only,
                mode=ctx.auth,
            )
            render(
                [s.model_dump(by_alias=True, mode="json") for s in items],
                json_output=ctx.json_output,
                table_title="Statistics",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@statistics_group.command("show")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_table")
@click.argument("stat_name")
@click.option(
    "--histogram",
    "histogram_only",
    is_flag=True,
    default=False,
    help="Show only the histogram steps (skip header and density vector).",
)
@click.pass_obj
@coro
async def show_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_table: str,
    stat_name: str,
    histogram_only: bool,
) -> None:
    """Show details of STAT_NAME on QUALIFIED_TABLE (schema.table) in WORKSPACE.

    Uses DBCC SHOW_STATISTICS with STAT_HEADER, DENSITY_VECTOR, and HISTOGRAM
    variants. Pass --histogram to show only the histogram steps.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    parse_qualified_name(qualified_table, kind="table")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            details = await _stats_svc.show_statistics(
                target,
                qualified_table,
                stat_name,
                histogram_only=histogram_only,
                mode=ctx.auth,
            )
            render(details.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@statistics_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option(
    "--table",
    "qualified_table",
    required=True,
    help="Qualified table name: schema.table.",
)
@click.option(
    "--column",
    required=True,
    help=(
        "Column name to create the statistic on. "
        "Only single-column statistics are supported (Fabric limitation)."
    ),
)
@click.option("--name", "stat_name", default=None, help="Statistic name.")
@click.option(
    "--fullscan",
    "fullscan",
    is_flag=True,
    default=True,
    help="Use FULLSCAN sampling (default). Mutually exclusive with --sample-percent.",
)
@click.option(
    "--sample-percent",
    "sample_percent",
    type=click.IntRange(1, 100),
    default=None,
    help=("Sample a percentage of the table (1-100). When set, overrides --fullscan."),
)
@click.pass_obj
@coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_table: str,
    column: str,
    stat_name: str | None,
    fullscan: bool,
    sample_percent: int | None,
) -> None:
    """Create a statistic on --table (schema.table) on ITEM in WORKSPACE.

    Only Data Warehouses support DDL; SQL Analytics Endpoints are read-only.
    Only single-column statistics are supported (Fabric limitation).
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    parse_qualified_name(qualified_table, kind="table")
    if stat_name is None:
        raise click.UsageError("--name is required: Fabric requires an explicit statistic name.")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            stat = await _stats_svc.create_statistics(
                target,
                qualified_table,
                column,
                name=stat_name,
                fullscan=fullscan,
                sample_percent=sample_percent,
                kind=entry.kind,
                mode=ctx.auth,
            )
            render(stat.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@statistics_group.command("update")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_table")
@click.argument("stat_name")
@click.option(
    "--fullscan",
    "fullscan",
    is_flag=True,
    default=True,
    help="Use FULLSCAN sampling (default). Mutually exclusive with --sample-percent.",
)
@click.option(
    "--sample-percent",
    "sample_percent",
    type=click.IntRange(1, 100),
    default=None,
    help="Sample a percentage of the table (1-100). When set, overrides --fullscan.",
)
@click.pass_obj
@coro
async def update_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_table: str,
    stat_name: str,
    fullscan: bool,
    sample_percent: int | None,
) -> None:
    """Update STAT_NAME on QUALIFIED_TABLE (schema.table) on ITEM in WORKSPACE.

    Only Data Warehouses support DDL; SQL Analytics Endpoints are read-only.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    parse_qualified_name(qualified_table, kind="table")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            await _stats_svc.update_statistics(
                target,
                qualified_table,
                stat_name,
                fullscan=fullscan,
                sample_percent=sample_percent,
                kind=entry.kind,
                mode=ctx.auth,
            )
            if ctx.json_output:
                render(
                    {"status": "updated", "stat_name": stat_name, "table": qualified_table},
                    json_output=True,
                )
            else:
                click.echo(f"Statistics [{stat_name}] on {qualified_table} updated.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@statistics_group.command("delete")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_table")
@click.argument("stat_name")
@click.pass_obj
@coro
async def delete_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_table: str,
    stat_name: str,
) -> None:
    """Drop STAT_NAME on QUALIFIED_TABLE (schema.table) from ITEM in WORKSPACE.

    Only Data Warehouses support DDL; SQL Analytics Endpoints are read-only.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    parse_qualified_name(qualified_table, kind="table")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            if not confirm_destructive(
                f"Drop statistic [{stat_name}] on {qualified_table} "
                f"from {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _stats_svc.drop_statistics(
                target,
                qualified_table,
                stat_name,
                kind=entry.kind,
                mode=ctx.auth,
            )
            if ctx.json_output:
                render(
                    {"status": "dropped", "stat_name": stat_name, "table": qualified_table},
                    json_output=True,
                )
            else:
                click.echo(f"Statistics [{stat_name}] on {qualified_table} dropped.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
