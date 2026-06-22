"""FastMCP server exposing all fabric-dw service functions as MCP tools.

Architecture
------------
This module is a **thin** entry point.  All tool implementations live in
per-domain sub-modules under :mod:`fabric_dw.mcp.tools`.

Startup / Shutdown
------------------
A :func:`~fabric_dw.mcp._context.fabric_lifespan` async context manager is
passed to the :class:`~mcp.server.fastmcp.FastMCP` constructor.  On startup
it calls :func:`~fabric_dw.mcp._context.build_context` to construct one
:class:`~fabric_dw.mcp._context.ServerContext` (HTTP client, cache, resolver,
auth mode) and stores it in a module-level sentinel accessible via
:func:`~fabric_dw.mcp._context.get_context`.  On shutdown (normal exit,
SIGTERM, CTRL-C) the lifespan uses ``async with ctx.http:`` so the HTTP client
is closed by its ``__aexit__`` (no standalone ``aclose()`` call needed).

Context access
--------------
Every tool function calls :func:`~fabric_dw.mcp._context.get_context` to
obtain the shared :class:`~fabric_dw.mcp._context.ServerContext`.  The sentinel
pattern (module-level ``ServerContext | None``) was chosen over injecting a
``Context`` parameter into every tool because FastMCP's lifespan context is
not ergonomically accessible from tool functions without either adding a
``Context`` import to every tool or using fragile ``request_context``
internals.  The sentinel is safe for streamable-HTTP concurrency because it is
**read-only** after startup — tools only call ``get_context()``; they never
re-assign the global.

Security environment variables
-------------------------------
``FABRIC_MCP_READONLY``
    Set to ``1``, ``true``, or ``yes`` to restrict ``execute_sql`` to
    SELECT/WITH statements and block all mutating tools.

``FABRIC_MCP_ALLOW_DESTRUCTIVE``
    Set to ``1``, ``true``, or ``yes`` to enable permanently-destructive
    tools (delete_warehouse, delete_snapshot, delete_restore_point,
    restore_warehouse_in_place, delete_schema, delete_table, clear_table,
    delete_sql_pool).  Defaults to **disabled**.

``FABRIC_MCP_WORKSPACES``
    Comma-separated workspace names or GUIDs the server may touch.
    Unset = all workspaces allowed.

``FABRIC_MCP_ALLOW_REMOTE``
    Set to ``1``, ``true``, or ``yes`` to allow the HTTP transport to bind
    on a non-loopback address.  A prominent WARNING is logged when set.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from typing import Literal

from fabric_dw.config import load_config
from fabric_dw.logging import setup_logging
from fabric_dw.mcp._context import fabric_lifespan
from fabric_dw.mcp._guards import env_flag as _guards_env_flag
from fabric_dw.mcp._helpers import InstrumentedFastMCP
from fabric_dw.mcp.tools import register_all
from fabric_dw.telemetry import (
    maybe_print_first_run_notice,
    record_app_exited,
    record_app_started,
    record_mcp_server_started,
    shutdown_telemetry,
)
from fabric_dw.telemetry_commands import now_ms

__all__ = ["mcp", "run"]

# ---------------------------------------------------------------------------
# FastMCP server instance (instrumented subclass emits command_invoked events)
# ---------------------------------------------------------------------------

mcp: InstrumentedFastMCP = InstrumentedFastMCP("fabric-dw", lifespan=fabric_lifespan)

# ---------------------------------------------------------------------------
# Register all domain tools
# ---------------------------------------------------------------------------

register_all(mcp)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _resolve_log_level() -> int:
    """Return the effective log level integer.

    Resolution order: env ``FABRIC_LOG_LEVEL`` > ``[logging] level`` in
    ``config.toml`` > :data:`logging.INFO`.

    Empty or whitespace-only values of ``FABRIC_LOG_LEVEL`` are treated as
    absent and fall through to the config/default layer.  Unrecognised
    (non-empty) values emit a :func:`logging.warning` to *stderr* via the root
    logger and also fall through rather than silently producing :data:`logging.INFO`.
    """
    from fabric_dw.config import VALID_LOG_LEVELS  # noqa: PLC0415

    env_raw = os.environ.get("FABRIC_LOG_LEVEL", "").strip()
    if env_raw:
        env_upper = env_raw.upper()
        if env_upper in VALID_LOG_LEVELS:
            return getattr(logging, env_upper)
        # Non-empty but unrecognised — warn to stderr and fall through to
        # config/default.  We write to stderr directly because setup_logging()
        # has not yet run so no handlers are attached to the named logger.
        print(  # noqa: T201
            f"WARNING: FABRIC_LOG_LEVEL={env_raw!r} is not a recognised log level "
            f"(valid: {', '.join(sorted(VALID_LOG_LEVELS))}); "
            "ignoring and falling through to config/default.",
            file=sys.stderr,
        )
    cfg_level = load_config().logging.level
    if cfg_level is not None:
        return getattr(logging, cfg_level.upper(), logging.INFO)
    return logging.INFO


def run(argv: Sequence[str] | None = None) -> None:
    """Parse CLI arguments and start the FastMCP server.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when ``None``).

    Transport options
    -----------------
    ``--transport stdio`` (default)
        Communicate over stdin/stdout — standard for Claude Desktop and similar.
    ``--transport http``
        Expose a streamable-HTTP endpoint.

    HTTP-transport options
    ----------------------
    ``--host HOST``
        Bind address for HTTP transport (default ``127.0.0.1``).  Binding to
        a non-loopback address requires ``FABRIC_MCP_ALLOW_REMOTE=1``.
    ``--port PORT``
        TCP port for HTTP transport (default ``8000``).
    """
    setup_logging(_resolve_log_level())

    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(
        prog="fabric-dw-mcp",
        description="Microsoft Fabric Data Warehouse MCP server",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport to use: 'stdio' (default) or 'http' (streamable-HTTP).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for HTTP transport (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="TCP port for HTTP transport (default: 8000).",
    )
    args = parser.parse_args(argv)

    transport: Literal["stdio", "streamable-http"] = (
        "streamable-http" if args.transport == "http" else "stdio"
    )

    if transport == "streamable-http":
        if args.host not in _LOOPBACK_HOSTS:
            if not _guards_env_flag("FABRIC_MCP_ALLOW_REMOTE"):
                logger.error(
                    "refusing to bind HTTP transport on %s:%s — this would expose the server "
                    "network-wide without authentication or TLS. "
                    "Set FABRIC_MCP_ALLOW_REMOTE=1 to override (ensure a reverse proxy provides "
                    "authentication and TLS termination).",
                    args.host,
                    args.port,
                )
                sys.exit(1)
            logger.warning(
                "WARNING: HTTP transport is bound on %s:%s (non-loopback). "
                "The MCP protocol has NO built-in authentication or TLS. "
                "Ensure an authenticating reverse proxy fronts this endpoint before "
                "exposing it to untrusted networks.",
                args.host,
                args.port,
            )

        # Update FastMCP settings before run so uvicorn picks them up.
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    # A2: print first-run notice to stderr before stdio transport starts so
    # the notice never pollutes the MCP stdio protocol stream.
    maybe_print_first_run_notice()
    record_app_started("mcp")
    record_mcp_server_started()

    start_ms = now_ms()
    exc_seen: BaseException | None = None
    try:
        mcp.run(transport=transport)
    except BaseException as exc:
        exc_seen = exc
        raise
    finally:
        duration_ms = now_ms() - start_ms
        # Map exit status: graceful stops (KeyboardInterrupt, SIGTERM-driven
        # SystemExit with code 0 or None, or normal return) → "ok".
        # Unexpected exceptions → "api_error".
        # "user_error" is not applicable for the MCP server surface.
        if exc_seen is None or isinstance(exc_seen, KeyboardInterrupt):
            exit_status = "ok"
        elif isinstance(exc_seen, SystemExit):
            code = getattr(exc_seen, "code", None)
            exit_status = "ok" if (code is None or code == 0) else "api_error"
        else:
            exit_status = "api_error"

        # Emit the session-end lifecycle event then flush/shut down the provider.
        # Telemetry teardown is fail-safe: errors here must NEVER mask the real
        # server exit exception (exc_seen, re-raised by the except block above).
        # shutdown_telemetry() must run even if record_app_exited() raises, so it
        # is guarded by its own try/finally.  The outer except swallows any
        # exception from the telemetry teardown path.
        try:
            try:
                record_app_exited(
                    duration_ms=duration_ms,
                    exit_status=exit_status,
                    error_category=None,
                )
            finally:
                shutdown_telemetry()
        except BaseException:  # noqa: S110
            pass  # telemetry teardown errors must never propagate
