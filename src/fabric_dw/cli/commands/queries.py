"""Queries sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

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


class _JsonModel(Protocol):
    def model_dump(self, *, by_alias: bool, mode: str) -> dict[str, Any]: ...


def _validate_watch(ctx: CliContext, watch: int | None) -> None:
    """Reject streaming JSON before opening a network client."""
    if watch is not None and ctx.json_output:
        raise click.UsageError("--watch cannot be used with --json.")


async def _watch_render(
    *,
    interval: int | None,
    command: str,
    title: str,
    json_output: bool,
    fetch: Callable[[], Awaitable[Sequence[_JsonModel]]],
) -> None:
    """Render once or continuously, in the familiar terminal-watch style."""
    while True:
        items = await fetch()
        if interval is not None:
            click.clear()
            timestamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            click.echo(f"Every {interval}s: {command}    {timestamp}")
            click.echo()
        render(
            [item.model_dump(by_alias=True, mode="json") for item in items],
            json_output=json_output,
            table_title=title,
            prune_null_columns=True,
        )
        if interval is None:
            return
        await asyncio.sleep(interval)


@click.group("queries")
def queries_group() -> None:
    """Inspect and manage running queries on Fabric warehouses and SQL Analytics Endpoints."""


@queries_group.command("running")
@click.argument("item", required=False, default=None)
@click.option(
    "--watch", type=click.IntRange(min=1), metavar="SECONDS", help="Refresh every SECONDS."
)
@click.pass_obj
@coro
async def running_cmd(ctx: CliContext, item: str | None, watch: int | None) -> None:
    """List currently running queries on ITEM (warehouse or endpoint)."""
    _validate_watch(ctx, watch)
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            await _watch_render(
                interval=watch,
                command="fdw queries running",
                title="Running Queries",
                json_output=ctx.json_output,
                fetch=lambda: _queries_svc.list_running(target, mode=ctx.auth),
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@queries_group.command("connections")
@click.argument("item", required=False, default=None)
@click.option(
    "--watch", type=click.IntRange(min=1), metavar="SECONDS", help="Refresh every SECONDS."
)
@click.pass_obj
@coro
async def connections_cmd(ctx: CliContext, item: str | None, watch: int | None) -> None:
    """List active SQL connections on ITEM (warehouse or endpoint)."""
    _validate_watch(ctx, watch)
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            await _watch_render(
                interval=watch,
                command="fdw queries connections",
                title="SQL Connections",
                json_output=ctx.json_output,
                fetch=lambda: _queries_svc.list_connections(target, mode=ctx.auth),
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
# locks
# ---------------------------------------------------------------------------


@queries_group.command("locks")
@click.argument("item", required=False, default=None)
@click.option(
    "--limit",
    type=click.IntRange(1, 10_000),
    default=100,
    show_default=True,
    help="Maximum rows to return (1-10000).",
)
@click.option(
    "--waiting-only",
    is_flag=True,
    default=False,
    help="Only show locks with request_status = WAIT or CONVERT (includes lock-upgrade waits).",
)
@click.option(
    "--blocked-only",
    is_flag=True,
    default=False,
    help=(
        "Only show sessions blocked by another session (victims). "
        "The blocker appears in the blocking_session_id column."
    ),
)
@click.option(
    "--include-database",
    is_flag=True,
    default=False,
    help="Include DATABASE-scoped lock rows (excluded by default).",
)
@click.option(
    "--watch", type=click.IntRange(min=1), metavar="SECONDS", help="Refresh every SECONDS."
)
@click.pass_obj
@coro
async def locks_cmd(
    ctx: CliContext,
    item: str | None,
    limit: int,
    waiting_only: bool,
    blocked_only: bool,
    include_database: bool,
    watch: int | None,
) -> None:
    """List active locks from sys.dm_tran_locks on ITEM."""
    _validate_watch(ctx, watch)
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            await _watch_render(
                interval=watch,
                command="fdw queries locks",
                title="Active Locks",
                json_output=ctx.json_output,
                fetch=lambda: _queries_svc.list_locks(
                    target,
                    limit=limit,
                    waiting_only=waiting_only,
                    blocked_only=blocked_only,
                    include_database=include_database,
                    mode=ctx.auth,
                ),
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@queries_group.command("show")
@click.argument("item", required=False, default=None)
@click.argument("dist_statement_id")
@click.pass_obj
@coro
async def show_cmd(ctx: CliContext, item: str | None, dist_statement_id: str) -> None:
    """Look up a completed query by DIST_STATEMENT_ID from queryinsights.exec_requests_history."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _ = await build_sql_target(http, ws, wh)
            result = await _qi_svc.get_request_detail(target, dist_statement_id, mode=ctx.auth)
            if result is None:
                if ctx.json_output:
                    render(None, json_output=True)
                else:
                    click.echo(
                        f"No request found with distributed_statement_id {dist_statement_id!r}."
                    )
                return
            render(
                result.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
                table_title="Request Detail",
                prune_null_columns=True,
            )
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
                prune_null_columns=True,
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
                prune_null_columns=True,
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
                prune_null_columns=True,
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
                prune_null_columns=True,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
