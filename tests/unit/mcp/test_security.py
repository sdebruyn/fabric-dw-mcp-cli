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
from pathlib import Path
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

    def test_rejects_select_after_line_comment(self) -> None:
        """FLIPPED (Option A): a leading '--' comment makes the first token 'get', not SELECT.

        The fully-raw scan has no comment-stripping step, so a statement that
        begins with a line comment is rejected because its first word token is
        not SELECT or WITH.  Unset FABRIC_MCP_READONLY for such queries.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="non-SELECT"):
            self._call("-- get rows\nSELECT id FROM dbo.t")

    def test_rejects_select_after_block_comment(self) -> None:
        """FLIPPED (Option A): a leading block comment makes the first token 'admin', not SELECT.

        No comment stripping means the raw first word is from the comment body.
        Unset FABRIC_MCP_READONLY for such queries.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="non-SELECT"):
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

    def test_rejects_semicolon_inside_string_literal_pre_fix(self) -> None:
        """FLIPPED: WHERE name = 'a;b' is now REJECTED (fail-closed raw-scan policy).

        Previously this was allowed because the string-literal masking hid the
        semicolon.  After the fix, the raw text is scanned and the semicolon
        trips the multi-statement guard.  Unset FABRIC_MCP_READONLY for such
        queries.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="multi-statement"):
            self._call("SELECT id FROM t WHERE name = 'a;b'")

    def test_accepts_string_literal_with_escaped_quote(self) -> None:
        """String literal with escaped single-quote and no forbidden keyword is accepted."""
        self._call("SELECT 'it''s' FROM t")

    def test_rejects_bracketed_keyword_identifier(self) -> None:
        """FLIPPED: SELECT [delete] FROM t is now REJECTED (fail-closed raw-scan policy).

        Previously this was allowed because the bracket-identifier masking
        replaced [delete] with [x] before the token scan.  After the fix, the
        raw text is scanned and the token 'delete' is found inside the brackets.
        Unset FABRIC_MCP_READONLY for such queries.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
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

    # ------------------------------------------------------------------
    # Fail-closed behavior: previously ALLOWED, now REJECTED
    # ------------------------------------------------------------------

    def test_rejects_forbidden_keyword_in_string_literal(self) -> None:
        """SECURITY: a forbidden keyword inside a string literal must be rejected.

        The raw-scan policy fails closed: read-only mode rejects queries that
        embed a write keyword in a string literal.  Unset FABRIC_MCP_READONLY
        to run such queries.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT * FROM cdc WHERE op='DELETE'")

    def test_rejects_forbidden_keyword_as_bracket_identifier(self) -> None:
        """SECURITY: a forbidden keyword used as a bracket-quoted identifier is rejected.

        Raw-scan policy: the token scanner sees 'delete' from [delete] and blocks it.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT [delete] FROM t")

    def test_rejects_forbidden_keyword_as_dquote_identifier(self) -> None:
        """SECURITY: a forbidden keyword used as a double-quoted identifier is rejected."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call('SELECT "drop" FROM t')

    def test_rejects_semicolon_inside_string_literal(self) -> None:
        """SECURITY: a semicolon inside a string literal now trips the multi-statement guard.

        Raw-scan policy: ';' in any context (including string literals) is rejected.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="multi-statement"):
            self._call("SELECT id FROM t WHERE name = 'a;b'")

    # ------------------------------------------------------------------
    # Bracket/double-quote identifier bypass regression tests (#788)
    # ------------------------------------------------------------------

    def test_rejects_bracket_quote_trick_delete(self) -> None:
        """CRITICAL #788: bracket-identifier with embedded quote hides DELETE."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT [a'];DELETE FROM t WHERE c='x'")

    def test_rejects_bracket_quote_trick_drop(self) -> None:
        """CRITICAL #788: bracket-identifier with embedded quote hides DROP."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT [a'];DROP TABLE t WHERE c='x'")

    def test_rejects_bracket_quote_trick_update(self) -> None:
        """CRITICAL #788: bracket-identifier with embedded quote hides UPDATE."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT [a'];UPDATE t SET x=1 WHERE c='x'")

    def test_rejects_bracket_quote_trick_insert(self) -> None:
        """CRITICAL #788: bracket-identifier with embedded quote hides INSERT."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT [a'];INSERT INTO t VALUES (1) WHERE c='x'")

    def test_rejects_bracket_quote_trick_merge(self) -> None:
        """CRITICAL #788: bracket-identifier with embedded quote hides MERGE."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT [a'];MERGE t USING s ON 1=0 WHERE c='x'")

    def test_rejects_bracket_quote_trick_truncate(self) -> None:
        """CRITICAL #788: bracket-identifier with embedded quote hides TRUNCATE."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT [a'];TRUNCATE TABLE t WHERE c='x'")

    def test_rejects_bracket_quote_trick_exec(self) -> None:
        """CRITICAL #788: bracket-identifier with embedded quote hides EXEC."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT [a'];EXEC sp_who WHERE c='x'")

    def test_rejects_dquote_identifier_bypass(self) -> None:
        """CRITICAL #788: double-quote identifier bypass hides DELETE."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT \"a'\";DELETE FROM t WHERE c='x'")

    def test_rejects_string_literal_hides_keyword(self) -> None:
        """CRITICAL #788: string literal that previously hid a forbidden keyword."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError):
            self._call("SELECT * FROM t WHERE x='DROP TABLE t--'")

    # ------------------------------------------------------------------
    # #797 bypass regression tests -- string-aware comment delimiter bypass
    # ------------------------------------------------------------------

    def test_rejects_string_line_comment_hides_delete(self) -> None:
        """CRITICAL #797: string literal containing '--' hides a ';DELETE' rider.

        The old _sanitise stripped '--';DELETE FROM t as a line comment starting
        at the '--' inside the string, removing the semicolon-separated DML.
        The fully-raw scan catches the semicolon immediately.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="multi-statement"):
            self._call("SELECT '--';DELETE FROM t")

    def test_rejects_string_block_comment_hides_drop(self) -> None:
        """CRITICAL #797: string literals forming fake block-comment delimiters hide DROP.

        SELECT '/*' AS a;DROP TABLE t;SELECT '*/' AS b -- the old iterative
        block-comment stripper consumed '/*' ... '*/' spanning the DML payload.
        The fully-raw scan catches the first semicolon.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="multi-statement"):
            self._call("SELECT '/*' AS a;DROP TABLE t;SELECT '*/' AS b")

    def test_rejects_string_block_comment_hides_update(self) -> None:
        """CRITICAL #797: fake block-comment delimiters in strings hide UPDATE."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="multi-statement"):
            self._call("SELECT '/*' AS a;UPDATE t SET x=1;SELECT '*/' AS b")

    def test_rejects_string_block_comment_hides_insert(self) -> None:
        """CRITICAL #797: fake block-comment delimiters in strings hide INSERT."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="multi-statement"):
            self._call("SELECT '/*' x;INSERT INTO t VALUES(1);SELECT '*/' y")

    # ------------------------------------------------------------------
    # Legitimate plain reads that must still PASS after fix
    # ------------------------------------------------------------------

    def test_accepts_plain_select_after_fix(self) -> None:
        """Regression: basic SELECT must still be allowed."""
        self._call("SELECT * FROM t")

    def test_accepts_select_1_after_fix(self) -> None:
        """Regression: bare SELECT 1 must still be allowed."""
        self._call("select 1")

    def test_accepts_with_cte_then_select_after_fix(self) -> None:
        """Regression: CTE followed by SELECT must still be allowed."""
        self._call("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_accepts_multiline_select_after_fix(self) -> None:
        """Regression: multi-line SELECT must still be allowed."""
        self._call("SELECT\n    id,\n    name\nFROM dbo.users\nWHERE active = 1")

    def test_rejects_select_with_leading_block_comment_after_fix(self) -> None:
        """FLIPPED (Option A): a leading block comment causes rejection.

        '/* admin */ SELECT id FROM t -- end' has 'admin' as its first raw
        token, which is not SELECT or WITH.  Rejected by the non-SELECT gate.
        Trailing '-- end' contains no forbidden keyword so that part would pass,
        but the leading comment fails first.  Unset FABRIC_MCP_READONLY to run.
        """
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        with pytest.raises(ToolError, match="non-SELECT"):
            self._call("/* admin */ SELECT id FROM t -- end")


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
# 4b. resolve_workspace_allowlist — 3-layer precedence
# ---------------------------------------------------------------------------


class TestResolveWorkspaceAllowlist:
    """3-layer resolution: env > config > no restriction."""

    def test_no_restriction_when_both_absent(self) -> None:
        """Unset env AND unset config → None (no restriction)."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            assert resolve_workspace_allowlist(None) is None

    def test_env_wins_over_config(self) -> None:
        """Non-empty env var takes precedence over config."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "env-ws"}):
            result = resolve_workspace_allowlist(["config-ws"])
        assert result == frozenset({"env-ws"})

    def test_config_used_when_env_absent(self) -> None:
        """When env is absent, config layer provides the allowlist."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            result = resolve_workspace_allowlist(["Sales WS", "Finance WS"])
        assert result == frozenset({"sales ws", "finance ws"})

    def test_empty_env_falls_through_to_config(self) -> None:
        """Empty FABRIC_MCP_WORKSPACES= falls through to config layer."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": ""}):
            result = resolve_workspace_allowlist(["config-ws"])
        assert result == frozenset({"config-ws"})

    def test_whitespace_only_env_falls_through_to_config(self) -> None:
        """Whitespace-only env value falls through to config, not no-restriction."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "   "}):
            result = resolve_workspace_allowlist(["config-ws"])
        assert result == frozenset({"config-ws"})

    def test_comma_only_env_falls_through_to_config(self) -> None:
        """Comma-only env value falls through to config."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": " , , "}):
            result = resolve_workspace_allowlist(["config-ws"])
        assert result == frozenset({"config-ws"})

    def test_empty_config_list_means_no_restriction(self) -> None:
        """Empty TOML array [] is treated as absent — no restriction, NOT block-all."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            assert resolve_workspace_allowlist([]) is None

    def test_empty_env_and_empty_config_means_no_restriction(self) -> None:
        """Both empty env and empty config → no restriction."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": ""}):
            assert resolve_workspace_allowlist([]) is None

    def test_non_empty_allowlist_restricts(self) -> None:
        """Non-empty allowlist returns a frozenset of lower-cased entries."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "  Sales WS , Finance WS  "}):
            result = resolve_workspace_allowlist(None)
        assert result == frozenset({"sales ws", "finance ws"})

    def test_config_entries_trimmed_and_lowercased(self) -> None:
        """Config entries are trimmed and lowercased before comparison."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            result = resolve_workspace_allowlist(["  PROD  ", " Staging "])
        assert result == frozenset({"prod", "staging"})


class TestWorkspaceAllowlistActive:
    """workspace_allowlist_active mirrors resolve_workspace_allowlist semantics."""

    def test_false_when_no_restriction(self) -> None:
        from fabric_dw.mcp._guards import workspace_allowlist_active  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            assert workspace_allowlist_active(None) is False

    def test_true_when_env_set(self) -> None:
        from fabric_dw.mcp._guards import workspace_allowlist_active  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "prod"}):
            assert workspace_allowlist_active(None) is True

    def test_true_when_config_set(self) -> None:
        from fabric_dw.mcp._guards import workspace_allowlist_active  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            assert workspace_allowlist_active(["prod"]) is True

    def test_false_when_empty_env_and_no_config(self) -> None:
        from fabric_dw.mcp._guards import workspace_allowlist_active  # noqa: PLC0415

        with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": ""}):
            assert workspace_allowlist_active(None) is False

    def test_false_when_empty_config_list(self) -> None:
        from fabric_dw.mcp._guards import workspace_allowlist_active  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            assert workspace_allowlist_active([]) is False


class TestAssertWorkspaceAllowedConfigLayer:
    """assert_workspace_allowed 3-layer semantics."""

    def test_config_layer_blocks_unlisted(self) -> None:
        """Config-only allowlist blocks workspaces not in the list."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with (
            patch.dict(os.environ, env_without_workspaces, clear=True),
            pytest.raises(ToolError, match="allowlist"),
        ):
            assert_workspace_allowed("dev", config_allowlist=["prod", "staging"])

    def test_config_layer_allows_listed(self) -> None:
        """Config-only allowlist permits workspaces in the list."""
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            assert_workspace_allowed("prod", config_allowlist=["prod", "staging"])

    def test_env_overrides_config(self) -> None:
        """Env allowlist overrides config; workspace in config but not env is blocked."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        with (
            patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "env-ws"}),
            pytest.raises(ToolError, match="allowlist"),
        ):
            # "config-ws" is in the config layer but not the env layer — env wins
            assert_workspace_allowed("config-ws", config_allowlist=["config-ws"])

    def test_no_restriction_when_both_absent(self) -> None:
        """No env, no config → all workspaces allowed."""
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            assert_workspace_allowed("any-ws", config_allowlist=None)

    def test_empty_env_falls_through_config_blocks(self) -> None:
        """Empty env falls through to config; config restriction is applied."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        with (
            patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": ""}),
            pytest.raises(ToolError, match="allowlist"),
        ):
            assert_workspace_allowed("dev", config_allowlist=["prod"])


# ---------------------------------------------------------------------------
# 5. execute_sql max_rows truncation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 8. Workspace allowlist gate via server tool invocation
# ---------------------------------------------------------------------------


async def test_get_workspace_blocked_by_workspace_allowlist() -> None:
    """get_workspace raises ToolError when workspace not in FABRIC_MCP_WORKSPACES."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    mock_resolver = AsyncMock()
    mock_resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    ctx = ServerContext(
        http=AsyncMock(),
        cache=MagicMock(),
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "allowed-ws"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "get_workspace",
            {"workspace": "forbidden-ws"},
        )


async def test_list_warehouses_all_workspaces_blocked_when_allowlist_set() -> None:
    """list_warehouses(all_workspaces=True) raises ToolError when FABRIC_MCP_WORKSPACES is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = ServerContext(
        http=AsyncMock(),
        cache=MagicMock(),
        resolver=AsyncMock(),
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "prod"}),
        pytest.raises(ToolError, match="all_workspaces"),
    ):
        await mcp._tool_manager.call_tool(
            "list_warehouses",
            {"workspace": _WS_NAME, "all_workspaces": True},
        )


async def test_list_sql_endpoints_all_workspaces_blocked_when_allowlist_set() -> None:
    """list_sql_endpoints(all_workspaces=True) raises ToolError when allowlist is set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    ctx = ServerContext(
        http=AsyncMock(),
        cache=MagicMock(),
        resolver=AsyncMock(),
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
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


# ---------------------------------------------------------------------------
# M03 — drop_view must require FABRIC_MCP_ALLOW_DESTRUCTIVE
# ---------------------------------------------------------------------------


async def test_drop_view_blocked_without_destructive_flag() -> None:
    """M03: drop_view raises ToolError when FABRIC_MCP_ALLOW_DESTRUCTIVE is not set."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    env_copy = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_DESTRUCTIVE"}
    with (
        patch.dict(os.environ, env_copy, clear=True),
        pytest.raises(ToolError, match="FABRIC_MCP_ALLOW_DESTRUCTIVE"),
    ):
        await mcp._tool_manager.call_tool(
            "drop_view",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "dbo.vw_sales"},
        )


async def test_drop_view_allowed_with_destructive_flag() -> None:
    """M03: drop_view succeeds when FABRIC_MCP_ALLOW_DESTRUCTIVE=1 is set."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.cache import ItemEntry  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import WarehouseKind  # noqa: PLC0415

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
    mock_resolver.clear_negative_cache = MagicMock()

    ctx = ServerContext(
        http=AsyncMock(),
        cache=MagicMock(),
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict(os.environ, {"FABRIC_MCP_ALLOW_DESTRUCTIVE": "1"}),
        patch("fabric_dw.services.views.drop_view", new=AsyncMock(return_value=None)),
    ):
        result = await mcp._tool_manager.call_tool(
            "drop_view",
            {"workspace": _WS_NAME, "item": _WH_NAME, "qualified_name": "dbo.vw_sales"},
        )

    assert result == {"dropped": True}


# ---------------------------------------------------------------------------
# M04 — refresh_sql_endpoint_metadata(recreate_tables=True) requires FABRIC_MCP_ALLOW_DESTRUCTIVE
# ---------------------------------------------------------------------------


async def test_refresh_sql_endpoint_metadata_recreate_blocked_without_destructive_flag() -> None:
    """M04: recreate_tables=True raises ToolError without FABRIC_MCP_ALLOW_DESTRUCTIVE."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp.server import mcp  # noqa: PLC0415

    env_copy = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_DESTRUCTIVE"}
    with (
        patch.dict(os.environ, env_copy, clear=True),
        pytest.raises(ToolError, match="FABRIC_MCP_ALLOW_DESTRUCTIVE"),
    ):
        await mcp._tool_manager.call_tool(
            "refresh_sql_endpoint_metadata",
            {"workspace": _WS_NAME, "endpoint": _WH_NAME, "recreate_tables": True},
        )


async def test_refresh_sql_endpoint_metadata_no_recreate_allowed_without_destructive_flag() -> None:
    """M04: recreate_tables=False (default) does NOT require FABRIC_MCP_ALLOW_DESTRUCTIVE."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.cache import ItemEntry  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import WarehouseKind  # noqa: PLC0415

    entry = ItemEntry(
        id=_WH_ID,
        kind=WarehouseKind.SQL_ENDPOINT,
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

    env_copy = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_ALLOW_DESTRUCTIVE"}
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict(os.environ, env_copy, clear=True),
        patch(
            "fabric_dw.services.sql_endpoints.refresh_metadata",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await mcp._tool_manager.call_tool(
            "refresh_sql_endpoint_metadata",
            {"workspace": _WS_NAME, "endpoint": _WH_NAME, "recreate_tables": False},
        )

    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# M06 — list_workspaces must filter by FABRIC_MCP_WORKSPACES allowlist
# ---------------------------------------------------------------------------


async def test_list_workspaces_filtered_by_allowlist() -> None:
    """M06: list_workspaces returns only workspaces matching FABRIC_MCP_WORKSPACES."""
    from uuid import UUID  # noqa: PLC0415

    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import Workspace  # noqa: PLC0415

    allowed_ws = Workspace.model_validate(
        {"id": str(UUID("aaaaaaaa-0000-0000-0000-000000000001")), "displayName": "prod"}
    )
    forbidden_ws = Workspace.model_validate(
        {"id": str(UUID("bbbbbbbb-0000-0000-0000-000000000002")), "displayName": "dev"}
    )

    ctx = ServerContext(
        http=AsyncMock(),
        cache=MagicMock(),
        resolver=AsyncMock(),
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "prod"}),
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(return_value=[allowed_ws, forbidden_ws]),
        ),
    ):
        result = await mcp._tool_manager.call_tool("list_workspaces", {})

    assert len(result) == 1
    assert result[0]["displayName"] == "prod"


async def test_list_workspaces_filtered_by_guid_allowlist() -> None:
    """M06: list_workspaces filters by GUID when allowlist contains GUIDs."""
    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import Workspace  # noqa: PLC0415

    allowed_id = "aaaaaaaa-0000-0000-0000-000000000001"
    allowed_ws = Workspace.model_validate({"id": allowed_id, "displayName": "prod"})
    forbidden_ws = Workspace.model_validate(
        {"id": "bbbbbbbb-0000-0000-0000-000000000002", "displayName": "dev"}
    )

    ctx = ServerContext(
        http=AsyncMock(),
        cache=MagicMock(),
        resolver=AsyncMock(),
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": allowed_id}),
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(return_value=[allowed_ws, forbidden_ws]),
        ),
    ):
        result = await mcp._tool_manager.call_tool("list_workspaces", {})

    assert len(result) == 1
    assert result[0]["displayName"] == "prod"


async def test_list_workspaces_no_filter_when_allowlist_unset() -> None:
    """M06: list_workspaces returns all workspaces when FABRIC_MCP_WORKSPACES is not set."""
    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.models import Workspace  # noqa: PLC0415

    ws1 = Workspace.model_validate(
        {"id": "aaaaaaaa-0000-0000-0000-000000000001", "displayName": "ws1"}
    )
    ws2 = Workspace.model_validate(
        {"id": "bbbbbbbb-0000-0000-0000-000000000002", "displayName": "ws2"}
    )

    ctx = ServerContext(
        http=AsyncMock(),
        cache=MagicMock(),
        resolver=AsyncMock(),
        auth_mode=_auth.CredentialMode.DEFAULT,
    )

    env_copy = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"}
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict(os.environ, env_copy, clear=True),
        patch(
            "fabric_dw.services.workspaces.list_all",
            new=AsyncMock(return_value=[ws1, ws2]),
        ),
    ):
        result = await mcp._tool_manager.call_tool("list_workspaces", {})

    assert len(result) == 2


# ---------------------------------------------------------------------------
# M13 — pre-resolve allowlist check must not block name when allowlist has only GUIDs
# ---------------------------------------------------------------------------


def test_assert_workspace_allowed_name_passes_pre_resolve_when_allowlist_guid_only() -> None:
    """M13: pre-resolve call with a name does not block when allowlist contains only GUIDs.

    A workspace name cannot match a GUID-only allowlist pre-resolve.  The guard
    must defer to the post-resolve call (which has the GUID) rather than falsely
    rejecting.
    """
    from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

    guid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": guid}):
        # Pre-resolve call with a name — must NOT raise even though name != GUID
        assert_workspace_allowed("my-workspace")  # no resolved_id


def test_assert_workspace_allowed_name_blocked_post_resolve_when_allowlist_guid_only() -> None:
    """M13: post-resolve call blocks when the resolved GUID is not in the allowlist."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

    allowed_guid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    other_guid = "00000000-0000-0000-0000-000000000001"
    with (
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": allowed_guid}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        # Post-resolve with a different GUID — must raise
        assert_workspace_allowed("my-workspace", resolved_id=other_guid)


def test_assert_workspace_allowed_name_passes_post_resolve_when_guid_matches() -> None:
    """M13: post-resolve call permits when the resolved GUID matches the GUID-only allowlist."""
    from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

    allowed_guid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    with patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": allowed_guid}):
        # Post-resolve with the matching GUID — must NOT raise
        assert_workspace_allowed("my-workspace", resolved_id=allowed_guid)


def test_assert_workspace_allowed_still_blocks_name_when_allowlist_has_names() -> None:
    """M13: pre-resolve call blocks when allowlist has name-shaped entries that don't match."""
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

    with (
        patch.dict(os.environ, {"FABRIC_MCP_WORKSPACES": "allowed-workspace"}),
        pytest.raises(ToolError, match="allowlist"),
    ):
        assert_workspace_allowed("forbidden-workspace")  # pre-resolve, name-only allowlist


# ---------------------------------------------------------------------------
# FIX 1(a): build_context wiring — workspace_allowlist propagated to ServerContext
# ---------------------------------------------------------------------------


def test_build_context_propagates_workspace_allowlist(tmp_path: Path) -> None:
    """build_context passes cfg.mcp.workspace_allowlist into ServerContext."""
    from fabric_dw.config import McpConfig, UserConfig, save_config  # noqa: PLC0415
    from fabric_dw.mcp._context import build_context  # noqa: PLC0415

    cfg_file = tmp_path / "config.toml"
    save_config(
        UserConfig(mcp=McpConfig(workspace_allowlist=["Sales WS", "Finance WS"])),
        cfg_file,
    )

    env_without_workspaces = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"}
    with patch.dict(os.environ, env_without_workspaces, clear=True):
        ctx = build_context(config_path=cfg_file)

    assert ctx.workspace_allowlist == ["Sales WS", "Finance WS"]


def test_build_context_workspace_allowlist_none_when_absent(tmp_path: Path) -> None:
    """build_context sets workspace_allowlist=None when the key is absent from config."""
    from fabric_dw.config import UserConfig, save_config  # noqa: PLC0415
    from fabric_dw.mcp._context import build_context  # noqa: PLC0415

    cfg_file = tmp_path / "config.toml"
    save_config(UserConfig(), cfg_file)

    env_without_workspaces = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"}
    with patch.dict(os.environ, env_without_workspaces, clear=True):
        ctx = build_context(config_path=cfg_file)

    assert ctx.workspace_allowlist is None


# ---------------------------------------------------------------------------
# FIX 1(b): tool-level test — config layer blocks when env is UNSET
# ---------------------------------------------------------------------------


async def test_get_workspace_blocked_by_config_allowlist_env_unset() -> None:
    """get_workspace blocks a workspace via config layer when FABRIC_MCP_WORKSPACES is unset.

    This test proves that:
    1.  The tool reads ctx.workspace_allowlist from ServerContext.
    2.  When FABRIC_MCP_WORKSPACES is absent, the config layer still restricts access.
    """
    from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.cache import LookupCache  # noqa: PLC0415
    from fabric_dw.mcp._context import ServerContext  # noqa: PLC0415
    from fabric_dw.mcp.server import mcp  # noqa: PLC0415
    from fabric_dw.resolver import Resolver  # noqa: PLC0415

    mock_http = AsyncMock()
    mock_cache = LookupCache()
    mock_resolver = AsyncMock(spec=Resolver)

    ctx = ServerContext(
        http=mock_http,
        cache=mock_cache,
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
        workspace_allowlist=["allowed-ws"],
    )

    env_without_workspaces = {k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"}
    with (
        patch("fabric_dw.mcp._context._SERVER_CTX", ctx),
        patch.dict(os.environ, env_without_workspaces, clear=True),
        pytest.raises(ToolError, match="allowlist"),
    ):
        await mcp._tool_manager.call_tool(
            "get_workspace",
            {"workspace": "forbidden-ws"},
        )


# ---------------------------------------------------------------------------
# #697 — GUID canonicalization + mixed name/GUID allowlist fixes
# ---------------------------------------------------------------------------

# Canonical form of the test GUID used across these tests.
_CANONICAL_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


class TestGuidCanonicalization:
    """Trap 1: non-canonical GUID allowlist entries must match the canonical resolved GUID."""

    def test_braced_guid_entry_matches_canonical_resolved_id(self) -> None:
        """A braced {guid} allowlist entry must match the canonical hyphenated resolved GUID."""
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        braced = "{" + _CANONICAL_GUID + "}"
        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            # Must not raise: braced entry should match canonical resolved_id
            assert_workspace_allowed(
                _CANONICAL_GUID,
                resolved_id=_CANONICAL_GUID,
                config_allowlist=[braced],
            )

    def test_unhyphenated_guid_entry_matches_canonical_resolved_id(self) -> None:
        """A 32-hex unhyphenated GUID allowlist entry must match the canonical resolved GUID."""
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        unhyphenated = _CANONICAL_GUID.replace("-", "")
        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            assert_workspace_allowed(
                _CANONICAL_GUID,
                resolved_id=_CANONICAL_GUID,
                config_allowlist=[unhyphenated],
            )

    def test_urn_uuid_entry_matches_canonical_resolved_id(self) -> None:
        """A urn:uuid:<guid> allowlist entry must match the canonical resolved GUID."""
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        urn = "urn:uuid:" + _CANONICAL_GUID
        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            assert_workspace_allowed(
                _CANONICAL_GUID,
                resolved_id=_CANONICAL_GUID,
                config_allowlist=[urn],
            )

    def test_non_listed_workspace_still_blocked_with_non_canonical_entry(self) -> None:
        """A workspace not on the allowlist is still denied even when the entry is non-canonical."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        other_guid = "00000000-0000-0000-0000-000000000099"
        braced = "{" + _CANONICAL_GUID + "}"
        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with (
            patch.dict(os.environ, env_without_workspaces, clear=True),
            pytest.raises(ToolError, match="allowlist"),
        ):
            assert_workspace_allowed(
                other_guid,
                resolved_id=other_guid,
                config_allowlist=[braced],
            )

    def test_resolve_workspace_allowlist_canonicalizes_braced_guid(self) -> None:
        """resolve_workspace_allowlist stores braced GUIDs in canonical form."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        braced = "{" + _CANONICAL_GUID + "}"
        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            result = resolve_workspace_allowlist([braced])
        assert result == frozenset({_CANONICAL_GUID})

    def test_resolve_workspace_allowlist_canonicalizes_unhyphenated_guid(self) -> None:
        """resolve_workspace_allowlist stores 32-hex GUIDs in canonical form."""
        from fabric_dw.mcp._guards import resolve_workspace_allowlist  # noqa: PLC0415

        unhyphenated = _CANONICAL_GUID.replace("-", "")
        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            result = resolve_workspace_allowlist([unhyphenated])
        assert result == frozenset({_CANONICAL_GUID})


class TestMixedNameGuidAllowlist:
    """Trap 2: a name addressed against a mixed name+GUID allowlist must defer to post-resolve."""

    def test_name_defers_to_post_resolve_with_mixed_allowlist(self) -> None:
        """Pre-resolve name call must NOT raise when allowlist is a mix of GUIDs and names.

        The name 'sales' can't be matched pre-resolve against the GUID entry;
        the guard must defer to the post-resolve call.
        """
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            # Pre-resolve call with a name against a mixed allowlist — must NOT raise
            assert_workspace_allowed(
                "sales",
                resolved_id=None,
                config_allowlist=[_CANONICAL_GUID, "finance"],
            )

    def test_post_resolve_matches_guid_in_mixed_allowlist(self) -> None:
        """Post-resolve call succeeds when the resolved GUID is in a mixed allowlist."""
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            # 'sales' resolves to _CANONICAL_GUID which IS in the allowlist
            assert_workspace_allowed(
                "sales",
                resolved_id=_CANONICAL_GUID,
                config_allowlist=[_CANONICAL_GUID, "finance"],
            )

    def test_post_resolve_blocks_when_guid_not_in_mixed_allowlist(self) -> None:
        """Post-resolve call blocks when the resolved GUID is NOT in a mixed allowlist."""
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        other_guid = "00000000-0000-0000-0000-000000000099"
        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with (
            patch.dict(os.environ, env_without_workspaces, clear=True),
            pytest.raises(ToolError, match="allowlist"),
        ):
            # 'evil-ws' resolves to other_guid which is NOT in the allowlist
            assert_workspace_allowed(
                "evil-ws",
                resolved_id=other_guid,
                config_allowlist=[_CANONICAL_GUID, "finance"],
            )

    def test_name_in_mixed_allowlist_matches_directly(self) -> None:
        """A name that IS in the mixed allowlist matches at the post-resolve call."""
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            # 'finance' is explicitly in the allowlist by name
            assert_workspace_allowed(
                "finance",
                resolved_id=None,
                config_allowlist=[_CANONICAL_GUID, "finance"],
            )

    def test_guid_arg_against_mixed_allowlist_is_not_deferred(self) -> None:
        """A GUID workspace_arg does NOT defer pre-resolve; it is checked immediately."""
        from fabric_dw.mcp._guards import assert_workspace_allowed  # noqa: PLC0415

        env_without_workspaces = {
            k: v for k, v in os.environ.items() if k != "FABRIC_MCP_WORKSPACES"
        }
        with patch.dict(os.environ, env_without_workspaces, clear=True):
            # Canonical GUID arg against matching allowlist — must succeed pre-resolve
            assert_workspace_allowed(
                _CANONICAL_GUID,
                resolved_id=None,
                config_allowlist=[_CANONICAL_GUID, "finance"],
            )
