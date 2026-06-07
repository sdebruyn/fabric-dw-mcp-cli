"""Cache sub-commands: show, clear, invalidate."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import ParamSpec, TypeVar
from uuid import UUID

import anyio
import click

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import GUID_RE, Resolver

_P = ParamSpec("_P")
_R = TypeVar("_R")

_log = logging.getLogger(__name__)


def _coro(f: Callable[_P, Coroutine[None, None, _R]]) -> Callable[_P, _R]:
    """Wrap an async Click command so it runs via anyio.run.

    ``anyio.run`` does not support keyword arguments, so we wrap the call
    in a no-argument async closure.
    """

    @wraps(f)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        async def _inner() -> _R:
            return await f(*args, **kwargs)

        return anyio.run(_inner)

    return wrapper


@click.group("cache")
def cache_group() -> None:
    """Manage the local name-to-UUID lookup cache."""


@cache_group.command("show")
@click.pass_obj
def show(ctx: CliContext) -> None:
    """Print the current cache contents."""
    cache = LookupCache()
    # Read raw JSON shape from the cache file
    raw = cache._read()  # pylint: disable=protected-access
    render(raw, json_output=ctx.json_output, table_title="Cache")


@cache_group.command("clear")
@click.pass_obj
def clear(ctx: CliContext) -> None:
    """Clear all cached entries."""
    if confirm("Clear the entire lookup cache?", yes=ctx.yes):
        cache = LookupCache()
        cache.clear()
        click.echo("Cache cleared.")
    else:
        click.echo("Aborted.")


@cache_group.command("invalidate")
@click.argument("workspace")
@click.pass_obj
@_coro
async def invalidate(ctx: CliContext, workspace: str) -> None:
    """Invalidate cache entries for WORKSPACE (name or GUID).

    If WORKSPACE is already a GUID, the resolver is bypassed and the
    workspace entry is removed directly.  If it is a name, the resolver
    is used to look up the GUID (hitting the cache or the Fabric API).
    """
    if GUID_RE.match(workspace):
        ws_uuid = UUID(workspace)
    else:
        # Name path: need to resolve via Resolver (requires HTTP client)
        credential = _auth.get_credential(ctx.auth)
        async with FabricHttpClient(credential) as http:
            cache = LookupCache()
            resolver = Resolver(http=http, cache=cache)
            ws_uuid = await resolver.workspace_id(workspace)

    cache = LookupCache()
    cache.invalidate_workspace(ws_uuid)
    click.echo(f"Invalidated cache for workspace {ws_uuid}.")
