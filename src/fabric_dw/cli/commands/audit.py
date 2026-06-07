"""Audit sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from functools import wraps
from typing import ParamSpec, TypeVar
from uuid import UUID

import anyio
import click

from fabric_dw import auth as _auth
from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver
from fabric_dw.services import audit as _audit_svc

_P = ParamSpec("_P")
_R = TypeVar("_R")

_log = logging.getLogger(__name__)


def _coro(f: Callable[_P, Coroutine[None, None, _R]]) -> Callable[_P, _R]:
    """Wrap an async Click command so it runs via anyio.run."""

    @wraps(f)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        async def _inner() -> _R:
            return await f(*args, **kwargs)

        return anyio.run(_inner)

    return wrapper


@asynccontextmanager
async def _build_clients(
    ctx: CliContext,
) -> AsyncIterator[tuple[FabricHttpClient, None]]:
    """Build and yield an HTTP client for audit commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http, None


async def _resolve_item(
    http: FabricHttpClient,
    workspace: str,
    warehouse: str,
) -> tuple[UUID, ItemEntry]:
    """Resolve workspace and warehouse names/GUIDs to UUIDs + item entry."""
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    ws_id = await resolver.workspace_id(workspace)
    entry = await resolver.item(workspace, warehouse)
    return ws_id, entry


@click.group("audit")
def audit_group() -> None:
    """Manage SQL audit settings for Microsoft Fabric Data Warehouses."""


@audit_group.command("get")
@click.argument("workspace")
@click.argument("warehouse")
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str, warehouse: str) -> None:
    """Get the current audit settings for WAREHOUSE in WORKSPACE."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            obj = await _audit_svc.get_settings(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("enable")
@click.argument("workspace")
@click.argument("warehouse")
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
    workspace: str,
    warehouse: str,
    retention_days: int,
) -> None:
    """Enable SQL auditing on WAREHOUSE in WORKSPACE."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            obj = await _audit_svc.enable(http, ws_id, entry.id, retention_days=retention_days)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("disable")
@click.argument("workspace")
@click.argument("warehouse")
@click.pass_obj
@_coro
async def disable_cmd(ctx: CliContext, workspace: str, warehouse: str) -> None:
    """Disable SQL auditing on WAREHOUSE in WORKSPACE."""
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            if not confirm(
                f"Disable auditing on warehouse {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            obj = await _audit_svc.disable(http, ws_id, entry.id)
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@audit_group.command("set-groups")
@click.argument("workspace")
@click.argument("warehouse")
@click.argument("groups", nargs=-1, required=True)
@click.pass_obj
@_coro
async def set_groups_cmd(
    ctx: CliContext, workspace: str, warehouse: str, groups: tuple[str, ...]
) -> None:
    """Set audit action GROUPS for WAREHOUSE in WORKSPACE.

    GROUPS is a space-separated list of action group names (e.g.
    BATCH_COMPLETED_GROUP SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP).
    """
    try:
        async with _build_clients(ctx) as (http, _):
            ws_id, entry = await _resolve_item(http, workspace, warehouse)
            obj = await _audit_svc.set_action_groups(http, ws_id, entry.id, list(groups))
            render(obj.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
