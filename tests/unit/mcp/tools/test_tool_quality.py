"""Tests for MCP tool quality improvements (refactor/mcp-tool-quality).

Coverage
--------
1. ``fabric_err`` — structured meta suffix in ToolError message for FabricError.
2. ``tool_err`` — FabricError → fabric_err, ValueError → plain ToolError.
3. ``make_sql_target`` — raises ToolError when connection_string is None.
4. ``resolve_item`` — returns (ws_id, entry) in one call.
5. ``safe_rows`` — applies json_safe to all cells.
6. ``parse_qualified_name`` — consistent ToolError on bad input.
7. Int param bounds — FastMCP rejects out-of-range values.
8. ``next()`` fallback in create_sql_pool / update_sql_pool.
9. ``clear_cache`` scope + stats.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw import auth as _auth
from fabric_dw.cache import ItemEntry
from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import ServerContext
from fabric_dw.mcp._helpers import (
    fabric_err,
    make_sql_target,
    parse_qualified_name,
    resolve_item,
    safe_rows,
    tool_err,
)
from fabric_dw.models import WarehouseKind

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_WH_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
_WS_NAME = "my-workspace"
_WH_NAME = "my-warehouse"
_CONN_STRING = "wh.fabric.microsoft.com"


def _make_entry(
    *,
    connection_string: str | None = _CONN_STRING,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
) -> ItemEntry:
    return ItemEntry(
        id=_WH_ID,
        kind=kind,
        connection_string=connection_string,
        fetched_at=datetime.now(tz=UTC),
        display_name=_WH_NAME,
    )


def _make_ctx() -> ServerContext:
    mock_http = AsyncMock()
    mock_cache = MagicMock()
    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=_make_entry())
    return ServerContext(
        http=mock_http,
        cache=mock_cache,
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )


# ---------------------------------------------------------------------------
# 1. fabric_err — structured meta
# ---------------------------------------------------------------------------


class TestFabricErr:
    def test_plain_exception_no_meta(self) -> None:
        te = fabric_err(ValueError("bad value"))
        assert "bad value" in str(te)
        # No meta block for non-FabricError
        assert "|meta|" not in str(te)

    def test_fabric_error_without_fields(self) -> None:
        exc = FabricError("something failed")
        te = fabric_err(exc)
        msg = str(te)
        assert "FabricError" in msg
        assert "something failed" in msg
        # No extra meta if no structured fields present
        assert "|meta|" not in msg

    def test_fabric_error_with_status(self) -> None:
        exc = FabricError("rate limited", status=429)
        te = fabric_err(exc)
        msg = str(te)
        assert "|meta|" in msg
        meta_str = msg.split("|meta|", 1)[1].strip()
        meta = json.loads(meta_str)
        assert meta["error_type"] == "FabricError"
        assert meta["status"] == 429

    def test_fabric_error_with_request_id(self) -> None:
        exc = FabricError("not found", request_id="rid-123")
        te = fabric_err(exc)
        msg = str(te)
        assert "|meta|" in msg
        meta = json.loads(msg.split("|meta|", 1)[1].strip())
        assert meta["request_id"] == "rid-123"

    def test_fabric_error_with_hint(self) -> None:
        exc = FabricError("forbidden", status=403, hint="Grant Viewer role")
        te = fabric_err(exc)
        msg = str(te)
        assert "|meta|" in msg
        meta = json.loads(msg.split("|meta|", 1)[1].strip())
        assert meta["hint"] == "Grant Viewer role"
        assert meta["status"] == 403

    def test_returns_tool_error_type(self) -> None:
        exc = FabricError("err", status=500)
        te = fabric_err(exc)
        assert isinstance(te, ToolError)


# ---------------------------------------------------------------------------
# 2. tool_err — uniform funnel
# ---------------------------------------------------------------------------


class TestToolErr:
    def test_fabric_error_uses_fabric_err(self) -> None:
        exc = FabricError("api error", status=404)
        te = tool_err(exc)
        assert isinstance(te, ToolError)
        assert "FabricError" in str(te)
        assert "api error" in str(te)

    def test_value_error_plain_message(self) -> None:
        exc = ValueError("bad input")
        te = tool_err(exc)
        assert isinstance(te, ToolError)
        assert "bad input" in str(te)
        assert "|meta|" not in str(te)

    def test_generic_exception(self) -> None:
        exc = RuntimeError("unexpected")
        te = tool_err(exc)
        assert isinstance(te, ToolError)
        assert "unexpected" in str(te)


# ---------------------------------------------------------------------------
# 3. make_sql_target — ToolError when connection_string is None
# ---------------------------------------------------------------------------


class TestMakeSqlTarget:
    def test_raises_tool_error_when_no_connection_string(self) -> None:
        entry = _make_entry(connection_string=None)
        with pytest.raises(ToolError, match="has no connection string"):
            make_sql_target(_WS_ID, entry, "my-warehouse")

    def test_returns_sql_target_with_fields(self) -> None:
        from fabric_dw.sql import SqlTarget  # noqa: PLC0415

        entry = _make_entry(connection_string=_CONN_STRING)
        target = make_sql_target(_WS_ID, entry, "my-warehouse")
        assert isinstance(target, SqlTarget)
        assert target.connection_string == _CONN_STRING
        assert target.database == _WH_NAME
        assert target.workspace_id == str(_WS_ID)

    def test_error_message_includes_item_name(self) -> None:
        entry = _make_entry(connection_string=None)
        with pytest.raises(ToolError, match="my-warehouse"):
            make_sql_target(_WS_ID, entry, "my-warehouse")


# ---------------------------------------------------------------------------
# 4. resolve_item — returns (ws_id, entry) without double lookup
# ---------------------------------------------------------------------------


async def test_resolve_item_returns_pair() -> None:
    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    expected_entry = _make_entry()
    mock_resolver.item = AsyncMock(return_value=expected_entry)

    ws_id, entry = await resolve_item(mock_resolver, _WS_NAME, _WH_NAME)

    assert ws_id == _WS_ID
    assert entry is expected_entry
    mock_resolver.workspace_id.assert_awaited_once_with(_WS_NAME)
    mock_resolver.item.assert_awaited_once_with(str(_WS_ID), _WH_NAME)


# ---------------------------------------------------------------------------
# 5. safe_rows — applies json_safe
# ---------------------------------------------------------------------------


class TestSafeRows:
    def test_string_passthrough(self) -> None:
        rows = [["hello", "world"]]
        result = safe_rows(rows)
        assert result == [["hello", "world"]]

    def test_converts_decimal_to_string(self) -> None:
        from decimal import Decimal  # noqa: PLC0415

        rows = [[Decimal("3.14")]]
        result = safe_rows(rows)
        assert result == [["3.14"]]

    def test_converts_datetime_to_string(self) -> None:
        from datetime import datetime  # noqa: PLC0415

        dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        rows = [[dt]]
        result = safe_rows(rows)
        assert isinstance(result[0][0], str)

    def test_accepts_list_of_tuples(self) -> None:
        # The service layer may return tuples from the DB driver
        rows = [("hello", 42)]
        result = safe_rows(rows)
        assert result == [["hello", 42]]

    def test_empty_rows(self) -> None:
        assert safe_rows([]) == []


# ---------------------------------------------------------------------------
# 6. parse_qualified_name — consistent ToolError
# ---------------------------------------------------------------------------


class TestParseQualifiedName:
    def test_valid_schema_table(self) -> None:
        schema, name = parse_qualified_name("dbo.my_table", kind="table")
        assert schema == "dbo"
        assert name == "my_table"

    def test_valid_view(self) -> None:
        schema, name = parse_qualified_name("sales.vw_orders", kind="view")
        assert schema == "sales"
        assert name == "vw_orders"

    def test_rejects_no_dot(self) -> None:
        with pytest.raises(ToolError, match="Invalid qualified name"):
            parse_qualified_name("nodot", kind="table")

    def test_rejects_empty_schema(self) -> None:
        with pytest.raises(ToolError, match="Invalid qualified name"):
            parse_qualified_name(".name", kind="table")

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ToolError, match="Invalid qualified name"):
            parse_qualified_name("schema.", kind="table")

    def test_kind_in_error_message(self) -> None:
        with pytest.raises(ToolError, match="view"):
            parse_qualified_name("nodot", kind="view")

    def test_rejects_whitespace_only_schema(self) -> None:
        """Whitespace-only schema now raises ToolError (behaviour change from #826).

        The previous standalone implementation used a plain truthiness check
        (``if not schema``) which accepted ``"  "`` as a truthy string.  The
        canonical implementation in identifiers.py strips before checking,
        so whitespace-only parts are now correctly rejected.
        """
        with pytest.raises(ToolError, match="schema part must not be empty"):
            parse_qualified_name("  .name", kind="table")

    def test_rejects_whitespace_only_object(self) -> None:
        """Whitespace-only object part now raises ToolError (behaviour change from #826)."""
        with pytest.raises(ToolError, match="table part must not be empty"):
            parse_qualified_name("schema.  ", kind="table")

    def test_multi_dot_splits_on_first_dot(self) -> None:
        """Multi-dot input is split on the first dot; remainder is the object name."""
        schema, name = parse_qualified_name("dbo.schema.table")
        assert schema == "dbo"
        assert name == "schema.table"


# ---------------------------------------------------------------------------
# 7. Int param bounds
# ---------------------------------------------------------------------------


async def test_read_table_count_bound_rejection() -> None:
    """read_table must reject count > 10000 at the FastMCP schema layer."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "read_table",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.t",
                "count": 99999,
            },
        )


async def test_read_view_count_bound_rejection() -> None:
    """read_view must reject count > 10000 at the FastMCP schema layer."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "read_view",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.v",
                "count": 99999,
            },
        )


async def test_execute_sql_max_rows_bound_rejection() -> None:
    """execute_sql must reject max_rows > 10000."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "execute_sql",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "query": "SELECT 1",
                "max_rows": 99999,
            },
        )


async def test_list_request_history_limit_bound_rejection() -> None:
    """list_request_history must reject limit > 10000."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_request_history",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "limit": 99999,
            },
        )


async def test_create_sql_pool_max_percent_bound_rejection() -> None:
    """create_sql_pool must reject max_percent > 100."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_sql_pool",
            {
                "workspace": _WS_NAME,
                "name": "mypool",
                "max_percent": 200,
            },
        )


# ---------------------------------------------------------------------------
# 8. next() fallback in create_sql_pool / update_sql_pool
# ---------------------------------------------------------------------------


async def test_create_sql_pool_next_fallback() -> None:
    """create_sql_pool raises ToolError (not StopIteration) when pool absent after create."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import SqlPoolsConfiguration  # noqa: PLC0415

    ctx = _make_ctx()
    # Return a config that does NOT include the created pool (eventual consistency simulation)
    empty_config = SqlPoolsConfiguration.model_validate(
        {"customSQLPoolsEnabled": True, "customSQLPools": []}
    )
    with (
        patch("fabric_dw.services.sql_pools.create_pool", new=AsyncMock(return_value=empty_config)),
        patch("fabric_dw.services.sql_pools.get_configuration", new=AsyncMock()),
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict("os.environ", {}, clear=False),
        pytest.raises(ToolError, match="eventual consistency"),
    ):
        await mcp._tool_manager.call_tool(
            "create_sql_pool",
            {
                "workspace": _WS_NAME,
                "name": "mypool",
                "max_percent": 50,
            },
        )


async def test_update_sql_pool_next_fallback() -> None:
    """update_sql_pool raises ToolError (not StopIteration) when pool absent after update."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import SqlPoolsConfiguration  # noqa: PLC0415

    ctx = _make_ctx()
    empty_config = SqlPoolsConfiguration.model_validate(
        {"customSQLPoolsEnabled": True, "customSQLPools": []}
    )
    with (
        patch("fabric_dw.services.sql_pools.update_pool", new=AsyncMock(return_value=empty_config)),
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict("os.environ", {}, clear=False),
        pytest.raises(ToolError, match="eventual consistency"),
    ):
        await mcp._tool_manager.call_tool(
            "update_sql_pool",
            {
                "workspace": _WS_NAME,
                "name": "mypool",
            },
        )


# ---------------------------------------------------------------------------
# 9. clear_cache scope + stats  (T05: use real LookupCache, not mock internals)
# ---------------------------------------------------------------------------


def _make_ctx_with_real_cache(tmp_path: Any) -> ServerContext:
    """Return a ServerContext with a real LookupCache backed by a temp directory.

    *tmp_path* should be the pytest ``tmp_path`` fixture (a :class:`pathlib.Path`).
    """
    from pathlib import Path  # noqa: PLC0415

    from fabric_dw.cache import LookupCache  # noqa: PLC0415

    real_cache = LookupCache(path=Path(tmp_path) / "lookup.json")
    mock_http = AsyncMock()
    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=_make_entry())
    return ServerContext(
        http=mock_http,
        cache=real_cache,
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )


async def test_clear_cache_all_returns_stats(tmp_path: Any) -> None:
    """clear_cache(scope='all') reports 0 counts on an empty cache."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx_with_real_cache(tmp_path)

    with patch("fabric_dw.mcp._context._SERVER_CTX", ctx):
        result = await mcp._tool_manager.call_tool("clear_cache", {"scope": "all"})

    assert result["scope"] == "all"
    assert result["workspaces_cleared"] == 0
    assert result["items_cleared"] == 0
    assert "negative_cache_cleared" not in result


async def test_clear_cache_workspaces_scope(tmp_path: Any) -> None:
    """clear_cache(scope='workspaces') reports 2 cleared when 2 workspaces are present."""

    from uuid import UUID  # noqa: PLC0415

    from fabric_dw.cache import LookupCache  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    # Seed the real cache with two workspace entries.
    real_cache = LookupCache(path=tmp_path / "lookup_ws.json")
    real_cache.put_workspace("ws1", UUID("11111111-0000-0000-0000-000000000001"))
    real_cache.put_workspace("ws2", UUID("11111111-0000-0000-0000-000000000002"))

    mock_http = AsyncMock()
    mock_resolver = AsyncMock()
    ctx = ServerContext(
        http=mock_http,
        cache=real_cache,
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with patch("fabric_dw.mcp._context._SERVER_CTX", ctx):
        result = await mcp._tool_manager.call_tool("clear_cache", {"scope": "workspaces"})

    assert result["scope"] == "workspaces"
    assert result["workspaces_cleared"] == 2
    assert result["items_cleared"] == 0
    assert "negative_cache_cleared" not in result

    # Verify the workspace entries are actually gone.
    assert real_cache.get_workspace("ws1") is None
    assert real_cache.get_workspace("ws2") is None


async def test_clear_cache_items_scope(tmp_path: Any) -> None:
    """clear_cache(scope='items') reports 2 cleared when 2 workspace item buckets exist."""
    from datetime import UTC, datetime  # noqa: PLC0415
    from uuid import UUID  # noqa: PLC0415

    from fabric_dw.cache import ItemEntry, LookupCache  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import WarehouseKind  # noqa: PLC0415

    ws_a = UUID("aaaaaaaa-0000-0000-0000-000000000001")
    ws_b = UUID("bbbbbbbb-0000-0000-0000-000000000002")
    item_entry = ItemEntry(
        id=UUID("cccccccc-0000-0000-0000-000000000003"),
        kind=WarehouseKind.WAREHOUSE,
        connection_string=None,
        fetched_at=datetime.now(tz=UTC),
    )

    # Seed the real cache with items in two workspace buckets.
    real_cache = LookupCache(path=tmp_path / "lookup_items.json")
    real_cache.put_item(ws_a, "item-x", item_entry)
    real_cache.put_item(ws_b, "item-y", item_entry)

    mock_http = AsyncMock()
    mock_resolver = AsyncMock()
    ctx = ServerContext(
        http=mock_http,
        cache=real_cache,
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with patch("fabric_dw.mcp._context._SERVER_CTX", ctx):
        result = await mcp._tool_manager.call_tool("clear_cache", {"scope": "items"})

    assert result["scope"] == "items"
    assert result["workspaces_cleared"] == 0
    assert result["items_cleared"] == 2
    assert "negative_cache_cleared" not in result

    # Verify the item entries are actually gone.
    assert real_cache.get_item(ws_a, "item-x") is None
    assert real_cache.get_item(ws_b, "item-y") is None


# ---------------------------------------------------------------------------
# 11. make_sql_target used instead of TRY301 pattern (no_connection_string path)
# ---------------------------------------------------------------------------


async def test_list_tables_raises_tool_error_on_no_connection() -> None:
    """list_tables must raise ToolError (not FabricError) when no connection string."""
    from typing import cast as _cast  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    _cast(Any, ctx.resolver).item = AsyncMock(return_value=_make_entry(connection_string=None))

    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        pytest.raises(ToolError, match="has no connection string"),
    ):
        await mcp._tool_manager.call_tool(
            "list_tables",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )


async def test_list_running_queries_raises_tool_error_on_no_connection() -> None:
    """list_running_queries must raise ToolError when no connection string."""
    from typing import cast as _cast  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    _cast(Any, ctx.resolver).item = AsyncMock(return_value=_make_entry(connection_string=None))

    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        pytest.raises(ToolError, match="has no connection string"),
    ):
        await mcp._tool_manager.call_tool(
            "list_running_queries",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )


# ---------------------------------------------------------------------------
# M21 — mutating_tool single-sources the tool name
# ---------------------------------------------------------------------------


async def test_mutating_tool_blocks_in_readonly_mode() -> None:
    """mutating_tool must raise ToolError with the tool name when FABRIC_MCP_READONLY=1."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict("os.environ", {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_warehouse",
            {"workspace": _WS_NAME, "name": "new-wh"},
        )

    # The error message must include the tool name (single-sourced via mutating_tool)
    assert "create_warehouse" in str(exc_info.value)


async def test_mutating_tool_create_view_blocked_in_readonly() -> None:
    """create_view (using mutating_tool) must surface the correct tool name in read-only error."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict("os.environ", {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_view",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.vw_x",
                "select_body": "SELECT 1",
            },
        )

    assert "create_view" in str(exc_info.value)


async def test_mutating_tool_create_sql_pool_blocked_in_readonly() -> None:
    """create_sql_pool (mutating_tool) must surface the correct tool name in read-only mode."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict("os.environ", {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_sql_pool",
            {"workspace": _WS_NAME, "name": "pool1", "max_percent": 10},
        )

    assert "create_sql_pool" in str(exc_info.value)


# ---------------------------------------------------------------------------
# M21 exhaustive — ALL mutating tools must be single-sourced via mutating_tool
# ---------------------------------------------------------------------------

# Complete list of mutating MCP tool names.  Every entry here must be
# registered via ``mutating_tool(mcp, <name>)`` (or ``mutating_tool(mcp,
# <name>, destructive=True)``).  The single exception is
# ``refresh_sql_endpoint_metadata``, which uses ``mutating_tool`` for
# ``assert_writes_allowed`` but calls ``assert_destructive_allowed``
# conditionally inside the function body (because the guard depends on the
# ``recreate_tables`` argument, not the tool name).
#
# The test verifies the invariant by calling each tool with
# ``FABRIC_MCP_READONLY=1`` and asserting that (a) a ``ToolError`` is raised
# and (b) the error message contains the tool name — which only happens when
# ``mutating_tool`` injects ``assert_writes_allowed(name)`` with that exact
# string.  If any tool were still using a bare ``@mcp.tool`` + manual
# ``assert_writes_allowed("…")`` call, the name in the error message would
# still appear, but the *source* would be duplicated (detectable by code
# review, not this test).  This test therefore serves as a regression guard:
# if a new mutating tool is added without ``mutating_tool``, adding it here
# will expose the omission at review time.

_ALL_MUTATING_TOOLS: list[str] = [
    # views.py
    "create_view",
    "update_view",
    "drop_view",
    "rename_view",
    "transfer_view",
    # warehouses.py
    "create_warehouse",
    "rename_warehouse",
    "delete_warehouse",
    "takeover_warehouse",
    # sql_pools.py
    "create_sql_pool",
    "update_sql_pool",
    "delete_sql_pool",
    "enable_sql_pools",
    "disable_sql_pools",
    # tables.py
    "create_table",
    "delete_table",
    "clear_table",
    "clone_table",
    "rename_table",
    "transfer_table",
    # procedures.py
    "create_procedure",
    "update_procedure",
    "drop_procedure",
    # snapshots.py
    "create_snapshot",
    "rename_snapshot",
    "delete_snapshot",
    "roll_snapshot_timestamp",
    # restore.py
    "create_restore_point",
    "update_restore_point",
    "delete_restore_point",
    "restore_warehouse_in_place",
    # schemas.py
    "create_schema",
    "delete_schema",
    # workspaces.py
    "set_workspace_collation",
    # sql_endpoints.py
    # refresh_sql_endpoint_metadata: uses mutating_tool for assert_writes_allowed;
    # assert_destructive_allowed is called conditionally on recreate_tables inside
    # the function body (cannot be unconditional at the decorator level).
    "refresh_sql_endpoint_metadata",
]


@pytest.mark.parametrize("tool_name", _ALL_MUTATING_TOOLS)
async def test_all_mutating_tools_blocked_in_readonly(tool_name: str) -> None:
    """Every mutating tool must raise ToolError containing the tool name in read-only mode.

    This exhaustively verifies M21: each tool is registered via
    ``mutating_tool(mcp, tool_name)`` so that ``assert_writes_allowed`` is
    called with the *same* string used for tool registration.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = _make_ctx()
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict("os.environ", {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        # Call with minimal / empty args — the write guard fires before any
        # argument validation, so the exact args don't matter.
        await mcp._tool_manager.call_tool(tool_name, {"workspace": _WS_NAME})

    error_text = str(exc_info.value)
    assert tool_name in error_text, (
        f"ToolError for {tool_name!r} in read-only mode did not contain the tool name. "
        f"Got: {error_text!r}. "
        "This suggests the tool is NOT using mutating_tool and has a stale name string."
    )
