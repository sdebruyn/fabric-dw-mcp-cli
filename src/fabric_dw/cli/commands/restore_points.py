"""Restore-points sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import click

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    _coro,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver
from fabric_dw.services import restore as _restore_svc

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_http_client(ctx: CliContext) -> AsyncIterator[FabricHttpClient]:
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http


async def _resolve_ws(http: FabricHttpClient, workspace: str) -> UUID:
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    return await resolver.workspace_id(workspace)


async def _resolve_wh(http: FabricHttpClient, workspace: str, warehouse: str) -> tuple[UUID, UUID]:
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    ws_id = await resolver.workspace_id(workspace)
    entry = await resolver.item(workspace, warehouse)
    return ws_id, entry.id


@click.group("restore-points")
def restore_points_group() -> None:
    """Manage Microsoft Fabric Warehouse restore points."""


@restore_points_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str | None, warehouse: str | None) -> None:
    """List all restore points for WAREHOUSE in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, wh_id = await _resolve_wh(http, ws, wh)
            items = await _restore_svc.list_points(http, ws_id, wh_id)
            render(
                [rp.model_dump(by_alias=True, mode="json") for rp in items],
                json_output=ctx.json_output,
                table_title="Restore Points",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@restore_points_group.command("get")
@click.argument("workspace")
@click.argument("warehouse")
@click.argument("restore_point_id")
@click.pass_obj
@_coro
async def get_cmd(
    ctx: CliContext,
    workspace: str,
    warehouse: str,
    restore_point_id: str,
) -> None:
    """Get a restore point by RESTORE_POINT_ID for WAREHOUSE in WORKSPACE."""
    try:
        async with _build_http_client(ctx) as http:
            ws_id, wh_id = await _resolve_wh(http, workspace, warehouse)
            rp = await _restore_svc.get_point(http, ws_id, wh_id, restore_point_id)
            render(rp.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@restore_points_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("warehouse", required=False, default=None)
@click.option("--name", default=None, help="Optional display name (max 128 chars).")
@click.option("--description", default=None, help="Optional description (max 512 chars).")
@click.pass_obj
@_coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    warehouse: str | None,
    name: str | None,
    description: str | None,
) -> None:
    """Create a restore point for WAREHOUSE in WORKSPACE at the current timestamp."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, warehouse)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, wh_id = await _resolve_wh(http, ws, wh)
            rp = await _restore_svc.create_point(
                http, ws_id, wh_id, name=name, description=description
            )
            render(rp.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@restore_points_group.command("rename")
@click.argument("workspace")
@click.argument("warehouse")
@click.argument("restore_point_id")
@click.argument("new_name")
@click.option("--description", default=None, help="Optional new description.")
@click.pass_obj
@_coro
async def rename_cmd(  # noqa: PLR0913
    ctx: CliContext,
    workspace: str,
    warehouse: str,
    restore_point_id: str,
    new_name: str,
    description: str | None,
) -> None:
    """Rename RESTORE_POINT_ID to NEW_NAME on WAREHOUSE in WORKSPACE."""
    try:
        async with _build_http_client(ctx) as http:
            ws_id, wh_id = await _resolve_wh(http, workspace, warehouse)
            rp = await _restore_svc.update_point(
                http,
                ws_id,
                wh_id,
                restore_point_id,
                name=new_name,
                description=description,
            )
            render(rp.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@restore_points_group.command("delete")
@click.argument("workspace")
@click.argument("warehouse")
@click.argument("restore_point_id")
@click.pass_obj
@_coro
async def delete_cmd(
    ctx: CliContext,
    workspace: str,
    warehouse: str,
    restore_point_id: str,
) -> None:
    """Delete RESTORE_POINT_ID on WAREHOUSE in WORKSPACE.

    Only user-defined restore points can be deleted; system-created points
    are automatically managed by the service.
    You will be asked to confirm unless --yes is passed.
    """
    try:
        async with _build_http_client(ctx) as http:
            ws_id, wh_id = await _resolve_wh(http, workspace, warehouse)
            confirmed = confirm(
                f"Delete restore point {restore_point_id!r} on warehouse in {workspace!r}?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            await _restore_svc.delete_point(http, ws_id, wh_id, restore_point_id)
            click.echo(f"Restore point {restore_point_id!r} deleted.")
    except click.Abort:
        raise
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@restore_points_group.command("restore")
@click.argument("workspace")
@click.argument("warehouse")
@click.argument("restore_point_id")
@click.pass_obj
@_coro
async def restore_cmd(
    ctx: CliContext,
    workspace: str,
    warehouse: str,
    restore_point_id: str,
) -> None:
    """Restore WAREHOUSE in-place to RESTORE_POINT_ID in WORKSPACE.

    WARNING: This is a destructive operation. The warehouse will be
    unavailable for approximately 10 minutes while the restore completes.
    You will be asked to confirm unless --yes is passed.
    """
    try:
        async with _build_http_client(ctx) as http:
            ws_id, wh_id = await _resolve_wh(http, workspace, warehouse)
            confirmed = confirm(
                f"Restore warehouse in {workspace!r} to restore point "
                f"{restore_point_id!r}? "
                f"The warehouse will be unavailable for ~10 minutes.",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            await _restore_svc.restore_in_place(http, ws_id, wh_id, restore_point_id)
            click.echo(f"Warehouse restored to restore point {restore_point_id!r} successfully.")
    except click.Abort:
        raise
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
