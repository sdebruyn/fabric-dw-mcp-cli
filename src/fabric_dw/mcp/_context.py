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
from fabric_dw.config import load_config
from fabric_dw.config_resolve import resolve_float_knob, resolve_int_knob
from fabric_dw.exceptions import ConfigError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver
from fabric_dw.sql import reset_pool

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

_MIN_RETRY_DEADLINE_S: float = 0.1


def build_context(
    environ: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> ServerContext:
    """Construct a fresh :class:`ServerContext` from the environment and config file.

    The 429 retry budget is resolved with precedence env > config > built-in
    default (10 / 300.0) via the shared :func:`~fabric_dw.config_resolve.resolve_int_knob`
    and :func:`~fabric_dw.config_resolve.resolve_float_knob` helpers.

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

    # 1. Env var (wins when non-empty/non-whitespace).
    # Normalise to lowercase so FABRIC_AUTH=SP / Interactive / DEFAULT all work,
    # matching the case-insensitive behaviour documented for config/CLI.
    raw_env_mode = env.get("FABRIC_AUTH", "").strip().lower()

    cfg = load_config(config_path)

    if raw_env_mode:
        # Non-empty env value — must be a recognised mode or we raise.
        try:
            mode = _auth.CredentialMode(raw_env_mode)
        except ValueError as exc:
            raise ConfigError(
                f"invalid FABRIC_AUTH value {raw_env_mode!r}; "
                f"expected one of {[m.value for m in _auth.CredentialMode]}"
            ) from exc
    elif cfg.defaults.auth_mode is not None:
        # 2. Config file value.  In normal operation this branch is guaranteed to
        # hold an exact lowercase enum member because `_parse_defaults_section`
        # already discards invalid values to ``None`` and lowercases valid ones.
        # The try/except is a belt-and-suspenders guard against parse-time
        # validation regressing (e.g. a direct ``save_config`` bypass) and cannot
        # be reached by any test without monkeypatching ``load_config``.
        try:
            mode = _auth.CredentialMode(cfg.defaults.auth_mode)
        except ValueError:
            _logger.warning(
                "[defaults] auth_mode %r from config is not a recognised credential mode; "
                "falling back to built-in default.",
                cfg.defaults.auth_mode,
            )
            mode = _auth.CredentialMode.DEFAULT
    else:
        # 3. Built-in default.
        mode = _auth.CredentialMode.DEFAULT

    credential = _auth.get_credential(mode)

    retries = resolve_int_knob(
        cli_value=None,
        env_key="FABRIC_DW_MAX_429_RETRIES",
        env=env,
        config_value=cfg.defaults.max_429_retries,
        min_val=1,
        knob_name="max_429_retries",
    )
    deadline = resolve_float_knob(
        cli_value=None,
        env_key="FABRIC_DW_RETRY_DEADLINE_S",
        env=env,
        config_value=cfg.defaults.retry_deadline_s,
        min_val=_MIN_RETRY_DEADLINE_S,
        knob_name="retry_deadline_s",
    )

    http = FabricHttpClient(
        credential=credential,
        **({"max_429_retries": retries} if retries is not None else {}),  # type: ignore[arg-type]
        **({"combined_deadline_s": deadline} if deadline is not None else {}),  # type: ignore[arg-type]
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
            reset_pool()
