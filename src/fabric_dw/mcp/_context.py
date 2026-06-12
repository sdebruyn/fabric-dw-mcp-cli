"""ServerContext dataclass and factory for the fabric-dw MCP server.

The :class:`ServerContext` bundles the three shared service objects — HTTP
client, lookup cache, and resolver — together with the active credential mode.
A single instance is created during server startup via :func:`build_context`
and cleared on shutdown.

Design note
-----------
FastMCP's lifespan mechanism stores the yielded object inside the low-level
request context (``request_context.lifespan_context``), but retrieving it
requires injecting a ``Context`` parameter into every one of 65 tool
functions.  Instead, we store the single ``ServerContext`` instance in a
module-level sentinel (``_SERVER_CTX``) that is set during the
``asynccontextmanager`` lifespan and cleared on teardown.  A
:func:`get_context` accessor raises ``RuntimeError`` when called outside the
lifespan (i.e. before startup or after shutdown), making mis-use visible.

The :class:`FabricHttpClient` is closed (``await ctx.http.aclose()``) in the
lifespan's cleanup block, ensuring that open sockets are drained on SIGTERM /
CTRL-C rather than leaking.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.exceptions import ConfigError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

__all__ = [
    "ServerContext",
    "build_context",
    "fabric_lifespan",
    "get_context",
]


@dataclass
class ServerContext:
    """Bundles the shared service objects needed by every MCP tool.

    Attributes:
        http: The shared HTTP client (must be ``aclose()``'d on shutdown).
        cache: Name-to-UUID lookup cache.
        resolver: Workspace / item resolver backed by *http* and *cache*.
        auth_mode: The active credential mode (e.g. ``"default"``).
    """

    http: FabricHttpClient
    cache: LookupCache
    resolver: Resolver
    auth_mode: _auth.CredentialMode


# ---------------------------------------------------------------------------
# Module-level sentinel — set during lifespan, cleared on shutdown
# ---------------------------------------------------------------------------

_SERVER_CTX: ServerContext | None = None


def get_context() -> ServerContext:
    """Return the active :class:`ServerContext`.

    Raises:
        RuntimeError: When called outside the server lifespan (before startup
            or after shutdown).
    """
    if _SERVER_CTX is None:
        raise RuntimeError(  # noqa: TRY003
            "ServerContext is not initialised — get_context() called outside the server lifespan"
        )
    return _SERVER_CTX


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_context(environ: Mapping[str, str] | None = None) -> ServerContext:
    """Construct a fresh :class:`ServerContext` from the environment.

    Args:
        environ: Mapping to read ``FABRIC_AUTH`` from.  Defaults to
            ``os.environ`` when ``None``.

    Returns:
        A fully initialised :class:`ServerContext`.

    Raises:
        :class:`~fabric_dw.exceptions.ConfigError`: When ``FABRIC_AUTH``
            contains an unrecognised value.
    """
    env = environ if environ is not None else os.environ
    raw_mode = env.get("FABRIC_AUTH", "default")
    try:
        mode = _auth.CredentialMode(raw_mode)
    except ValueError as exc:
        raise ConfigError(  # noqa: TRY003
            f"invalid FABRIC_AUTH value {raw_mode!r}; "
            f"expected one of {[m.value for m in _auth.CredentialMode]}"
        ) from exc

    credential = _auth.get_credential(mode)
    http = FabricHttpClient(credential=credential)
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    return ServerContext(http=http, cache=cache, resolver=resolver, auth_mode=mode)


# ---------------------------------------------------------------------------
# FastMCP lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def fabric_lifespan(app: FastMCP) -> AsyncIterator[None]:  # noqa: ARG001
    """FastMCP lifespan that initialises and tears down the :class:`ServerContext`.

    Usage::

        mcp = FastMCP("fabric-dw", lifespan=fabric_lifespan)

    The lifespan:

    1. Calls :func:`build_context` to create the ``ServerContext`` and stores
       it in the module-level ``_SERVER_CTX`` sentinel.
    2. Enters the HTTP client as an async context manager (``async with ctx.http``),
       which initialises the underlying ``httpx.AsyncClient``.
    3. Yields control to the server.
    4. On exit (normal or via exception / signal), the HTTP client context manager
       calls ``aclose()`` on the underlying ``httpx.AsyncClient`` to drain open
       connections, then clears the sentinel.
    """
    global _SERVER_CTX  # noqa: PLW0603
    ctx = build_context()
    async with ctx.http:
        _SERVER_CTX = ctx
        try:
            yield
        finally:
            _SERVER_CTX = None
