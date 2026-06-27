"""Unit tests for fabric_dw.mcp.tools.sql_endpoints — list_sql_endpoints behaviour.

Focuses on the optional-workspace fix introduced in #763:
  - all_workspaces=True with no workspace arg → success (aggregation)
  - all_workspaces=False with no workspace arg → clear ToolError
  - workspace provided + all_workspaces=False → existing happy path
  - all_workspaces=True with workspace allowlist configured → ToolError
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fabric_dw.models import Warehouse, WarehouseKind
from tests.unit.mcp.conftest import (
    WS_ID,
    WS_NAME,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EP_ID = UUID("f1e2d3c4-b5a6-7890-1234-567890abcdef")
_EP_NAME = "my-sql-endpoint"


def _make_sql_endpoint() -> Warehouse:
    """Return a Warehouse model with SQL_ENDPOINT kind."""
    return Warehouse.model_validate(
        {
            "id": str(_EP_ID),
            "displayName": _EP_NAME,
            "workspaceId": str(WS_ID),
            "kind": WarehouseKind.SQL_ENDPOINT,
            "connectionString": "ep.fabric.microsoft.com",
        }
    )


# ---------------------------------------------------------------------------
# list_sql_endpoints — all_workspaces=True with no workspace (issue #763)
# ---------------------------------------------------------------------------


async def test_list_sql_endpoints_all_workspaces_true_no_workspace_succeeds(
    ctx_patch,
) -> None:
    """all_workspaces=True with no workspace arg must aggregate without error."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep = _make_sql_endpoint()
    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_endpoints.list_all_workspaces",
            new=AsyncMock(return_value=[ep]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_sql_endpoints",
            {"all_workspaces": True},
        )

    assert isinstance(result, list)
    assert result[0]["id"] == str(_EP_ID)


async def test_list_sql_endpoints_no_workspace_no_all_workspaces_raises_clear_error(
    ctx_patch,
) -> None:
    """No workspace and all_workspaces=False must raise a clear ToolError."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_sql_endpoints",
            {},
        )

    assert "workspace is required" in str(exc_info.value).lower()
    assert "all_workspaces" in str(exc_info.value).lower()


async def test_list_sql_endpoints_workspace_provided_succeeds(mock_ctx, ctx_patch) -> None:
    """Existing behaviour: list_sql_endpoints with workspace and all_workspaces=False works."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ep = _make_sql_endpoint()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_endpoints.list_endpoints",
            new=AsyncMock(return_value=[ep]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_sql_endpoints",
            {"workspace": WS_NAME},
        )

    assert isinstance(result, list)
    assert result[0]["id"] == str(_EP_ID)


async def test_list_sql_endpoints_all_workspaces_with_allowlist_raises(ctx_patch) -> None:
    """all_workspaces=True must raise ToolError when FABRIC_MCP_WORKSPACES is configured."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": WS_NAME}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_sql_endpoints",
            {"all_workspaces": True},
        )

    assert "FABRIC_MCP_WORKSPACES" in str(exc_info.value)
