"""Queries sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    _coro,
    build_http_client,
    build_sql_target,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import queries as _queries_svc

_log = logging.getLogger(__name__)


@click.group("queries")
def queries_group() -> None:
    """Inspect and manage running queries on Fabric warehouses and SQL Analytics Endpoints."""


@queries_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """List currently running queries on WAREHOUSE_OR_ENDPOINT in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _queries_svc.list_running(target, mode=ctx.auth)
            render(
                [q.model_dump(by_alias=True, mode="json") for q in items],
                json_output=ctx.json_output,
                table_title="Running Queries",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@queries_group.command("list-connections")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@_coro
async def list_connections_cmd(
    ctx: CliContext, workspace: str | None, warehouse: str | None
) -> None:
    """List active SQL connections on WAREHOUSE_OR_ENDPOINT in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _queries_svc.list_connections(target, mode=ctx.auth)
            render(
                [c.model_dump(by_alias=True, mode="json") for c in items],
                json_output=ctx.json_output,
                table_title="SQL Connections",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@queries_group.command("kill")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.argument("session_id", type=int)
@click.pass_obj
@_coro
async def kill_cmd(
    ctx: CliContext, workspace: str | None, warehouse: str | None, session_id: int
) -> None:
    """Kill the session SESSION_ID on WAREHOUSE_OR_ENDPOINT in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            confirmed = confirm(
                f"Kill session {session_id} on {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            await _queries_svc.kill(target, session_id, mode=ctx.auth)
            click.echo(f"Session {session_id} killed.")
    except click.Abort:
        raise
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
