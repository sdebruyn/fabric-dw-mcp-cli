"""Tests for fabric_dw.mcp._client_info — recording which MCP client connected (#1048).

Driven through a real server over in-memory streams rather than against a
hand-built context, because the value's location is the whole difficulty here:
it is in ``initialize``'s raw params in the handshake era and in the connection
built from each request's ``_meta`` envelope in the 2026-07-28 era, and an
earlier attempt at this feature looked in neither place and concluded the server
could not see it at all.
"""

from __future__ import annotations

import importlib
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from mcp.server.mcpserver import MCPServer
from mcp_types import JSONRPCRequest

from fabric_dw.mcp._client_info import ClientInfoMiddleware, client_name_from_context
from fabric_dw.telemetry import MCP_CLIENT_UNKNOWN
from tests.unit._mcp_session import exchange, handshake, modern_request

# Patch targets, as strings: the middleware resolves telemetry through
# ``sys.modules`` on every call, and tests/unit/test_telemetry.py replaces that
# entry with a freshly imported module, so a target bound at import time here
# would sometimes patch an object nothing uses.
_SCOPE = "fabric_dw.telemetry.mcp_client_scope"
_EMIT_EVENT = "fabric_dw.telemetry.emit_event"


def _live_telemetry() -> Any:
    """Return the telemetry module currently in ``sys.modules``."""
    return importlib.import_module("fabric_dw.telemetry")


@pytest.fixture
def server() -> MCPServer[Any]:
    """A server carrying the middleware and one tool, and nothing else."""
    mcp: MCPServer[Any] = MCPServer(
        "client-info-test", version="0.0.0", middleware=[ClientInfoMiddleware()]
    )

    @mcp.tool(name="echo")
    async def _echo(value: str) -> str:
        """Echo the value back."""
        return value

    return mcp


def _recorded(scope_mock: Any) -> list[object]:
    """The raw names handed to ``mcp_client_scope``, in order."""
    return [call.args[0] for call in scope_mock.call_args_list]


async def test_the_handshake_client_name_is_recorded(server: MCPServer[Any]) -> None:
    """The 2025-era ``initialize`` carries ``clientInfo`` in its params.

    Read from the raw params rather than from the connection, because the SDK
    commits the handshake to ``Connection.client_params`` only *after* the
    middleware chain returns, so a middleware asking the session during
    ``initialize`` sees nothing.
    """
    with patch(_SCOPE) as scope:
        await exchange(server, handshake("claude-ai"))

    assert _recorded(scope)[0] == "claude-ai"


async def test_the_client_is_still_known_after_the_handshake(server: MCPServer[Any]) -> None:
    """Every later message on the connection must carry the client too.

    ``command_invoked`` is emitted from inside a ``tools/call``, so a value that
    were only available on ``initialize`` would answer nothing.
    """
    with patch(_SCOPE) as scope:
        await exchange(
            server,
            [
                *handshake("claude-ai"),
                JSONRPCRequest(
                    jsonrpc="2.0",
                    id=2,
                    method="tools/call",
                    params={"name": "echo", "arguments": {"value": "hi"}},
                ),
            ],
        )

    assert _recorded(scope) == ["claude-ai", "claude-ai", "claude-ai"]


async def test_the_envelope_era_client_name_is_recorded(server: MCPServer[Any]) -> None:
    """2026-07-28 drops the handshake and puts client info in each request's ``_meta``.

    The SDK turns that envelope into ``Connection.client_params`` before the
    first message reaches middleware, so here the session is the accessor and
    there is no ``initialize`` to read.
    """
    with patch(_SCOPE) as scope:
        await exchange(server, [modern_request(1, "tools/list", client_name="vscode")])

    assert _recorded(scope) == ["vscode"]


async def test_a_client_that_identifies_itself_nowhere_is_still_served(
    server: MCPServer[Any],
) -> None:
    """Client info is optional from 2026-07-28 on, so this is a supported client.

    Not a case the handshake era can produce: there ``clientInfo`` is a required
    field and the SDK answers INVALID_PARAMS without it, so the only way to be
    anonymous is the envelope era, where capabilities are required and identity
    is not.
    """
    with patch(_SCOPE) as scope:
        responses = await exchange(server, [modern_request(1, "tools/list", client_name=None)])

    assert "result" in responses[0], "an anonymous client must still be served"
    assert _recorded(scope) == [None]


async def test_an_unknown_client_is_recorded_under_the_placeholder(
    server: MCPServer[Any],
) -> None:
    """End to end, the ``None`` above becomes the documented placeholder value."""
    _live_telemetry()._seen_mcp_clients.clear()
    with patch(_EMIT_EVENT) as emit:
        await exchange(server, [modern_request(1, "tools/list", client_name=None)])

    connected = [call for call in emit.call_args_list if call.args[0] == "mcp_client_connected"]
    assert connected, "no mcp_client_connected event was emitted"
    assert connected[0].args[1] == {"mcp_client": MCP_CLIENT_UNKNOWN}


async def test_a_broken_client_info_shape_does_not_break_the_request(
    server: MCPServer[Any],
) -> None:
    """Middleware runs before params validation, so the shape is not guaranteed.

    A client sending ``clientInfo: "claude"`` gets its request served; the field
    simply falls back to the placeholder.
    """
    request = JSONRPCRequest(
        jsonrpc="2.0",
        id=1,
        method="initialize",
        params={
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": "not-an-object",
        },
    )

    with patch(_SCOPE) as scope:
        responses = await exchange(server, [request])

    assert _recorded(scope) == [None]
    assert responses[0].get("error", {}).get("code") != -32603, "must not be an internal error"


async def test_telemetry_failing_never_breaks_the_server(server: MCPServer[Any]) -> None:
    """The tool call has to go through even when the telemetry side throws.

    A crash here would take down a working MCP server for the sake of a usage
    counter, which is never the right trade.
    """
    with patch(_SCOPE, side_effect=RuntimeError("boom")):
        responses = await exchange(
            server,
            [
                *handshake("claude-ai"),
                JSONRPCRequest(
                    jsonrpc="2.0",
                    id=2,
                    method="tools/call",
                    params={"name": "echo", "arguments": {"value": "hi"}},
                ),
            ],
        )

    assert "hi" in str(responses[-1])


def test_the_initialize_params_win_over_a_stale_session_value() -> None:
    """A connection reused by a different client must report the new one.

    ``initialize`` is the point where identity changes, and its params are the
    fresh value; the connection still holds whatever the previous handshake
    committed.
    """

    class _Info:
        name = "stale-client"

    class _Params:
        client_info = _Info()

    class _Session:
        client_params = _Params()

    class _Ctx:
        method = "initialize"
        params: ClassVar[dict[str, Any]] = {"clientInfo": {"name": "fresh-client"}}
        session = _Session()

    assert client_name_from_context(_Ctx()) == "fresh-client"  # ty: ignore[invalid-argument-type]


async def test_the_client_name_can_change_per_message_on_one_connection(
    server: MCPServer[Any],
) -> None:
    """From 2026-07-28 on, the name is per message, not per connection.

    It rides each request's ``_meta`` envelope rather than a handshake, so a
    client can send a different one every time, with no reconnect. That is what
    makes the name a channel rather than an identity, and why the recorded value
    is bounded per process (see tests/unit/test_telemetry.py).
    """
    with patch(_SCOPE) as scope:
        await exchange(
            server,
            [
                modern_request(index, "tools/list", client_name=f"EXFIL-CHUNK-{index:04d}")
                for index in range(3)
            ],
        )

    assert _recorded(scope) == ["EXFIL-CHUNK-0000", "EXFIL-CHUNK-0001", "EXFIL-CHUNK-0002"]


async def test_a_client_name_with_control_characters_is_recorded_clean(
    server: MCPServer[Any],
) -> None:
    """End to end, since the filtering happens a layer below the middleware."""
    tel = _live_telemetry()
    tel._seen_mcp_clients.clear()

    with patch(_EMIT_EVENT) as emit:
        await exchange(server, handshake("claude\n\r\x00-ai"))

    connected = [call for call in emit.call_args_list if call.args[0] == "mcp_client_connected"]
    assert connected
    assert connected[0].args[1] == {"mcp_client": "claude-ai"}
