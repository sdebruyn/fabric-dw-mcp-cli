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
    QueryLock,
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "session_id": 42},
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "session_id": 42},
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "session_id": 99},
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "session_id": 5},
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "session_id": 5},
        )


@pytest.mark.parametrize("bad_session_id", [0, -1])
async def test_kill_session_rejects_non_positive_session_id(ctx_patch, bad_session_id: int) -> None:
    """kill_session raises ToolError for session_id=0 or session_id=-1 (Field(ge=1) bound)."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        ctx_patch,
        pytest.raises(ToolError, match="greater than or equal to 1"),
    ):
        await mcp._tool_manager.call_tool(
            "kill_session",
            {"workspace": _WS_NAME, "item": _WH_NAME, "session_id": bad_session_id},
        )


async def test_kill_session_accepts_positive_session_id(mock_ctx, ctx_patch) -> None:
    """kill_session accepts a positive session_id (ge=1 boundary: session_id=1 is valid)."""
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "session_id": 1},
        )

    assert result == {"killed": True, "session_id": 1}


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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
                "item": _WH_NAME,
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "since": "not-a-date"},
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "until": "bad-ts"},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
                "item": _WH_NAME,
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "since": "bad-ts"},
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "until": "bad-ts"},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
                "item": _WH_NAME,
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "since": "bad"},
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "until": "bad"},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
                "item": _WH_NAME,
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "since": "bad"},
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
            {"workspace": _WS_NAME, "item": _WH_NAME, "until": "bad"},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
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
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )


# ---------------------------------------------------------------------------
# Security: raw driver exceptions must not escape the MCP boundary
# ---------------------------------------------------------------------------
# Tools in queries.py call SQL-based services via run_query, which documents
# "Exception: Any other driver error is propagated unchanged" for unclassified
# driver exceptions. The tool boundary must catch and sanitise these before
# they reach the MCP client.


class _FakeDriverError(Exception):
    """Simulated raw driver exception (not FabricError, not ValueError)."""

    _INTERNAL_DETAIL = "ODBC 17: TDS protocol error; host=wh.fabric.microsoft.com; state=08S01"


_RAW_DRIVER_EXC = _FakeDriverError(_FakeDriverError._INTERNAL_DETAIL)


async def test_list_running_queries_raw_driver_exc_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """A raw driver exception from list_running is converted to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_running",
            new=AsyncMock(side_effect=_RAW_DRIVER_EXC),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_running_queries",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )

    assert _FakeDriverError._INTERNAL_DETAIL not in str(exc_info.value), (
        "raw driver exception detail must not appear in the ToolError message"
    )


async def test_list_connections_raw_driver_exc_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """A raw driver exception from list_connections is converted to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_connections",
            new=AsyncMock(side_effect=_RAW_DRIVER_EXC),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_connections",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )

    assert _FakeDriverError._INTERNAL_DETAIL not in str(exc_info.value), (
        "raw driver exception detail must not appear in the ToolError message"
    )


async def test_kill_session_raw_driver_exc_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """A raw driver exception from queries.kill is converted to ToolError without leaking."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.kill",
            new=AsyncMock(side_effect=_RAW_DRIVER_EXC),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "kill_session",
            {"workspace": _WS_NAME, "item": _WH_NAME, "session_id": 42},
        )

    assert _FakeDriverError._INTERNAL_DETAIL not in str(exc_info.value), (
        "raw driver exception detail must not appear in the ToolError message"
    )


# ---------------------------------------------------------------------------
# list_locks
# ---------------------------------------------------------------------------


def _make_query_lock() -> QueryLock:
    return QueryLock.model_validate(
        {
            "session_id": 42,
            "resource_type": "OBJECT",
            "request_mode": "S",
            "request_status": "GRANT",
            "schema_name": "dbo",
            "object_name": "sales",
            "blocking_session_id": None,
            "wait_type": None,
            "wait_time": None,
            "command": "SELECT",
        }
    )


async def test_list_locks_happy_path(mock_ctx, ctx_patch) -> None:
    """list_locks returns list of serialised lock dicts."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    lock = _make_query_lock()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_locks",
            new=AsyncMock(return_value=[lock]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_locks",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["session_id"] == 42
    assert result[0]["resource_type"] == "OBJECT"


async def test_list_locks_returns_dicts(mock_ctx, ctx_patch) -> None:
    """list_locks returns plain dicts, not model instances."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    lock = _make_query_lock()
    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_locks",
            new=AsyncMock(return_value=[lock]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_locks",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )

    for row in result:
        assert isinstance(row, dict)


async def test_list_locks_fabric_error_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """list_locks wraps FabricError as ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_locks",
            new=AsyncMock(side_effect=FabricError("lock query failed")),
        ),
        pytest.raises(ToolError),
    ):
        await mcp._tool_manager.call_tool(
            "list_locks",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )


async def test_list_locks_empty_result(mock_ctx, ctx_patch) -> None:
    """list_locks returns empty list when no locks are held."""
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    item = make_item_entry()
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=item)

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_locks",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "list_locks",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )

    assert result == []


async def test_list_locks_raw_driver_exc_raises_tool_error(mock_ctx, ctx_patch) -> None:
    """A raw driver exception from list_locks is converted to ToolError."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_ctx.resolver.item = AsyncMock(return_value=make_item_entry())

    with (
        ctx_patch,
        patch(
            "fabric_dw.services.queries.list_locks",
            new=AsyncMock(side_effect=_RAW_DRIVER_EXC),
        ),
        pytest.raises(ToolError) as exc_info,
    ):
        await mcp._tool_manager.call_tool(
            "list_locks",
            {"workspace": _WS_NAME, "item": _WH_NAME},
        )

    assert _FakeDriverError._INTERNAL_DETAIL not in str(exc_info.value), (
        "raw driver exception detail must not appear in the ToolError message"
    )
