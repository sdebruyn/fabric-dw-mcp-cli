"""MCP tool for generic SQL execution."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_readonly_sql, assert_workspace_allowed, env_flag
from fabric_dw.mcp._helpers import fabric_err, make_sql_target, resolve_item
from fabric_dw.services import sql_exec as _sql_exec_svc

__all__ = ["register"]

_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register sql_exec tools against *mcp*."""

    @mcp.tool(name="execute_sql")
    async def execute_sql(
        workspace: str,
        item: str,
        query: str,
        max_rows: Annotated[int, Field(ge=1, le=10000)] = 1000,
    ) -> dict[str, Any]:
        """Execute an arbitrary SQL statement or batch against a warehouse or SQL Analytics
        Endpoint.

        WARNING: this tool executes arbitrary SQL against the target. DDL (DROP,
        ALTER, TRUNCATE) and DML (DELETE, UPDATE) are permitted unless
        ``FABRIC_MCP_READONLY=1`` is set. Use only when the user explicitly
        requests data modification. Default to SELECT when the user's intent is
        read-only investigation.

        Supports both Warehouse and SQL Analytics Endpoint items.  Multi-statement
        batches are allowed; only the **last** result set is returned.  DDL/DML
        statements that produce no result set return ``columns=[]`` and ``rows=[]``.

        ``datetime`` and ``Decimal`` column values are pre-serialised to strings.
        ``bytes`` / varbinary columns are base64-encoded and their column names are
        suffixed with ``__base64``.

        For large tables, add a TOP clause or WHERE predicate to the query rather
        than relying solely on ``max_rows``.  The driver fetches at most
        ``max_rows + 1`` rows (enough to detect truncation) so memory is bounded,
        but pushing the limit into the query itself is always more efficient.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            query: SQL statement or batch to execute.
            max_rows: Maximum rows to return (1-10000, default 1000).  When the
                result set is larger the response includes ``"truncated": true``.

        Returns:
            A dict with keys ``columns`` (list[str]), ``rows`` (list[list[Any]]),
            ``rowcount`` (int; ``-1`` when the driver does not report a count),
            ``row_count_returned`` (int), and ``truncated`` (bool).
        """
        if env_flag("FABRIC_MCP_READONLY"):
            assert_readonly_sql(query)
        assert_workspace_allowed(workspace)
        ctx = get_context()
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(workspace, str(ws_id))
            _log.debug("execute_sql ws=%s item=%s max_rows=%d", ws_id, entry.id, max_rows)
            target = make_sql_target(ws_id, entry, item)
            result = await _sql_exec_svc.execute(
                target, query, mode=ctx.auth_mode, row_limit=max_rows
            )
        except FabricError as exc:
            raise fabric_err(exc) from exc
        # The service fetches max_rows+1 rows so we can detect truncation without
        # pulling the entire result set over the wire.  Slice back to max_rows here.
        truncated = len(result.rows) > max_rows
        sliced_rows = result.rows[:max_rows]
        return {
            "columns": result.columns,
            "rows": sliced_rows,
            "rowcount": result.rowcount,
            "row_count_returned": len(sliced_rows),
            "truncated": truncated,
        }
