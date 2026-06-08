"""SQL Analytics Endpoint sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import click

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import _coro, _resolve_item
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver
from fabric_dw.services import sql_endpoints as _sql_endpoints_svc

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_clients(
    ctx: CliContext,
) -> AsyncIterator[tuple[FabricHttpClient, None]]:
    """Build and yield an HTTP client for endpoint commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http, None


@click.group("sql-endpoints")
def endpoints_group() -> None:
    """Manage Microsoft Fabric SQL Analytics Endpoints."""


@endpoints_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.option(
    "-A",
    "--all-workspaces",
    "all_workspaces",
    is_flag=True,
    default=False,
    help="Scan all visible workspaces and aggregate results.",
)
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str | None, all_workspaces: bool) -> None:
    """List all SQL analytics endpoints in WORKSPACE (name or GUID).

    Pass -A / --all-workspaces to scan every visible workspace instead.
    WORKSPACE and --all-workspaces are mutually exclusive.
    """
    if workspace and all_workspaces:
        raise click.UsageError("WORKSPACE and --all-workspaces are mutually exclusive.")  # noqa: TRY003
    if not workspace and not all_workspaces:
        raise click.UsageError("Provide WORKSPACE or pass --all-workspaces / -A.")  # noqa: TRY003
    try:
        async with _build_clients(ctx) as (http, _):
            if all_workspaces:
                items = await _sql_endpoints_svc.list_all_workspaces(http)
            else:
                cache = LookupCache()
                resolver = Resolver(http=http, cache=cache)
                assert workspace is not None  # noqa: S101 - guarded above
                ws_id = await resolver.workspace_id(workspace)
                items = await _sql_endpoints_svc.list_endpoints(http, ws_id)
            render(
                [ep.model_dump(by_alias=True, mode="json") for ep in items],
                json_output=ctx.json_output,
                table_title="SQL Analytics Endpoints",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@endpoints_group.command("get")
@click.argument("workspace")
@click.argument("endpoint")
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str, endpoint: str) -> None:
    """Get details for ENDPOINT in WORKSPACE (both accept name or GUID)."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, endpoint)
            obj = await _sql_endpoints_svc.get_endpoint(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@endpoints_group.command("refresh")
@click.argument("workspace")
@click.argument("endpoint")
@click.pass_obj
@_coro
async def refresh_cmd(ctx: CliContext, workspace: str, endpoint: str) -> None:
    """Refresh metadata for ENDPOINT in WORKSPACE (both accept name or GUID).

    Triggers a metadata sync from the underlying Lakehouse delta tables.
    This is a long-running operation (LRO) that is polled to completion.
    """
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, endpoint)
            result = await _sql_endpoints_svc.refresh_metadata(http, ws_id, entry.id)
            render(result, json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
