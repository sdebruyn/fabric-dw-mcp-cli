"""Shared helpers used by all per-noun CLI command modules."""

from __future__ import annotations

import os
from collections.abc import Callable, Coroutine, Sequence
from functools import wraps
from typing import TYPE_CHECKING, ParamSpec, TypeVar
from uuid import UUID

import anyio
import click
from rich.console import Console
from rich.table import Table

from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import ItemAccess
from fabric_dw.resolver import Resolver

if TYPE_CHECKING:
    from fabric_dw.cli._context import CliContext

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _coro(f: Callable[_P, Coroutine[None, None, _R]]) -> Callable[_P, _R]:
    """Wrap an async Click command so it runs via anyio.run."""

    @wraps(f)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        async def _inner() -> _R:
            return await f(*args, **kwargs)

        return anyio.run(_inner)

    return wrapper


async def _resolve_item(
    http: FabricHttpClient,
    workspace: str,
    item: str,
) -> tuple[UUID, ItemEntry]:
    """Resolve workspace and item names/GUIDs to UUIDs + item entry."""
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    ws_id = await resolver.workspace_id(workspace)
    entry = await resolver.item(workspace, item)
    return ws_id, entry


async def _resolve_item_with_cache(
    http: FabricHttpClient,
    workspace: str,
    item: str,
) -> tuple[UUID, ItemEntry, LookupCache]:
    """Resolve workspace and item names/GUIDs and return the shared cache instance.

    Use this variant when the caller needs the cache for subsequent eviction or
    population (e.g. after rename or delete).
    """
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    ws_id = await resolver.workspace_id(workspace)
    entry = await resolver.item(workspace, item)
    return ws_id, entry, cache


def resolve_workspace_arg(ctx: CliContext, value: str | None) -> str:
    """Resolve the workspace argument using the priority order.

    1. Explicit positional arg (*value*).
    2. ``FABRIC_DW_DEFAULT_WORKSPACE`` environment variable.
    3. ``ctx.config.defaults.workspace`` from the config file.
    4. Neither → :class:`click.UsageError`.
    """
    if value is not None:
        return value
    env = os.environ.get("FABRIC_DW_DEFAULT_WORKSPACE")
    if env:
        return env
    cfg_val = ctx.config.defaults.workspace
    if cfg_val is not None:
        return cfg_val
    raise click.UsageError(  # noqa: TRY003
        "no workspace specified; pass as argument or run 'fabric-dw config set workspace ...'"
    )


def render_permissions_table(
    accesses: Sequence[ItemAccess],
    *,
    title: str,
    console: Console | None = None,
) -> None:
    """Render a sequence of :class:`~fabric_dw.models.ItemAccess` as a Rich table.

    Args:
        accesses: The list of item access records to display.
        title: Table title shown in the Rich header.
        console: Optional Rich console; defaults to a new :class:`~rich.console.Console`.
    """
    con = console or Console()
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Display Name", no_wrap=True)
    table.add_column("UPN / App ID")
    table.add_column("Type")
    table.add_column("Permissions")
    table.add_column("Additional Permissions")

    for entry in accesses:
        p = entry.principal
        display = p.display_name or ""
        identity = p.user_principal_name or (str(p.aad_app_id) if p.aad_app_id else "")
        ptype = p.type
        perms = ", ".join(entry.item_access_details.permissions)
        additional = ", ".join(entry.item_access_details.additional_permissions)
        table.add_row(display, identity, ptype, perms, additional)

    con.print(table)


def resolve_warehouse_arg(ctx: CliContext, value: str | None) -> str:
    """Resolve the warehouse argument using the priority order.

    1. Explicit positional arg (*value*).
    2. ``FABRIC_DW_DEFAULT_WAREHOUSE`` environment variable.
    3. ``ctx.config.defaults.warehouse`` from the config file.
    4. Neither → :class:`click.UsageError`.
    """
    if value is not None:
        return value
    env = os.environ.get("FABRIC_DW_DEFAULT_WAREHOUSE")
    if env:
        return env
    cfg_val = ctx.config.defaults.warehouse
    if cfg_val is not None:
        return cfg_val
    raise click.UsageError(  # noqa: TRY003
        "no warehouse specified; pass as argument or run 'fabric-dw config set warehouse ...'"
    )
