"""SQL Analytics Endpoint sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import json as _json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import click
from rich.console import Console
from rich.table import Table

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    _coro,
    _resolve_item,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import ItemAccess, TableSyncStatus
from fabric_dw.resolver import Resolver
from fabric_dw.services import permissions as _permissions_svc
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
def sql_endpoints_group() -> None:
    """Manage Microsoft Fabric SQL Analytics Endpoints."""


@sql_endpoints_group.command("list")
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


@sql_endpoints_group.command("get")
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


_STATUS_STYLES: dict[str, str] = {
    "Success": "green",
    "Failure": "red",
    "NotRun": "yellow",
}

_ERROR_MAX_LEN = 60


def _render_refresh_table(
    statuses: list[TableSyncStatus], *, console: Console | None = None
) -> None:
    """Render a list of :class:`TableSyncStatus` as a Rich table."""
    con = console or Console()
    table = Table(title="Metadata Refresh Results", show_header=True, header_style="bold")
    table.add_column("Table", no_wrap=True)
    table.add_column("Status")
    table.add_column("End Time")
    table.add_column("Error", max_width=_ERROR_MAX_LEN)

    for s in statuses:
        status_text = s.status
        style = _STATUS_STYLES.get(s.status, "")
        end_dt = s.end_date_time.isoformat() if s.end_date_time else ""

        error_text = ""
        if s.error:
            parts = []
            if s.error.error_code:
                parts.append(s.error.error_code)
            if s.error.message:
                parts.append(s.error.message)
            error_text = ": ".join(parts)

        table.add_row(
            s.table_name,
            f"[{style}]{status_text}[/{style}]" if style else status_text,
            end_dt,
            error_text,
        )

    con.print(table)


@sql_endpoints_group.command("refresh")
@click.argument("workspace")
@click.argument("endpoint")
@click.option(
    "--recreate-tables",
    "recreate_tables",
    is_flag=True,
    default=False,
    help=(
        "Drop and recreate all tables during the refresh. "
        "Use to resolve inconsistencies or force a clean rebuild. "
        "DESTRUCTIVE — use with caution."
    ),
)
@click.pass_obj
@_coro
async def refresh_cmd(
    ctx: CliContext, workspace: str, endpoint: str, recreate_tables: bool
) -> None:
    """Refresh metadata for ENDPOINT in WORKSPACE (both accept name or GUID).

    Triggers a metadata sync from the underlying Lakehouse delta tables.
    This is a long-running operation (LRO) that is polled to completion.

    By default, results are shown as a Rich table.  Pass --json (on the root
    command) to emit raw JSON instead.
    """
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, endpoint)
            statuses = await _sql_endpoints_svc.refresh_metadata(
                http, ws_id, entry.id, recreate_tables=recreate_tables
            )
            if ctx.json_output:
                click.echo(
                    _json.dumps(
                        [s.model_dump(by_alias=True, mode="json") for s in statuses],
                        indent=2,
                        default=str,
                    )
                )
            else:
                _render_refresh_table(statuses)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


def _render_permissions_table(
    accesses: Sequence[ItemAccess], *, console: Console | None = None
) -> None:
    """Render a sequence of :class:`~fabric_dw.models.ItemAccess` as a Rich table."""
    con = console or Console()
    table = Table(title="SQL Analytics Endpoint Permissions", show_header=True, header_style="bold")
    table.add_column("Display Name", no_wrap=True)
    table.add_column("UPN / App ID")
    table.add_column("Type")
    table.add_column("Permissions")
    table.add_column("Additional")

    for entry in accesses:
        p = entry.principal
        display = p.display_name or ""
        identity = p.user_principal_name or (str(p.aad_app_id) if p.aad_app_id else "")
        ptype = p.type
        perms = ", ".join(entry.item_access_details.permissions)
        additional = ", ".join(entry.item_access_details.additional_permissions)
        table.add_row(display, identity, ptype, perms, additional)

    con.print(table)


@sql_endpoints_group.command("permissions")
@click.argument("workspace", required=False, default=None)
@click.argument("endpoint")
@click.pass_obj
@_coro
async def permissions_cmd(ctx: CliContext, workspace: str | None, endpoint: str) -> None:
    """List principals with access to ENDPOINT in WORKSPACE (both accept name or GUID).

    Requires Fabric Administrator role.
    See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, ws, endpoint)
            items = await _permissions_svc.list_item_access(http, ws_id, entry.id)
            if ctx.json_output:
                click.echo(
                    _json.dumps(
                        [a.model_dump(by_alias=True, mode="json") for a in items],
                        indent=2,
                        default=str,
                    )
                )
            else:
                _render_permissions_table(items)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
