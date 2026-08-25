"""Raw JSON-RPC regression tests for the production server's protocol handling.

``test_contract.py`` drives the server through :class:`mcp.client.Client`, which
is the right tool for everything a well-behaved client can produce.  This file
covers what it cannot: a method the protocol does not define, and the
2026-07-28 per-request ``_meta`` envelope built by hand rather than by the
client.  Both go through ``tests/unit/_mcp_session.py``, which speaks the wire
format directly.

What these pin down is that the SDK's own request pipeline -- handler lookup,
its built-in middleware chain, the error envelope -- serves this server's
messages unchanged.  That pipeline includes the SDK's OpenTelemetry middleware,
which sees only the API package's no-op provider here and must stay invisible
either way.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from mcp_types import JSONRPCRequest

from tests.unit._mcp_session import exchange, handshake, modern_request

# JSON-RPC's own code for "no such method", per the base spec the MCP protocol
# inherits.  Written out rather than imported so a change in the SDK's constant
# is a visible test failure and not a silently redefined expectation.
_METHOD_NOT_FOUND = -32601

_EXPECTED_TOOL_COUNT = 121


@pytest.fixture
def _mocked_context():
    """Patch ``build_context`` so the lifespan starts without touching the network."""
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    ctx = ServerContext(
        http=mock_http,
        cache=AsyncMock(),
        resolver=AsyncMock(),
        auth_mode=AsyncMock(),
    )
    with patch("fabric_dw.mcp._context.build_context", return_value=ctx):
        yield


def _server() -> Any:
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    return mcp


@pytest.mark.usefixtures("_mocked_context")
async def test_the_handshake_era_lists_every_tool() -> None:
    """``initialize`` followed by ``tools/list`` returns the full roster."""
    responses = await exchange(
        _server(),
        [
            *handshake("protocol-test"),
            JSONRPCRequest(jsonrpc="2.0", id=2, method="tools/list", params={}),
        ],
    )

    initialize, tools = responses
    assert initialize["result"]["serverInfo"]["name"] == "fabric-dw"
    assert len(tools["result"]["tools"]) == _EXPECTED_TOOL_COUNT


@pytest.mark.usefixtures("_mocked_context")
async def test_the_envelope_era_lists_every_tool() -> None:
    """The handshake-free 2026-07-28 path reaches the same handler."""
    responses = await exchange(_server(), [modern_request(1, "tools/list")])

    assert len(responses[0]["result"]["tools"]) == _EXPECTED_TOOL_COUNT


@pytest.mark.usefixtures("_mocked_context")
async def test_an_undefined_method_gets_the_protocol_error() -> None:
    """An unknown method is answered with METHOD_NOT_FOUND, not a crash.

    A middleware that raises on a message it cannot classify would turn this
    into a transport teardown or an internal error instead.
    """
    responses = await exchange(
        _server(),
        [
            *handshake("protocol-test"),
            JSONRPCRequest(jsonrpc="2.0", id=2, method="fabric/not-a-method", params={}),
        ],
    )

    assert responses[1]["error"]["code"] == _METHOD_NOT_FOUND
