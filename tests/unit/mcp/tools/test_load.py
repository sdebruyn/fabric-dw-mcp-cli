"""Unit tests for fabric_dw.mcp.tools.load — import_table_from_url tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.models import CopyIntoResult
from tests.unit.mcp.conftest import (
    WH_NAME,
    WS_ID,
    WS_NAME,
    make_item_entry,
    make_sql_endpoint_entry,
)


def _make_result() -> CopyIntoResult:
    return CopyIntoResult(rows_loaded=3, rows_rejected=0, target="dbo.sales")


# ---------------------------------------------------------------------------
# import_table_from_url — write guard (READONLY mode)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("mock_ctx")
async def test_import_table_from_url_blocked_in_readonly(
    ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url raises ToolError when FABRIC_MCP_READONLY=1."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.setenv("FABRIC_MCP_READONLY", "1")

    with ctx_patch, pytest.raises(ToolError, match="mutating tool"):
        await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
                "file_type": "PARQUET",
            },
        )


# ---------------------------------------------------------------------------
# import_table_from_url — SQL Endpoint rejection
# ---------------------------------------------------------------------------


async def test_import_table_from_url_rejects_sql_endpoint(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url raises ToolError for SQL Analytics Endpoint items."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)
    endpoint_entry = make_sql_endpoint_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=endpoint_entry)

    with ctx_patch, pytest.raises(ToolError, match="read-only"):
        await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://example.com/f.parquet",
                "file_type": "PARQUET",
            },
        )


# ---------------------------------------------------------------------------
# import_table_from_url — destructive guard for truncate/replace
# ---------------------------------------------------------------------------


async def test_import_table_from_url_truncate_blocked_without_destructive_flag(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """truncate is blocked when FABRIC_MCP_ALLOW_DESTRUCTIVE is unset (table exists)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)
    monkeypatch.delenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", raising=False)

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=True),
        ),
        pytest.raises(ToolError, match="destructive"),
    ):
        await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://example.com/f.parquet",
                "file_type": "PARQUET",
                "if_exists": "truncate",
            },
        )


async def test_import_table_from_url_replace_raises_tool_error(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url: if_exists=replace raises ToolError (not supported for remote URLs)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)
    monkeypatch.setenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", "1")

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=True),
        ),
        pytest.raises(ToolError, match=r"replace.*remote"),
    ):
        await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://example.com/f.parquet",
                "file_type": "PARQUET",
                "if_exists": "replace",
            },
        )


# ---------------------------------------------------------------------------
# import_table_from_url — if_exists=fail + table exists
# ---------------------------------------------------------------------------


async def test_import_table_from_url_fail_when_table_exists(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url with if_exists=fail raises ToolError when table exists."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)
    monkeypatch.delenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", raising=False)

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=True),
        ),
        pytest.raises(ToolError, match="already exists"),
    ):
        await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://example.com/f.parquet",
                "file_type": "PARQUET",
                "if_exists": "fail",
            },
        )


# ---------------------------------------------------------------------------
# import_table_from_url — absent table raises friendly error for fail/append
# ---------------------------------------------------------------------------


async def test_import_table_from_url_append_absent_table_raises(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url with if_exists=append raises ToolError when table does not exist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=False),
        ),
        pytest.raises(ToolError, match="does not exist"),
    ):
        await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
                "file_type": "PARQUET",
                "if_exists": "append",
            },
        )


async def test_import_table_from_url_fail_absent_table_raises(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url with if_exists=fail raises ToolError when table does not exist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=False),
        ),
        pytest.raises(ToolError, match="does not exist"),
    ):
        await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://example.com/f.parquet",
                "file_type": "PARQUET",
                "if_exists": "fail",
            },
        )


# ---------------------------------------------------------------------------
# import_table_from_url — happy path (append, table exists)
# ---------------------------------------------------------------------------


async def test_import_table_from_url_append_table_exists_happy_path(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url with if_exists=append + existing table loads successfully."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "fabric_dw.mcp.tools.load.copy_into_from_url",
            new=AsyncMock(return_value=_make_result()),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
                "file_type": "PARQUET",
                "if_exists": "append",
            },
        )

    assert result["rows_loaded"] == 3
    assert result["target"] == "dbo.sales"


# ---------------------------------------------------------------------------
# import_table_from_url — truncate + allowed
# ---------------------------------------------------------------------------


async def test_import_table_from_url_truncate_with_flag_succeeds(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url with if_exists=truncate + flag set → TRUNCATE + load."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)
    monkeypatch.setenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", "1")

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "fabric_dw.services.load.truncate_table",
            new=AsyncMock(),
        ) as mock_truncate,
        patch(
            "fabric_dw.mcp.tools.load.copy_into_from_url",
            new=AsyncMock(return_value=_make_result()),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
                "file_type": "PARQUET",
                "if_exists": "truncate",
            },
        )

    mock_truncate.assert_called_once()
    assert result["rows_loaded"] == 3


# ---------------------------------------------------------------------------
# import_table_from_url — unsupported file_type
# ---------------------------------------------------------------------------


async def test_import_table_from_url_rejects_json_file_type(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url raises ToolError for JSON file_type (not supported for remote URLs)."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with ctx_patch, pytest.raises(ToolError):
        await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://example.com/f.json",
                "file_type": "JSON",
            },
        )


# ---------------------------------------------------------------------------
# import_table_from_url — table absent with truncate/replace
# ---------------------------------------------------------------------------


async def test_import_table_from_url_truncate_absent_table_raises(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url with if_exists=truncate raises ToolError when table does not exist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)
    monkeypatch.setenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", "1")

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=False),
        ),
        pytest.raises(ToolError, match="does not exist"),
    ):
        await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://example.com/f.parquet",
                "file_type": "PARQUET",
                "if_exists": "truncate",
            },
        )


async def test_import_table_from_url_replace_absent_table_raises(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_table_from_url with if_exists=replace raises ToolError when table does not exist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)
    monkeypatch.setenv("FABRIC_MCP_ALLOW_DESTRUCTIVE", "1")

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=False),
        ),
        pytest.raises(ToolError, match="does not exist"),
    ):
        await mcp._tool_manager.call_tool(
            "import_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://example.com/f.parquet",
                "file_type": "PARQUET",
                "if_exists": "replace",
            },
        )


# ---------------------------------------------------------------------------
# load_table_from_url — SQL endpoint rejection fires before table-existence check
# ---------------------------------------------------------------------------


async def test_load_table_from_url_rejects_sql_endpoint(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_table_from_url raises ToolError for SQL Analytics Endpoint items.

    The endpoint guard must fire before the table-existence check so that
    'SQL endpoint + absent table' yields the endpoint error, not 'table not found'.
    """
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_sql_endpoint_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=False),
        ),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "load_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://example.com/f.parquet",
                "file_type": "PARQUET",
            },
        )


# ---------------------------------------------------------------------------
# load_table_from_url — absent table raises friendly error
# ---------------------------------------------------------------------------


async def test_load_table_from_url_absent_table_raises(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_table_from_url raises ToolError when the target table does not exist."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=False),
        ),
        pytest.raises(ToolError, match="does not exist"),
    ):
        await mcp._tool_manager.call_tool(
            "load_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
                "file_type": "PARQUET",
            },
        )


# ---------------------------------------------------------------------------
# load_table_from_url — happy path (table exists)
# ---------------------------------------------------------------------------


async def test_load_table_from_url_table_exists_happy_path(
    mock_ctx, ctx_patch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_table_from_url loads rows when the target table already exists."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    monkeypatch.delenv("FABRIC_MCP_READONLY", raising=False)

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.load.table_exists",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "fabric_dw.mcp.tools.load.copy_into_from_url",
            new=AsyncMock(return_value=_make_result()),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "load_table_from_url",
            {
                "workspace": WS_NAME,
                "item": WH_NAME,
                "qualified_name": "dbo.sales",
                "url": "https://onelake.dfs.fabric.microsoft.com/ws/lh/Files/f.parquet",
                "file_type": "PARQUET",
            },
        )

    assert result["rows_loaded"] == 3
    assert result["target"] == "dbo.sales"
