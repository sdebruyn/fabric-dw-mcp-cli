"""MCP contract tests — exercising the MCP protocol IN-MEMORY.

Architecture note
-----------------
``mcp.shared.memory.create_connected_server_and_client_session`` wires a
real FastMCP server to a real :class:`~mcp.ClientSession` via in-memory
anyio streams, exercising the full MCP JSON-RPC handshake without any TCP
sockets.  This gives us a contract-level check that:

1. ``list_tools`` returns the expected tool names.
2. A read-only tool (``list_workspaces``) round-trips through the protocol
   and returns structured content.
3. A destructive-guarded tool (``delete_restore_point``) raises an error
   when ``FABRIC_MCP_ALLOW_DESTRUCTIVE`` is unset.

Unlike ``test_server.py`` (which calls ``_tool_manager.call_tool`` directly),
these tests go through the MCP serialisation layer (JSON-RPC encoding/decoding,
``CallToolResult`` wrapping, etc.) so they would catch regressions in tool
registration, schema export, and result serialisation.

Testing strategy
----------------
The ``fabric_lifespan`` sets ``_SERVER_CTX`` from ``build_context()`` at
server startup.  We patch ``fabric_dw.mcp._context.build_context`` to return
a pre-built mocked :class:`ServerContext` so the lifespan never attempts Azure
credential discovery or HTTP connections.  Because the mock HTTP client is also
entered as an async context manager inside the lifespan, we configure the mock
to behave as one.

The ``_SERVER_CTX`` module-level sentinel is then set by the lifespan itself
(which is the real code path), and ``get_context()`` in each tool function
returns the mock context without any extra patching.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fabric_dw.models import Workspace
from tests.unit._tool_introspection import SNAKE_CASE_RE
from tests.unit.mcp.conftest import WS_ID, WS_NAME, make_item_entry

# ---------------------------------------------------------------------------
# Minimum tool count — guards against catastrophic registration drops.
# Set just below the current count (~96) so adding tools never requires a
# bump, while a whole-domain disappearance (≥6 tools) is still caught.
# ---------------------------------------------------------------------------

MIN_TOOL_COUNT = 90


# ---------------------------------------------------------------------------
# Fixture: a mocked ServerContext whose http is a proper async context manager
# ---------------------------------------------------------------------------


@pytest.fixture
def contract_ctx():
    """ServerContext with fully mocked internals suitable for lifespan injection.

    The ``http`` mock is configured as an async context manager so the
    lifespan's ``async with ctx.http:`` block works without error.
    """
    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415

    mock_http = AsyncMock()
    # Make http behave as an async context manager (lifespan uses `async with ctx.http`).
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    mock_cache = MagicMock()
    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_resolver.item = AsyncMock(return_value=make_item_entry())
    mock_resolver.clear_negative_cache = MagicMock()

    return ServerContext(
        http=mock_http,
        cache=mock_cache,
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )


# ---------------------------------------------------------------------------
# Fixture: live tool list via the full MCP protocol round-trip
# ---------------------------------------------------------------------------


@pytest.fixture
async def live_tools(contract_ctx):
    """Return the list of Tool objects enumerated via the MCP protocol.

    Wires a real FastMCP server to a real ClientSession through in-memory
    streams (no TCP), so this exercises the same JSON-RPC ``tools/list``
    handshake the production server uses.
    """
    from mcp.shared.memory import create_connected_server_and_client_session  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with patch("fabric_dw.mcp._context.build_context", return_value=contract_ctx):
        async with create_connected_server_and_client_session(mcp) as client:
            result = await client.list_tools()

    return result.tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspace() -> Workspace:
    return Workspace.model_validate(
        {
            "id": str(WS_ID),
            "displayName": WS_NAME,
            "description": "Contract-test workspace",
            "capacityId": None,
        }
    )


# ---------------------------------------------------------------------------
# 1. list_tools — full tool roster via MCP protocol
# ---------------------------------------------------------------------------


async def test_list_tools_minimum_count(live_tools) -> None:
    """list_tools() via the MCP protocol returns at least MIN_TOOL_COUNT tools.

    The minimum threshold catches catastrophic registration regressions while
    allowing tools to be added without bumping a hardcoded number.
    """
    tool_names = {t.name for t in live_tools}
    assert len(tool_names) >= MIN_TOOL_COUNT, (
        f"Expected at least {MIN_TOOL_COUNT} tools via MCP protocol; got {len(tool_names)}. "
        "Registration may have silently dropped tools."
    )


async def test_list_tools_no_duplicates(live_tools) -> None:
    """list_tools() must return no duplicate tool names."""
    all_names = [t.name for t in live_tools]
    unique_names = set(all_names)
    assert len(all_names) == len(unique_names), (
        f"Duplicate tool names detected: "
        f"{sorted(n for n in unique_names if all_names.count(n) > 1)}"
    )


async def test_list_tools_naming_convention(live_tools) -> None:
    """Every tool name must follow the snake_case naming convention."""
    bad = [t.name for t in live_tools if not SNAKE_CASE_RE.match(t.name)]
    assert not bad, f"Tool names violating snake_case convention: {sorted(bad)}"


async def test_list_tools_non_empty_descriptions(live_tools) -> None:
    """Every tool must have a non-empty description string."""
    missing = [t.name for t in live_tools if not (t.description or "").strip()]
    assert not missing, f"Tools with missing or empty description: {sorted(missing)}"


async def test_list_tools_all_resolve_to_domain(live_tools) -> None:
    """Every tool registered via MCP must resolve to a known telemetry domain.

    This replicates the invariant enforced by test_telemetry_commands, but
    exercises it through the full MCP protocol round-trip so contract tests
    are self-contained.
    """
    from fabric_dw.telemetry_commands import resolve_domain  # noqa: PLC0415

    unknown = [t.name for t in live_tools if resolve_domain(t.name) == "unknown"]
    assert not unknown, (
        f"MCP tools with no DOMAIN_MAP entry (would log domain='unknown'): {sorted(unknown)}. "
        "Add each missing name to DOMAIN_MAP in fabric_dw.telemetry_commands."
    )


async def test_list_tools_contains_read_tool(live_tools) -> None:
    """The tool roster must include the 'list_workspaces' read tool."""
    tool_names = {t.name for t in live_tools}
    assert "list_workspaces" in tool_names


async def test_list_tools_contains_destructive_tool(live_tools) -> None:
    """The tool roster must include the guarded 'delete_restore_point' tool."""
    tool_names = {t.name for t in live_tools}
    assert "delete_restore_point" in tool_names


# ---------------------------------------------------------------------------
# 2. call_tool: list_workspaces — read tool round-trips through MCP protocol
# ---------------------------------------------------------------------------


async def test_call_tool_list_workspaces_round_trips(contract_ctx) -> None:
    """Calling list_workspaces via MCP protocol returns serialised workspace data.

    Verifies:
    - JSON-RPC ``tools/call`` request is processed without error.
    - The result content contains the expected workspace data.
    - The protocol wraps the return value as TextContent (JSON string).
    """
    from mcp.shared.memory import create_connected_server_and_client_session  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ws = _make_workspace()

    with (
        patch("fabric_dw.mcp._context.build_context", return_value=contract_ctx),
        patch("fabric_dw.services.workspaces.list_all", new=AsyncMock(return_value=[ws])),
    ):
        async with create_connected_server_and_client_session(mcp) as client:
            result = await client.call_tool("list_workspaces", {})

    assert result is not None
    assert not result.isError
    # The MCP protocol wraps results in ContentBlock items.
    assert len(result.content) >= 1
    # Extract text from the first content block.
    first = result.content[0]
    # TextContent has a .text attribute; parse it as JSON.
    text = getattr(first, "text", None)
    assert text is not None, f"Expected text content, got: {first!r}"
    parsed = json.loads(text)
    # The tool returns a list of workspace dicts.
    if isinstance(parsed, list):
        assert len(parsed) >= 1
        workspace_data = parsed[0]
    else:
        # Some FastMCP versions embed the list inside a wrapper
        workspace_data = parsed
    assert str(WS_ID) in json.dumps(workspace_data)


async def test_call_tool_list_workspaces_empty_returns_list(contract_ctx) -> None:
    """list_workspaces with no workspaces returns a successful result."""
    from mcp.shared.memory import create_connected_server_and_client_session  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        patch("fabric_dw.mcp._context.build_context", return_value=contract_ctx),
        patch("fabric_dw.services.workspaces.list_all", new=AsyncMock(return_value=[])),
    ):
        async with create_connected_server_and_client_session(mcp) as client:
            result = await client.call_tool("list_workspaces", {})

    assert result is not None
    assert not result.isError


# ---------------------------------------------------------------------------
# 3. Destructive guard: delete_restore_point blocked without env flag
# ---------------------------------------------------------------------------


async def test_destructive_tool_blocked_without_env_flag(
    contract_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delete_restore_point raises a ToolError when FABRIC_MCP_ALLOW_DESTRUCTIVE is unset.

    The MCP protocol returns this as ``isError=True`` on the CallToolResult.
    This validates that the guard logic survives the full protocol round-trip
    (the guard runs inside the tool function, which the protocol invokes).
    """
    from mcp.shared.memory import create_connected_server_and_client_session  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    # Ensure the destructive flag is NOT set.
    monkeypatch.delenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", raising=False)

    with patch("fabric_dw.mcp._context.build_context", return_value=contract_ctx):
        async with create_connected_server_and_client_session(
            mcp,
            raise_exceptions=False,  # Return errors as isError=True instead of raising
        ) as client:
            result = await client.call_tool(
                "delete_restore_point",
                {
                    "workspace": "my-workspace",
                    "warehouse": "my-warehouse",
                    "restore_point_id": "1726617378000",
                },
            )

    # The protocol must reflect the ToolError as an error result.
    assert result.isError, (
        f"Expected isError=True for destructive tool without flag; got: {result!r}"
    )


async def test_destructive_tool_allowed_with_env_flag(
    contract_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delete_restore_point proceeds when FABRIC_MCP_ALLOW_DESTRUCTIVE=1.

    The service layer is mocked so no real HTTP occurs.
    """
    from mcp.shared.memory import create_connected_server_and_client_session  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", "1")

    _rp_id = "1726617378000"

    with (
        patch("fabric_dw.mcp._context.build_context", return_value=contract_ctx),
        patch(
            "fabric_dw.services.restore.delete_point",
            new=AsyncMock(return_value=None),
        ),
    ):
        async with create_connected_server_and_client_session(
            mcp, raise_exceptions=False
        ) as client:
            result = await client.call_tool(
                "delete_restore_point",
                {
                    "workspace": "my-workspace",
                    "warehouse": "my-warehouse",
                    "restore_point_id": _rp_id,
                },
            )

    assert not result.isError, f"Expected success; got error: {result!r}"


