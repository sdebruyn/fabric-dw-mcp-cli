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
the loopback bind keeps the SDK's automatic protection untouched.  Passing
``--allowed-host`` **replaces** that automatic allowlist rather than adding to
it, on every bind address including loopback.
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys
from collections.abc import Sequence
from typing import Literal, NoReturn

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


# Default ports a browser omits from an Origin header, per scheme.  An origin
# written with one of these is normalised down to the port-less form the
# browser actually sends, so `--allowed-origin https://app.example.com:443`
# matches instead of silently matching nothing.
_DEFAULT_PORTS: dict[str, str] = {"http": "80", "https": "443", "ws": "80", "wss": "443"}

_MAX_PORT = 65535


def _reject(reason: str) -> NoReturn:
    """Raise the error argparse reports as a CLI usage failure.

    argparse already prefixes its output with ``argument --allowed-host:``, so
    these messages never repeat the option name; they describe the value.
    """
    raise argparse.ArgumentTypeError(reason)


def _normalise_ipv6(text: str, original: str) -> str:
    """Return the canonical compressed form of an IPv6 literal, or reject it."""
    try:
        return str(ipaddress.IPv6Address(text))
    except ValueError:
        _reject(f"{original!r} is not a valid IPv6 address")


def _split_authority(value: str) -> tuple[str, str | None]:
    """Split ``host[:port]`` into a normalised host and its port.

    Normalisation exists because the SDK compares allowlist entries to header
    values with ``==``.  Every deviation therefore matches nothing and fails
    closed, which is safe but produces an opaque HTTP 421 and a startup log
    that reads like success.  Case, a trailing root dot and a zero-padded port
    are all differences a client never sends, so they are folded away here
    rather than left to surprise the operator.

    Anything that could not match a header no matter how it is normalised is
    rejected instead, because accepting it would build exactly that misleading
    allowlist.
    """
    # `*` and `*.example.com` are the obvious guesses for "any host", and the
    # SDK supports neither: they would produce a server that refuses every
    # request, the opposite of what the operator wrote.  Only the trailing
    # port wildcard the SDK does understand survives.
    if "*" in value and (not value.endswith(":*") or value.count("*") > 1):
        _reject(f"{value!r} contains a wildcard; only a trailing ':*' port wildcard is supported")

    port: str | None = None
    if value.startswith("["):
        # Bracketed IPv6 literal.  Anything after the closing bracket must be
        # a port, since an Origin or Host header allows nothing else there.
        inner, sep, rest = value[1:].partition("]")
        if not sep:
            _reject(f"{value!r} opens a bracketed IPv6 literal that is never closed")
        if rest and not rest.startswith(":"):
            _reject(f"{value!r} has trailing text after the IPv6 literal")
        port = rest[1:] if rest else None
        host = f"[{_normalise_ipv6(inner, value)}]"
    elif value.count(":") > 1:
        # A bare IPv6 literal.  It cannot carry a port without brackets, so the
        # whole value is the address.
        host = f"[{_normalise_ipv6(value, value)}]"
    else:
        host, sep, port_text = value.partition(":")
        port = port_text if sep else None
        # DNS names are case-insensitive and clients send them lowercased, and
        # a trailing root dot is legal in a Host header but never sent.
        host = host.lower().rstrip(".")
        if not host:
            _reject(f"{value!r} has no host part")

    if port is not None and port != "*":
        if not port.isdigit():
            _reject(f"{value!r} has a non-numeric port")
        if not 1 <= int(port) <= _MAX_PORT:
            _reject(f"{value!r} has a port outside 1-{_MAX_PORT}")
        port = str(int(port))  # fold 0-padding, which no client sends
    return host, port


def _normalise_allowed_host(value: str) -> str:
    """Validate and normalise one ``--allowed-host`` value.  argparse ``type=``."""
    raw = value.strip()
    if not raw:
        # The realistic source of this is `--allowed-host "$MCP_PUBLIC_HOST"`
        # in a unit file with the variable unset.  Left alone it starts a
        # server that refuses every request while logging what reads like a
        # successful allowlist.
        _reject("value may not be empty")
    if "://" in raw:
        _reject(f"{raw!r} is a URL; write the host on its own, e.g. 'mcp.example.com'")
    if "/" in raw:
        _reject(f"{raw!r} has a path; a Host header never carries one")
    host, port = _split_authority(raw)
    return host if port is None else f"{host}:{port}"


def _normalise_allowed_origin(value: str) -> str:
    """Validate and normalise one ``--allowed-origin`` value.  argparse ``type=``."""
    raw = value.strip()
    if not raw:
        _reject("value may not be empty")
    scheme, sep, authority = raw.partition("://")
    if not sep or not scheme:
        _reject(f"{raw!r} has no scheme; write e.g. 'https://client.example.com'")
    scheme = scheme.lower()
    # A copy-pasted origin often keeps the browser's trailing slash; an Origin
    # header never carries one, nor any other path.
    authority = authority.rstrip("/")
    if "/" in authority:
        _reject(f"{raw!r} has a path; an Origin header never carries one")
    if not authority:
        _reject(f"{raw!r} has no host")
    host, port = _split_authority(authority)
    if port == "*":
        # Deliberately not supported.  A web origin is scheme plus host plus
        # port, so another port on the same host is a different security
        # principal: a dev server or a second app there would become an origin
        # allowed to drive every tool.  Naming each port is the whole point.
        _reject(
            "a ':*' port wildcard is not supported: a different port is a different web "
            "origin. Repeat --allowed-origin once per port instead."
        )
    if port is not None and _DEFAULT_PORTS.get(scheme) == port:
        port = None  # browsers omit the default port for the scheme
    return f"{scheme}://{host}" if port is None else f"{scheme}://{host}:{port}"


def _expand_allowed_host(value: str) -> list[str]:
    """Return the ``allowed_hosts`` patterns one normalised host stands for.

    The SDK matches a pattern against the raw ``Host`` header: either exactly,
    or, for a pattern ending in ``:*``, against any ``<base>:<port>`` header.
    Those two forms do **not** overlap, so ``mcp.example.com:*`` rejects a
    port-less ``Host: mcp.example.com`` (what a reverse proxy on 80 or 443
    sends) and a bare ``mcp.example.com`` rejects ``Host: mcp.example.com:8000``
    (what a direct client sends).  A value without an explicit port therefore
    expands to both forms so the operator does not have to know which one their
    clients will produce.

    This widening is safe for a ``Host`` header, which only ever names the
    server the client meant to reach, and it is deliberately **not** applied to
    origins: see :func:`_normalise_allowed_origin`.

    A value that carries a port or an explicit ``:*`` is already exactly what
    the operator asked for and is returned unchanged.
    """
    if value.endswith(":*"):
        return [value]
    # A bracketed IPv6 literal contains colons of its own, so the port has to
    # be looked for after the closing bracket.
    tail = value.rpartition("]")[2] if value.startswith("[") else value
    if ":" in tail:
        return [value]
    return [value, f"{value}:*"]


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
    # Origins are used exactly as given: no port widening, see
    # _normalise_allowed_origin.
    allowed_origins = _dedupe(args.allowed_origin)
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
        type=_normalise_allowed_host,
        help=(
            "Host header value clients use to reach this server, e.g. "
            "mcp.example.com or 192.0.2.1:8000. Repeatable, requires "
            "--transport http. Enables Host-header validation (DNS-rebinding "
            "protection) with this allowlist, replacing any the SDK would apply "
            "on its own. Written without a port it accepts the host on any port. "
            "Without this option a non-loopback bind serves every request "
            "unvalidated."
        ),
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        type=_normalise_allowed_origin,
        help=(
            "Origin header value to accept, e.g. https://client.example.com. "
            "Repeatable, requires --allowed-host. Matched exactly, since a "
            "different port is a different web origin; name each one. Needed "
            "only for browser-based clients, which includes Electron and webview "
            "hosts: with --allowed-host alone, every request carrying an Origin "
            "header is refused."
        ),
    )
    args = parser.parse_args(argv)

    if args.transport != "http" and (args.allowed_host or args.allowed_origin):
        # Neither option can be honoured without an HTTP server to apply them
        # to, and silently ignoring them would leave an operator who forgot
        # `--transport http` with no diagnostics at all.  Nobody can be relying
        # on the current silence: both options are new.
        parser.error("--allowed-host and --allowed-origin require --transport http")

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
        Repeatable, requires ``--transport http``.  Turns Host-header
        validation (DNS-rebinding protection) on with this allowlist,
        **replacing** whatever the SDK would have applied on its own.  Omitted,
        the SDK's own behaviour applies unchanged: automatic loopback-only
        protection on a loopback bind, none at all on a non-loopback bind (a
        WARNING is logged in that case).
    ``--allowed-origin ORIGIN``
        Repeatable, requires ``--allowed-host``.  Adds to the Origin-header
        allowlist, matched exactly.  With ``--allowed-host`` but no
        ``--allowed-origin`` every request that carries an Origin header is
        refused, which is what most non-browser MCP clients want; pass this for
        a browser-based client, Electron renderer or webview host.

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
