"""MCP server exposing all fabric-dw service functions as MCP tools.

Architecture
------------
This module is a **thin** entry point.  All tool implementations live in
per-domain sub-modules under :mod:`fabric_dw.mcp.tools`.

Startup / Shutdown
------------------
A :func:`~fabric_dw.mcp._context.fabric_lifespan` async context manager is
passed to the :class:`~mcp.server.mcpserver.MCPServer` constructor.  On startup
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
``Context`` parameter into every tool because the SDK's lifespan context is
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

DNS-rebinding protection
------------------------
The SDK turns Host and Origin validation on automatically, but only when the
HTTP transport binds on a loopback address.  A non-loopback bind leaves
``transport_security`` unset and the middleware then fails open, serving every
request without validating either header.  ``--allowed-host`` (repeatable)
supplies the allowlist explicitly so protection can be switched on for exactly
the deployment that needs it; ``--allowed-origin`` (repeatable) does the same
for the Origin header.  Neither option changes anything when it is not passed:
the loopback bind keeps the SDK's automatic protection untouched.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from typing import Literal

from mcp.server.transport_security import TransportSecuritySettings

from fabric_dw import __version__
from fabric_dw.config import load_config
from fabric_dw.logging import setup_logging
from fabric_dw.mcp._context import fabric_lifespan
from fabric_dw.mcp._guards import env_flag as _guards_env_flag
from fabric_dw.mcp._helpers import InstrumentedMCPServer
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
# Server instructions: surfaced to clients in the initialize response.
# This text is permanently resident in every client's context, so it is a
# token budget, not a manual: a capability map plus one preference rule.
# Character budget: 900 chars (asserted in tests/unit/mcp/test_instructions.py).
# The original 700-char budget covered 18 named tools across 5 domains.
# The domain index added in issue #992 brings the total to 795 chars.
# Adding list_capabilities + server domain (#1018) brings the total to 822 chars.
# 900 provides ~78 chars of headroom for future minor additions without
# requiring another budget justification.
# ---------------------------------------------------------------------------

_SERVER_INSTRUCTIONS: str = (
    "Prefer dedicated tools over execute_sql.\n"
    "Dedicated tools return typed, structured results and have no SQL dialect pitfalls; "
    "execute_sql returns only the last result set of a batch and base64-encodes "
    "varbinary columns.\n"
    "Discover: list_capabilities, list_schemas, list_tables, list_views, list_functions, "
    "list_procedures, list_security_policies, list_masked_columns.\n"
    "Read: read_table, read_view, count_table_rows, count_view_rows.\n"
    "Inspect: get_table_columns, get_view_columns, get_table_health_metrics.\n"
    "Mutate: create_table, delete_table, rename_table, clear_table, delete_schema, "
    "transfer_table.\n"
    "Also: permissions, audit, queries, snapshots, "
    "restore points, sql pools, warehouses, workspaces, statistics, settings, "
    "sql endpoints, dbt, cache, server.\n"
    "Use execute_sql ONLY for arbitrary SQL that no dedicated tool can express."
)

# ---------------------------------------------------------------------------
# MCP server instance (instrumented subclass emits command_invoked events)
# ---------------------------------------------------------------------------

mcp: InstrumentedMCPServer = InstrumentedMCPServer(
    "fabric-dw",
    lifespan=fabric_lifespan,
    instructions=_SERVER_INSTRUCTIONS,
    # Reported to clients as `serverInfo.version` in the initialize response.
    # The SDK defaults it to the empty string, so without this the server has
    # no version at all on the wire.
    version=__version__,
)

# ---------------------------------------------------------------------------
# Register all domain tools
# ---------------------------------------------------------------------------

register_all(mcp)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _expand_allowed_host(value: str) -> list[str]:
    """Return the ``allowed_hosts`` patterns a single ``--allowed-host`` stands for.

    The SDK matches a pattern against the raw ``Host`` header: either exactly,
    or, for a pattern ending in ``:*``, against any ``<base>:<port>`` header.
    Those two forms do **not** overlap, so ``mcp.example.com:*`` rejects a
    port-less ``Host: mcp.example.com`` (what a reverse proxy on 80/443 sends)
    and a bare ``mcp.example.com`` rejects ``Host: mcp.example.com:8000`` (what
    a direct client sends).  A value without an explicit port therefore expands
    to both forms so the operator does not have to know which one their clients
    will produce.

    A value that already carries a port (``host:8000``) or an explicit port
    wildcard (``host:*``) is passed through untouched.  An IPv6 literal is
    normalised to the bracketed form the ``Host`` header uses, so both ``::1``
    and ``[::1]`` are accepted here.
    """
    value = value.strip()
    if value.startswith("["):
        # Bracketed IPv6 literal.  Anything after the closing bracket is a port
        # (or port wildcard) the caller wrote deliberately.
        base, _, rest = value.partition("]")
        base = f"{base}]"
        return [value] if rest else [base, f"{base}:*"]
    if value.count(":") > 1:
        # Bare IPv6 literal: the Host header always brackets it.
        base = f"[{value}]"
        return [base, f"{base}:*"]
    if ":" in value:
        return [value]
    return [value, f"{value}:*"]


def _expand_allowed_origin(value: str) -> list[str]:
    """Return the ``allowed_origins`` patterns a single ``--allowed-origin`` stands for.

    An Origin header is ``<scheme>://<host>[:<port>]`` and the SDK matches it
    with the same exact-or-``:*`` rule it uses for hosts, so the authority part
    gets the same treatment as :func:`_expand_allowed_host`.  A value with no
    ``://`` cannot match an Origin header at all and is passed through
    unchanged rather than guessed at.
    """
    value = value.strip()
    scheme, sep, authority = value.partition("://")
    if not sep:
        return [value]
    return [f"{scheme}{sep}{host}" for host in _expand_allowed_host(authority)]


def _dedupe(values: Sequence[str]) -> list[str]:
    """Return *values* without duplicates, preserving first-seen order."""
    return list(dict.fromkeys(values))


def _resolve_transport_security(
    args: argparse.Namespace, logger: logging.Logger
) -> TransportSecuritySettings | None:
    """Return the DNS-rebinding settings for an HTTP bind, or ``None``.

    ``None`` means "pass nothing to ``run()``", which is what happened before
    ``--allowed-host`` existed: the SDK then applies its own heuristic, which
    switches protection on for a loopback bind and leaves it off, fail-open,
    for anything else.  So the default invocation is unaffected on every host,
    and the loopback path in particular keeps the automatic allowlist it has
    always had.

    Without ``--allowed-host`` a non-loopback bind additionally gets a warning:
    that combination is the one that serves every request unvalidated.
    """
    if not args.allowed_host:
        if args.host not in _LOOPBACK_HOSTS:
            logger.warning(
                "WARNING: Host and Origin validation is OFF for the bind on %s:%s. "
                "The SDK enables it automatically for loopback binds only, so every "
                "request reaching this port is served without checking which name it "
                "was addressed to. A page in a browser on this network can then point "
                "its own domain at this address and drive every tool, execute_sql "
                "included, under this server's Fabric credentials. "
                "Pass --allowed-host with the host name clients use to reach this "
                "server (repeatable) to turn validation on.",
                args.host,
                args.port,
            )
        return None

    allowed_hosts = _dedupe([p for v in args.allowed_host for p in _expand_allowed_host(v)])
    allowed_origins = _dedupe([p for v in args.allowed_origin for p in _expand_allowed_origin(v)])
    logger.info(
        "Host and Origin validation enabled: allowed_hosts=%s allowed_origins=%s",
        allowed_hosts,
        allowed_origins or "[] (every request carrying an Origin header is refused)",
    )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


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


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Build the CLI parser, parse *argv*, and reject unusable combinations.

    Split out of :func:`run` so the option list can grow without pushing the
    entry point past its complexity budget.
    """
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
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOST",
        help=(
            "Host header value clients use to reach this server, e.g. "
            "mcp.example.com or 192.0.2.1:8000. Repeatable. Enables Host-header "
            "validation (DNS-rebinding protection) with this allowlist. Without "
            "it, a non-loopback bind serves every request unvalidated."
        ),
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help=(
            "Origin header value to accept, e.g. https://mcp.example.com. "
            "Repeatable, requires --allowed-host. Only needed for browser-based "
            "clients: with --allowed-host alone, every request carrying an "
            "Origin header is refused."
        ),
    )
    args = parser.parse_args(argv)

    if args.allowed_origin and not args.allowed_host:
        # allowed_origins is only consulted once DNS-rebinding protection is on,
        # and protection is only switched on by --allowed-host.  Worse, turning
        # it on with an empty allowed_hosts would reject every request, since no
        # Host header can match an empty allowlist.  Fail at parse time rather
        # than start a server that answers nothing.
        parser.error("--allowed-origin requires --allowed-host")

    return args


def run(argv: Sequence[str] | None = None) -> None:
    """Parse CLI arguments and start the MCP server.

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
    ``--allowed-host HOST``
        Repeatable.  Turns Host-header validation (DNS-rebinding protection) on
        with this allowlist.  Omitted, the SDK's own behaviour applies
        unchanged: automatic loopback-only protection on a loopback bind, none
        at all on a non-loopback bind (a WARNING is logged in that case).
    ``--allowed-origin ORIGIN``
        Repeatable, requires ``--allowed-host``.  Adds to the Origin-header
        allowlist.  With ``--allowed-host`` but no ``--allowed-origin`` every
        request that carries an Origin header is refused, which is what a
        non-browser MCP client wants; pass this only for a browser-based client.

    Host and port are passed straight through to ``MCPServer.run()``, which is
    overloaded per transport; the stdio overload takes no keyword arguments at
    all.  ``transport_security`` is passed only when ``--allowed-host`` is
    given, so the default call is byte-for-byte the one made before this option
    existed.

    The HTTP transport caps request bodies at 4 MiB and answers anything larger
    with HTTP 413.  The default is kept: for this server the only realistic way
    to reach it is a single ``execute_sql`` script or a procedure or view
    definition of several megabytes.  ``max_request_body_size`` on ``run()`` is
    the escape hatch if that ever becomes a real constraint.
    """
    setup_logging(_resolve_log_level())

    logger = logging.getLogger(__name__)

    args = _parse_args(argv)

    transport: Literal["stdio", "streamable-http"] = (
        "streamable-http" if args.transport == "http" else "stdio"
    )

    # Unchanged loopback guard.  It used to sit under a second `if transport ==
    # "streamable-http"` block that also assigned mcp.settings.host/port; with
    # that assignment gone the two conditions collapse into one `and`, which
    # short-circuits identically.
    if transport == "streamable-http" and args.host not in _LOOPBACK_HOSTS:
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

    transport_security = (
        _resolve_transport_security(args, logger) if transport == "streamable-http" else None
    )

    # A2: print first-run notice to stderr before stdio transport starts so
    # the notice never pollutes the MCP stdio protocol stream.
    maybe_print_first_run_notice()
    record_app_started("mcp")
    record_mcp_server_started()

    start_ms = now_ms()
    exc_seen: BaseException | None = None
    try:
        # Host and port are transport-specific run() kwargs.  There is no
        # settings object to mutate: MCPServer exposes no `.settings.host` /
        # `.settings.port` and assigning to them raises.  run() is overloaded
        # per transport and the stdio overload accepts no kwargs at all, hence
        # the two branches.
        # transport_security is passed only when it was actually built, so the
        # default HTTP call is identical to the one made before --allowed-host
        # existed rather than an explicit `transport_security=None`.
        if transport == "streamable-http" and transport_security is not None:
            mcp.run(
                transport="streamable-http",
                host=args.host,
                port=args.port,
                transport_security=transport_security,
            )
        elif transport == "streamable-http":
            mcp.run(transport="streamable-http", host=args.host, port=args.port)
        else:
            mcp.run(transport="stdio")
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
