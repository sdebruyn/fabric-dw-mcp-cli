"""ServerContext dataclass and factory for the fabric-dw MCP server.

The :class:`ServerContext` bundles the three shared service objects — HTTP
client, lookup cache, and resolver — together with the active credential mode.
A single instance is created during server startup via :func:`build_context`
and cleared on shutdown.

Design note
-----------
FastMCP's lifespan mechanism stores the yielded object inside the low-level
request context (``request_context.lifespan_context``), but retrieving it
requires injecting a ``Context`` parameter into every tool function.  Instead,
we store the single ``ServerContext`` instance in a module-level sentinel
(``_SERVER_CTX``) that is set during the
``asynccontextmanager`` lifespan and cleared on teardown.  A
:func:`get_context` accessor raises ``RuntimeError`` when called outside the
lifespan (i.e. before startup or after shutdown), making mis-use visible.

The :class:`FabricHttpClient` is closed via ``async with ctx.http:`` inside the
lifespan.  :class:`FabricHttpClient` has no standalone ``aclose()`` method;
cleanup is handled by its ``__aexit__``, which drains open sockets on normal
exit, SIGTERM, or CTRL-C.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fabric_dw import auth as _auth
from fabric_dw.cache import LookupCache
from fabric_dw.config import VALID_AUTH_MODES, load_config
from fabric_dw.config_resolve import resolve_auth_mode, resolve_int_knob
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
        http: The shared HTTP client (closed via ``async with ctx.http:`` in the lifespan).
        cache: Name-to-UUID lookup cache.
        resolver: Workspace / item resolver backed by *http* and *cache*.
        auth_mode: The active credential mode (e.g. ``"default"``).
        workspace_allowlist: The ``[mcp] workspace_allowlist`` value from
            ``config.toml``, or ``None`` when the key is absent.  The guard
            functions in :mod:`fabric_dw.mcp._guards` resolve the effective
            allowlist from env var (highest) > this value > no restriction.
    """

    http: FabricHttpClient
    cache: LookupCache
    resolver: Resolver
    auth_mode: _auth.CredentialMode
    workspace_allowlist: list[str] | None = None


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
        raise RuntimeError(
            "ServerContext is not initialised — get_context() called outside the server lifespan"
        )
    return _SERVER_CTX


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


_logger = logging.getLogger(__name__)

_MIN_RETRY_DEADLINE_S: int = 1


def build_context(
    environ: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> ServerContext:
    """Construct a fresh :class:`ServerContext` from the environment and config file.

    The 429 retry budget is resolved with precedence env > config > built-in
    default (10 / 300) via the shared :func:`~fabric_dw.config_resolve.resolve_int_knob`
    helper.

    The credential mode is resolved with precedence:

    1. ``FABRIC_AUTH`` environment variable (when non-empty/non-whitespace).
    2. ``[defaults] auth_mode`` in ``config.toml``.
    3. Built-in default: ``default`` (``DefaultAzureCredential``).

    An empty or whitespace-only ``FABRIC_AUTH`` is treated as absent (falls
    through to config/default) rather than raising an error.  An unrecognised
    non-empty value raises :class:`~fabric_dw.exceptions.ConfigError`.

    Args:
        environ: Mapping to read environment variables from.  Defaults to
            ``os.environ`` when ``None``.
        config_path: Path to the config file.  Defaults to the platform
            standard path when ``None``.

    Returns:
        A fully initialised :class:`ServerContext`.

    Raises:
        :class:`~fabric_dw.exceptions.ConfigError`: When ``FABRIC_AUTH``
            contains an unrecognised non-empty value.
    """
    env = environ if environ is not None else os.environ

    cfg = load_config(config_path)

    # Resolve the credential mode via the shared 3-layer helper
    # (env > config > built-in default).  The MCP server has no CLI flag so
    # cli_value is always None here; the CLI path passes an explicit flag value
    # when the user sets --auth on the command line.
    try:
        raw_mode = resolve_auth_mode(
            cli_value=None,
            env=env,
            config_value=cfg.defaults.auth_mode,
            valid_modes=VALID_AUTH_MODES,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    mode = _auth.CredentialMode(raw_mode)

    credential = _auth.get_credential(mode)

    retries = resolve_int_knob(
        cli_value=None,
        env_key="FABRIC_DW_MAX_429_RETRIES",
        env=env,
        config_value=cfg.defaults.max_429_retries,
        min_val=1,
        knob_name="max_429_retries",
    )
    deadline = resolve_int_knob(
        cli_value=None,
        env_key="FABRIC_DW_RETRY_DEADLINE_S",
        env=env,
        config_value=cfg.defaults.retry_deadline_s,
        min_val=_MIN_RETRY_DEADLINE_S,
        knob_name="retry_deadline_s",
    )

    http = FabricHttpClient(
        credential=credential,
        **({"max_429_retries": retries} if retries is not None else {}),
        **({"combined_deadline_s": deadline} if deadline is not None else {}),
    )
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    return ServerContext(
        http=http,
        cache=cache,
        resolver=resolver,
        auth_mode=mode,
        workspace_allowlist=cfg.mcp.workspace_allowlist,
    )


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
    4. On exit (normal or via exception / signal), ``__aexit__`` of ``ctx.http``
       drains open connections (the lifespan uses ``async with ctx.http:``;
       there is no standalone ``aclose()`` call), then clears the sentinel.
    """
    global _SERVER_CTX  # noqa: PLW0603
    try:
        ctx = build_context()
    except ConfigError as exc:
        raise RuntimeError(
            f"fabric-dw MCP server failed to start due to a configuration error: {exc}"
        ) from exc
    async with ctx.http:
        _SERVER_CTX = ctx
        try:
            yield
        finally:
            _SERVER_CTX = None
