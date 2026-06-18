"""Settings sub-commands for the fabric-dw CLI.

This group manages **server-side** database settings (stored in the Fabric
Data Warehouse / SQL Analytics Endpoint).  It is distinct from the ``config``
group, which manages *client-side* CLI defaults (workspace, warehouse, etc.).
"""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    coro,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import settings as _settings_svc


@click.group("settings")
def settings_group() -> None:
    """Manage server-side database settings on Fabric Data Warehouses.

    ``settings`` manages server-side warehouse/database configuration
    (result-set caching, time-travel retention).  For client-side CLI
    defaults (workspace, warehouse) use the ``config`` group instead.

    Both Data Warehouses and SQL Analytics Endpoints support ``show``.
    The write commands (``result-set-caching``, ``retention``) target
    Data Warehouses; the behaviour on a SQL Analytics Endpoint is not
    guaranteed.
    """


@settings_group.command("show")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@coro
async def show_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
) -> None:
    """Show all server-side settings for ITEM in WORKSPACE.

    WORKSPACE and ITEM may be display names or GUIDs.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            result = await _settings_svc.get_settings(target, mode=ctx.auth)
            render(
                result.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
                table_title="Warehouse Settings",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@settings_group.command("result-set-caching")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("state", type=click.Choice(["on", "off"], case_sensitive=False))
@click.pass_obj
@coro
async def result_set_caching_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    state: str,
) -> None:
    """Enable or disable result-set caching on ITEM in WORKSPACE.

    STATE must be ``on`` or ``off`` (case-insensitive).

    Executes ``ALTER DATABASE CURRENT SET RESULT_SET_CACHING { ON | OFF }``
    on the target warehouse.

    WORKSPACE and ITEM may be display names or GUIDs.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    enabled = state.lower() == "on"
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            result = await _settings_svc.set_result_set_caching(
                target, enabled=enabled, mode=ctx.auth
            )
            if ctx.json_output:
                render(result.model_dump(by_alias=True, mode="json"), json_output=True)
            else:
                click.echo(
                    f"Result-set caching {'enabled' if enabled else 'disabled'} "
                    f"on {result.database!r}."
                )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@settings_group.command("retention")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option(
    "--days",
    required=True,
    type=click.IntRange(_settings_svc._RETENTION_MIN, _settings_svc._RETENTION_MAX),
    help=(
        f"Retention period in days "
        f"({_settings_svc._RETENTION_MIN}-{_settings_svc._RETENTION_MAX}).  "
        "Time-travel data older than this many days is no longer retained. "
        "Primarily a Data Warehouse concept; may be a no-op on a SQL Analytics Endpoint."
    ),
)
@click.pass_obj
@coro
async def retention_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    days: int,
) -> None:
    """Set the time-travel retention period on ITEM in WORKSPACE.

    Executes ``ALTER DATABASE CURRENT SET TIME_TRAVEL_RETENTION_PERIOD = <DAYS> DAYS``
    on the target warehouse.

    WORKSPACE and ITEM may be display names or GUIDs.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            result = await _settings_svc.set_time_travel_retention(target, days, mode=ctx.auth)
            if ctx.json_output:
                render(result.model_dump(by_alias=True, mode="json"), json_output=True)
            else:
                click.echo(f"Time-travel retention set to {days} day(s) on {result.database!r}.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
