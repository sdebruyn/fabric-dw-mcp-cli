"""MCP contract tests — exercising the MCP protocol IN-MEMORY.

Architecture note
-----------------
:class:`mcp.client.Client` connects to an in-process server without any TCP
sockets.  This gives us a contract-level check that:

1. ``list_tools`` returns the expected tool names.
2. A read-only tool (``list_workspaces``) round-trips through the protocol
   and returns structured content.
3. A destructive-guarded tool (``delete_restore_point``) raises an error
   when ``FABRIC_MCP_ALLOW_DESTRUCTIVE`` is unset.
4. The server identifies itself with the fabric-dw version in ``serverInfo``.

Unlike ``test_server.py`` (which goes through the shared ``call_tool()`` test
helper, itself a thin wrapper around ``_tool_manager.call_tool``), these tests
go through the MCP serialisation layer (JSON-RPC encoding/decoding,
``CallToolResult`` wrapping, etc.) so they would catch regressions in tool
registration, schema export, and result serialisation.

``mode`` is pinned, never defaulted
-----------------------------------
``Client`` defaults to ``mode="auto"``, and for an in-process server that
dispatches through a ``DirectDispatcher``: no JSON-RPC framing, no initialize
handshake, Python objects handed straight across.  Under that mode every
assertion in this file would still pass while the serialisation layer this file
exists to cover went completely untested.

So the mode is always explicit here, and both are exercised via the
``client_mode`` fixture:

``legacy``
    Forces the initialize handshake and full JSON-RPC framing.  This is the
    path a stdio or streamable-HTTP client actually takes, and the one that
    makes these serialisation tests worth running.
``auto``
    The modern per-request path.  Skips framing but still runs handler lookup,
    parameter validation, middleware, and v2's new result-schema validation.

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
# Set well below the current count (121 at time of writing) so adding tools
# never requires a bump, while a whole-domain disappearance is still caught.
# The exact number is deliberately not asserted; only a floor is.
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
    return ServerContext(
        http=mock_http,
        cache=mock_cache,
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )


# ---------------------------------------------------------------------------
# Fixture: the connect mode, always explicit.  See the module docstring for why
# the `Client` default of "auto" must never be relied on here.
# ---------------------------------------------------------------------------


@pytest.fixture(params=["legacy", "auto"])
def client_mode(request) -> str:
    """The ``Client(mode=...)`` value under test.

    ``legacy`` is the load-bearing one: it is the only mode that puts JSON-RPC
    framing and the initialize handshake in the path, which is what this file
    is here to cover.  ``auto`` is included so the modern per-request path is
    covered too.
    """
    return request.param


# ---------------------------------------------------------------------------
# Fixture: live tool list via the full MCP protocol round-trip
# ---------------------------------------------------------------------------


@pytest.fixture
async def live_tools(contract_ctx, client_mode):
    """Return the list of Tool objects enumerated via the MCP protocol.

    Connects a real :class:`mcp.client.Client` to the production server
    in-process (no TCP), so this exercises the same ``tools/list`` round-trip
    the production server serves.
    """
    from mcp.client import Client  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with patch("fabric_dw.mcp._context.build_context", return_value=contract_ctx):
        async with Client(mcp, mode=client_mode) as client:
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
    """Every registered tool must have a domain, or list_capabilities hides it.

    A tool with no entry lands in an ``unknown`` bucket, which is how a new
    tool silently fails to show up where a client looks for it.
    """
    from fabric_dw.mcp._domains import domain_for_tool  # noqa: PLC0415

    unknown = [t.name for t in live_tools if domain_for_tool(t.name) == "unknown"]
    assert not unknown, (
        f"MCP tools with no domain (list_capabilities would bucket them as "
        f"'unknown'): {sorted(unknown)}. Add each name to TOOL_DOMAINS in "
        "fabric_dw.mcp._domains."
    )


async def test_domain_table_has_no_stale_entries(live_tools) -> None:
    """Every name in the domain table must still be a registered tool.

    The inverse of the check above.  Without it a renamed or removed tool
    leaves its old name behind and the table slowly fills with entries that
    classify nothing.
    """
    from fabric_dw.mcp._domains import TOOL_DOMAINS  # noqa: PLC0415

    live_names = {t.name for t in live_tools}
    stale = sorted(name for name in TOOL_DOMAINS if name not in live_names)
    assert not stale, (
        f"TOOL_DOMAINS names tools that are not registered: {stale}. "
        "Remove them from fabric_dw.mcp._domains."
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


async def test_call_tool_list_workspaces_round_trips(contract_ctx, client_mode) -> None:
    """Calling list_workspaces via MCP protocol returns serialised workspace data.

    Verifies:
    - JSON-RPC ``tools/call`` request is processed without error.
    - The result content contains the expected workspace data.
    - The protocol wraps the return value as TextContent (JSON string).
    """
    from mcp.client import Client  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ws = _make_workspace()

    with (
        patch("fabric_dw.mcp._context.build_context", return_value=contract_ctx),
        patch("fabric_dw.services.workspaces.list_all", new=AsyncMock(return_value=[ws])),
    ):
        async with Client(mcp, mode=client_mode) as client:
            result = await client.call_tool("list_workspaces", {})

    assert result is not None
    assert not result.is_error
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
        # Some SDK versions embed the list inside a wrapper
        workspace_data = parsed
    assert str(WS_ID) in json.dumps(workspace_data)


async def test_call_tool_list_workspaces_empty_returns_list(contract_ctx, client_mode) -> None:
    """list_workspaces with no workspaces returns a successful result."""
    from mcp.client import Client  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        patch("fabric_dw.mcp._context.build_context", return_value=contract_ctx),
        patch("fabric_dw.services.workspaces.list_all", new=AsyncMock(return_value=[])),
    ):
        async with Client(mcp, mode=client_mode) as client:
            result = await client.call_tool("list_workspaces", {})

    assert result is not None
    assert not result.is_error


# ---------------------------------------------------------------------------
# 3. Destructive guard: delete_restore_point blocked without env flag
# ---------------------------------------------------------------------------


async def test_destructive_tool_blocked_without_env_flag(
    contract_ctx, client_mode, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delete_restore_point raises a ToolError when FABRIC_MCP_ALLOW_DESTRUCTIVE is unset.

    The MCP protocol returns this as ``is_error=True`` on the CallToolResult.
    This validates that the guard logic survives the full protocol round-trip
    (the guard runs inside the tool function, which the protocol invokes).
    """
    from mcp.client import Client  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    # Ensure the destructive flag is NOT set.
    monkeypatch.delenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", raising=False)

    with patch("fabric_dw.mcp._context.build_context", return_value=contract_ctx):
        async with Client(
            mcp,
            mode=client_mode,
            raise_exceptions=False,  # Return errors as is_error=True instead of raising
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
    assert result.is_error, (
        f"Expected is_error=True for destructive tool without flag; got: {result!r}"
    )


async def test_destructive_tool_allowed_with_env_flag(
    contract_ctx, client_mode, monkeypatch: pytest.MonkeyPatch
) -> None:
    """delete_restore_point proceeds when FABRIC_MCP_ALLOW_DESTRUCTIVE=1.

    The service layer is mocked so no real HTTP occurs.
    """
    from mcp.client import Client  # noqa: PLC0415

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
        async with Client(mcp, mode=client_mode, raise_exceptions=False) as client:
            result = await client.call_tool(
                "delete_restore_point",
                {
                    "workspace": "my-workspace",
                    "warehouse": "my-warehouse",
                    "restore_point_id": _rp_id,
                },
            )

    assert not result.is_error, f"Expected success; got error: {result!r}"


# ---------------------------------------------------------------------------
# 4. serverInfo — the server identifies itself over the protocol
# ---------------------------------------------------------------------------


async def test_server_info_reports_fabric_dw_version(contract_ctx, client_mode) -> None:
    """The version the client sees must be the fabric-dw version.

    Under SDK v1 the server reported the SDK's own version here; under v2 the
    constructor defaults it to the empty string. Either way it has to be passed
    explicitly, and this is the assertion that proves the client actually
    receives it.

    The SDK guarantees ``serverInfo`` only on legacy connections; on modern ones
    it is an optional ``_meta`` stamp. This server populates it in both cases
    (measured), so both modes assert unconditionally rather than tolerating
    ``None``. A version of this test that skipped its assertions whenever
    ``info`` happened to be ``None`` would assert nothing at all under ``auto``,
    which is worse than not running it there. If a future SDK stops stamping
    ``serverInfo`` on modern connections, this fails loudly and the decision to
    accept that gets made deliberately.
    """
    from mcp.client import Client  # noqa: PLC0415

    from fabric_dw import __version__  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with patch("fabric_dw.mcp._context.build_context", return_value=contract_ctx):
        async with Client(mcp, mode=client_mode) as client:
            info = client.server_info

    assert info is not None, (
        f"no serverInfo on a {client_mode} connection; the client cannot tell "
        "which server or version it is talking to"
    )
    assert info.name == "fabric-dw"
    assert info.version == __version__
    assert info.version, "server version must not be empty"
