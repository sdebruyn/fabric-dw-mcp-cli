"""Tests for the MCP server — written BEFORE the implementation (TDD).

Testing strategy
----------------
FastMCP 1.x ships no in-process test transport in its public API (as of
mcp==1.27.2), so we fall back to **unit-style mocking**:

1. ``test_tools_registered`` — import ``mcp`` from the server module, inspect
   ``_tool_manager._tools`` and assert every expected tool name is present.
2. ``test_list_workspaces_happy_path`` — call the tool function directly via
   ``mcp._tool_manager.call_tool(...)`` with a mocked service layer. Verify
   the returned value is a ``list[dict]`` with the expected keys.
3. ``test_clear_cache_side_effect`` — call ``clear_cache`` tool and assert
   that the underlying ``LookupCache.clear()`` method was called once.
4. ``test_fabric_error_becomes_tool_error`` — inject a service that raises
   ``FabricError`` and assert the tool re-raises ``ToolError``.
5. ``test_run_exposes_stdio_as_default`` — ensure ``run()`` from
   ``fabric_dw.mcp`` invokes ``FastMCP.run`` with transport="stdio".
6. ``test_run_accepts_http_transport`` — ensure ``--transport http`` calls
   ``FastMCP.run`` with transport="streamable-http".
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from fabric_dw.cache import ItemEntry
from fabric_dw.exceptions import NotFound
from fabric_dw.models import (
    AuditSettings,
    RunningQuery,
    TableSyncStatus,
    Warehouse,
    WarehouseKind,
    WarehouseSnapshot,
    Workspace,
)

# ---------------------------------------------------------------------------
# Expected tool names (canonical list — test fails if any are missing/typo'd)
# ---------------------------------------------------------------------------

EXPECTED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # Workspaces
        "list_workspaces",
        "get_workspace",
        "set_workspace_collation",
        # Warehouses
        "list_warehouses",
        "get_warehouse",
        "create_warehouse",
        "rename_warehouse",
        "delete_warehouse",
        "takeover_warehouse",
        # SQL Endpoints
        "list_sql_endpoints",
        "get_sql_endpoint",
        "refresh_sql_endpoint_metadata",
        # Audit
        "get_audit_settings",
        "enable_audit",
        "disable_audit",
        "set_audit_action_groups",
        "add_audit_group",
        "remove_audit_group",
        "set_audit_retention",
        # Queries
        "list_running_queries",
        "kill_session",
        # Query Insights
        "list_request_history",
        "list_session_history",
        "list_frequent_queries",
        "list_long_running_queries",
        "list_sql_pool_insights",
        # Generic SQL execution
        "execute_sql",
        # Snapshots
        "list_snapshots",
        "create_snapshot",
        "rename_snapshot",
        "delete_snapshot",
        "roll_snapshot_timestamp",
        # Cache
        "clear_cache",
    }
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_WH_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
_SNAP_ID = UUID("e5f6a7b8-c9d0-1234-ef01-23456789abcd")

_WS_NAME = "my-workspace"
_WH_NAME = "my-warehouse"


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
    return ItemEntry(
        id=item_id,
        kind=WarehouseKind.WAREHOUSE,
        connection_string=connection_string,
        fetched_at=datetime.now(tz=UTC),
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


# ---------------------------------------------------------------------------
# 1. Tool registration
# ---------------------------------------------------------------------------


def test_tools_registered() -> None:
    """Every expected tool name must be registered in the FastMCP server."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    registered = set(mcp._tool_manager._tools.keys())
    missing = EXPECTED_TOOL_NAMES - registered
    assert not missing, f"Missing tools: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 2. list_workspaces happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_workspaces_happy_path() -> None:
    """list_workspaces returns a list of serialised workspace dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ws = _make_workspace()

    mock_resolver = MagicMock()
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_clear_cache_side_effect() -> None:
    """clear_cache must call LookupCache.clear() exactly once."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_cache = MagicMock()

    with patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache):
        result = await mcp._tool_manager.call_tool("clear_cache", {})

    mock_cache.clear.assert_called_once()
    assert result == {"cleared": True}


# ---------------------------------------------------------------------------
# 4. FabricError translates into ToolError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fabric_error_becomes_tool_error() -> None:
    """A FabricError raised by the service layer must become a ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_http = AsyncMock()
    mock_resolver = MagicMock()
    mock_cache = MagicMock()

    not_found_error = NotFound("workspace 'x' not found")

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(side_effect=not_found_error),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool("list_workspaces", {})

    err = exc_info.value
    assert "NotFound" in str(err) or "not found" in str(err).lower()


# ---------------------------------------------------------------------------
# 5. get_workspace happy path (resolver usage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_workspace_happy_path() -> None:
    """get_workspace resolves the name via Resolver and returns a dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ws = _make_workspace()

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
        patch("fabric_dw.services.workspaces.get", new=AsyncMock(return_value=ws)),
    ):
        result = await mcp._tool_manager.call_tool("get_workspace", {"workspace": _WS_NAME})

    assert isinstance(result, dict)
    assert result["id"] == str(_WS_ID)
    mock_resolver.workspace_id.assert_called_once_with(_WS_NAME)


# ---------------------------------------------------------------------------
# 7. list_warehouses happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_warehouses_happy_path() -> None:
    """list_warehouses resolves workspace and returns list of warehouse dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    wh = _make_warehouse()

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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
# 9. run() accepts --transport http → streamable-http
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


@pytest.mark.asyncio
async def test_get_audit_settings_happy_path() -> None:
    """get_audit_settings resolves workspace + warehouse and returns a dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings()
    item = _make_item_entry()

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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
    mock_resolver.item.assert_called_once_with(_WS_NAME, _WH_NAME)


# ---------------------------------------------------------------------------
# 11. list_running_queries happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_running_queries_happy_path() -> None:
    """list_running_queries returns list of dicts from the SQL service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    query = _make_running_query()
    item = _make_item_entry()

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
        patch(
            "fabric_dw.services.queries.list_running",
            new=AsyncMock(return_value=[query]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_running_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert isinstance(result, list)
    assert result[0]["session_id"] == 42


# ---------------------------------------------------------------------------
# 12. Service ValueError does NOT become ToolError (propagates as-is per FastMCP)
#     but a FabricError does.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_found_error_becomes_tool_error() -> None:
    """NotFound (a FabricError subclass) must become a ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(side_effect=NotFound("workspace 'boom' not found"))
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool("get_workspace", {"workspace": "boom"})


# ---------------------------------------------------------------------------
# 13. Bad ISO-8601 input → ToolError (not raw ValueError)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_snapshot_bad_datetime_becomes_tool_error() -> None:
    """create_snapshot raises ToolError when snapshot_dt is not ISO-8601."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_resolver = AsyncMock()
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_roll_snapshot_timestamp_bad_datetime_becomes_tool_error() -> None:
    """roll_snapshot_timestamp raises ToolError when new_dt is not ISO-8601."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_resolver = AsyncMock()
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_list_sql_endpoints_happy_path() -> None:
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

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_get_sql_endpoint_happy_path() -> None:
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

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_refresh_sql_endpoint_metadata_happy_path() -> None:
    """refresh_sql_endpoint_metadata resolves workspace + endpoint and returns a list of dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep_id = UUID("e5f6a7b8-c9d0-1234-ef01-234567890abc")
    item = _make_item_entry(item_id=ep_id, display_name="SalesLakehouse")

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_refresh_sql_endpoint_metadata_recreate_tables() -> None:
    """refresh_sql_endpoint_metadata passes recreate_tables=True to the service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep_id = UUID("e5f6a7b8-c9d0-1234-ef01-234567890abc")
    item = _make_item_entry(item_id=ep_id, display_name="SalesLakehouse")

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()
    mock_refresh = AsyncMock(return_value=_make_table_sync_statuses())

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_list_warehouses_all_workspaces() -> None:
    """list_warehouses with all_workspaces=True dispatches to list_all_workspaces."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    wh = _make_warehouse()

    mock_resolver = AsyncMock()
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_list_sql_endpoints_all_workspaces() -> None:
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

    mock_resolver = AsyncMock()
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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
# add_audit_group / remove_audit_group happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_audit_group_happy_path() -> None:
    """add_audit_group resolves workspace + warehouse and returns a dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings()
    item = _make_item_entry()

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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
    mock_resolver.item.assert_called_once_with(_WS_NAME, _WH_NAME)


@pytest.mark.asyncio
async def test_remove_audit_group_happy_path() -> None:
    """remove_audit_group resolves workspace + warehouse and returns a dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    settings = _make_audit_settings()
    item = _make_item_entry()

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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
    mock_resolver.item.assert_called_once_with(_WS_NAME, _WH_NAME)


# ---------------------------------------------------------------------------
# set_audit_retention happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_audit_retention_happy_path() -> None:
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

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_set_audit_retention_value_error_becomes_tool_error() -> None:
    """set_audit_retention converts ValueError (disabled audit or out-of-range) to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_execute_sql_happy_path() -> None:
    """execute_sql calls sql_exec.execute and returns a dict with columns/rows/rowcount."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import SqlResult  # noqa: PLC0415

    sql_result = SqlResult(columns=["id", "name"], rows=[[1, "foo"], [2, "bar"]], rowcount=2)
    item = _make_item_entry()

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_execute_sql_no_connection_string_raises_tool_error() -> None:
    """execute_sql raises ToolError when the item has no connection string."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry(connection_string=None)

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=item)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "execute_sql",
            {"workspace": _WS_NAME, "item": _WH_NAME, "query": "SELECT 1"},
        )


# ---------------------------------------------------------------------------
# SQL Endpoint guard — create_table / delete_table / clear_table via MCP
# ---------------------------------------------------------------------------

_SE_NAME = "SalesLakehouse"
_SE_ID = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")


def _make_sql_endpoint_entry() -> ItemEntry:
    return ItemEntry(
        id=_SE_ID,
        kind=WarehouseKind.SQL_ENDPOINT,
        connection_string="lakehouse.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name=_SE_NAME,
    )


@pytest.mark.asyncio
async def test_create_table_sql_endpoint_raises_tool_error() -> None:
    """create_table must raise ToolError when the item is a SQL Endpoint."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=_make_sql_endpoint_entry())
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
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


@pytest.mark.asyncio
async def test_delete_table_sql_endpoint_raises_tool_error() -> None:
    """delete_table must raise ToolError when the item is a SQL Endpoint."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=_make_sql_endpoint_entry())
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "delete_table",
            {"workspace": _WS_NAME, "item": _SE_NAME, "qualified_name": "dbo.sales"},
        )

    assert "read-only" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_clear_table_sql_endpoint_raises_tool_error() -> None:
    """clear_table must raise ToolError when the item is a SQL Endpoint."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=_make_sql_endpoint_entry())
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    with (
        patch("fabric_dw.mcp.server._get_http", return_value=mock_http),
        patch("fabric_dw.mcp.server._get_resolver", return_value=mock_resolver),
        patch("fabric_dw.mcp.server._get_cache", return_value=mock_cache),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "clear_table",
            {"workspace": _WS_NAME, "item": _SE_NAME, "qualified_name": "dbo.sales"},
        )

    assert "read-only" in str(exc_info.value).lower()
