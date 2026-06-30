"""Unit tests for MCP procedures tools.

Tests cover:
- Happy path for list/get/create/update/drop
- readonly guard blocks create/update/drop
- destructive guard blocks drop
- workspace allowlist enforcement
- No endpoint rejection — procedures work on both DW and SQL endpoint
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from fabric_dw.cache import ItemEntry
from fabric_dw.models import StoredProcedure, WarehouseKind

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_WH_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
_WS_NAME = "my-workspace"
_WH_NAME = "my-warehouse"
_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_item_entry(
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    connection_string: str | None = "wh.fabric.microsoft.com",
) -> ItemEntry:
    return ItemEntry(
        id=_WH_ID,
        kind=kind,
        connection_string=connection_string,
        fetched_at=datetime.now(tz=UTC),
        display_name=_WH_NAME,
    )


def _make_proc(*, with_definition: bool = True) -> StoredProcedure:
    return StoredProcedure(
        schema_name="dbo",
        name="usp_load",
        qualified_name="dbo.usp_load",
        definition="BEGIN SELECT 1 AS id END" if with_definition else None,
        created=_NOW,
        modified=_NOW,
    )


# ===========================================================================
# list_procedures
# ===========================================================================


async def test_list_procedures_happy_path(mock_ctx, ctx_patch) -> None:
    """list_procedures calls procedures_svc.list_procedures and returns serialised list."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.procedures.list_procedures",
            new=AsyncMock(return_value=[_make_proc()]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_procedures",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "usp_load"


async def test_list_procedures_with_schema_filter(mock_ctx, ctx_patch) -> None:
    """list_procedures passes schema filter to the service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_list = AsyncMock(return_value=[])

    with (
        ctx_patch,
        patch("fabric_dw.services.procedures.list_procedures", new=mock_list),
    ):
        await mcp._tool_manager.call_tool(
            "list_procedures",
            {"workspace": _WS_NAME, "item": _WH_NAME, "schema": "dbo"},
        )

    mock_list.assert_awaited_once()
    _, kwargs = mock_list.call_args
    assert kwargs.get("schema") == "dbo"


async def test_list_procedures_on_sql_endpoint_no_error(mock_ctx, ctx_patch) -> None:
    """list_procedures must NOT raise a ToolError for SQL Analytics Endpoint items.

    This asserts the absence of a DW-only guard: procedures work on both
    Fabric Data Warehouses and SQL Analytics Endpoints.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    # Use a SQL endpoint item
    item = _make_item_entry(kind=WarehouseKind.SQL_ENDPOINT)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.procedures.list_procedures",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_procedures",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )

    assert isinstance(result, list)


async def test_list_procedures_workspace_allowlist_blocks(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """list_procedures raises ToolError when workspace is not in the allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_WORKSPACES", "other-workspace")
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with ctx_patch, pytest.raises(ToolError):
        await mcp._tool_manager.call_tool(
            "list_procedures",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )


# ===========================================================================
# get_procedure
# ===========================================================================


async def test_get_procedure_happy_path(mock_ctx, ctx_patch) -> None:
    """get_procedure returns the procedure dict with definition."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.procedures.get_procedure",
            new=AsyncMock(return_value=_make_proc()),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_procedure",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "dbo.usp_load"},
        )

    assert isinstance(result, dict)
    assert result["name"] == "usp_load"
    assert result["definition"] is not None


async def test_get_procedure_bad_qualified_name_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """get_procedure raises ToolError when qualified_name has no dot."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with ctx_patch, pytest.raises(ToolError):
        await mcp._tool_manager.call_tool(
            "get_procedure",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "nodot"},
        )


# ===========================================================================
# create_procedure
# ===========================================================================


async def test_create_procedure_happy_path(mock_ctx, ctx_patch) -> None:
    """create_procedure returns the newly created procedure."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.procedures.create_procedure",
            new=AsyncMock(return_value=_make_proc()),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_procedure",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.usp_load",
                "body": "BEGIN SELECT 1 END",
            },
        )

    assert isinstance(result, dict)
    assert result["name"] == "usp_load"


async def test_create_procedure_blocked_in_readonly(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create_procedure raises ToolError when FABRIC_MCP_READONLY is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_READONLY", "1")
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with ctx_patch, pytest.raises(ToolError, match="read-only"):
        await mcp._tool_manager.call_tool(
            "create_procedure",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.usp_load",
                "body": "BEGIN END",
            },
        )


async def test_create_procedure_on_sql_endpoint_no_error(mock_ctx, ctx_patch) -> None:
    """create_procedure must NOT raise for SQL Analytics Endpoint — no DW-only guard."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry(kind=WarehouseKind.SQL_ENDPOINT)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.procedures.create_procedure",
            new=AsyncMock(return_value=_make_proc()),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_procedure",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.usp_load",
                "body": "BEGIN SELECT 1 END",
            },
        )

    assert isinstance(result, dict)


# ===========================================================================
# update_procedure
# ===========================================================================


async def test_update_procedure_happy_path(mock_ctx, ctx_patch) -> None:
    """update_procedure returns the updated procedure."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.procedures.update_procedure",
            new=AsyncMock(return_value=_make_proc()),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "update_procedure",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.usp_load",
                "body": "BEGIN SELECT 2 END",
            },
        )

    assert isinstance(result, dict)
    assert result["name"] == "usp_load"


async def test_update_procedure_blocked_in_readonly(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """update_procedure raises ToolError when FABRIC_MCP_READONLY is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_READONLY", "1")
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with ctx_patch, pytest.raises(ToolError, match="read-only"):
        await mcp._tool_manager.call_tool(
            "update_procedure",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.usp_load",
                "body": "BEGIN END",
            },
        )


# ===========================================================================
# drop_procedure
# ===========================================================================


async def test_drop_procedure_happy_path(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drop_procedure returns {dropped: True} when FABRIC_MCP_ALLOW_DESTRUCTIVE is set."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", "1")
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.procedures.drop_procedure",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "drop_procedure",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "dbo.usp_load"},
        )

    assert result == {"dropped": True}


async def test_drop_procedure_blocked_without_destructive_flag(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drop_procedure raises ToolError when FABRIC_MCP_ALLOW_DESTRUCTIVE is not set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", raising=False)
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with ctx_patch, pytest.raises(ToolError, match="destructive"):
        await mcp._tool_manager.call_tool(
            "drop_procedure",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "dbo.usp_load"},
        )


async def test_drop_procedure_blocked_in_readonly(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drop_procedure raises ToolError when FABRIC_MCP_READONLY is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_READONLY", "1")
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with ctx_patch, pytest.raises(ToolError, match="read-only"):
        await mcp._tool_manager.call_tool(
            "drop_procedure",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "dbo.usp_load"},
        )


async def test_drop_procedure_on_sql_endpoint_no_error(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drop_procedure must NOT raise for SQL Analytics Endpoint — no DW-only guard."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", "1")
    item = _make_item_entry(kind=WarehouseKind.SQL_ENDPOINT)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.procedures.drop_procedure",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "drop_procedure",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "dbo.usp_load"},
        )

    assert result == {"dropped": True}


# ===========================================================================
# transfer_procedure
# ===========================================================================


async def test_transfer_procedure_happy_path(mock_ctx, ctx_patch) -> None:
    """transfer_procedure returns the moved procedure."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    moved = StoredProcedure(
        schema_name="archive",
        name="usp_load",
        qualified_name="archive.usp_load",
        definition="BEGIN SELECT 1 AS id END",
        created=_NOW,
        modified=_NOW,
    )

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.procedures.transfer_procedure",
            new=AsyncMock(return_value=moved),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "transfer_procedure",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.usp_load",
                "target_schema": "archive",
            },
        )

    assert isinstance(result, dict)
    assert result["schema_name"] == "archive"
    assert result["name"] == "usp_load"


async def test_transfer_procedure_forwards_args(mock_ctx, ctx_patch) -> None:
    """transfer_procedure forwards qualified_name and target_schema to the service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_transfer = AsyncMock(return_value=_make_proc())

    with (
        ctx_patch,
        patch("fabric_dw.services.procedures.transfer_procedure", new=mock_transfer),
    ):
        await mcp._tool_manager.call_tool(
            "transfer_procedure",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.usp_load",
                "target_schema": "archive",
            },
        )

    mock_transfer.assert_awaited_once()
    args, kwargs = mock_transfer.call_args
    assert args[1] == "dbo.usp_load"
    assert args[2] == "archive"
    assert kwargs.get("mode") is not None


async def test_transfer_procedure_blocked_in_readonly(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """transfer_procedure raises ToolError when FABRIC_MCP_READONLY is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_READONLY", "1")
    item = _make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with ctx_patch, pytest.raises(ToolError, match="read-only"):
        await mcp._tool_manager.call_tool(
            "transfer_procedure",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.usp_load",
                "target_schema": "archive",
            },
        )


async def test_transfer_procedure_on_sql_endpoint_no_error(mock_ctx, ctx_patch) -> None:
    """transfer_procedure must NOT raise for SQL Analytics Endpoint — no DW-only guard."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = _make_item_entry(kind=WarehouseKind.SQL_ENDPOINT)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.procedures.transfer_procedure",
            new=AsyncMock(return_value=_make_proc()),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "transfer_procedure",
            {
                "workspace": _WS_NAME,
                "item": _WH_NAME,
                "qualified_name": "dbo.usp_load",
                "target_schema": "archive",
            },
        )

    assert isinstance(result, dict)
