"""Unit tests for fabric_dw.mcp.tools.tables — targeting uncovered branches.

Coverage targets (lines from coverage report):
  54, 56-57  list_tables happy path and FabricError funnel
  74-94      read_table happy path (parse name, resolve, make target, service call)
  163        delete_table: return {"dropped": True} (happy-path success path)
  193        clear_table: return {"truncated": True} (happy-path success path)
  224-227    clone_table: at_dt UTC normalisation (naive timestamp → UTC)
  get_cluster_columns: happy path, empty result, SQL endpoint guard, error funnel

NOTE: The SQL-endpoint guard tests for create_table / delete_table / clear_table /
clone_table / rename_table already live in tests/unit/mcp/test_server.py —
those are NOT duplicated here.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.exceptions import ItemKindError, NotFoundError
from fabric_dw.models import ClusterColumn, ResultSet, Table, TableRowCount, WarehouseKind
from tests.unit.mcp.conftest import (
    WH_NAME,
    WS_ID,
    WS_NAME,
    make_item_entry,
    make_sql_endpoint_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_table(schema: str = "dbo", name: str = "sales") -> Table:
    _now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    return Table(
        schema_name=schema,
        name=name,
        qualified_name=f"{schema}.{name}",
        created=_now,
        modified=_now,
    )


# ---------------------------------------------------------------------------
# list_tables — happy path + error funnel (lines 54, 56-57)
# ---------------------------------------------------------------------------


async def test_list_tables_happy_path(mock_ctx, ctx_patch) -> None:
    """list_tables resolves workspace + item, builds sql target and returns list of dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    table = _make_table()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.list_tables",
            new=AsyncMock(return_value=[table]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_tables",
            {"workspace": WS_NAME, "item": WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "sales"
    assert result[0]["schema_name"] == "dbo"


async def test_list_tables_with_schema_filter(mock_ctx, ctx_patch) -> None:
    """list_tables passes schema filter to the service layer."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    table = _make_table(schema="myschema", name="orders")
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_list = AsyncMock(return_value=[table])

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.list_tables", new=mock_list),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_tables",
            {"workspace": WS_NAME, "item": WH_NAME, "schema": "myschema"},
        )

    assert len(result) == 1
    assert result[0]["schema_name"] == "myschema"
    _, kwargs = mock_list.call_args
    assert kwargs.get("schema") == "myschema"


async def test_list_tables_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """list_tables wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.list_tables",
            new=AsyncMock(side_effect=NotFoundError("warehouse not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_tables",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


async def test_list_tables_no_connection_string_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """list_tables raises ToolError when the item has no connection string."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry(connection_string=None)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_tables",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


async def test_list_tables_workspace_allowlist_blocks(ctx_patch) -> None:
    """list_tables raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "list_tables",
            {"workspace": WS_NAME, "item": WH_NAME},
        )


# ---------------------------------------------------------------------------
# read_table — happy path (lines 74-94)
# ---------------------------------------------------------------------------


async def test_read_table_happy_path(mock_ctx, ctx_patch) -> None:
    """read_table resolves item, executes SQL query, and returns columns + rows."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.read_table",
            new=AsyncMock(
                return_value=ResultSet(columns=["id", "name"], rows=[(1, "foo"), (2, "bar")])
            ),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "read_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )

    assert isinstance(result, dict)
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [[1, "foo"], [2, "bar"]]


async def test_read_table_with_count(mock_ctx, ctx_patch) -> None:
    """read_table passes custom count to the service layer."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_read = AsyncMock(return_value=ResultSet(columns=["id"], rows=[(42,)]))

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.read_table", new=mock_read),
    ):
        await mcp._tool_manager.call_tool(
            "read_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales", "count": 100},
        )

    _, kwargs = mock_read.call_args
    assert kwargs.get("count") == 100


async def test_read_table_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """read_table wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.read_table",
            new=AsyncMock(side_effect=NotFoundError("table not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "read_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )


async def test_read_table_bad_qualified_name_raises_tool_error(ctx_patch) -> None:
    """read_table raises ToolError when qualified_name has no dot."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError, match="qualified name"),
    ):
        await mcp._tool_manager.call_tool(
            "read_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "nodot"},
        )


async def test_read_table_no_connection_string_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """read_table raises ToolError when the item has no connection string."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry(connection_string=None)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "read_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )


async def test_read_table_workspace_allowlist_blocks(ctx_patch) -> None:
    """read_table raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "read_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )


# ---------------------------------------------------------------------------
# read_table — as_of (time-travel)
# ---------------------------------------------------------------------------


async def test_read_table_with_as_of_passes_to_service(mock_ctx, ctx_patch) -> None:
    """read_table parses as_of ISO-8601 and threads the datetime to the service."""
    from datetime import datetime  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_read = AsyncMock(return_value=ResultSet(columns=["id"], rows=[(1,)]))

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.read_table", new=mock_read),
    ):
        await mcp._tool_manager.call_tool(
            "read_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "as_of": "2024-03-15T10:30:00",
            },
        )

    _, kwargs = mock_read.call_args
    assert kwargs.get("as_of") is not None
    assert isinstance(kwargs["as_of"], datetime)


async def test_read_table_without_as_of_passes_none(mock_ctx, ctx_patch) -> None:
    """read_table without as_of calls the service with as_of=None."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_read = AsyncMock(return_value=ResultSet(columns=["id"], rows=[(1,)]))

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.read_table", new=mock_read),
    ):
        await mcp._tool_manager.call_tool(
            "read_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )

    _, kwargs = mock_read.call_args
    assert kwargs.get("as_of") is None


async def test_read_table_invalid_as_of_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """read_table raises ToolError when as_of is not a valid ISO-8601 string."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "read_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "as_of": "not-a-date",
            },
        )


# ---------------------------------------------------------------------------
# delete_table — happy-path return path (line 163)
# NOTE: SQL-endpoint guard tested in test_server.py — not duplicated here.
# ---------------------------------------------------------------------------


async def test_delete_table_happy_path(mock_ctx, ctx_patch) -> None:
    """delete_table calls the service and returns {"dropped": True}."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.tables.delete_table",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "delete_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )

    assert result == {"dropped": True}


async def test_delete_table_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """delete_table wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.tables.delete_table",
            new=AsyncMock(side_effect=NotFoundError("table not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "delete_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )


# ---------------------------------------------------------------------------
# clear_table — happy-path return path (line 193)
# NOTE: SQL-endpoint guard tested in test_server.py — not duplicated here.
# ---------------------------------------------------------------------------


async def test_clear_table_happy_path(mock_ctx, ctx_patch) -> None:
    """clear_table calls the service and returns {"truncated": True}."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.tables.clear_table",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "clear_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )

    assert result == {"truncated": True}


async def test_clear_table_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """clear_table wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.tables.clear_table",
            new=AsyncMock(side_effect=NotFoundError("table not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "clear_table",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )


# ---------------------------------------------------------------------------
# clone_table — at UTC normalisation (lines 224-227)
# The naive → UTC conversion branch: at_dt.replace(tzinfo=UTC) when tzinfo is None
# NOTE: clone_table happy path and SQL-endpoint guard already in test_server.py.
# ---------------------------------------------------------------------------


async def test_clone_table_naive_at_timestamp_normalised_to_utc(mock_ctx, ctx_patch) -> None:
    """clone_table converts a naive ISO-8601 timestamp to UTC before calling service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    table = _make_table(name="sales_clone")
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_clone = AsyncMock(return_value=table)

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.clone_table", new=mock_clone),
    ):
        result = await mcp._tool_manager.call_tool(
            "clone_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "source": "dbo.sales",
                "new_table": "dbo.sales_clone",
                "at": "2024-05-20T14:00:00",  # naive — no tzinfo
            },
        )

    assert isinstance(result, dict)
    mock_clone.assert_called_once()
    _, kwargs = mock_clone.call_args
    at_passed = kwargs.get("at")
    assert at_passed is not None
    # The naive timestamp must have been assigned UTC.
    assert at_passed.tzinfo is not None
    assert at_passed.tzinfo == UTC


async def test_clone_table_aware_at_timestamp_converted_to_utc(mock_ctx, ctx_patch) -> None:
    """clone_table converts a timezone-aware ISO-8601 timestamp to UTC."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    table = _make_table(name="sales_clone")
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())
    mock_clone = AsyncMock(return_value=table)

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.clone_table", new=mock_clone),
    ):
        result = await mcp._tool_manager.call_tool(
            "clone_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "source": "dbo.sales",
                "new_table": "dbo.sales_clone",
                "at": "2024-05-20T14:00:00+02:00",  # aware, non-UTC
            },
        )

    assert isinstance(result, dict)
    mock_clone.assert_called_once()
    _, kwargs = mock_clone.call_args
    at_passed = kwargs.get("at")
    assert at_passed is not None
    # Should be normalised to UTC
    assert at_passed.tzinfo == UTC
    # 14:00+02:00 → 12:00 UTC
    assert at_passed.hour == 12


async def test_clone_table_bad_at_timestamp_raises_tool_error(ctx_patch) -> None:
    """clone_table raises ToolError when at is not a valid ISO-8601 timestamp."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "clone_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "source": "dbo.sales",
                "new_table": "dbo.sales_clone",
                "at": "not-a-date",
            },
        )

    assert "ISO-8601" in str(exc_info.value)


# ---------------------------------------------------------------------------
# create_empty_table — MCP tool
# ---------------------------------------------------------------------------


async def test_create_empty_table_happy_path(mock_ctx, ctx_patch) -> None:
    """create_empty_table resolves workspace + item, builds target, calls service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    table = _make_table()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_create = AsyncMock(return_value=table)

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.create_empty_table", new=mock_create),
        patch.dict(os.environ, {"FABRIC_MCP_WRITES": "true"}),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_empty_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "columns": [
                    {"name": "id", "sql_type": "INT", "nullable": False},
                    {"name": "label", "sql_type": "VARCHAR(100)"},
                ],
            },
        )

    mock_create.assert_called_once()
    assert result["name"] == "sales"
    assert result["schema_name"] == "dbo"


async def test_create_empty_table_bad_column_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """create_empty_table raises ToolError when a column dict is missing required keys."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WRITES": "true"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_empty_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "columns": [{"name": "id"}],  # missing sql_type
            },
        )


async def test_create_empty_table_sql_endpoint_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """create_empty_table raises ToolError for SQL Analytics Endpoints."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from tests.unit.mcp.conftest import make_sql_endpoint_entry  # noqa: PLC0415

    endpoint_entry = make_sql_endpoint_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=endpoint_entry)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WRITES": "true"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_empty_table",
            {
                "workspace": WS_NAME,
                "item": "MySqlEndpoint",
                "qualified_name": "dbo.sales",
                "columns": [{"name": "id", "sql_type": "INT"}],
            },
        )


# ---------------------------------------------------------------------------
# count_table_rows
# ---------------------------------------------------------------------------


async def test_count_table_rows_happy_path(mock_ctx, ctx_patch) -> None:
    """count_table_rows resolves item, calls the service, and returns a row_count dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.count_table_rows",
            new=AsyncMock(
                return_value=TableRowCount(schema_name="dbo", name="sales", row_count=42)
            ),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "count_table_rows",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )

    assert isinstance(result, dict)
    # schema_name replaces the legacy "schema" key (deliberate behaviour change)
    assert result["schema_name"] == "dbo"
    assert result["name"] == "sales"
    assert result["row_count"] == 42


async def test_count_table_rows_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """count_table_rows wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.count_table_rows",
            new=AsyncMock(side_effect=NotFoundError("table not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "count_table_rows",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )


async def test_count_table_rows_workspace_allowlist_blocks(ctx_patch) -> None:
    """count_table_rows raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "count_table_rows",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )


async def test_count_table_rows_bad_qualified_name_raises_tool_error(ctx_patch) -> None:
    """count_table_rows raises ToolError when qualified_name has no dot."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError, match="qualified name"),
    ):
        await mcp._tool_manager.call_tool(
            "count_table_rows",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "nodot"},
        )


# ---------------------------------------------------------------------------
# count_table_rows — as_of (time-travel)
# ---------------------------------------------------------------------------


async def test_count_table_rows_with_as_of_passes_to_service(mock_ctx, ctx_patch) -> None:
    """count_table_rows parses as_of ISO-8601 and threads the datetime to the service."""
    from datetime import datetime  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_count = AsyncMock(
        return_value=TableRowCount(schema_name="dbo", name="sales", row_count=10)
    )

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.count_table_rows", new=mock_count),
    ):
        await mcp._tool_manager.call_tool(
            "count_table_rows",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "as_of": "2024-03-15T10:30:00",
            },
        )

    _, kwargs = mock_count.call_args
    assert kwargs.get("as_of") is not None
    assert isinstance(kwargs["as_of"], datetime)


async def test_count_table_rows_without_as_of_passes_none(mock_ctx, ctx_patch) -> None:
    """count_table_rows without as_of calls the service with as_of=None."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_count = AsyncMock(return_value=TableRowCount(schema_name="dbo", name="sales", row_count=0))

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.count_table_rows", new=mock_count),
    ):
        await mcp._tool_manager.call_tool(
            "count_table_rows",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )

    _, kwargs = mock_count.call_args
    assert kwargs.get("as_of") is None


async def test_count_table_rows_invalid_as_of_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """count_table_rows raises ToolError when as_of is not a valid ISO-8601 string."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "count_table_rows",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "as_of": "not-a-date",
            },
        )


# ---------------------------------------------------------------------------
# get_cluster_columns — happy path, empty result, SQL endpoint guard, error funnel
# ---------------------------------------------------------------------------


async def test_get_cluster_columns_happy_path(mock_ctx, ctx_patch) -> None:
    """get_cluster_columns returns list of column dicts ordered by ordinal."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    # service now returns ClusterColumn instances; tool converts to dicts via model_dump
    service_result = [
        ClusterColumn(column_name="city", clustering_ordinal=1),
        ClusterColumn(column_name="country", clustering_ordinal=2),
    ]
    expected_dicts = [
        {"column_name": "city", "clustering_ordinal": 1},
        {"column_name": "country", "clustering_ordinal": 2},
    ]

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.get_cluster_columns",
            new=AsyncMock(return_value=service_result),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_cluster_columns",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )

    assert result == expected_dicts


async def test_get_cluster_columns_empty_result(mock_ctx, ctx_patch) -> None:
    """get_cluster_columns returns an empty list when no clustering is defined."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.get_cluster_columns",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_cluster_columns",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )

    assert result == []


async def test_get_cluster_columns_sql_endpoint_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """get_cluster_columns raises ToolError when target is a SQL Analytics Endpoint."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    endpoint_entry = make_sql_endpoint_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=endpoint_entry)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.get_cluster_columns",
            new=AsyncMock(
                side_effect=ItemKindError(
                    "Data clustering is not supported on SQL Analytics Endpoints; "
                    "use a Fabric Data Warehouse"
                )
            ),
        ),
        pytest.raises(ToolError, match="SQL Analytics Endpoints"),
    ):
        await mcp._tool_manager.call_tool(
            "get_cluster_columns",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )


async def test_get_cluster_columns_fabric_error_becomes_tool_error(mock_ctx, ctx_patch) -> None:
    """get_cluster_columns wraps FabricError into ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.get_cluster_columns",
            new=AsyncMock(side_effect=NotFoundError("table not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_cluster_columns",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )


# ---------------------------------------------------------------------------
# get_table_columns — happy path + not found
# ---------------------------------------------------------------------------

_TABLE_COLUMNS = [
    {
        "ordinal": 1,
        "name": "id",
        "data_type": "INT",
        "nullable": False,
        "collation_name": None,
        "is_identity": True,
        "is_computed": False,
    },
    {
        "ordinal": 2,
        "name": "amount",
        "data_type": "DECIMAL(18,2)",
        "nullable": True,
        "collation_name": None,
        "is_identity": False,
        "is_computed": False,
    },
]


async def test_get_table_columns_happy_path(mock_ctx, ctx_patch) -> None:
    """get_table_columns resolves workspace/item, calls service, returns list."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.mcp.tools.tables._get_columns",
            new=AsyncMock(return_value=_TABLE_COLUMNS),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_table_columns",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["name"] == "id"
    assert result[0]["data_type"] == "INT"
    assert result[1]["data_type"] == "DECIMAL(18,2)"


async def test_get_table_columns_not_found_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """get_table_columns raises ToolError when the table does not exist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.mcp.tools.tables._get_columns",
            new=AsyncMock(side_effect=NotFoundError("Table [dbo].[ghost] not found")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_table_columns",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.ghost"},
        )


async def test_get_table_columns_works_on_sql_endpoint(mock_ctx, ctx_patch) -> None:
    """get_table_columns has no SQL-endpoint guard — works on both targets."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(
        return_value=make_item_entry(kind=WarehouseKind.SQL_ENDPOINT)
    )

    with (
        ctx_patch,
        patch(
            "fabric_dw.mcp.tools.tables._get_columns",
            new=AsyncMock(return_value=_TABLE_COLUMNS),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_table_columns",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.sales"},
        )

    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# get_table_health_metrics — contract tests (read-only, SQL-endpoint-only)
# ---------------------------------------------------------------------------


async def test_get_table_health_metrics_is_registered() -> None:
    """get_table_health_metrics is registered as an MCP tool."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "get_table_health_metrics" in tool_names


async def test_get_table_health_metrics_is_not_mutating(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_table_health_metrics must NOT require the FABRIC_MCP_ALLOW_MUTATING flag.

    Mutating tools call ``assert_writes_allowed`` which raises ToolError when
    ``FABRIC_MCP_ALLOW_MUTATING`` is unset.  A read-only tool must succeed
    without that flag.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    # Remove the mutating flag to ensure a read-only tool succeeds without it.
    monkeypatch.delenv("FABRIC_MCP_ALLOW_MUTATING", raising=False)

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.get_table_health_metrics",
            new=AsyncMock(return_value=ResultSet(columns=[], rows=[])),
        ),
    ):
        # Must NOT raise ToolError — read-only tools don't check writes_allowed.
        result = await mcp._tool_manager.call_tool(
            "get_table_health_metrics",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.FactSales"},
        )
    assert isinstance(result, dict)


async def test_get_table_health_metrics_happy_path(mock_ctx, ctx_patch) -> None:
    """get_table_health_metrics forwards args to the service and returns columns+rows."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())

    fake_cols = ["issue_type", "severity"]
    fake_rows: list[tuple[object, ...]] = [("small_files", "medium")]
    mock_svc = AsyncMock(return_value=ResultSet(columns=fake_cols, rows=fake_rows))

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.get_table_health_metrics", new=mock_svc),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_table_health_metrics",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.FactSales"},
        )

    assert isinstance(result, dict)
    assert result["columns"] == fake_cols
    assert len(result["rows"]) == 1


async def test_get_table_health_metrics_forwards_qualified_name(mock_ctx, ctx_patch) -> None:
    """get_table_health_metrics parses the qualified name and passes schema + table to service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())
    mock_svc = AsyncMock(return_value=ResultSet(columns=[], rows=[]))

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.get_table_health_metrics", new=mock_svc),
    ):
        await mcp._tool_manager.call_tool(
            "get_table_health_metrics",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "sales.Transactions"},
        )

    args, _kwargs = mock_svc.call_args
    # service is called with positional: target, schema, table_name
    assert args[1] == "sales"
    assert args[2] == "Transactions"


async def test_get_table_health_metrics_warehouse_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """get_table_health_metrics raises ToolError when service raises ItemKindError."""
    from fabric_dw.exceptions import ItemKindError  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    # Use a Warehouse entry so the service receives WarehouseKind.WAREHOUSE
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.tables.get_table_health_metrics",
            new=AsyncMock(
                side_effect=ItemKindError(
                    "Table health-check (sp_get_table_health_metrics) is only "
                    "available on SQL Analytics Endpoints, not Data Warehouses."
                )
            ),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_table_health_metrics",
            {"workspace": WS_NAME, "item": WH_NAME, "qualified_name": "dbo.FactSales"},
        )


# ---------------------------------------------------------------------------
# transfer_table
# ---------------------------------------------------------------------------


async def test_transfer_table_happy_path(mock_ctx, ctx_patch) -> None:
    """transfer_table resolves item, calls the service, and returns the moved Table dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    moved = _make_table(schema="archive", name="sales")
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_transfer = AsyncMock(return_value=moved)

    with (
        ctx_patch,
        patch("fabric_dw.services.tables.transfer_table", new=mock_transfer),
        patch.dict(os.environ, {"FABRIC_MCP_WRITES": "true"}),
    ):
        result = await mcp._tool_manager.call_tool(
            "transfer_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "target_schema": "archive",
            },
        )

    mock_transfer.assert_called_once()
    assert result["name"] == "sales"
    assert result["schema_name"] == "archive"


async def test_transfer_table_readonly_blocked(mock_ctx, ctx_patch) -> None:
    """transfer_table raises ToolError when FABRIC_MCP_READONLY is set."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "transfer_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "target_schema": "archive",
            },
        )


async def test_transfer_table_sql_endpoint_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """transfer_table raises ToolError when the item is a SQL Analytics Endpoint."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WRITES": "true"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "transfer_table",
            {
                "workspace": WS_NAME,
                "item": "MySqlEndpoint",
                "qualified_name": "dbo.sales",
                "target_schema": "archive",
            },
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_transfer_table_undotted_qualified_name_raises_tool_error(
    mock_ctx,  # noqa: ARG001
    ctx_patch,
) -> None:
    """transfer_table must raise ToolError immediately for an undotted qualified_name."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError, match="qualified name"),
    ):
        await mcp._tool_manager.call_tool(
            "transfer_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "nodot",
                "target_schema": "archive",
            },
        )


async def test_transfer_table_workspace_allowlist_blocks(ctx_patch) -> None:
    """transfer_table raises ToolError when workspace is not in FABRIC_MCP_WORKSPACES."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "transfer_table",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "target_schema": "archive",
            },
        )
