"""Shared helpers used by all per-noun CLI command modules."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from functools import wraps
from typing import ParamSpec, TypeVar
from uuid import UUID

import anyio

from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver

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
