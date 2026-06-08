"""Queries sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import click

from fabric_dw import auth as _auth
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import _coro, _resolve_item
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.services import queries as _queries_svc
from fabric_dw.sql import SqlTarget

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_http_client(
    ctx: CliContext,
) -> AsyncIterator[FabricHttpClient]:
    """Build and yield an HTTP client for query commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http


@click.group("queries")
def queries_group() -> None:
    """Inspect and manage running queries on Microsoft Fabric Data Warehouses."""


@queries_group.command("list")
@click.argument("workspace")
@click.argument("warehouse")
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str, warehouse: str) -> None:
    """List currently running queries on WAREHOUSE in WORKSPACE."""
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Warehouse {entry.display_name!r} has no connection string."
                )
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            items = await _queries_svc.list_running(target, mode=ctx.auth)
            render(
                [q.model_dump(by_alias=True, mode="json") for q in items],
                json_output=ctx.json_output,
                table_title="Running Queries",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@queries_group.command("kill")
@click.argument("workspace")
@click.argument("warehouse")
@click.argument("session_id", type=int)
@click.pass_obj
@_coro
async def kill_cmd(ctx: CliContext, workspace: str, warehouse: str, session_id: int) -> None:
    """Kill the session SESSION_ID on WAREHOUSE in WORKSPACE."""
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Warehouse {entry.display_name!r} has no connection string."
                )
            confirmed = confirm(
                f"Kill session {session_id} on warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            await _queries_svc.kill(target, session_id, mode=ctx.auth)
            click.echo(f"Session {session_id} killed.")
    except click.Abort:
        raise
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
