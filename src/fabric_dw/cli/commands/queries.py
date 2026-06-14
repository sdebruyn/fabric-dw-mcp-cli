"""Queries sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from datetime import datetime

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    _coro,
    build_http_client,
    build_sql_target,
    confirm_destructive,
    parse_iso_datetime,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import queries as _queries_svc
from fabric_dw.services import query_insights as _qi_svc

_log = logging.getLogger(__name__)


def _parse_iso(value: str | None, param_name: str) -> datetime | None:
    """Parse an optional ISO-8601 string; raise UsageError on bad input."""
    if value is None:
        return None
    return parse_iso_datetime(value, param_name, assume_utc=False)


_LIMIT_OPTION = click.option(
    "--limit",
    default=100,
    show_default=True,
    type=click.IntRange(1, 10_000),
    help="Maximum number of rows to return (1-10 000).",
)
_SINCE_OPTION = click.option(
    "--since",
    default=None,
    metavar="ISO8601",
    help="Return rows with timestamp >= this value (ISO-8601).",
)
_UNTIL_OPTION = click.option(
    "--until",
    default=None,
    metavar="ISO8601",
    help="Return rows with timestamp <= this value (ISO-8601).",
)


@click.group("queries")
def queries_group() -> None:
    """Inspect and manage running queries on Fabric warehouses and SQL Analytics Endpoints."""


@queries_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """List currently running queries on ITEM (warehouse or endpoint) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _queries_svc.list_running(target, mode=ctx.auth)
            render(
                [q.model_dump(by_alias=True, mode="json") for q in items],
                json_output=ctx.json_output,
                table_title="Running Queries",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@queries_group.command("list-connections")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@_coro
async def list_connections_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """List active SQL connections on ITEM (warehouse or endpoint) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _queries_svc.list_connections(target, mode=ctx.auth)
            render(
                [c.model_dump(by_alias=True, mode="json") for c in items],
                json_output=ctx.json_output,
                table_title="SQL Connections",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@queries_group.command("kill")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("session_id", type=int)
@click.pass_obj
@_coro
async def kill_cmd(
    ctx: CliContext, workspace: str | None, item: str | None, session_id: int
) -> None:
    """Kill the session SESSION_ID on ITEM (warehouse or endpoint) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            if not confirm_destructive(
                f"Kill session {session_id} on {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _queries_svc.kill(target, session_id, mode=ctx.auth)
            if ctx.json_output:
                render({"status": "killed", "session_id": session_id}, json_output=True)
            else:
                click.echo(f"Session {session_id} killed.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# request-history
# ---------------------------------------------------------------------------


@queries_group.command("request-history")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@_LIMIT_OPTION
@_SINCE_OPTION
@_UNTIL_OPTION
@click.pass_obj
@_coro
async def request_history_cmd(
    ctx: CliContext,
    workspace: str | None,
    warehouse: str | None,
    limit: int,
    since: str | None,
    until: str | None,
) -> None:
    """List completed SQL requests from queryinsights.exec_requests_history."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    since_dt = _parse_iso(since, "--since")
    until_dt = _parse_iso(until, "--until")
    try:
        async with build_http_client(ctx) as http:
            target, _ = await build_sql_target(http, ws, wh)
            items = await _qi_svc.list_request_history(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth
            )
            render(
                [q.model_dump(by_alias=True, mode="json") for q in items],
                json_output=ctx.json_output,
                table_title="Request History",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# session-history
# ---------------------------------------------------------------------------


@queries_group.command("session-history")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@_LIMIT_OPTION
@_SINCE_OPTION
@_UNTIL_OPTION
@click.pass_obj
@_coro
async def session_history_cmd(
    ctx: CliContext,
    workspace: str | None,
    warehouse: str | None,
    limit: int,
    since: str | None,
    until: str | None,
) -> None:
    """List completed sessions from queryinsights.exec_sessions_history."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    since_dt = _parse_iso(since, "--since")
    until_dt = _parse_iso(until, "--until")
    try:
        async with build_http_client(ctx) as http:
            target, _ = await build_sql_target(http, ws, wh)
            items = await _qi_svc.list_session_history(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth
            )
            render(
                [q.model_dump(by_alias=True, mode="json") for q in items],
                json_output=ctx.json_output,
                table_title="Session History",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# frequent
# ---------------------------------------------------------------------------


@queries_group.command("frequent")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@_LIMIT_OPTION
@_SINCE_OPTION
@_UNTIL_OPTION
@click.pass_obj
@_coro
async def frequent_cmd(
    ctx: CliContext,
    workspace: str | None,
    warehouse: str | None,
    limit: int,
    since: str | None,
    until: str | None,
) -> None:
    """List frequently-run queries from queryinsights.frequently_run_queries."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    since_dt = _parse_iso(since, "--since")
    until_dt = _parse_iso(until, "--until")
    try:
        async with build_http_client(ctx) as http:
            target, _ = await build_sql_target(http, ws, wh)
            items = await _qi_svc.list_frequent_queries(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth
            )
            render(
                [q.model_dump(by_alias=True, mode="json") for q in items],
                json_output=ctx.json_output,
                table_title="Frequently Run Queries",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# long-running
# ---------------------------------------------------------------------------


@queries_group.command("long-running")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@_LIMIT_OPTION
@_SINCE_OPTION
@_UNTIL_OPTION
@click.pass_obj
@_coro
async def long_running_cmd(
    ctx: CliContext,
    workspace: str | None,
    warehouse: str | None,
    limit: int,
    since: str | None,
    until: str | None,
) -> None:
    """List long-running queries from queryinsights.long_running_queries."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    since_dt = _parse_iso(since, "--since")
    until_dt = _parse_iso(until, "--until")
    try:
        async with build_http_client(ctx) as http:
            target, _ = await build_sql_target(http, ws, wh)
            items = await _qi_svc.list_long_running_queries(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth
            )
            render(
                [q.model_dump(by_alias=True, mode="json") for q in items],
                json_output=ctx.json_output,
                table_title="Long Running Queries",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
