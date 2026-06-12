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
All 67 tool functions call :func:`~fabric_dw.mcp._context.get_context` to
obtain the shared :class:`~fabric_dw.mcp._context.ServerContext`.  The sentinel
pattern (module-level ``ServerContext | None``) was chosen over injecting a
``Context`` parameter into every tool because FastMCP's lifespan context is
not ergonomically accessible from tool functions without either adding a
``Context`` import to all 67 tools or using fragile ``request_context``
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
    delete_sql_pool, reset_sql_pools).  Defaults to **disabled**.

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

from mcp.server.fastmcp import FastMCP

from fabric_dw.logging import setup_logging
from fabric_dw.mcp._context import fabric_lifespan
from fabric_dw.mcp._guards import _env_flag as _guards_env_flag
from fabric_dw.mcp.tools import register_all

__all__ = ["mcp", "run"]

# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

mcp: FastMCP = FastMCP("fabric-dw", lifespan=fabric_lifespan)

# ---------------------------------------------------------------------------
# Register all domain tools
# ---------------------------------------------------------------------------

register_all(mcp)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


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
    # Configure structured logging from env var (default INFO)
    raw_level = os.environ.get("FABRIC_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, raw_level, logging.INFO)
    setup_logging(log_level)

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

    mcp.run(transport=transport)
