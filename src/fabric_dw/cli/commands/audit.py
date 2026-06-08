"""Audit sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import click

from fabric_dw import auth as _auth
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    _coro,
    _resolve_item,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.services import audit as _audit_svc

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_clients(
    ctx: CliContext,
) -> AsyncIterator[tuple[FabricHttpClient, None]]:
    """Build and yield an HTTP client for audit commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http, None


@click.group("audit")
def audit_group() -> None:
    """Manage SQL audit settings for Microsoft Fabric Data Warehouses."""


@audit_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """Get the current audit settings for WAREHOUSE in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _audit_svc.get_settings(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("enable")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.option(
    "--retention-days",
    default=0,
    show_default=True,
    help="Audit log retention in days (0 = unlimited).",
)
@click.pass_obj
@_coro
async def enable_cmd(
    ctx: CliContext,
    workspace: str | None,
    warehouse: str | None,
    retention_days: int,
) -> None:
    """Enable SQL auditing on WAREHOUSE in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _audit_svc.enable(http, ws_id, entry.id, retention_days=retention_days)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("disable")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@_coro
async def disable_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """Disable SQL auditing on WAREHOUSE in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, ws, wh)
            confirmed = confirm(
                f"Disable auditing on warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            obj = await _audit_svc.disable(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except click.Abort:
        raise
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("set-groups")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.option(
    "-g",
    "--group",
    "groups",
    multiple=True,
    required=True,
    help="Audit action group name (repeat for multiple).",
)
@click.pass_obj
@_coro
async def set_groups_cmd(
    ctx: CliContext, workspace: str | None, warehouse: str | None, groups: tuple[str, ...]
) -> None:
    """Set audit action groups for WAREHOUSE in WORKSPACE.

    Pass --group for each action group name, e.g.
    --group BATCH_COMPLETED_GROUP --group SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _audit_svc.set_action_groups(http, ws_id, entry.id, list(groups))
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
