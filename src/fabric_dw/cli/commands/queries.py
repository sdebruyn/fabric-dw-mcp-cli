"""Queries sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    AGO_OPTION,
    LIMIT_OPTION,
    SINCE_OPTION,
    UNTIL_OPTION,
    build_http_client,
    build_sql_target,
    confirm_destructive,
    coro,
    parse_iso_optional,
    resolve_since,
    resolve_warehouse_arg,
    resolve_workspace,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import queries as _queries_svc
from fabric_dw.services import query_insights as _qi_svc


@click.group("queries")
def queries_group() -> None:
    """Inspect and manage running queries on Fabric warehouses and SQL Analytics Endpoints."""


@queries_group.command("running")
@click.argument("item", required=False, default=None)
@click.pass_obj
@coro
async def running_cmd(ctx: CliContext, item: str | None) -> None:
    """List currently running queries on ITEM (warehouse or endpoint)."""
    ws = resolve_workspace(ctx)
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


@queries_group.command("connections")
@click.argument("item", required=False, default=None)
@click.pass_obj
@coro
async def connections_cmd(ctx: CliContext, item: str | None) -> None:
    """List active SQL connections on ITEM (warehouse or endpoint)."""
    ws = resolve_workspace(ctx)
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
@click.argument("item", required=False, default=None)
@click.argument("session_id", type=int)
@click.pass_obj
@coro
async def kill_cmd(ctx: CliContext, item: str | None, session_id: int) -> None:
    """Kill the session SESSION_ID on ITEM (warehouse or endpoint)."""
    ws = resolve_workspace(ctx)
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
# history
# ---------------------------------------------------------------------------


@queries_group.command("history")
@click.argument("warehouse", required=False, default=None)
@LIMIT_OPTION
@SINCE_OPTION
@UNTIL_OPTION
@AGO_OPTION
@click.pass_obj
@coro
async def history_cmd(
    ctx: CliContext,
    warehouse: str | None,
    limit: int,
    since: str | None,
    until: str | None,
    ago: str | None,
) -> None:
    """List completed SQL requests from queryinsights.exec_requests_history."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, warehouse)
    since_dt = resolve_since(since, ago)
    until_dt = parse_iso_optional(until, "--until")
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
# sessions
# ---------------------------------------------------------------------------


@queries_group.command("sessions")
@click.argument("warehouse", required=False, default=None)
@LIMIT_OPTION
@SINCE_OPTION
@UNTIL_OPTION
@AGO_OPTION
@click.pass_obj
@coro
async def sessions_cmd(
    ctx: CliContext,
    warehouse: str | None,
    limit: int,
    since: str | None,
    until: str | None,
    ago: str | None,
) -> None:
    """List completed sessions from queryinsights.exec_sessions_history."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, warehouse)
    since_dt = resolve_since(since, ago)
    until_dt = parse_iso_optional(until, "--until")
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
@click.argument("warehouse", required=False, default=None)
@LIMIT_OPTION
@SINCE_OPTION
@UNTIL_OPTION
@AGO_OPTION
@click.pass_obj
@coro
async def frequent_cmd(
    ctx: CliContext,
    warehouse: str | None,
    limit: int,
    since: str | None,
    until: str | None,
    ago: str | None,
) -> None:
    """List frequently-run queries from queryinsights.frequently_run_queries."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, warehouse)
    since_dt = resolve_since(since, ago)
    until_dt = parse_iso_optional(until, "--until")
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
@click.argument("warehouse", required=False, default=None)
@LIMIT_OPTION
@SINCE_OPTION
@UNTIL_OPTION
@AGO_OPTION
@click.pass_obj
@coro
async def long_running_cmd(
    ctx: CliContext,
    warehouse: str | None,
    limit: int,
    since: str | None,
    until: str | None,
    ago: str | None,
) -> None:
    """List long-running queries from queryinsights.long_running_queries."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, warehouse)
    since_dt = resolve_since(since, ago)
    until_dt = parse_iso_optional(until, "--until")
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
