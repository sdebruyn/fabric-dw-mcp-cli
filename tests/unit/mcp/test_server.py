"""Tests for the MCP server — testing after the context-split refactor.

Testing strategy
----------------
FastMCP 1.x ships no in-process test transport in its public API, so we use
unit-style mocking via the shared ``mock_ctx`` / ``ctx_patch`` fixtures defined
in ``conftest.py``.

Tools are called via ``mcp._tool_manager.call_tool(name, args)`` which is the
same call path FastMCP uses at runtime, giving realistic coverage of the
``@mcp.tool`` decorator, Pydantic validation, and guard logic.

The ``ServerContext`` (http / cache / resolver) is injected by patching
``fabric_dw.mcp._context._SERVER_CTX`` with a ``ServerContext`` instance
that has mocked service objects.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from fabric_dw.cache import ItemEntry
from fabric_dw.exceptions import NotFoundError, PermissionDeniedError
from fabric_dw.models import (
    AuditSettings,
    ItemAccess,
    RunningQuery,
    Table,
    TableSyncStatus,
    Warehouse,
    WarehouseKind,
    WarehouseSnapshot,
    Workspace,
)
from tests.unit._tool_introspection import SNAKE_CASE_RE, collect_live_mcp_tool_names
from tests.unit.mcp.conftest import (
    WH_ID,
    WH_NAME,
    WS_ID,
    WS_NAME,
    make_item_entry,
    make_sql_endpoint_entry,
)

# ---------------------------------------------------------------------------
# Stable core tools — a small subset that must always be registered.
# These represent fundamental capabilities across all major domains.
# Additions never require touching this set; only intentional removal of a
# core tool should prompt updating it.
# ---------------------------------------------------------------------------

CORE_TOOLS: frozenset[str] = frozenset(
    {
        # Workspaces
        "list_workspaces",
        "get_workspace",
        # Warehouses
        "list_warehouses",
        "get_warehouse",
        # SQL execution
        "execute_sql",
        # Audit
        "get_audit_settings",
        # Snapshots
        "list_snapshots",
        # Restore points
        "list_restore_points",
        "delete_restore_point",
        # Tables
        "list_tables",
        # Views
        "list_views",
        # Stored procedures
        "list_procedures",
        # Functions
        "list_functions",
        # Statistics
        "list_statistics",
        # Queries
        "list_running_queries",
        # Cache
        "clear_cache",
    }
)

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

_WS_ID = WS_ID
_WH_ID = WH_ID
_SNAP_ID = UUID("e5f6a7b8-c9d0-1234-ef01-23456789abcd")
_WS_NAME = WS_NAME
_WH_NAME = WH_NAME


def _make_workspace() -> Workspace:
    return Workspace.model_validate(
        {
            "id": str(_WS_ID),
            "displayName": _WS_NAME,
            "description": "test workspace",
            "capacityId": None,
        }
    )


def _make_warehouse() -> Warehouse:
    return Warehouse.model_validate(
        {
            "id": str(_WH_ID),
            "displayName": _WH_NAME,
            "workspaceId": str(_WS_ID),
            "kind": WarehouseKind.WAREHOUSE,
            "connectionString": "wh.fabric.microsoft.com",
        }
    )


def _make_item_entry(
    *,
    item_id: UUID = _WH_ID,
    connection_string: str | None = "wh.fabric.microsoft.com",
    display_name: str = _WH_NAME,
) -> ItemEntry:
    return make_item_entry(
        item_id=item_id,
        connection_string=connection_string,
        display_name=display_name,
    )


def _make_sql_endpoint_entry(
    *,
    item_id: UUID = _WH_ID,
    connection_string: str | None = "ep.fabric.microsoft.com",
    display_name: str = "MySqlEndpoint",
) -> ItemEntry:
    return make_sql_endpoint_entry(
        item_id=item_id,
        connection_string=connection_string,
        display_name=display_name,
    )


def _make_audit_settings() -> AuditSettings:
    return AuditSettings.model_validate(
        {
            "state": "Enabled",
            "retentionDays": 30,
            "auditActionsAndGroups": ["BATCH_COMPLETED_GROUP"],
        }
    )


def _make_snapshot() -> WarehouseSnapshot:
    return WarehouseSnapshot.model_validate(
        {
            "id": str(_SNAP_ID),
            "displayName": "snap-1",
            "parentWarehouseId": str(_WH_ID),
            "snapshotDateTime": "2026-01-01T00:00:00",
        }
    )


def _make_running_query() -> RunningQuery:
    return RunningQuery.model_validate(
        {
            "session_id": 42,
            "request_id": "req-1",
            "status": "running",
            "start_time": "2026-01-01T12:00:00",
            "total_elapsed_time": 1000,
            "login_name": "user@example.com",
            "command": "SELECT 1",
            "query_text": None,
        }
    )


def _make_table_sync_statuses() -> list[TableSyncStatus]:
    return [
        TableSyncStatus.model_validate(
            {
                "tableName": "Table1",
                "status": "Success",
                "startDateTime": "2025-08-08T10:31:22.270Z",
                "endDateTime": "2025-08-08T10:36:54.965Z",
                "lastSuccessfulSyncDateTime": "2025-08-08T10:36:54.965Z",
            }
        ),
        TableSyncStatus.model_validate(
            {
                "tableName": "Table2",
                "status": "Failure",
                "startDateTime": "2025-08-08T10:31:22.270Z",
                "endDateTime": "2025-08-08T10:43:02.532Z",
                "error": {"errorCode": "TokenError", "message": "Auth failed"},
                "lastSuccessfulSyncDateTime": "2025-08-07T10:44:27.263Z",
            }
        ),
    ]


def _make_item_access() -> ItemAccess:
    return ItemAccess.model_validate(
        {
            "principal": {
                "id": str(_WH_ID),
                "displayName": "Jacob Hancock",
                "type": "User",
                "userDetails": {"userPrincipalName": "jacob@example.com"},
            },
            "itemAccessDetails": {
                "type": "Warehouse",
                "permissions": ["Read", "Write"],
                "additionalPermissions": ["ReadAll"],
            },
        }
    )


# ---------------------------------------------------------------------------
# 1. Tool registration
# ---------------------------------------------------------------------------


def test_core_tools_registered() -> None:
    """Every core tool must be registered in the FastMCP server.

    ``CORE_TOOLS`` is a stable subset of fundamental tools spanning all major
    domains.  A registration regression (e.g. a broken ``register_all`` call)
    is caught here without requiring a full name-set bump when new tools land.
    """
    registered_names = collect_live_mcp_tool_names()
    missing = CORE_TOOLS - registered_names
    assert not missing, (
        f"Core tools missing from registration: {sorted(missing)}. "
        "These tools are considered fundamental and should always be present."
    )


def test_registered_tools_no_duplicates() -> None:
    """The live tool registration must produce no duplicate tool names.

    ``list_tools()`` is the public API — a duplicate registration would appear
    as two entries with the same name in the returned list.
    """
    import asyncio  # noqa: PLC0415

    from fabric_dw.mcp._helpers import InstrumentedFastMCP  # noqa: PLC0415
    from fabric_dw.mcp.tools import register_all  # noqa: PLC0415

    mcp = InstrumentedFastMCP("dup-check")
    register_all(mcp)
    all_tools = asyncio.run(mcp.list_tools())
    all_names = [t.name for t in all_tools]
    unique_names = set(all_names)
    assert len(all_names) == len(unique_names), (
        f"Duplicate tool names detected via list_tools(): "
        f"{sorted(n for n in unique_names if all_names.count(n) > 1)}"
    )


def test_registered_tools_naming_convention() -> None:
    """Every registered tool name must follow the snake_case naming convention."""
    registered_names = collect_live_mcp_tool_names()
    bad = [name for name in registered_names if not SNAKE_CASE_RE.match(name)]
    assert not bad, f"Tool names violating snake_case convention: {sorted(bad)}"


def test_registered_tools_non_empty_descriptions() -> None:
    """Every registered tool must have a non-empty description string.

    Uses the public ``list_tools()`` API so the check mirrors what an MCP
    client actually receives rather than reading internal state.
    """
    import asyncio  # noqa: PLC0415

    from fabric_dw.mcp._helpers import InstrumentedFastMCP  # noqa: PLC0415
    from fabric_dw.mcp.tools import register_all  # noqa: PLC0415

    mcp = InstrumentedFastMCP("desc-check")
    register_all(mcp)
    all_tools = asyncio.run(mcp.list_tools())
    missing_desc = [t.name for t in all_tools if not (t.description or "").strip()]
    assert not missing_desc, f"Tools with missing or empty description: {sorted(missing_desc)}"


# ---------------------------------------------------------------------------
# 2. list_workspaces happy path
# ---------------------------------------------------------------------------


async def test_list_workspaces_happy_path(ctx_patch) -> None:
    """list_workspaces returns a list of serialised workspace dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ws = _make_workspace()

    with (
        ctx_patch,
        patch("fabric_dw.services.workspaces.list_all", new=AsyncMock(return_value=[ws])),
    ):
        result = await mcp._tool_manager.call_tool("list_workspaces", {})

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == str(_WS_ID)
    assert result[0]["displayName"] == _WS_NAME


# ---------------------------------------------------------------------------
# 3. clear_cache side effect
# ---------------------------------------------------------------------------


async def test_clear_cache_side_effect(mock_ctx, ctx_patch) -> None:
    """clear_cache(scope='all') must call LookupCache.clear() and clear_negative_cache."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.cache.counts.return_value = (2, 3)

    with ctx_patch:
        result = await mcp._tool_manager.call_tool("clear_cache", {})

    mock_ctx.cache.counts.assert_called_once()
    mock_ctx.cache.clear.assert_called_once()
    mock_ctx.resolver.clear_negative_cache.assert_called_once()
    assert result["scope"] == "all"
    assert result["workspaces_cleared"] == 2
    assert result["items_cleared"] == 3
    assert result["negative_cache_cleared"] is True


async def test_clear_cache_scope_workspaces(mock_ctx, ctx_patch) -> None:
    """clear_cache(scope='workspaces') must NOT call full clear() or clear_negative_cache."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    # Configure the public counts() API to return (1 workspace, 0 item buckets).
    mock_ctx.cache.counts.return_value = (1, 0)

    with ctx_patch:
        result = await mcp._tool_manager.call_tool("clear_cache", {"scope": "workspaces"})

    mock_ctx.cache.counts.assert_called_once()
    mock_ctx.cache.clear_scope.assert_called_once_with("workspaces")
    mock_ctx.cache.clear.assert_not_called()
    mock_ctx.resolver.clear_negative_cache.assert_not_called()
    assert result["scope"] == "workspaces"
    assert result["workspaces_cleared"] == 1
    assert result["items_cleared"] == 0
    assert result["negative_cache_cleared"] is False


async def test_clear_cache_scope_items(mock_ctx, ctx_patch) -> None:
    """clear_cache(scope='items') must NOT call full clear() or clear_negative_cache."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    # Configure the public counts() API to return (0 workspaces, 1 item bucket).
    mock_ctx.cache.counts.return_value = (0, 1)

    with ctx_patch:
        result = await mcp._tool_manager.call_tool("clear_cache", {"scope": "items"})

    mock_ctx.cache.counts.assert_called_once()
    mock_ctx.cache.clear_scope.assert_called_once_with("items")
    mock_ctx.cache.clear.assert_not_called()
    mock_ctx.resolver.clear_negative_cache.assert_not_called()
    assert result["scope"] == "items"
    assert result["workspaces_cleared"] == 0
    assert result["items_cleared"] == 1
    assert result["negative_cache_cleared"] is False


# ---------------------------------------------------------------------------
# 4. FabricError translates into ToolError
# ---------------------------------------------------------------------------


async def test_fabric_error_becomes_tool_error(ctx_patch) -> None:
    """A FabricError raised by the service layer must become a ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    not_found_error = NotFoundError("workspace 'x' not found")

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(side_effect=not_found_error),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool("list_workspaces", {})

    err = exc_info.value
    assert "NotFoundError" in str(err) or "not found" in str(err).lower()


# ---------------------------------------------------------------------------
# 5. get_workspace happy path (resolver usage)
# ---------------------------------------------------------------------------


async def test_get_workspace_happy_path(mock_ctx, ctx_patch) -> None:
    """get_workspace resolves the name via Resolver and returns a dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ws = _make_workspace()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch("fabric_dw.services.workspaces.get", new=AsyncMock(return_value=ws)),
    ):
        result = await mcp._tool_manager.call_tool("get_workspace", {"workspace": _WS_NAME})

    assert isinstance(result, dict)
    assert result["id"] == str(_WS_ID)
    mock_ctx.resolver.workspace_id.assert_called_once_with(_WS_NAME)


# ---------------------------------------------------------------------------
# 7. list_warehouses happy path
# ---------------------------------------------------------------------------


async def test_list_warehouses_happy_path(mock_ctx, ctx_patch) -> None:
    """list_warehouses resolves workspace and returns list of warehouse dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    wh = _make_warehouse()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(return_value=[wh]),
        ),
    ):
        result = await mcp._tool_manager.call_tool("list_warehouses", {"workspace": _WS_NAME})

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == str(_WH_ID)


# ---------------------------------------------------------------------------
# 8. run() uses stdio transport by default
# ---------------------------------------------------------------------------


def test_run_uses_stdio_by_default() -> None:
    """run() with no args calls FastMCP.run(transport='stdio')."""
    from fabric_dw.mcp import run  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with patch.object(mcp, "run") as mock_run:
        run([])  # empty argv — default transport

    mock_run.assert_called_once_with(transport="stdio")


# ---------------------------------------------------------------------------
# 9. run() accepts --transport http -> streamable-http
# ---------------------------------------------------------------------------


def test_run_accepts_http_transport() -> None:
    """run(['--transport', 'http']) calls FastMCP.run(transport='streamable-http')."""
    from fabric_dw.mcp import run  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with patch.object(mcp, "run") as mock_run:
        run(["--transport", "http"])

    mock_run.assert_called_once_with(transport="streamable-http")


# ---------------------------------------------------------------------------
# 10. get_audit_settings happy path
# ---------------------------------------------------------------------------


async def test_get_audit_settings_happy_path(mock_ctx, ctx_patch) -> None:
    """get_audit_settings resolves workspace + warehouse and returns a dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings()
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.get_settings",
            new=AsyncMock(return_value=settings),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_audit_settings",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert isinstance(result, dict)
    assert result["state"] == "Enabled"
    assert result["retentionDays"] == 30
    mock_ctx.resolver.item.assert_called_once_with(str(_WS_ID), _WH_NAME)


# ---------------------------------------------------------------------------
# 11. list_running_queries happy path
# ---------------------------------------------------------------------------


async def test_list_running_queries_happy_path(mock_ctx, ctx_patch) -> None:
    """list_running_queries returns list of dicts from the SQL service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    query = _make_running_query()
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_running",
            new=AsyncMock(return_value=[query]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_running_queries",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )

    assert isinstance(result, list)
    assert result[0]["session_id"] == 42


# ---------------------------------------------------------------------------
# 12. NotFoundError error becomes ToolError
# ---------------------------------------------------------------------------


async def test_not_found_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """NotFoundError (a FabricError subclass) must become a ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(
        side_effect=NotFoundError("workspace 'boom' not found")
    )

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool("get_workspace", {"workspace": "boom"})


# ---------------------------------------------------------------------------
# 13. Bad ISO-8601 input -> ToolError
# ---------------------------------------------------------------------------


async def test_create_snapshot_bad_datetime_becomes_tool_error(ctx_patch) -> None:
    """create_snapshot raises ToolError when snapshot_dt is not ISO-8601."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_snapshot",
            {
                "workspace": _WS_NAME,
                "warehouse": _WH_NAME,
                "name": "snap-bad",
                "snapshot_dt": "not-a-date",
            },
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_roll_snapshot_timestamp_bad_datetime_becomes_tool_error(
    ctx_patch,
) -> None:
    """roll_snapshot_timestamp raises ToolError when new_dt is not ISO-8601."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "roll_snapshot_timestamp",
            {
                "workspace": _WS_NAME,
                "warehouse": _WH_NAME,
                "snapshot_name": "snap-1",
                "new_dt": "not-a-date",
            },
        )

    assert "ISO-8601" in str(exc_info.value)


# ---------------------------------------------------------------------------
# SQL Endpoint tools
# ---------------------------------------------------------------------------


async def test_list_sql_endpoints_happy_path(mock_ctx, ctx_patch) -> None:
    """list_sql_endpoints resolves workspace and returns list of SQL endpoint dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep_id = UUID("e5f6a7b8-c9d0-1234-ef01-234567890abc")
    ep = Warehouse.model_validate(
        {
            "id": str(ep_id),
            "displayName": "SalesLakehouse",
            "workspaceId": str(_WS_ID),
            "kind": WarehouseKind.SQL_ENDPOINT,
            "connectionString": "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
        }
    )
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_endpoints.list_endpoints",
            new=AsyncMock(return_value=[ep]),
        ),
    ):
        result = await mcp._tool_manager.call_tool("list_sql_endpoints", {"workspace": _WS_NAME})

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == str(ep_id)
    assert result[0]["kind"] == "SQLEndpoint"


async def test_get_sql_endpoint_happy_path(mock_ctx, ctx_patch) -> None:
    """get_sql_endpoint resolves workspace + endpoint and returns a dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep_id = UUID("e5f6a7b8-c9d0-1234-ef01-234567890abc")
    ep = Warehouse.model_validate(
        {
            "id": str(ep_id),
            "displayName": "SalesLakehouse",
            "workspaceId": str(_WS_ID),
            "kind": WarehouseKind.SQL_ENDPOINT,
            "connectionString": "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
        }
    )
    item = _make_item_entry(item_id=ep_id, display_name="SalesLakehouse")
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_endpoints.get_endpoint",
            new=AsyncMock(return_value=ep),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_sql_endpoint",
            {"workspace": _WS_NAME, "endpoint": "SalesLakehouse"},
        )

    assert isinstance(result, dict)
    assert result["id"] == str(ep_id)
    assert result["kind"] == "SQLEndpoint"


async def test_refresh_sql_endpoint_metadata_happy_path(mock_ctx, ctx_patch) -> None:
    """refresh_sql_endpoint_metadata resolves workspace + endpoint and returns a list of dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep_id = UUID("e5f6a7b8-c9d0-1234-ef01-234567890abc")
    item = _make_item_entry(item_id=ep_id, display_name="SalesLakehouse")
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_endpoints.refresh_metadata",
            new=AsyncMock(return_value=_make_table_sync_statuses()),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "refresh_sql_endpoint_metadata",
            {"workspace": _WS_NAME, "endpoint": "SalesLakehouse"},
        )

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["tableName"] == "Table1"
    assert result[0]["status"] == "Success"
    assert result[1]["status"] == "Failure"


async def test_refresh_sql_endpoint_metadata_recreate_tables(mock_ctx, ctx_patch) -> None:
    """refresh_sql_endpoint_metadata passes recreate_tables=True to the service.

    Requires FABRIC_MCP_ALLOW_DESTRUCTIVE=1 because recreate_tables=True is destructive.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep_id = UUID("e5f6a7b8-c9d0-1234-ef01-234567890abc")
    item = _make_item_entry(item_id=ep_id, display_name="SalesLakehouse")
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_refresh = AsyncMock(return_value=_make_table_sync_statuses())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.sql_endpoints.refresh_metadata",
            new=mock_refresh,
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "refresh_sql_endpoint_metadata",
            {"workspace": _WS_NAME, "endpoint": "SalesLakehouse", "recreate_tables": True},
        )

    assert isinstance(result, list)
    mock_refresh.assert_called_once()
    _, kwargs = mock_refresh.call_args
    assert kwargs.get("recreate_tables") is True


# ---------------------------------------------------------------------------
# list_warehouses with all_workspaces=True
# ---------------------------------------------------------------------------


async def test_list_warehouses_all_workspaces(ctx_patch) -> None:
    """list_warehouses with all_workspaces=True dispatches to list_all_workspaces."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    wh = _make_warehouse()

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.warehouses.list_all_workspaces",
            new=AsyncMock(return_value=[wh]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_warehouses",
            {"workspace": "", "all_workspaces": True},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == str(_WH_ID)


# ---------------------------------------------------------------------------
# list_sql_endpoints with all_workspaces=True
# ---------------------------------------------------------------------------


async def test_list_sql_endpoints_all_workspaces(ctx_patch) -> None:
    """list_sql_endpoints with all_workspaces=True dispatches to list_all_workspaces."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep_id = UUID("e5f6a7b8-c9d0-1234-ef01-234567890abc")
    ep = Warehouse.model_validate(
        {
            "id": str(ep_id),
            "displayName": "SalesLakehouse",
            "workspaceId": str(_WS_ID),
            "kind": WarehouseKind.SQL_ENDPOINT,
            "connectionString": "lakehouse-sql-ep.datawarehouse.fabric.microsoft.com",
        }
    )

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_endpoints.list_all_workspaces",
            new=AsyncMock(return_value=[ep]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_sql_endpoints",
            {"workspace": "", "all_workspaces": True},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == str(ep_id)
    assert result[0]["kind"] == "SQLEndpoint"


# ---------------------------------------------------------------------------
# all_workspaces=True blocked when FABRIC_MCP_WORKSPACES is set (M18)
# ---------------------------------------------------------------------------


async def test_list_warehouses_all_workspaces_blocked_by_allowlist(ctx_patch) -> None:
    """all_workspaces=True raises ToolError when FABRIC_MCP_WORKSPACES is set (warehouses)."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "my-ws"}),
        pytest.raises(ToolError, match="not permitted"),
    ):
        await mcp._tool_manager.call_tool(
            "list_warehouses", {"workspace": "", "all_workspaces": True}
        )


async def test_list_sql_endpoints_all_workspaces_blocked_by_allowlist(ctx_patch) -> None:
    """all_workspaces=True raises ToolError when FABRIC_MCP_WORKSPACES is set (sql_endpoints)."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "my-ws"}),
        pytest.raises(ToolError, match="not permitted"),
    ):
        await mcp._tool_manager.call_tool(
            "list_sql_endpoints", {"workspace": "", "all_workspaces": True}
        )


# ---------------------------------------------------------------------------
# add_audit_group / remove_audit_group happy paths
# ---------------------------------------------------------------------------


async def test_add_audit_group_happy_path(mock_ctx, ctx_patch) -> None:
    """add_audit_group resolves workspace + warehouse and returns a dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings()
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.add_action_group",
            new=AsyncMock(return_value=settings),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "add_audit_group",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )

    assert isinstance(result, dict)
    assert result["state"] == "Enabled"
    mock_ctx.resolver.item.assert_called_once_with(str(_WS_ID), _WH_NAME)


async def test_remove_audit_group_happy_path(mock_ctx, ctx_patch) -> None:
    """remove_audit_group resolves workspace + warehouse and returns a dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings()
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.remove_action_group",
            new=AsyncMock(return_value=settings),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "remove_audit_group",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "group": "BATCH_COMPLETED_GROUP"},
        )

    assert isinstance(result, dict)
    assert result["state"] == "Enabled"
    mock_ctx.resolver.item.assert_called_once_with(str(_WS_ID), _WH_NAME)


# ---------------------------------------------------------------------------
# set_audit_retention happy path
# ---------------------------------------------------------------------------


async def test_set_audit_retention_happy_path(mock_ctx, ctx_patch) -> None:
    """set_audit_retention resolves workspace + warehouse and returns updated AuditSettings."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    updated = AuditSettings.model_validate(
        {
            "state": "Enabled",
            "retentionDays": 90,
            "auditActionsAndGroups": ["BATCH_COMPLETED_GROUP"],
        }
    )
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.set_retention",
            new=AsyncMock(return_value=updated),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_audit_retention",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "days": 90},
        )

    assert isinstance(result, dict)
    assert result["retentionDays"] == 90
    assert result["state"] == "Enabled"


async def test_set_audit_retention_value_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """set_audit_retention converts ValueError (disabled audit or out-of-range) to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.audit.set_retention",
            new=AsyncMock(side_effect=ValueError("audit is disabled; enable first")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "set_audit_retention",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "days": 30},
        )

    assert "disabled" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# execute_sql tool
# ---------------------------------------------------------------------------


async def test_execute_sql_happy_path(mock_ctx, ctx_patch) -> None:
    """execute_sql calls sql_exec.execute and returns a dict with columns/rows/rowcount."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import SqlResult  # noqa: PLC0415

    sql_result = SqlResult(columns=["id", "name"], rows=[[1, "foo"], [2, "bar"]], rowcount=2)
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_exec.execute",
            new=AsyncMock(return_value=sql_result),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "execute_sql",
            {"workspace": _WS_NAME, "item": _WH_NAME, "query": "SELECT id, name FROM t"},
        )

    assert isinstance(result, dict)
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [[1, "foo"], [2, "bar"]]
    assert result["rowcount"] == 2


async def test_execute_sql_no_connection_string_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """execute_sql raises ToolError when the item has no connection string."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry(connection_string=None)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "execute_sql",
            {"workspace": _WS_NAME, "item": _WH_NAME, "query": "SELECT 1"},
        )


# ---------------------------------------------------------------------------
# Permissions tools
# ---------------------------------------------------------------------------


async def test_get_warehouse_permissions_happy_path(mock_ctx, ctx_patch) -> None:
    """get_warehouse_permissions returns a list of serialised ItemAccess dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    access = _make_item_access()
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.permissions.list_item_access",
            new=AsyncMock(return_value=[access]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_warehouse_permissions",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["principal"]["displayName"] == "Jacob Hancock"


async def test_get_sql_endpoint_permissions_happy_path(mock_ctx, ctx_patch) -> None:
    """get_sql_endpoint_permissions returns a list of serialised ItemAccess dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    access = _make_item_access()
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.permissions.list_item_access",
            new=AsyncMock(return_value=[access]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_sql_endpoint_permissions",
            {"workspace": _WS_NAME, "sql_endpoint": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["principal"]["type"] == "User"


async def test_get_warehouse_permissions_permission_denied_becomes_tool_error(
    mock_ctx, ctx_patch
) -> None:
    """get_warehouse_permissions wraps PermissionDeniedError into ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.permissions.list_item_access",
            new=AsyncMock(side_effect=PermissionDeniedError("Fabric Administrator")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_warehouse_permissions",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


# ---------------------------------------------------------------------------
# SQL Endpoint guard -- create_table / delete_table / clear_table via MCP
# ---------------------------------------------------------------------------

_SE_NAME = "SalesLakehouse"
_SE_ID = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")


async def test_create_table_sql_endpoint_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """create_table must raise ToolError when the item is a SQL Endpoint."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(
        return_value=_make_sql_endpoint_entry(item_id=_SE_ID, display_name=_SE_NAME)
    )

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_table",
            {
                "workspace": _WS_NAME,
                "item": _SE_NAME,
                "qualified_name": "dbo.sales",
                "select_body": "SELECT id FROM src.raw",
            },
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_delete_table_sql_endpoint_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """delete_table must raise ToolError when the item is a SQL Endpoint.

    FABRIC_MCP_ALLOW_DESTRUCTIVE=1 is set so the destructive guard passes and
    the SQL-endpoint read-only guard fires instead.
    """
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(
        return_value=_make_sql_endpoint_entry(item_id=_SE_ID, display_name=_SE_NAME)
    )

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "delete_table",
            {"workspace": _WS_NAME, "item": _SE_NAME, "qualified_name": "dbo.sales"},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_clear_table_sql_endpoint_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """clear_table must raise ToolError when the item is a SQL Endpoint.

    FABRIC_MCP_ALLOW_DESTRUCTIVE=1 is set so the destructive guard passes and
    the SQL-endpoint read-only guard fires instead.
    """
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(
        return_value=_make_sql_endpoint_entry(item_id=_SE_ID, display_name=_SE_NAME)
    )

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "clear_table",
            {"workspace": _WS_NAME, "item": _SE_NAME, "qualified_name": "dbo.sales"},
        )

    assert "read-only" in str(exc_info.value).lower()


def _make_clone_table() -> Table:
    _now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    return Table(
        schema_name="dbo",
        name="sales_clone",
        qualified_name="dbo.sales_clone",
        created=_now,
        modified=_now,
    )


async def test_clone_table_happy_path(mock_ctx, ctx_patch) -> None:
    """clone_table resolves workspace + item and returns a Table dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=_make_item_entry())
    mock_clone = AsyncMock(return_value=_make_clone_table())

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.clone_table", new=mock_clone),
    ):
        result = await mcp._tool_manager.call_tool(
            "clone_table",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "source": "dbo.sales",
                "new_table": "dbo.sales_clone",
            },
        )

    assert isinstance(result, dict)
    assert result["name"] == "sales_clone"
    mock_clone.assert_called_once()
    _, kwargs = mock_clone.call_args
    assert kwargs.get("at") is None


async def test_clone_table_with_at_timestamp(mock_ctx, ctx_patch) -> None:
    """clone_table passes a parsed datetime to the service when --at is supplied."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=_make_item_entry())
    mock_clone = AsyncMock(return_value=_make_clone_table())

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.clone_table", new=mock_clone),
    ):
        result = await mcp._tool_manager.call_tool(
            "clone_table",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "source": "dbo.sales",
                "new_table": "dbo.sales_clone",
                "at": "2024-05-20T14:00:00",
            },
        )

    assert isinstance(result, dict)
    mock_clone.assert_called_once()
    _, kwargs = mock_clone.call_args
    assert kwargs.get("at") is not None


async def test_clone_table_readonly_mode_blocked(ctx_patch) -> None:
    """clone_table must raise ToolError in readonly mode (FABRIC_MCP_READONLY=1)."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "clone_table",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "source": "dbo.sales",
                "new_table": "dbo.sales_clone",
            },
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_clone_table_workspace_not_in_allowlist(ctx_patch) -> None:
    """clone_table must raise ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "clone_table",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "source": "dbo.sales",
                "new_table": "dbo.sales_clone",
            },
        )

    assert "allowlist" in str(exc_info.value).lower()


async def test_clone_table_sql_endpoint_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """clone_table must raise ToolError when the item is a SQL Endpoint."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(
        return_value=_make_sql_endpoint_entry(item_id=_SE_ID, display_name=_SE_NAME)
    )

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "clone_table",
            {
                "workspace": _WS_NAME,
                "item": _SE_NAME,
                "source": "dbo.sales",
                "new_table": "dbo.sales_clone",
            },
        )

    assert "read-only" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Schema tool SQL Endpoint guard
# ---------------------------------------------------------------------------


async def test_create_schema_works_on_sql_endpoint(mock_ctx, ctx_patch) -> None:
    """create_schema is allowed on SQL Analytics Endpoints.

    CREATE SCHEMA is listed in the Applies-to for 'SQL analytics endpoint in
    Microsoft Fabric' in the Fabric T-SQL reference — the client-side guard
    was too strict and has been removed.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import Schema  # noqa: PLC0415

    ep_entry = _make_sql_endpoint_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=ep_entry)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.schemas.create_schema",
            new=AsyncMock(return_value=Schema(name="newschema", principal_id=5)),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_schema",
            {"workspace": _WS_NAME, "item": "MySqlEndpoint", "name": "newschema"},
        )

    assert result["name"] == "newschema"


async def test_delete_schema_works_on_sql_endpoint(mock_ctx, ctx_patch) -> None:
    """delete_schema is allowed on SQL Analytics Endpoints.

    DROP SCHEMA is listed in the Applies-to for 'SQL analytics endpoint in
    Microsoft Fabric' in the Fabric T-SQL reference — the client-side guard
    was too strict and has been removed.

    FABRIC_MCP_ALLOW_DESTRUCTIVE=1 is set so the destructive guard passes.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep_entry = _make_sql_endpoint_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=ep_entry)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.schemas.delete_schema",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "delete_schema",
            {"workspace": _WS_NAME, "item": "MySqlEndpoint", "name": "oldschema"},
        )

    assert result == {"deleted": True}


async def test_list_schemas_works_on_sql_endpoint(mock_ctx, ctx_patch) -> None:
    """list_schemas is a read-only operation and must work on SQL Analytics Endpoints."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import Schema  # noqa: PLC0415

    ep_entry = _make_sql_endpoint_entry()
    schemas_result = [Schema(name="dbo", principal_id=1)]
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=ep_entry)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.schemas.list_schemas",
            new=AsyncMock(return_value=schemas_result),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_schemas",
            {"workspace": _WS_NAME, "item": "MySqlEndpoint"},
        )

    assert isinstance(result, list)
    assert result[0]["name"] == "dbo"


async def test_delete_schema_cascade_sql_endpoint_succeeds(mock_ctx, ctx_patch) -> None:
    """delete_schema with cascade=True on a SQL Analytics Endpoint must SUCCEED.

    The service drops views/procedures/functions and skips tables (Warehouse-only).
    No error is raised at the MCP layer.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep_entry = _make_sql_endpoint_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=ep_entry)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.schemas.delete_schema",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "delete_schema",
            {"workspace": _WS_NAME, "item": "MySqlEndpoint", "name": "oldschema", "cascade": True},
        )

    assert result == {"deleted": True}


async def test_delete_schema_no_cascade_sql_endpoint_succeeds(mock_ctx, ctx_patch) -> None:
    """delete_schema with cascade=False on a SQL Analytics Endpoint must succeed.

    DROP SCHEMA (without cascade) is valid on endpoints.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep_entry = _make_sql_endpoint_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=ep_entry)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.schemas.delete_schema",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "delete_schema",
            {
                "workspace": _WS_NAME,
                "item": "MySqlEndpoint",
                "name": "oldschema",
                "cascade": False,
            },
        )

    assert result == {"deleted": True}


async def test_delete_schema_passes_kind_to_service(mock_ctx, ctx_patch) -> None:
    """delete_schema must forward entry.kind to the service layer."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()  # WAREHOUSE kind
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_delete = AsyncMock(return_value=None)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch("fabric_dw.services.schemas.delete_schema", new=mock_delete),
    ):
        await mcp._tool_manager.call_tool(
            "delete_schema",
            {"workspace": _WS_NAME, "item": _WH_NAME, "name": "myschema"},
        )

    _, kwargs = mock_delete.call_args
    assert kwargs.get("kind") == WarehouseKind.WAREHOUSE


async def test_delete_schema_passes_sql_endpoint_kind_to_service(mock_ctx, ctx_patch) -> None:
    """delete_schema must forward kind=SQL_ENDPOINT when the entry is a SQL Analytics Endpoint.

    Without this test, a bug that always passed kind=WAREHOUSE would not be caught
    by the Warehouse-only test above.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_sql_endpoint_entry()  # SQL_ENDPOINT kind
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_delete = AsyncMock(return_value=None)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch("fabric_dw.services.schemas.delete_schema", new=mock_delete),
    ):
        await mcp._tool_manager.call_tool(
            "delete_schema",
            {"workspace": _WS_NAME, "item": "MySqlEndpoint", "name": "myschema"},
        )

    _, kwargs = mock_delete.call_args
    assert kwargs.get("kind") == WarehouseKind.SQL_ENDPOINT


async def test_read_view_happy_path(mock_ctx, ctx_patch) -> None:
    """read_view calls views_svc.read_view and returns {columns, rows}."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.read_view",
            new=AsyncMock(return_value=(["id", "amount"], [(1, 100), (2, 200)])),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "read_view",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "dbo.vw_sales"},
        )

    assert isinstance(result, dict)
    assert result["columns"] == ["id", "amount"]
    assert result["rows"] == [[1, 100], [2, 200]]


async def test_read_view_no_connection_string_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """read_view raises ToolError when the item has no connection string."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry(connection_string=None)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "read_view",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "dbo.vw_sales"},
        )


async def test_read_view_bad_qualified_name_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """read_view raises ToolError when qualified_name has no dot."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "read_view",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "nodot"},
        )


# ---------------------------------------------------------------------------
# rename_table MCP tool tests
# ---------------------------------------------------------------------------


async def test_rename_table_happy_path(mock_ctx, ctx_patch) -> None:
    """rename_table returns the renamed Table model dict on success."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import Table  # noqa: PLC0415

    renamed = Table(
        schema_name="dbo",
        name="sales_v2",
        qualified_name="dbo.sales_v2",
        created=datetime(2024, 6, 1, tzinfo=UTC),
        modified=datetime(2024, 6, 1, tzinfo=UTC),
    )

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.rename_table",
            new=AsyncMock(return_value=renamed),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "rename_table",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.sales",
                "new_name": "sales_v2",
            },
        )

    assert result["name"] == "sales_v2"
    assert result["schema_name"] == "dbo"


async def test_rename_table_readonly_blocked(mock_ctx, ctx_patch) -> None:
    """rename_table raises ToolError when FABRIC_MCP_READONLY is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "rename_table",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.sales",
                "new_name": "sales_v2",
            },
        )


async def test_rename_table_sql_endpoint_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """rename_table must raise ToolError when the item is a SQL Analytics Endpoint."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    _se_id = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(
        return_value=make_sql_endpoint_entry(item_id=_se_id, display_name="SalesLakehouse")
    )

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "rename_table",
            {
                "workspace": _WS_NAME,
                "item": "SalesLakehouse",
                "qualified_name": "dbo.sales",
                "new_name": "sales_v2",
            },
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_rename_table_workspace_allowlist_blocks(mock_ctx, ctx_patch) -> None:
    """rename_table raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="FABRIC_MCP_WORKSPACES"),
    ):
        await mcp._tool_manager.call_tool(
            "rename_table",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.sales",
                "new_name": "sales_v2",
            },
        )


async def test_rename_table_undotted_qualified_name_raises_tool_error(
    mock_ctx,  # noqa: ARG001
    ctx_patch,
) -> None:
    """rename_table must raise ToolError immediately for an undotted qualified_name."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError, match="qualified_name"),
    ):
        await mcp._tool_manager.call_tool(
            "rename_table",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "nodot",
                "new_name": "sales_v2",
            },
        )


# ---------------------------------------------------------------------------
# rename_view MCP tool tests
# ---------------------------------------------------------------------------


async def test_rename_view_happy_path(mock_ctx, ctx_patch) -> None:
    """rename_view calls views_svc.rename_view and returns the renamed View."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import View  # noqa: PLC0415

    renamed_view = View(
        schema_name="dbo",
        name="vw_revenue",
        qualified_name="dbo.vw_revenue",
        definition=None,
        created=datetime.now(tz=UTC),
        modified=datetime.now(tz=UTC),
    )
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.rename_view",
            new=AsyncMock(return_value=renamed_view),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "new_name": "vw_revenue",
            },
        )

    assert isinstance(result, dict)
    assert result["name"] == "vw_revenue"
    assert result["schema_name"] == "dbo"


async def test_rename_view_blocked_by_readonly(ctx_patch) -> None:
    """rename_view raises ToolError when FABRIC_MCP_READONLY is set."""
    import os  # noqa: PLC0415

    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only mode"),
    ):
        await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "new_name": "vw_revenue",
            },
        )


async def test_rename_view_blocked_by_workspace_allowlist(ctx_patch) -> None:
    """rename_view raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    import os  # noqa: PLC0415

    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "allowed-ws"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": "forbidden-ws",
                "item": _WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "new_name": "vw_revenue",
            },
        )


async def test_rename_view_accepts_sql_endpoint_item(mock_ctx, ctx_patch) -> None:
    """rename_view must not reject SQL Analytics Endpoint items (no DW-only guard)."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import View  # noqa: PLC0415

    renamed_view = View(
        schema_name="dbo",
        name="vw_revenue",
        qualified_name="dbo.vw_revenue",
        definition=None,
        created=datetime.now(tz=UTC),
        modified=datetime.now(tz=UTC),
    )
    ep_entry = make_item_entry(kind=WarehouseKind.SQL_ENDPOINT)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=ep_entry)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.views.rename_view",
            new=AsyncMock(return_value=renamed_view),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.vw_sales",
                "new_name": "vw_revenue",
            },
        )

    assert result["name"] == "vw_revenue"


async def test_rename_view_bad_qualified_name_raises_tool_error(ctx_patch) -> None:
    """rename_view raises ToolError when qualified_name has no dot."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "rename_view",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "nodot",
                "new_name": "vw_revenue",
            },
        )


# ---------------------------------------------------------------------------
# get_query_plan tool
# ---------------------------------------------------------------------------

_PLAN_XML = (
    "<ShowPlanXML xmlns='http://schemas.microsoft.com/sqlserver/2004/07/showplan'>"
    "<Batch><Statements><StmtSimple /></Statements></Batch></ShowPlanXML>"
)


async def test_get_query_plan_happy_path(mock_ctx, ctx_patch) -> None:
    """get_query_plan calls sql_exec.get_plan and returns plan_xml dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_exec.get_plan",
            new=AsyncMock(return_value=_PLAN_XML),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_query_plan",
            {"workspace": _WS_NAME, "item": _WH_NAME, "query": "SELECT 1"},
        )

    assert isinstance(result, dict)
    assert result["plan_xml"] == _PLAN_XML


async def test_get_query_plan_allowed_under_readonly(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_query_plan is permitted even when FABRIC_MCP_READONLY=1 (read-only safe)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_READONLY", "1")

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_exec.get_plan",
            new=AsyncMock(return_value=_PLAN_XML),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_query_plan",
            {"workspace": _WS_NAME, "item": _WH_NAME, "query": "SELECT 1"},
        )

    # Must succeed (not raise ToolError) under readonly
    assert isinstance(result, dict)
    assert "plan_xml" in result


async def test_get_query_plan_no_connection_string_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """get_query_plan raises ToolError when the item has no connection string."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry(connection_string=None)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_query_plan",
            {"workspace": _WS_NAME, "item": _WH_NAME, "query": "SELECT 1"},
        )


# ===========================================================================
# set_cluster_columns
# ===========================================================================


def _make_reclustered_table() -> Table:
    _now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    return Table(
        schema_name="dbo",
        name="sales",
        qualified_name="dbo.sales",
        created=_now,
        modified=_now,
    )


async def test_set_cluster_columns_requires_destructive_flag() -> None:
    """set_cluster_columns raises ToolError without FABRIC_MCP_ALLOW_DESTRUCTIVE=1."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    env_copy = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_DESTRUCTIVE"}
    with (
        patch.dict(os.environ, env_copy, clear=True),
        pytest.raises(ToolError, match="FABRIC_MCP_ALLOW_DESTRUCTIVE"),
    ):
        await mcp._tool_manager.call_tool(
            "set_cluster_columns",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.sales",
                "cluster_by": ["CustomerID"],
            },
        )


async def test_set_cluster_columns_happy_path(mock_ctx, ctx_patch) -> None:
    """set_cluster_columns resolves workspace + item and returns a Table dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=_make_item_entry())
    mock_recluster = AsyncMock(return_value=_make_reclustered_table())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch("fabric_dw.services.tables.recluster_table", new=mock_recluster),
    ):
        result = await mcp._tool_manager.call_tool(
            "set_cluster_columns",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.sales",
                "cluster_by": ["CustomerID", "SaleDate"],
            },
        )

    assert isinstance(result, dict)
    assert result["name"] == "sales"
    mock_recluster.assert_called_once()
    _, kwargs = mock_recluster.call_args
    assert kwargs.get("cluster_by") == ["CustomerID", "SaleDate"]


async def test_set_cluster_columns_sql_endpoint_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """set_cluster_columns raises ToolError when the item is a SQL Endpoint.

    FABRIC_MCP_ALLOW_DESTRUCTIVE=1 is set so the destructive guard passes and
    the SQL-endpoint clustering guard fires instead.
    """
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(
        return_value=_make_sql_endpoint_entry(item_id=_WH_ID, display_name="SalesLakehouse")
    )

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "set_cluster_columns",
            {
                "workspace": _WS_NAME,
                "item": "SalesLakehouse",
                "qualified_name": "dbo.sales",
            },
        )

    assert "clustering" in str(exc_info.value).lower()
