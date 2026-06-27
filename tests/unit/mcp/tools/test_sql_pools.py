"""Unit tests for the sql_pools MCP tool wrappers.

Coverage targets
----------------
- get_sql_pools_status         (lines 34-54)
- list_sql_pools               (lines 56-71)
- get_sql_pool                 (lines 73-95)
- create_sql_pool              (lines 97-160)
- update_sql_pool              (lines 162-212)
- delete_sql_pool              (lines 214-237)
- enable_sql_pools             (lines 239-255)
- disable_sql_pools            (lines 257-275)
- _parse_dt helper             (lines 277-284)
- list_sql_pool_insights       (lines 286-317)

Each tool is covered for:
  1. Happy path (service returns expected data -> correct dict/list shape)
  2. FabricError / subclass -> ToolError funnel
  3. Guard preconditions (READONLY, ALLOW_DESTRUCTIVE, WORKSPACES allowlist)
  4. Arg validation / branching (classifier, missing pool after create/update, etc.)
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from fabric_dw.exceptions import AlreadyExistsError, BadRequestError, FabricError, NotFoundError
from fabric_dw.models import (
    SqlPool,
    SqlPoolInsight,
    SqlPoolsConfiguration,
)
from tests.unit.mcp.conftest import (
    WH_ID,
    WH_NAME,
    WS_ID,
    WS_NAME,
    make_item_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WS_ID = WS_ID
_WH_ID = WH_ID
_WS_NAME = WS_NAME
_WH_NAME = WH_NAME


def _make_pool(
    name: str = "pool-1",
    max_pct: int = 30,
    *,
    is_default: bool = False,
) -> SqlPool:
    return SqlPool.model_validate(
        {
            "name": name,
            "isDefault": is_default,
            "maxResourcePercentage": max_pct,
            "optimizeForReads": True,
        }
    )


def _make_config(
    *,
    enabled: bool = True,
    pools: list[SqlPool] | None = None,
) -> SqlPoolsConfiguration:
    pool_list = pools if pools is not None else [_make_pool()]
    return SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": enabled,
            "customSQLPools": [p.model_dump(by_alias=True, mode="json") for p in pool_list],
        }
    )


def _make_pool_insight() -> SqlPoolInsight:
    return SqlPoolInsight.model_validate(
        {
            "sql_pool_name": "pool-1",
            "timestamp": "2026-01-01T00:00:00",
            "max_resource_percentage": 30,
            "is_optimized_for_reads": True,
        }
    )


# ---------------------------------------------------------------------------
# get_sql_pools_status
# ---------------------------------------------------------------------------


async def test_get_sql_pools_status_happy_path(mock_ctx, ctx_patch) -> None:
    """get_sql_pools_status returns only the enabled flag, not the pool list."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.get_status",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_sql_pools_status",
            {"workspace": _WS_NAME},
        )

    assert result == {"customSQLPoolsEnabled": True}


async def test_get_sql_pools_status_disabled(mock_ctx, ctx_patch) -> None:
    """get_sql_pools_status returns False when custom pools are disabled."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.get_status",
            new=AsyncMock(return_value=False),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_sql_pools_status",
            {"workspace": _WS_NAME},
        )

    assert result == {"customSQLPoolsEnabled": False}


async def test_get_sql_pools_status_resolver_fabric_error(mock_ctx, ctx_patch) -> None:
    """get_sql_pools_status wraps FabricError from resolver as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(side_effect=NotFoundError("workspace not found"))

    with (
        ctx_patch,
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_sql_pools_status",
            {"workspace": _WS_NAME},
        )


async def test_get_sql_pools_status_workspace_not_allowed(ctx_patch) -> None:
    """get_sql_pools_status raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "get_sql_pools_status",
            {"workspace": _WS_NAME},
        )

    assert "allowlist" in str(exc_info.value).lower()


async def test_get_sql_pools_status_service_fabric_error(mock_ctx, ctx_patch) -> None:
    """get_sql_pools_status surfaces FabricError from service as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.get_status",
            new=AsyncMock(side_effect=FabricError("api error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_sql_pools_status",
            {"workspace": _WS_NAME},
        )


async def test_get_sql_pools_status_broken_pool_schema_does_not_crash(mock_ctx, ctx_patch) -> None:
    """get_sql_pools_status still returns the flag when nested pool fields are missing.

    This verifies the integration between get_status() and the MCP tool: even
    if the beta API returns a pool list with missing required fields (which would
    fail SqlPoolsConfiguration.model_validate), the status call must succeed.
    get_status() reads only the top-level key, so get_sql_pools_status must not
    raise ToolError in this scenario.  See issue #905.

    The mock_ctx HTTP client is configured to return the broken payload directly,
    bypassing the real network layer (respx cannot intercept mock objects).
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    broken_payload = {
        "customSQLPoolsEnabled": True,
        "customSQLPools": [
            # 'name' and 'maxResourcePercentage' are intentionally absent.
            {"isDefault": True, "optimizeForReads": False}
        ],
    }
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    # Configure the mock HTTP client to return the broken payload.
    mock_response = MagicMock()
    mock_response.json.return_value = broken_payload
    mock_ctx.http.request = AsyncMock(return_value=mock_response)

    with ctx_patch:
        result = await mcp._tool_manager.call_tool(
            "get_sql_pools_status",
            {"workspace": _WS_NAME},
        )

    assert result == {"customSQLPoolsEnabled": True}


# ---------------------------------------------------------------------------
# list_sql_pools
# ---------------------------------------------------------------------------


async def test_list_sql_pools_happy_path(mock_ctx, ctx_patch) -> None:
    """list_sql_pools returns list of pool dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    config = _make_config(pools=[_make_pool("alpha", 40), _make_pool("beta", 30)])
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.get_configuration",
            new=AsyncMock(return_value=config),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_sql_pools",
            {"workspace": _WS_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["name"] == "alpha"
    assert result[1]["name"] == "beta"


async def test_list_sql_pools_fabric_error(mock_ctx, ctx_patch) -> None:
    """list_sql_pools wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.get_configuration",
            new=AsyncMock(side_effect=FabricError("boom")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_sql_pools",
            {"workspace": _WS_NAME},
        )


async def test_list_sql_pools_workspace_not_allowed(ctx_patch) -> None:
    """list_sql_pools raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_sql_pools",
            {"workspace": _WS_NAME},
        )


# ---------------------------------------------------------------------------
# get_sql_pool
# ---------------------------------------------------------------------------


async def test_get_sql_pool_happy_path(mock_ctx, ctx_patch) -> None:
    """get_sql_pool returns the matching pool dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    pool = _make_pool("pool-x", 50)
    config = _make_config(pools=[pool])
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.get_configuration",
            new=AsyncMock(return_value=config),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "get_sql_pool",
            {"workspace": _WS_NAME, "pool_name": "pool-x"},
        )

    assert isinstance(result, dict)
    assert result["name"] == "pool-x"
    assert result["maxResourcePercentage"] == 50


async def test_get_sql_pool_not_found_in_config(mock_ctx, ctx_patch) -> None:
    """get_sql_pool raises ToolError when the named pool is absent from config."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    config = _make_config(pools=[_make_pool("other")])
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.get_configuration",
            new=AsyncMock(return_value=config),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "get_sql_pool",
            {"workspace": _WS_NAME, "pool_name": "missing-pool"},
        )

    assert "missing-pool" in str(exc_info.value)


async def test_get_sql_pool_fabric_error(mock_ctx, ctx_patch) -> None:
    """get_sql_pool propagates FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.get_configuration",
            new=AsyncMock(side_effect=FabricError("api error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_sql_pool",
            {"workspace": _WS_NAME, "pool_name": "pool-x"},
        )


async def test_get_sql_pool_workspace_not_allowed(ctx_patch) -> None:
    """get_sql_pool raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "get_sql_pool",
            {"workspace": _WS_NAME, "pool_name": "pool-x"},
        )


# ---------------------------------------------------------------------------
# create_sql_pool
# ---------------------------------------------------------------------------


async def test_create_sql_pool_happy_path(mock_ctx, ctx_patch) -> None:
    """create_sql_pool returns the created pool dict and clears negative cache."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    created_pool = _make_pool("new-pool", 20)
    config = _make_config(pools=[created_pool])
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.create_pool",
            new=AsyncMock(return_value=config),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_sql_pool",
            {"workspace": _WS_NAME, "name": "new-pool", "max_percent": 20},
        )

    assert isinstance(result, dict)
    assert result["name"] == "new-pool"
    assert result["maxResourcePercentage"] == 20


async def test_create_sql_pool_with_classifier(mock_ctx, ctx_patch) -> None:
    """create_sql_pool passes classifier fields to the service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    created_pool = SqlPool.model_validate(
        {
            "name": "pool-cls",
            "isDefault": False,
            "maxResourcePercentage": 25,
            "optimizeForReads": True,
            "classifier": {"type": "Application Name", "value": ["MyApp"]},
        }
    )
    config = _make_config(pools=[created_pool])
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_create = AsyncMock(return_value=config)

    with (
        ctx_patch,
        patch("fabric_dw.services.sql_pools.create_pool", new=mock_create),
    ):
        result = await mcp._tool_manager.call_tool(
            "create_sql_pool",
            {
                "workspace": _WS_NAME,
                "name": "pool-cls",
                "max_percent": 25,
                "classifier_type": "Application Name",
                "classifier_values": ["MyApp"],
            },
        )

    assert result["name"] == "pool-cls"
    mock_create.assert_called_once()
    # The pool argument is the 3rd positional arg (index 2)
    pool_arg = mock_create.call_args[0][2]
    assert pool_arg.classifier is not None
    assert pool_arg.classifier.type == "Application Name"


async def test_create_sql_pool_already_exists(mock_ctx, ctx_patch) -> None:
    """create_sql_pool raises ToolError (not AlreadyExistsError) when pool exists."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.create_pool",
            new=AsyncMock(side_effect=AlreadyExistsError("pool exists")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_sql_pool",
            {"workspace": _WS_NAME, "name": "dupe", "max_percent": 10},
        )

    assert "pool exists" in str(exc_info.value)


async def test_create_sql_pool_fabric_error(mock_ctx, ctx_patch) -> None:
    """create_sql_pool wraps generic FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.create_pool",
            new=AsyncMock(side_effect=FabricError("server error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_sql_pool",
            {"workspace": _WS_NAME, "name": "p", "max_percent": 10},
        )


async def test_create_sql_pool_pool_missing_after_create(mock_ctx, ctx_patch) -> None:
    """create_sql_pool raises ToolError when pool absent from response (eventual consistency)."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    # Return config without the newly created pool to simulate eventual consistency gap
    config = _make_config(pools=[_make_pool("other-pool")])
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.create_pool",
            new=AsyncMock(return_value=config),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_sql_pool",
            {"workspace": _WS_NAME, "name": "new-pool", "max_percent": 10},
        )

    assert "not found in the API response" in str(exc_info.value)


async def test_create_sql_pool_readonly_blocked(ctx_patch) -> None:
    """create_sql_pool raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "create_sql_pool",
            {"workspace": _WS_NAME, "name": "p", "max_percent": 10},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_create_sql_pool_workspace_not_allowed(ctx_patch) -> None:
    """create_sql_pool raises ToolError when workspace is not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "create_sql_pool",
            {"workspace": _WS_NAME, "name": "p", "max_percent": 10},
        )


# ---------------------------------------------------------------------------
# update_sql_pool
# ---------------------------------------------------------------------------


async def test_update_sql_pool_happy_path(mock_ctx, ctx_patch) -> None:
    """update_sql_pool returns the updated pool dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    updated_pool = _make_pool("pool-1", 60)
    config = _make_config(pools=[updated_pool])
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.update_pool",
            new=AsyncMock(return_value=config),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "update_sql_pool",
            {"workspace": _WS_NAME, "name": "pool-1", "max_percent": 60},
        )

    assert isinstance(result, dict)
    assert result["name"] == "pool-1"
    assert result["maxResourcePercentage"] == 60


async def test_update_sql_pool_not_found(mock_ctx, ctx_patch) -> None:
    """update_sql_pool raises ToolError when pool does not exist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.update_pool",
            new=AsyncMock(side_effect=NotFoundError("pool not found")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "update_sql_pool",
            {"workspace": _WS_NAME, "name": "ghost", "max_percent": 10},
        )

    assert "pool not found" in str(exc_info.value)


async def test_update_sql_pool_fabric_error(mock_ctx, ctx_patch) -> None:
    """update_sql_pool wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.update_pool",
            new=AsyncMock(side_effect=FabricError("server error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "update_sql_pool",
            {"workspace": _WS_NAME, "name": "pool-1", "max_percent": 10},
        )


async def test_update_sql_pool_missing_after_update(mock_ctx, ctx_patch) -> None:
    """update_sql_pool raises ToolError when pool absent from response after update."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    config = _make_config(pools=[_make_pool("other-pool")])
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.update_pool",
            new=AsyncMock(return_value=config),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "update_sql_pool",
            {"workspace": _WS_NAME, "name": "pool-1", "max_percent": 10},
        )

    assert "not found in the API response" in str(exc_info.value)


async def test_update_sql_pool_readonly_blocked(ctx_patch) -> None:
    """update_sql_pool raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "update_sql_pool",
            {"workspace": _WS_NAME, "name": "pool-1", "max_percent": 50},
        )


# ---------------------------------------------------------------------------
# delete_sql_pool
# ---------------------------------------------------------------------------


async def test_delete_sql_pool_happy_path(mock_ctx, ctx_patch) -> None:
    """delete_sql_pool returns deleted=True dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.sql_pools.delete_pool",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "delete_sql_pool",
            {"workspace": _WS_NAME, "pool_name": "old-pool"},
        )

    assert result == {"deleted": True, "pool_name": "old-pool"}


async def test_delete_sql_pool_not_found(mock_ctx, ctx_patch) -> None:
    """delete_sql_pool raises ToolError when pool does not exist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.sql_pools.delete_pool",
            new=AsyncMock(side_effect=NotFoundError("pool not found")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "delete_sql_pool",
            {"workspace": _WS_NAME, "pool_name": "ghost"},
        )

    assert "pool not found" in str(exc_info.value)


async def test_delete_sql_pool_fabric_error(mock_ctx, ctx_patch) -> None:
    """delete_sql_pool wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch(
            "fabric_dw.services.sql_pools.delete_pool",
            new=AsyncMock(side_effect=FabricError("server error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "delete_sql_pool",
            {"workspace": _WS_NAME, "pool_name": "p"},
        )


async def test_delete_sql_pool_destructive_disabled(ctx_patch) -> None:
    """delete_sql_pool raises ToolError when FABRIC_MCP_ALLOW_DESTRUCTIVE is not set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    env = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_DESTRUCTIVE"}

    with (
        ctx_patch,
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "delete_sql_pool",
            {"workspace": _WS_NAME, "pool_name": "p"},
        )

    assert "destructive" in str(exc_info.value).lower()


async def test_delete_sql_pool_readonly_blocked(ctx_patch) -> None:
    """delete_sql_pool raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1", "FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "delete_sql_pool",
            {"workspace": _WS_NAME, "pool_name": "p"},
        )


# ---------------------------------------------------------------------------
# enable_sql_pools
# ---------------------------------------------------------------------------


async def test_enable_sql_pools_happy_path(mock_ctx, ctx_patch) -> None:
    """enable_sql_pools returns config dict with enabled=True."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    config = _make_config(enabled=True)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.enable",
            new=AsyncMock(return_value=config),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "enable_sql_pools",
            {"workspace": _WS_NAME},
        )

    assert isinstance(result, dict)
    assert result["customSQLPoolsEnabled"] is True


async def test_enable_sql_pools_fabric_error(mock_ctx, ctx_patch) -> None:
    """enable_sql_pools wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.enable",
            new=AsyncMock(side_effect=FabricError("server error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "enable_sql_pools",
            {"workspace": _WS_NAME},
        )


async def test_enable_sql_pools_no_pools_defined_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """enable_sql_pools surfaces BadRequestError from service as ToolError, not a raw traceback.

    The service raises BadRequestError when the workspace has no custom SQL pools
    (the Fabric API rejects an empty pool list with HTTP 400).  The MCP tool only
    catches FabricError; BadRequestError is a subclass, so it must be caught and
    re-raised as ToolError — not escape as a raw exception.
    """
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.enable",
            new=AsyncMock(side_effect=BadRequestError("no pools — use sql-pools create")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "enable_sql_pools",
            {"workspace": _WS_NAME},
        )

    assert "sql-pools create" in str(exc_info.value)


async def test_enable_sql_pools_readonly_blocked(ctx_patch) -> None:
    """enable_sql_pools raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "enable_sql_pools",
            {"workspace": _WS_NAME},
        )


async def test_enable_sql_pools_workspace_not_allowed(ctx_patch) -> None:
    """enable_sql_pools raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "enable_sql_pools",
            {"workspace": _WS_NAME},
        )


# ---------------------------------------------------------------------------
# disable_sql_pools
# ---------------------------------------------------------------------------


async def test_disable_sql_pools_happy_path(mock_ctx, ctx_patch) -> None:
    """disable_sql_pools returns config dict with enabled=False."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    config = _make_config(enabled=False)
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.disable",
            new=AsyncMock(return_value=config),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "disable_sql_pools",
            {"workspace": _WS_NAME},
        )

    assert isinstance(result, dict)
    assert result["customSQLPoolsEnabled"] is False


async def test_disable_sql_pools_fabric_error(mock_ctx, ctx_patch) -> None:
    """disable_sql_pools wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.sql_pools.disable",
            new=AsyncMock(side_effect=FabricError("server error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "disable_sql_pools",
            {"workspace": _WS_NAME},
        )


async def test_disable_sql_pools_readonly_blocked(ctx_patch) -> None:
    """disable_sql_pools raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "disable_sql_pools",
            {"workspace": _WS_NAME},
        )


async def test_disable_sql_pools_workspace_not_allowed(ctx_patch) -> None:
    """disable_sql_pools raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "disable_sql_pools",
            {"workspace": _WS_NAME},
        )


# ---------------------------------------------------------------------------
# list_sql_pool_insights
# ---------------------------------------------------------------------------


async def test_list_sql_pool_insights_happy_path(mock_ctx, ctx_patch) -> None:
    """list_sql_pool_insights returns list of insight dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    insight = _make_pool_insight()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.query_insights.list_sql_pool_insights",
            new=AsyncMock(return_value=[insight]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_sql_pool_insights",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["sql_pool_name"] == "pool-1"


async def test_list_sql_pool_insights_with_since_until(mock_ctx, ctx_patch) -> None:
    """list_sql_pool_insights passes parsed datetimes to service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_svc = AsyncMock(return_value=[])

    with (
        ctx_patch,
        patch("fabric_dw.services.query_insights.list_sql_pool_insights", new=mock_svc),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_sql_pool_insights",
            {
                "workspace": _WS_NAME,
                "warehouse": _WH_NAME,
                "since": "2026-01-01T00:00:00",
                "until": "2026-01-02T00:00:00",
                "limit": 50,
            },
        )

    assert result == []
    mock_svc.assert_called_once()
    _, kwargs = mock_svc.call_args
    assert kwargs["since"] is not None
    assert kwargs["until"] is not None
    assert kwargs["limit"] == 50


async def test_list_sql_pool_insights_bad_since(ctx_patch) -> None:
    """list_sql_pool_insights raises ToolError on invalid ISO-8601 since."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_sql_pool_insights",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "since": "not-a-date"},
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_list_sql_pool_insights_bad_until(ctx_patch) -> None:
    """list_sql_pool_insights raises ToolError on invalid ISO-8601 until."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_sql_pool_insights",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "until": "bad-date"},
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_list_sql_pool_insights_fabric_error(mock_ctx, ctx_patch) -> None:
    """list_sql_pool_insights wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.query_insights.list_sql_pool_insights",
            new=AsyncMock(side_effect=FabricError("sql error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_sql_pool_insights",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


async def test_list_sql_pool_insights_workspace_not_allowed(ctx_patch) -> None:
    """list_sql_pool_insights raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_sql_pool_insights",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )
