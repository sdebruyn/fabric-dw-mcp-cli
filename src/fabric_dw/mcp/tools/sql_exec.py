"""MCP tool for generic SQL execution."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Literal, assert_never

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from fabric_dw.cli._plan_mermaid import render_plan_mermaid
from fabric_dw.cli._plan_parse import parse_showplan
from fabric_dw.cli._plan_render import operator_to_dict
from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import assert_readonly_sql, assert_workspace_allowed, env_flag
from fabric_dw.mcp._helpers import make_sql_target, resolve_item, tool_err
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
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("execute_sql ws=%s item=%s max_rows=%d", ws_id, entry.id, max_rows)
            target = make_sql_target(ws_id, entry, item)
            result = await _sql_exec_svc.execute(
                target, query, mode=ctx.auth_mode, row_limit=max_rows
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            # Re-raise ToolError instances unchanged — they are already structured
            # (e.g. from make_sql_target when the item has no connection string).
            if isinstance(exc, ToolError):
                raise
            # The service execute() re-raises unmapped driver errors unchanged
            # (documented bare `raise`). These can contain internal connection
            # detail, ODBC state, or query text — they must not reach the MCP
            # client. Convert to a generic safe message here at the trust boundary.
            _log.debug("execute_sql: unhandled driver exception (suppressed)", exc_info=True)
            raise ToolError("SQL execution failed due to a driver or network error.") from exc
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

    @mcp.tool(name="get_query_plan")
    async def get_query_plan(
        workspace: str,
        item: str,
        query: str,
        format: Literal["xml", "tree", "json", "mermaid"] = "xml",
    ) -> dict[str, Any]:
        """Capture the estimated SHOWPLAN_XML execution plan for a SQL query without executing it.

        This tool does NOT execute the query — it only retrieves the estimated execution
        plan as SHOWPLAN_XML.  Because no data is modified, this tool is permitted even
        under ``FABRIC_MCP_READONLY=1``.

        The plan XML uses the standard namespace
        ``http://schemas.microsoft.com/sqlserver/2004/07/showplan`` and can be opened
        in SSMS, Azure Data Studio, or uploaded to pastetheplan.com for visual analysis.

        Since the query is not executed, DDL/DML query text is safe to plan without
        modifying any data.

        Supports both Warehouse and SQL Analytics Endpoint items.

        **Format options:**

        - ``"xml"`` *(default, backwards-compatible)* — returns the raw SHOWPLAN_XML
          string in ``plan_xml``.  Existing callers relying on ``{"plan_xml": str}``
          continue to work unchanged.
        - ``"tree"`` — parses the XML into a native nested list of dicts (one entry
          per statement) in ``plan``.  Best for agent reasoning over the plan structure.
        - ``"json"`` — same tree, serialised to an indented JSON string in ``plan_json``.
          Ready to write out or pass through as compact text.
        - ``"mermaid"`` — renders a Mermaid ``flowchart TD`` diagram string in
          ``mermaid``.  Paste into mermaid.live or embed in GitHub Markdown.

        **Artifact formats (SVG/HTML/DOT) are CLI-only.**  They write files to disk and
        are only available via ``fdw sql plan --format <fmt> -o <file>``.  The MCP
        server never writes files (ambiguous cwd, invisible side-effects).

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL Analytics Endpoint name or GUID.
            query: SQL statement to generate an estimated execution plan for.
            format: Output format — one of ``"xml"`` (default), ``"tree"``,
                ``"json"``, or ``"mermaid"``.

        Returns:
            A dict whose shape depends on *format*:

            - ``xml``     → ``{"format": "xml",     "plan_xml":   str}``
            - ``tree``    → ``{"format": "tree",    "plan":       list[dict]}``
            - ``json``    → ``{"format": "json",    "plan_json":  str}``
            - ``mermaid`` → ``{"format": "mermaid", "mermaid":    str}``
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("get_query_plan ws=%s item=%s format=%s", ws_id, entry.id, format)
            target = make_sql_target(ws_id, entry, item)
            plan_xml = await _sql_exec_svc.get_plan(target, query, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        except Exception as exc:
            if isinstance(exc, ToolError):
                raise
            # The service get_plan() re-raises unmapped driver errors unchanged
            # (documented bare `raise`). These can contain internal connection
            # detail or ODBC state — they must not reach the MCP client.
            _log.debug("get_query_plan: unhandled driver exception (suppressed)", exc_info=True)
            raise ToolError(
                "Query plan retrieval failed due to a driver or network error."
            ) from exc

        if format == "xml":
            return {"format": "xml", "plan_xml": plan_xml}
        # Parse once; all non-xml formats share the same operator tree.
        operators = parse_showplan(plan_xml)
        if format == "tree":
            return {"format": "tree", "plan": [operator_to_dict(op) for op in operators]}
        if format == "json":
            payload = [operator_to_dict(op) for op in operators]
            return {"format": "json", "plan_json": json.dumps(payload, indent=2)}
        if format == "mermaid":
            return {"format": "mermaid", "mermaid": render_plan_mermaid(operators)}
        assert_never(format)
