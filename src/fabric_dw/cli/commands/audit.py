"""Audit sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    _coro,
    _resolve_item,
    build_http_client,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import audit as _audit_svc

_log = logging.getLogger(__name__)


@click.group("audit")
def audit_group() -> None:
    """Manage SQL audit settings for Microsoft Fabric Data Warehouses."""


@audit_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """Get the current audit settings for ITEM (warehouse) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _audit_svc.get_settings(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("enable")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option(
    "--retention-days",
    "retention_days",
    default=None,
    type=int,
    help="Audit log retention in days (>= 1). Mutually exclusive with --unlimited.",
)
@click.option(
    "--unlimited",
    "unlimited",
    is_flag=True,
    default=False,
    help="Set unlimited audit log retention (maps to 0 on the service). "
    "Mutually exclusive with --retention-days.",
)
@click.pass_obj
@_coro
async def enable_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    retention_days: int | None,
    unlimited: bool,
) -> None:
    """Enable SQL auditing on ITEM (warehouse) in WORKSPACE.

    Omitting both --retention-days and --unlimited defaults to unlimited retention.
    """
    if retention_days is not None and unlimited:
        raise click.UsageError("--retention-days and --unlimited are mutually exclusive.")  # noqa: TRY003
    if retention_days is not None and retention_days < 1:
        raise click.UsageError("--retention-days must be >= 1; use --unlimited for no limit.")  # noqa: TRY003
    # Map to service value: 0 means unlimited.
    effective_days: int = 0
    if retention_days is not None:
        effective_days = retention_days
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _audit_svc.enable(http, ws_id, entry.id, retention_days=effective_days)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("disable")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@_coro
async def disable_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """Disable SQL auditing on ITEM (warehouse) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            confirmed = confirm(
                f"Disable auditing on warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                click.echo("Aborted.")
                return
            obj = await _audit_svc.disable(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("set-retention")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option(
    "--days",
    required=True,
    type=int,
    help="Retention period in days (>= 1). Does not change the audit enabled/disabled state.",
)
@click.pass_obj
@_coro
async def set_retention_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    days: int,
) -> None:
    """Update the audit log retention period for ITEM (warehouse) in WORKSPACE.

    Audit must already be enabled; if disabled, enable it first with
    ``audit enable``.  This command does NOT change the audit state.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _audit_svc.set_retention(http, ws_id, entry.id, days=days)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("set-groups")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
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
    ctx: CliContext, workspace: str | None, item: str | None, groups: tuple[str, ...]
) -> None:
    """Set audit action groups for ITEM (warehouse) in WORKSPACE.

    Pass --group for each action group name, e.g.
    --group BATCH_COMPLETED_GROUP --group SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _audit_svc.set_action_groups(http, ws_id, entry.id, list(groups))
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("add-group")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("group")
@click.pass_obj
@_coro
async def add_group_cmd(
    ctx: CliContext, workspace: str | None, item: str | None, group: str
) -> None:
    """Add GROUP to the audit action groups for ITEM (warehouse) in WORKSPACE.

    Idempotent — if GROUP is already present the command succeeds without
    modifying the configuration.  Auditing must already be enabled.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _audit_svc.add_action_group(http, ws_id, entry.id, group)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("remove-group")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("group")
@click.pass_obj
@_coro
async def remove_group_cmd(
    ctx: CliContext, workspace: str | None, item: str | None, group: str
) -> None:
    """Remove GROUP from the audit action groups for ITEM (warehouse) in WORKSPACE.

    Idempotent — if GROUP is not present the command succeeds without
    modifying the configuration.  Auditing must already be enabled.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            obj = await _audit_svc.remove_action_group(http, ws_id, entry.id, group)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
