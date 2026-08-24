"""Drive a real MCP server over in-memory streams with hand-written JSON-RPC.

``tests/unit/_call.py`` reaches past the protocol and calls a tool function
directly, which is the right trade for the several hundred tests that assert on
a tool's return value.  This helper is the opposite trade, for the handful of
tests that are *about* the protocol layer: the middleware chain, the SDK's
OpenTelemetry spans, the handshake.  None of that runs on the ``_call.py`` path.

Messages are built by the caller as raw ``JSONRPCRequest`` /
``JSONRPCNotification`` objects rather than through ``ClientSession``, because
the interesting cases are the ones a well-behaved client cannot produce: a
method the protocol does not define, a string request id, an ``initialize``
without ``clientInfo``.
"""

from __future__ import annotations

from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp_types import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    PROTOCOL_VERSION_META_KEY,
    JSONRPCNotification,
    JSONRPCRequest,
)

__all__ = ["exchange", "handshake", "modern_request"]

#: Protocol revision used by the handshake-era helpers below.
HANDSHAKE_VERSION = "2025-06-18"

#: Protocol revision used by the envelope-era helper below.
MODERN_VERSION = "2026-07-28"


async def exchange(server: MCPServer[Any], messages: list[Any]) -> list[dict[str, Any]]:
    """Send *messages* to *server* over memory streams and return the responses.

    One response per request, in order; notifications produce none.  The server
    task is cancelled once the last response is in, so the caller does not have
    to arrange a shutdown.

    Args:
        server: The server under test, already carrying its tools and middleware.
        messages: Raw JSON-RPC requests and notifications, in send order.

    Returns:
        The decoded response envelopes, one per request in *messages*.
    """
    responses: list[dict[str, Any]] = []

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams

        async with anyio.create_task_group() as tg:

            async def _run() -> None:
                await server._lowlevel_server.run(
                    server_read,
                    server_write,
                    server._lowlevel_server.create_initialization_options(),
                    raise_exceptions=False,
                )

            tg.start_soon(_run)

            for message in messages:
                await client_write.send(SessionMessage(message))
                if isinstance(message, JSONRPCNotification):
                    continue
                out = await client_read.receive()
                # The stream is typed to carry an exception instead of a reply
                # when the transport tears down; surface it rather than hiding
                # it behind an attribute error two lines later.
                assert isinstance(out, SessionMessage), (
                    f"transport raised instead of replying: {out!r}"
                )
                raw = out.message
                responses.append(raw.model_dump(by_alias=True, mode="json", exclude_none=True))

            tg.cancel_scope.cancel()

    return responses


def handshake(client_name: str | None = "test-client") -> list[Any]:
    """Return the handshake-era opening messages: ``initialize`` plus the notification.

    Args:
        client_name: The name to put in ``clientInfo``.  ``None`` omits
            ``clientInfo`` entirely, which the protocol permits.
    """
    params: dict[str, Any] = {"protocolVersion": HANDSHAKE_VERSION, "capabilities": {}}
    if client_name is not None:
        params["clientInfo"] = {"name": client_name, "version": "1.0"}
    return [
        JSONRPCRequest(jsonrpc="2.0", id=1, method="initialize", params=params),
        JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized", params={}),
    ]


def modern_request(
    request_id: int,
    method: str,
    client_name: str | None = "test-client",
    params: dict[str, Any] | None = None,
) -> JSONRPCRequest:
    """Return a 2026-07-28 request carrying the per-request ``_meta`` envelope.

    That era has no handshake: the reserved ``_meta`` keys are what open the
    connection and carry the client's identity, so the server builds its
    ``Connection`` from them before the first message reaches middleware.

    Args:
        request_id: The JSON-RPC id.
        method: The method to call.
        client_name: The name to put in the client-info envelope key, or
            ``None`` to leave that key out (capabilities alone are valid).
        params: Extra params merged alongside ``_meta``.
    """
    meta: dict[str, Any] = {
        PROTOCOL_VERSION_META_KEY: MODERN_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
    }
    if client_name is not None:
        meta[CLIENT_INFO_META_KEY] = {"name": client_name, "version": "1.0"}
    return JSONRPCRequest(
        jsonrpc="2.0",
        id=request_id,
        method=method,
        params={**(params or {}), "_meta": meta},
    )
