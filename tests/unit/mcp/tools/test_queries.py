"""Unit tests for the queries MCP tool wrappers.

Coverage targets
----------------
- _parse_dt helper             (lines 25-32)
- list_running_queries         (lines 38-51)
- list_connections             (lines 53-66)
- kill_session                 (lines 68-82)
- list_request_history         (lines 84-115)
- list_session_history         (lines 117-148)
- list_frequent_queries        (lines 150-181)
- list_long_running_queries    (lines 183-214)

Each tool is covered for:
  1. Happy path (service returns expected data -> correct dict/list shape)
  2. FabricError -> ToolError funnel
  3. Guard preconditions (READONLY, WORKSPACES allowlist)
  4. Arg validation / branching (since/until parsing, ValueError from kill)
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from fabric_dw.exceptions import FabricError
from fabric_dw.models import (
    Connection,
    ExecRequestHistory,
    ExecSessionHistory,
    FrequentlyRunQuery,
    LongRunningQuery,
    RunningQuery,
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


def _make_connection() -> Connection:
    return Connection.model_validate(
        {
            "session_id": 7,
            "connect_time": "2026-01-01T10:00:00",
            "net_transport": "TCP",
        }
    )


def _make_exec_request_history() -> ExecRequestHistory:
    return ExecRequestHistory.model_validate(
        {
            "session_id": 1,
            "row_count": 5,
            "status": "Succeeded",
        }
    )


def _make_exec_session_history() -> ExecSessionHistory:
    return ExecSessionHistory.model_validate(
        {
            "session_id": 99,
            "connection_id": str(UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")),
            "session_start_time": "2026-01-01T09:00:00",
            "total_query_elapsed_time_ms": 500,
            "last_request_start_time": "2026-01-01T09:00:01",
            "login_name": "user@example.com",
            "status": "closed",
            "is_user_process": True,
            "prev_error": 0,
            "group_id": 0,
            "text_size": 65536,
            "date_first": 7,
            "quoted_identifier": True,
            "arithabort": False,
            "ansi_null_dflt_on": True,
            "ansi_defaults": False,
            "ansi_warnings": True,
            "ansi_padding": True,
            "ansi_nulls": True,
            "concat_null_yields_null": True,
            "transaction_isolation_level": 2,
            "lock_timeout": -1,
            "deadlock_priority": 0,
            "original_security_id": b"\x00",
        }
    )


def _make_frequently_run_query() -> FrequentlyRunQuery:
    return FrequentlyRunQuery.model_validate(
        {
            "number_of_runs": 10,
            "avg_total_elapsed_time_ms": 200,
            "last_run_total_elapsed_time_ms": 180,
            "min_run_total_elapsed_time_ms": 100,
            "max_run_total_elapsed_time_ms": 300,
            "number_of_successful_runs": 9,
            "number_of_failed_runs": 1,
            "number_of_canceled_runs": 0,
        }
    )


def _make_long_running_query() -> LongRunningQuery:
    return LongRunningQuery.model_validate(
        {
            "median_total_elapsed_time_ms": 5000,
            "number_of_runs": 3,
            "last_run_total_elapsed_time_ms": 6000,
        }
    )


# ---------------------------------------------------------------------------
# list_running_queries
# ---------------------------------------------------------------------------


async def test_list_running_queries_happy_path(mock_ctx, ctx_patch) -> None:
    """list_running_queries returns list of serialised query dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    query = _make_running_query()
    item = make_item_entry()
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
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["session_id"] == 42
    assert result[0]["status"] == "running"


async def test_list_running_queries_fabric_error(mock_ctx, ctx_patch) -> None:
    """list_running_queries wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_running",
            new=AsyncMock(side_effect=FabricError("sql error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_running_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


async def test_list_running_queries_workspace_not_allowed(ctx_patch) -> None:
    """list_running_queries raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_running_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


async def test_list_running_queries_empty(mock_ctx, ctx_patch) -> None:
    """list_running_queries returns empty list when no queries running."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_running",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_running_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert result == []


# ---------------------------------------------------------------------------
# list_connections
# ---------------------------------------------------------------------------


async def test_list_connections_happy_path(mock_ctx, ctx_patch) -> None:
    """list_connections returns list of serialised connection dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    conn = _make_connection()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_connections",
            new=AsyncMock(return_value=[conn]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_connections",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["session_id"] == 7
    assert result[0]["net_transport"] == "TCP"


async def test_list_connections_fabric_error(mock_ctx, ctx_patch) -> None:
    """list_connections wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_connections",
            new=AsyncMock(side_effect=FabricError("sql error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_connections",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


async def test_list_connections_workspace_not_allowed(ctx_patch) -> None:
    """list_connections raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_connections",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


# ---------------------------------------------------------------------------
# kill_session
# ---------------------------------------------------------------------------


async def test_kill_session_happy_path(mock_ctx, ctx_patch) -> None:
    """kill_session returns killed=True dict."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.kill",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "kill_session",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "session_id": 42},
        )

    assert result == {"killed": True, "session_id": 42}


async def test_kill_session_fabric_error(mock_ctx, ctx_patch) -> None:
    """kill_session wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.kill",
            new=AsyncMock(side_effect=FabricError("kill failed")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "kill_session",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "session_id": 42},
        )


async def test_kill_session_value_error(mock_ctx, ctx_patch) -> None:
    """kill_session wraps ValueError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.kill",
            new=AsyncMock(side_effect=ValueError("invalid session")),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "kill_session",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "session_id": 99},
        )

    assert "invalid session" in str(exc_info.value)


async def test_kill_session_readonly_blocked(ctx_patch) -> None:
    """kill_session raises ToolError in read-only mode."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "kill_session",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "session_id": 5},
        )

    assert "read-only" in str(exc_info.value).lower()


async def test_kill_session_workspace_not_allowed(ctx_patch) -> None:
    """kill_session raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "kill_session",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "session_id": 5},
        )


# ---------------------------------------------------------------------------
# list_request_history
# ---------------------------------------------------------------------------


async def test_list_request_history_happy_path(mock_ctx, ctx_patch) -> None:
    """list_request_history returns list of serialised history dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    hist = _make_exec_request_history()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.query_insights.list_request_history",
            new=AsyncMock(return_value=[hist]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_request_history",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["row_count"] == 5
    assert result[0]["status"] == "Succeeded"


async def test_list_request_history_with_since_until(mock_ctx, ctx_patch) -> None:
    """list_request_history passes parsed datetimes to service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_svc = AsyncMock(return_value=[])

    with (
        ctx_patch,
        patch("fabric_dw.services.query_insights.list_request_history", new=mock_svc),
    ):
        await mcp._tool_manager.call_tool(
            "list_request_history",
            {
                "workspace": _WS_NAME,
                "warehouse": _WH_NAME,
                "since": "2026-01-01T00:00:00",
                "until": "2026-12-31T23:59:59",
                "limit": 200,
            },
        )

    mock_svc.assert_called_once()
    _, kwargs = mock_svc.call_args
    assert kwargs["since"] is not None
    assert kwargs["until"] is not None
    assert kwargs["limit"] == 200


async def test_list_request_history_bad_since(ctx_patch) -> None:
    """list_request_history raises ToolError on invalid ISO-8601 since."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_request_history",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "since": "not-a-date"},
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_list_request_history_bad_until(ctx_patch) -> None:
    """list_request_history raises ToolError on invalid ISO-8601 until."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_request_history",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "until": "bad-ts"},
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_list_request_history_fabric_error(mock_ctx, ctx_patch) -> None:
    """list_request_history wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.query_insights.list_request_history",
            new=AsyncMock(side_effect=FabricError("sql error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_request_history",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


async def test_list_request_history_workspace_not_allowed(ctx_patch) -> None:
    """list_request_history raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_request_history",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


# ---------------------------------------------------------------------------
# list_session_history
# ---------------------------------------------------------------------------


async def test_list_session_history_happy_path(mock_ctx, ctx_patch) -> None:
    """list_session_history returns list of serialised session dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    sess = _make_exec_session_history()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.query_insights.list_session_history",
            new=AsyncMock(return_value=[sess]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_session_history",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["session_id"] == 99
    assert result[0]["login_name"] == "user@example.com"


async def test_list_session_history_with_since_until(mock_ctx, ctx_patch) -> None:
    """list_session_history passes parsed datetimes to service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_svc = AsyncMock(return_value=[])

    with (
        ctx_patch,
        patch("fabric_dw.services.query_insights.list_session_history", new=mock_svc),
    ):
        await mcp._tool_manager.call_tool(
            "list_session_history",
            {
                "workspace": _WS_NAME,
                "warehouse": _WH_NAME,
                "since": "2026-01-01T00:00:00",
                "until": "2026-06-01T00:00:00",
            },
        )

    mock_svc.assert_called_once()
    _, kwargs = mock_svc.call_args
    assert kwargs["since"] is not None
    assert kwargs["until"] is not None


async def test_list_session_history_bad_since(ctx_patch) -> None:
    """list_session_history raises ToolError on invalid ISO-8601 since."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_session_history",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "since": "bad-ts"},
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_list_session_history_bad_until(ctx_patch) -> None:
    """list_session_history raises ToolError on invalid ISO-8601 until."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_session_history",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "until": "bad-ts"},
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_list_session_history_fabric_error(mock_ctx, ctx_patch) -> None:
    """list_session_history wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.query_insights.list_session_history",
            new=AsyncMock(side_effect=FabricError("sql error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_session_history",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


async def test_list_session_history_workspace_not_allowed(ctx_patch) -> None:
    """list_session_history raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_session_history",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


# ---------------------------------------------------------------------------
# list_frequent_queries
# ---------------------------------------------------------------------------


async def test_list_frequent_queries_happy_path(mock_ctx, ctx_patch) -> None:
    """list_frequent_queries returns list of serialised frequent query dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    fq = _make_frequently_run_query()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.query_insights.list_frequent_queries",
            new=AsyncMock(return_value=[fq]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_frequent_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["number_of_runs"] == 10
    assert result[0]["number_of_successful_runs"] == 9


async def test_list_frequent_queries_with_since_until(mock_ctx, ctx_patch) -> None:
    """list_frequent_queries passes parsed datetimes and limit to service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_svc = AsyncMock(return_value=[])

    with (
        ctx_patch,
        patch("fabric_dw.services.query_insights.list_frequent_queries", new=mock_svc),
    ):
        await mcp._tool_manager.call_tool(
            "list_frequent_queries",
            {
                "workspace": _WS_NAME,
                "warehouse": _WH_NAME,
                "since": "2026-01-01T00:00:00",
                "until": "2026-06-01T00:00:00",
                "limit": 500,
            },
        )

    _, kwargs = mock_svc.call_args
    assert kwargs["since"] is not None
    assert kwargs["until"] is not None
    assert kwargs["limit"] == 500


async def test_list_frequent_queries_bad_since(ctx_patch) -> None:
    """list_frequent_queries raises ToolError on invalid ISO-8601 since."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_frequent_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "since": "bad"},
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_list_frequent_queries_bad_until(ctx_patch) -> None:
    """list_frequent_queries raises ToolError on invalid ISO-8601 until."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_frequent_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "until": "bad"},
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_list_frequent_queries_fabric_error(mock_ctx, ctx_patch) -> None:
    """list_frequent_queries wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.query_insights.list_frequent_queries",
            new=AsyncMock(side_effect=FabricError("sql error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_frequent_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


async def test_list_frequent_queries_workspace_not_allowed(ctx_patch) -> None:
    """list_frequent_queries raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_frequent_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


# ---------------------------------------------------------------------------
# list_long_running_queries
# ---------------------------------------------------------------------------


async def test_list_long_running_queries_happy_path(mock_ctx, ctx_patch) -> None:
    """list_long_running_queries returns list of serialised long-running query dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    lrq = _make_long_running_query()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.query_insights.list_long_running_queries",
            new=AsyncMock(return_value=[lrq]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_long_running_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["median_total_elapsed_time_ms"] == 5000
    assert result[0]["number_of_runs"] == 3


async def test_list_long_running_queries_with_since_until(mock_ctx, ctx_patch) -> None:
    """list_long_running_queries passes parsed datetimes and limit to service."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)
    mock_svc = AsyncMock(return_value=[])

    with (
        ctx_patch,
        patch("fabric_dw.services.query_insights.list_long_running_queries", new=mock_svc),
    ):
        await mcp._tool_manager.call_tool(
            "list_long_running_queries",
            {
                "workspace": _WS_NAME,
                "warehouse": _WH_NAME,
                "since": "2026-01-01T00:00:00",
                "until": "2026-06-01T00:00:00",
                "limit": 250,
            },
        )

    _, kwargs = mock_svc.call_args
    assert kwargs["since"] is not None
    assert kwargs["until"] is not None
    assert kwargs["limit"] == 250


async def test_list_long_running_queries_bad_since(ctx_patch) -> None:
    """list_long_running_queries raises ToolError on invalid ISO-8601 since."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_long_running_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "since": "bad"},
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_list_long_running_queries_bad_until(ctx_patch) -> None:
    """list_long_running_queries raises ToolError on invalid ISO-8601 until."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_long_running_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME, "until": "bad"},
        )

    assert "ISO-8601" in str(exc_info.value)


async def test_list_long_running_queries_fabric_error(mock_ctx, ctx_patch) -> None:
    """list_long_running_queries wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.query_insights.list_long_running_queries",
            new=AsyncMock(side_effect=FabricError("sql error")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_long_running_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


async def test_list_long_running_queries_workspace_not_allowed(ctx_patch) -> None:
    """list_long_running_queries raises ToolError when workspace not in allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "other-ws"}),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_long_running_queries",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )
