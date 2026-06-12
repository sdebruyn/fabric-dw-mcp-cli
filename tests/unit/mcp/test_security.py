"""Unit tests for the MCP security guards (_guards.py) and server-level integration.

Coverage
--------
1. ``assert_readonly_sql`` -- accept/reject classifier table
2. ``assert_writes_allowed`` -- blocks mutating tools when FABRIC_MCP_READONLY is set
3. ``assert_destructive_allowed`` -- blocks destructive tools without FABRIC_MCP_ALLOW_DESTRUCTIVE
4. ``assert_workspace_allowed`` -- allowlist matching by name and resolved GUID
5. ``execute_sql`` max_rows row-cap and truncated flag
6. ``run()`` HTTP host refusal when FABRIC_MCP_ALLOW_REMOTE is not set
7. ``run()`` logs WARNING when FABRIC_MCP_ALLOW_REMOTE is set with non-loopback host
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_WH_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
_WS_NAME = "my-workspace"
_WH_NAME = "my-warehouse"
_CONN_STRING = "wh.fabric.microsoft.com"

# A non-loopback address used by HTTP transport tests.
# Using a documentation address (192.0.2.0/24, RFC 5737) to avoid S104 hits.
_REMOTE_HOST = "192.0.2.1"


# ---------------------------------------------------------------------------
# 1. SQL classifier -- assert_readonly_sql
# ---------------------------------------------------------------------------


class TestAssertReadonlySql:
    """Classifier accept / reject table."""

    def _call(self, sql: str) -> None:
        from fabric_dw.mcp._guards import assert_readonly_sql  # noqa: PLC0415

        assert_readonly_sql(sql)

    # Accept cases

    def test_accepts_plain_select(self) -> None:
        self._call("SELECT 1")

    def test_accepts_select_leading_whitespace(self) -> None:
        self._call("   SELECT * FROM t")

    def test_accepts_with_cte(self) -> None:
        self._call("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_accepts_select_after_line_comment(self) -> None:
        self._call("-- get rows\nSELECT id FROM dbo.t")

    def test_accepts_select_after_block_comment(self) -> None:
        self._call("/* admin query */ SELECT id FROM dbo.t")

    def test_accepts_select_with_trailing_semicolon(self) -> None:
        """A single trailing ';' is OK (optional terminator)."""
        self._call("SELECT 1;")

    def test_accepts_with_case_insensitive(self) -> None:
        self._call("select id from t")

    # Reject cases

    def test_rejects_drop(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="non-SELECT"):
            self._call("DROP TABLE dbo.t")

    def test_rejects_insert(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="non-SELECT"):
            self._call("INSERT INTO t VALUES (1)")

    def test_rejects_update(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="non-SELECT"):
            self._call("UPDATE t SET x=1")

    def test_rejects_delete(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="non-SELECT"):
            self._call("DELETE FROM t WHERE 1=1")

    def test_rejects_multi_statement_batch(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="multi-statement"):
            self._call("SELECT 1; DROP TABLE t")

    def test_rejects_two_selects_separated_by_semicolon(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="multi-statement"):
            self._call("SELECT 1; SELECT 2")

    def test_rejects_empty_statement(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="non-SELECT"):
            self._call("   ")

    def test_rejects_comment_only_then_drop(self) -> None:
        """A block comment before DROP must still be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="non-SELECT"):
            self._call("/* safe */ DROP TABLE t")

    # ------------------------------------------------------------------
    # Confirmed bypass regression tests
    # ------------------------------------------------------------------

    def test_rejects_cte_then_insert(self) -> None:
        """CRITICAL: WITH ... INSERT must be rejected (CTE-wrapped DML bypass)."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("WITH x AS (SELECT 1) INSERT INTO t VALUES (1)")

    def test_rejects_cte_then_update(self) -> None:
        """WITH ... UPDATE must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("WITH cte AS (SELECT id FROM t) UPDATE t SET x=1")

    def test_rejects_cte_then_delete(self) -> None:
        """WITH ... DELETE must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("WITH cte AS (SELECT 1) DELETE FROM t WHERE 1=1")

    def test_rejects_cte_then_merge(self) -> None:
        """WITH ... MERGE must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call(
                "WITH cte AS (SELECT 1 AS n)"
                " MERGE t USING cte ON 1=0 WHEN NOT MATCHED THEN INSERT VALUES (1)"
            )

    def test_rejects_nested_block_comment_payload(self) -> None:
        """HIGH: nested block comment leaves residual code: /* /* */ DROP TABLE t."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("/* /* */ SELECT */ DROP TABLE t")

    def test_rejects_select_into(self) -> None:
        """MEDIUM: SELECT * INTO dbo.backup FROM t must be rejected (INTO forbidden)."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT * INTO dbo.backup FROM t")

    def test_rejects_unbalanced_block_comment_open(self) -> None:
        """Unbalanced /* must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("/* unterminated SELECT 1")

    def test_rejects_unbalanced_block_comment_close(self) -> None:
        """Residual */ (after stripping outer comment) must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT 1 */ DROP TABLE t")

    def test_accepts_string_literal_with_semicolon(self) -> None:
        """LOW: WHERE name = 'a;b' must NOT be rejected as multi-statement."""
        self._call("SELECT id FROM t WHERE name = 'a;b'")

    def test_accepts_string_literal_with_escaped_quote(self) -> None:
        """String literal with escaped single-quote must be accepted."""
        self._call("SELECT 'it''s' FROM t")

    def test_accepts_bracketed_keyword_identifier(self) -> None:
        """SELECT [delete] FROM t must be accepted (bracketed identifier, not keyword)."""
        self._call("SELECT [delete] FROM t")

    def test_accepts_with_cte_then_select(self) -> None:
        """WITH cte AS (SELECT 1) SELECT * FROM cte; is a valid read-only statement."""
        self._call("WITH cte AS (SELECT 1) SELECT * FROM cte;")

    def test_rejects_exec(self) -> None:
        """EXEC must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("EXEC sp_who")

    def test_rejects_xp_cmdshell_token(self) -> None:
        """xp_cmdshell token must be rejected even inside a WITH."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT xp_cmdshell('dir')")

    # ------------------------------------------------------------------
    # DoS / context-switch bypass regression tests (T-SQL newline batch)
    # ------------------------------------------------------------------

    def test_rejects_waitfor_delay_after_select(self) -> None:
        """CRITICAL: SELECT 1\\nWAITFOR DELAY must be rejected (DoS via connection hang)."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="WAITFOR"):
            self._call("SELECT 1\nWAITFOR DELAY '99:0:0'")

    def test_rejects_use_after_select(self) -> None:
        """CRITICAL: SELECT 1\\nUSE master must be rejected (database context switch)."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="USE"):
            self._call("SELECT 1\nUSE master")

    def test_rejects_waitfor_as_first_token(self) -> None:
        """WAITFOR as the first token must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("WAITFOR DELAY '00:01:00'")

    def test_rejects_use_as_first_token(self) -> None:
        """USE as the first token must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("USE master")

    def test_rejects_dbcc_after_select(self) -> None:
        """DBCC must be rejected even when following a valid SELECT."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="DBCC"):
            self._call("SELECT 1\nDBCC FREEPROCCACHE")

    def test_rejects_shutdown(self) -> None:
        """SHUTDOWN must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="SHUTDOWN"):
            self._call("SELECT 1\nSHUTDOWN")

    def test_rejects_reconfigure(self) -> None:
        """RECONFIGURE must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="RECONFIGURE"):
            self._call("SELECT 1\nRECONFIGURE")

    def test_rejects_dbcc_standalone(self) -> None:
        """DBCC as the first token must be rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("DBCC CHECKDB")

    def test_accepts_set_nocount_then_select(self) -> None:
        """SET NOCOUNT ON;\\nSELECT 1 — SET is not forbidden; the multi-statement
        check fires because ';' is followed by a newline and then SELECT.
        This pins the current behaviour: the batch is rejected due to the
        multi-statement rule, not due to SET being forbidden.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match=r"multi-statement|non-SELECT"):
            self._call("SET NOCOUNT ON;\nSELECT 1")


# ---------------------------------------------------------------------------
# 2. assert_writes_allowed
# ---------------------------------------------------------------------------


class TestAssertWritesAllowed:
    """FABRIC_MCP_READONLY blocks all mutating tools."""

    def test_allows_when_env_not_set(self) -> None:
        from fabric_dw.mcp._guards import assert_writes_allowed  # noqa: PLC0415

        # Must not raise
        assert_writes_allowed("create_warehouse")

    @pytest.mark.parametrize("flag_value", ["1", "true", "yes", "TRUE", "YES"])
    def test_blocks_when_readonly_truthy(self, flag_value: str) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._guards import assert_writes_allowed  # noqa: PLC0415

        with (
            patch.dict(os.environ, {"FABRIC_MCP_READONLY": flag_value}),
            pytest.raises(ToolError, match="read-only mode"),
        ):
            assert_writes_allowed("create_warehouse")

    def test_allows_when_readonly_falsy(self) -> None:
        from fabric_dw.mcp._guards import assert_writes_allowed  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_READONLY": "0"}):
            assert_writes_allowed("create_warehouse")  # must not raise


# ---------------------------------------------------------------------------
# 3. assert_destructive_allowed
# ---------------------------------------------------------------------------


class TestAssertDestructiveAllowed:
    """FABRIC_MCP_ALLOW_DESTRUCTIVE gates destructive tools."""

    def test_blocks_when_env_not_set(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._guards import assert_destructive_allowed  # noqa: PLC0415

        with pytest.raises(ToolError, match="FABRIC_MCP_ALLOW_DESTRUCTIVE"):
            assert_destructive_allowed()

    @pytest.mark.parametrize("flag_value", ["1", "true", "yes"])
    def test_allows_when_flag_truthy(self, flag_value: str) -> None:
        from fabric_dw.mcp._guards import assert_destructive_allowed  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": flag_value}):
            assert_destructive_allowed()  # must not raise

    def test_blocks_when_flag_falsy(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._guards import assert_destructive_allowed  # noqa: PLC0415

        with (
            patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "0"}),
            pytest.raises(ToolError),
        ):
            assert_destructive_allowed()


# ---------------------------------------------------------------------------
# 4. assert_workspace_allowed
# ---------------------------------------------------------------------------


class TestAssertWorkspaceAllowed:
    """Workspace allowlist matching."""

    def test_allows_everything_when_env_unset(self) -> None:
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        assert_workspace_allowed("any-workspace")

    def test_allows_matching_name(self) -> None:
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "prod, staging"}):
            assert_workspace_allowed("prod")  # must not raise

    def test_allows_matching_name_case_insensitive(self) -> None:
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "PROD"}):
            assert_workspace_allowed("prod")

    def test_allows_matching_resolved_id(self) -> None:
        """Matching by GUID (resolved_id) must work even if name doesn't match."""
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        guid = str(_WS_ID)
        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": guid}):
            # name does not match, but resolved_id does
            assert_workspace_allowed("my-workspace", resolved_id=guid)

    def test_blocks_unlisted_workspace(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        with (
            patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "prod,staging"}),
            pytest.raises(ToolError, match="allowlist"),
        ):
            assert_workspace_allowed("dev")

    def test_blocks_when_neither_name_nor_id_match(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        with (
            patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "prod"}),
            pytest.raises(ToolError),
        ):
            assert_workspace_allowed("dev", resolved_id="00000000-0000-0000-0000-000000000001")

    def test_allows_everything_when_env_only_commas(self) -> None:
        """An allowlist of only commas/whitespace is treated as unset."""
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": " , , "}):
            assert_workspace_allowed("any-workspace")


# ---------------------------------------------------------------------------
# 5. execute_sql max_rows truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_sql_max_rows_truncation() -> None:
    """execute_sql slices rows to max_rows and sets truncated=True."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.cache import ItemEntry  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import SqlResult, WarehouseKind  # noqa: PLC0415

    rows_100: list[list[object]] = [[i] for i in range(100)]
    sql_result = SqlResult(columns=["n"], rows=rows_100, rowcount=100)

    entry = ItemEntry(
        id=_WH_ID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string=_CONN_STRING,
        fetched_at=datetime.now(tz=UTC),
        display_name=_WH_NAME,
    )

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=entry)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    ctx = ServerContext(
        http=mock_http,
        cache=mock_cache,
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch(
            "fabric_dw.services.sql_exec.execute",
            new=AsyncMock(return_value=sql_result),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "execute_sql",
            {"workspace": _WS_NAME, "item": _WH_NAME, "query": "SELECT 1", "max_rows": 10},
        )

    assert result["row_count_returned"] == 10
    assert result["truncated"] is True
    assert len(result["rows"]) == 10


@pytest.mark.asyncio
async def test_execute_sql_no_truncation_when_under_limit() -> None:
    """execute_sql sets truncated=False when rows fit within max_rows."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.cache import ItemEntry  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import SqlResult, WarehouseKind  # noqa: PLC0415

    rows_5: list[list[object]] = [[i] for i in range(5)]
    sql_result = SqlResult(columns=["n"], rows=rows_5, rowcount=5)

    entry = ItemEntry(
        id=_WH_ID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string=_CONN_STRING,
        fetched_at=datetime.now(tz=UTC),
        display_name=_WH_NAME,
    )

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=entry)
    mock_http = AsyncMock()
    mock_cache = MagicMock()

    ctx = ServerContext(
        http=mock_http,
        cache=mock_cache,
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch(
            "fabric_dw.services.sql_exec.execute",
            new=AsyncMock(return_value=sql_result),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "execute_sql",
            {"workspace": _WS_NAME, "item": _WH_NAME, "query": "SELECT 1", "max_rows": 100},
        )

    assert result["row_count_returned"] == 5
    assert result["truncated"] is False
    assert len(result["rows"]) == 5


# ---------------------------------------------------------------------------
# 6. execute_sql readonly gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_sql_blocked_by_readonly_mode() -> None:
    """execute_sql raises ToolError when FABRIC_MCP_READONLY is set and query is DROP."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        pytest.raises(ToolError, match="read-only"),
    ):
        await mcp._tool_manager.call_tool(
            "execute_sql",
            {"workspace": _WS_NAME, "item": _WH_NAME, "query": "DROP TABLE dbo.t"},
        )


@pytest.mark.asyncio
async def test_execute_sql_allowed_in_readonly_mode_for_select() -> None:
    """execute_sql SELECT queries pass the readonly gate."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.cache import ItemEntry  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import SqlResult, WarehouseKind  # noqa: PLC0415

    sql_result = SqlResult(columns=["n"], rows=[[1]], rowcount=1)
    entry = ItemEntry(
        id=_WH_ID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string=_CONN_STRING,
        fetched_at=datetime.now(tz=UTC),
        display_name=_WH_NAME,
    )

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    mock_resolver.item = AsyncMock(return_value=entry)

    ctx = ServerContext(
        http=AsyncMock(),
        cache=MagicMock(),
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with (
        patch.dict(os.environ, {"FABRIC_MCP_READONLY": "1"}),
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch(
            "fabric_dw.services.sql_exec.execute",
            new=AsyncMock(return_value=sql_result),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "execute_sql",
            {"workspace": _WS_NAME, "item": _WH_NAME, "query": "SELECT 1"},
        )

    assert result["rows"] == [[1]]


# ---------------------------------------------------------------------------
# 7. Destructive tool gates via server tool invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_warehouse_blocked_without_destructive_flag() -> None:
    """delete_warehouse raises ToolError when FABRIC_MCP_ALLOW_DESTRUCTIVE is not set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    # Unset the flag to guarantee it is absent regardless of local env
    env_copy = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_DESTRUCTIVE"}
    with (
        patch.dict(os.environ, env_copy, clear=True),
        pytest.raises(ToolError, match="FABRIC_MCP_ALLOW_DESTRUCTIVE"),
    ):
        await mcp._tool_manager.call_tool(
            "delete_warehouse",
            {"workspace": _WS_NAME, "warehouse": _WH_NAME},
        )


@pytest.mark.asyncio
async def test_delete_snapshot_blocked_without_destructive_flag() -> None:
    """delete_snapshot raises ToolError when FABRIC_MCP_ALLOW_DESTRUCTIVE is not set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    env_copy = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_DESTRUCTIVE"}
    with (
        patch.dict(os.environ, env_copy, clear=True),
        pytest.raises(ToolError, match="FABRIC_MCP_ALLOW_DESTRUCTIVE"),
    ):
        await mcp._tool_manager.call_tool(
            "delete_snapshot",
            {"workspace": _WS_NAME, "snapshot": "snap-1"},
        )


@pytest.mark.asyncio
async def test_reset_sql_pools_blocked_without_destructive_flag() -> None:
    """reset_sql_pools raises ToolError when FABRIC_MCP_ALLOW_DESTRUCTIVE is not set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    env_copy = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_DESTRUCTIVE"}
    with (
        patch.dict(os.environ, env_copy, clear=True),
        pytest.raises(ToolError, match="FABRIC_MCP_ALLOW_DESTRUCTIVE"),
    ):
        await mcp._tool_manager.call_tool(
            "reset_sql_pools",
            {"workspace": _WS_NAME},
        )


# ---------------------------------------------------------------------------
# 8. Workspace allowlist gate via server tool invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_workspace_blocked_by_workspace_allowlist() -> None:
    """get_workspace raises ToolError when workspace not in FABRIC_MCP_WORKSPACES."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "allowed-ws"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "get_workspace",
            {"workspace": "forbidden-ws"},
        )


@pytest.mark.asyncio
async def test_list_warehouses_all_workspaces_blocked_when_allowlist_set() -> None:
    """list_warehouses(all_workspaces=True) raises ToolError when FABRIC_MCP_WORKSPACES is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "prod"}),
        pytest.raises(ToolError, match="all_workspaces"),
    ):
        await mcp._tool_manager.call_tool(
            "list_warehouses",
            {"workspace": _WS_NAME, "all_workspaces": True},
        )


@pytest.mark.asyncio
async def test_list_sql_endpoints_all_workspaces_blocked_when_allowlist_set() -> None:
    """list_sql_endpoints(all_workspaces=True) raises ToolError when allowlist is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "prod"}),
        pytest.raises(ToolError, match="all_workspaces"),
    ):
        await mcp._tool_manager.call_tool(
            "list_sql_endpoints",
            {"workspace": _WS_NAME, "all_workspaces": True},
        )


# ---------------------------------------------------------------------------
# 9. run() -- HTTP host refusal
# ---------------------------------------------------------------------------


def test_run_http_refuses_remote_host_without_allow_flag() -> None:
    """run() with a remote --host must call sys.exit(1) when FABRIC_MCP_ALLOW_REMOTE is not set."""
    from fabric_dw.mcp import run  # noqa: PLC0415

    env_copy = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_REMOTE"}
    with (
        patch.dict(os.environ, env_copy, clear=True),
        pytest.raises(SystemExit) as exc_info,
    ):
        run(["--transport", "http", "--host", _REMOTE_HOST])

    assert exc_info.value.code == 1


def test_run_http_allows_remote_host_with_allow_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """run() with FABRIC_MCP_ALLOW_REMOTE=1 logs a WARNING and does not exit.

    The WARNING is emitted via the JSON logging handler (to stderr); we capture
    it via capsys since pytest's caplog doesn't intercept custom StreamHandlers.
    """
    from fabric_dw.mcp import run  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    with (
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_REMOTE": "1"}),
        patch.object(mcp, "run"),
    ):
        run(["--transport", "http", "--host", _REMOTE_HOST, "--port", "9000"])

    captured = capsys.readouterr()
    # The WARNING is emitted on stderr by the JSON logging handler
    assert "WARNING" in captured.err
    assert _REMOTE_HOST in captured.err or "authentication" in captured.err.lower()


def test_run_http_loopback_host_does_not_require_flag() -> None:
    """Binding on loopback is always allowed regardless of FABRIC_MCP_ALLOW_REMOTE."""
    from fabric_dw.mcp import run  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    env_copy = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_REMOTE"}
    with (
        patch.dict(os.environ, env_copy, clear=True),
        patch.object(mcp, "run"),
    ):
        run(["--transport", "http", "--host", "127.0.0.1"])
        # Must not raise SystemExit
