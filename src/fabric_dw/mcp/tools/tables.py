"""MCP tools for SQL table operations."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from fabric_dw.exceptions import FabricError
from fabric_dw.mcp._context import get_context
from fabric_dw.mcp._guards import (
    assert_workspace_allowed,
)
from fabric_dw.mcp._helpers import (
    make_sql_target,
    mutating_tool,
    parse_iso8601,
    parse_qualified_name,
    resolve_item,
    safe_rows,
    tool_err,
)
from fabric_dw.models import ColumnSpec
from fabric_dw.services import tables as tables_svc
from fabric_dw.services.columns import get_object_columns_or_raise as _get_columns

__all__ = ["register"]


def _parse_column_dict(i: int, col: object) -> ColumnSpec:
    """Convert a raw dict from MCP input into a :class:`ColumnSpec`.

    Raises :class:`TypeError` when *col* is not a ``dict``, and
    :class:`ValueError` when required keys are missing.
    """
    if not isinstance(col, dict):
        raise TypeError(f"columns[{i}] must be an object, got {type(col).__name__}")
    name = col.get("name")
    sql_type = col.get("sql_type")
    if not name or not sql_type:
        raise ValueError(f"columns[{i}] must have 'name' and 'sql_type' keys")
    nullable = bool(col.get("nullable", True))
    return ColumnSpec(name=str(name), sql_type=str(sql_type), nullable=nullable)


_log = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:  # noqa: PLR0915
    """Register table tools against *mcp*."""

    @mcp.tool(name="list_tables")
    async def list_tables(
        workspace: str, item: str, schema: str | None = None
    ) -> list[dict[str, Any]]:
        """List SQL tables on a warehouse or SQL Analytics Endpoint.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            schema: When provided, only tables in this schema are returned.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("list_tables ws=%s item=%s schema=%r", ws_id, entry.id, schema)
            target = make_sql_target(ws_id, entry, item)
            result = await tables_svc.list_tables(target, schema=schema, mode=ctx.auth_mode)
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return [t.model_dump(mode="json") for t in result]

    @mcp.tool(name="read_table")
    async def read_table(
        workspace: str,
        item: str,
        qualified_name: str,
        count: Annotated[int, Field(ge=1, le=10000)] = 10,
    ) -> dict[str, Any]:
        """Return up to *count* rows from a table as JSON-serialisable columns + rows.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
            count: Maximum number of rows to return (1-10000, default 10).
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "read_table ws=%s item=%s table=%s.%s count=%d",
                ws_id,
                entry.id,
                schema,
                table_name,
                count,
            )
            target = make_sql_target(ws_id, entry, item)
            columns, rows = await tables_svc.read_table(
                target, schema, table_name, count=count, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "columns": columns,
            "rows": safe_rows(rows),
        }

    @mcp.tool(name="count_table_rows")
    async def count_table_rows(
        workspace: str,
        item: str,
        qualified_name: str,
    ) -> dict[str, Any]:
        """Return the total row count of a table via ``SELECT COUNT_BIG(*)``.

        Works on both Fabric Data Warehouses and SQL Analytics Endpoints.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "count_table_rows ws=%s item=%s table=%s.%s",
                ws_id,
                entry.id,
                schema,
                table_name,
            )
            target = make_sql_target(ws_id, entry, item)
            row_count = await tables_svc.count_table_rows(
                target, schema, table_name, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"schema": schema, "name": table_name, "row_count": row_count}

    @mcp.tool(name="get_cluster_columns")
    async def get_cluster_columns(
        workspace: str,
        item: str,
        qualified_name: str,
    ) -> list[dict[str, object]]:
        """Return the data-clustering columns of a table, ordered by clustering ordinal.

        Only supported on Fabric Data Warehouses.  SQL Analytics Endpoints raise a
        ``ToolError``.  Returns an empty list when no clustering columns are defined.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "get_cluster_columns ws=%s item=%s table=%s.%s",
                ws_id,
                entry.id,
                schema,
                table_name,
            )
            target = make_sql_target(ws_id, entry, item)
            return await tables_svc.get_cluster_columns(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc

    @mcp.tool(name="get_table_columns")
    async def get_table_columns(
        workspace: str,
        item: str,
        qualified_name: str,
    ) -> list[dict[str, Any]]:
        """Return column metadata for a SQL table via ``sys.columns``.

        Works on both Fabric Data Warehouses and SQL Analytics Endpoints.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse or SQL endpoint name or GUID.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "get_table_columns ws=%s item=%s table=%s.%s",
                ws_id,
                entry.id,
                schema,
                table_name,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await _get_columns(
                target, schema, table_name, kind_label="table", mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result

    @mutating_tool(mcp, "create_table")
    async def create_table(
        workspace: str,
        item: str,
        qualified_name: str,
        select_body: str,
        cluster_by: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new SQL table via CTAS (CREATE TABLE AS SELECT).

        Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).
        The service rejects SQL Analytics Endpoints with a ``ToolError``.

        CAUTION: ``select_body`` is executed verbatim as DDL on the warehouse.
        Ensure the body matches the user's intent before calling this tool.

        ``select_body`` must be a single read-only SELECT or WITH (CTE)
        statement.  The guard is always on and fail-closed: a write keyword
        (DELETE, DROP, INSERT, etc.) or a semicolon anywhere in the body is
        rejected, even inside a string literal or quoted identifier.  If a
        legitimate query body contains a write keyword (e.g. a column alias
        'DELETE'), rewrite the expression to avoid the keyword.

        When ``cluster_by`` is supplied, the DDL becomes
        ``CREATE TABLE … WITH (CLUSTER BY ([c1], [c2])) AS SELECT …``.
        Column existence is not validated for CTAS because the result columns
        come from the SELECT and are not known ahead of time.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.  SQL Analytics Endpoints are rejected.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
            select_body: Single read-only SELECT or WITH (CTE) statement for the
                CTAS source.  Write keywords and semicolons are rejected
                fail-closed, even inside string literals or quoted identifiers.
            cluster_by: Optional list of column names for the ``CLUSTER BY`` clause
                (up to 4).
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "create_table ws=%s item=%s table=%s.%s", ws_id, entry.id, schema, table_name
            )
            target = make_sql_target(ws_id, entry, item)
            result = await tables_svc.create_table(
                target,
                schema,
                table_name,
                select_body,
                cluster_by=cluster_by,
                kind=entry.kind,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "delete_table", destructive=True)
    async def delete_table(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
        """Drop a SQL table.

        Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).
        The service rejects SQL Analytics Endpoints with a ``ToolError``.

        CAUTION: This is a destructive, irreversible operation.  The table and all
        its data will be permanently deleted.  Confirm with the user before calling.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.  SQL Analytics Endpoints are rejected.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "delete_table ws=%s item=%s table=%s.%s", ws_id, entry.id, schema, table_name
            )
            target = make_sql_target(ws_id, entry, item)
            await tables_svc.delete_table(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"dropped": True}

    @mutating_tool(mcp, "clear_table", destructive=True)
    async def clear_table(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
        """Truncate a SQL table (remove all rows, keep structure).

        Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).
        The service rejects SQL Analytics Endpoints with a ``ToolError``.

        CAUTION: This is a destructive, irreversible operation.  All rows will be
        permanently deleted.  The table structure and schema are preserved.
        Confirm with the user before calling.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.  SQL Analytics Endpoints are rejected.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug("clear_table ws=%s item=%s table=%s.%s", ws_id, entry.id, schema, table_name)
            target = make_sql_target(ws_id, entry, item)
            await tables_svc.clear_table(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {"truncated": True}

    @mutating_tool(mcp, "create_empty_table")
    async def create_empty_table_tool(
        workspace: str,
        item: str,
        qualified_name: str,
        columns: list[dict[str, Any]],
        cluster_by: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create an empty table from an explicit column spec (DDL only, no data).

        Builds ``CREATE TABLE [schema].[table] (col TYPE [NULL|NOT NULL], …)`` from
        the supplied column definitions.  No data is read or inserted; this is a
        pure DDL operation.

        Server-side file access is unreliable in MCP deployments, so CSV/Parquet
        inference is not available via this tool — use the ``fabric-dw tables create
        --from-parquet`` or ``--from-csv`` CLI commands instead.

        Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).

        When ``cluster_by`` is supplied, each column must appear in ``columns``.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
            columns: List of column definitions, each a dict with:
                ``name`` (str) — column identifier;
                ``sql_type`` (str) — Fabric-DW T-SQL type, e.g. ``"INT"``, ``"VARCHAR(255)"``;
                ``nullable`` (bool, optional, default true) — whether the column allows NULL.
            cluster_by: Optional list of column names for the ``CLUSTER BY`` clause
                (up to 4).  Each name must appear in ``columns``.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            col_specs = [_parse_column_dict(i, col) for i, col in enumerate(columns)]
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "create_empty_table ws=%s item=%s table=%s.%s cols=%d",
                ws_id,
                entry.id,
                schema,
                table_name,
                len(col_specs),
            )
            target = make_sql_target(ws_id, entry, item)
            result = await tables_svc.create_empty_table(
                target,
                schema,
                table_name,
                col_specs,
                cluster_by=cluster_by,
                kind=entry.kind,
                mode=ctx.auth_mode,
            )
        except (TypeError, ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "clone_table")
    async def clone_table(
        workspace: str,
        item: str,
        source: str,
        new_table: str,
        at: str | None = None,
    ) -> dict[str, Any]:
        """Create a zero-copy clone of a table using ``CREATE TABLE … AS CLONE OF …``.

        Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.
            source: Qualified source table name, e.g. ``dbo.sales``.
            new_table: Qualified name for the new cloned table, e.g. ``dbo.sales_clone``.
            at: Optional ISO-8601 UTC timestamp for a point-in-time clone,
                e.g. ``2024-05-20T14:00:00``.  Must be within the data-retention
                window (30 days by default).  When omitted, the clone reflects the
                current state of the source table.
        """
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)

        at_dt_raw = parse_iso8601(at, "at")
        at_dt: datetime | None = (
            None
            if at_dt_raw is None
            else (
                at_dt_raw.replace(tzinfo=UTC)
                if at_dt_raw.tzinfo is None
                else at_dt_raw.astimezone(UTC)
            )
        )

        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "clone_table ws=%s item=%s source=%s new_table=%s at=%s",
                ws_id,
                entry.id,
                source,
                new_table,
                at,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await tables_svc.clone_table(
                target,
                source,
                new_table,
                at=at_dt,
                kind=entry.kind,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mcp.tool(name="get_table_health_metrics")
    async def get_table_health_metrics(
        workspace: str,
        item: str,
        qualified_name: str,
    ) -> dict[str, Any]:
        """Return health metrics for a table via ``sp_get_table_health_metrics``.

        Only supported on SQL Analytics Endpoints (not Data Warehouses).  The
        proc surfaces Delta/Parquet layout issues such as small files,
        fragmentation, excessive deletes/updates, and delayed checkpoints.

        The stored procedure is Generally Available (announced at Build 2026)
        but its output column schema is not yet documented by Microsoft.
        Columns and rows are passed through verbatim.

        Args:
            workspace: Workspace name or GUID.
            item: SQL Analytics Endpoint name or GUID.  Data Warehouses are
                rejected with a ``ToolError``.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "get_table_health_metrics ws=%s item=%s table=%s.%s",
                ws_id,
                entry.id,
                schema,
                table_name,
            )
            target = make_sql_target(ws_id, entry, item)
            columns, rows = await tables_svc.get_table_health_metrics(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return {
            "columns": columns,
            "rows": safe_rows(rows),
        }

    @mutating_tool(mcp, "rename_table")
    async def rename_table(
        workspace: str, item: str, qualified_name: str, new_name: str
    ) -> dict[str, Any]:
        """Rename a SQL table via ``sp_rename`` (Data-Warehouse-only).

        Renames the table in-place within the same schema using T-SQL
        ``EXEC sp_rename``.  Both the current qualified name and the new bare
        name are passed as bound parameters — no SQL injection is possible.

        ``sp_rename`` cannot move a table to a different schema, so *new_name*
        must be an unqualified (bare) name without a dot.

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.  SQL Analytics Endpoints are rejected.
            qualified_name: Current dot-separated qualified table name, e.g.
                ``dbo.sales``.
            new_name: New table name (unqualified, e.g. ``sales_v2``).  Must not
                contain a dot.
        """
        parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "rename_table ws=%s item=%s qualified=%r new_name=%r",
                ws_id,
                entry.id,
                qualified_name,
                new_name,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await tables_svc.rename_table(
                target, qualified_name, new_name, kind=entry.kind, mode=ctx.auth_mode
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")

    @mutating_tool(mcp, "set_cluster_columns", destructive=True)
    async def set_cluster_columns(
        workspace: str,
        item: str,
        qualified_name: str,
        cluster_by: list[str] | None = None,
    ) -> dict[str, Any]:
        """Change (or remove) the data-clustering columns of an existing table.

        Rebuilds the table via a transactional CTAS-swap:

        1. ``CREATE TABLE [schema].[__recluster_<hex>] [WITH (CLUSTER BY (...))]
           AS SELECT * FROM [schema].[orig]``
        2. ``DROP TABLE [schema].[orig]``
        3. ``EXEC sp_rename`` to restore the original name

        All three steps run inside ONE transaction.  Any failure rolls back
        automatically — no orphan temp table is left behind.

        CAUTION: This operation copies the full table (runtime is proportional
        to table size).  Dependent views and stored procedures that reference
        this table by name are NOT automatically updated by ``sp_rename`` and
        may need refreshing after the swap.

        Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).

        Args:
            workspace: Workspace name or GUID.
            item: Warehouse name or GUID.  SQL Analytics Endpoints are rejected.
            qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
            cluster_by: New list of column names for the ``CLUSTER BY`` clause
                (up to 4).  Pass ``null`` or an empty list to remove clustering
                (rebuilds table without ``CLUSTER BY``).
        """
        schema, table_name = parse_qualified_name(qualified_name, kind="table")
        ctx = get_context()
        assert_workspace_allowed(workspace, config_allowlist=ctx.workspace_allowlist)
        try:
            ws_id, entry = await resolve_item(ctx.resolver, workspace, item)
            assert_workspace_allowed(
                workspace, str(ws_id), config_allowlist=ctx.workspace_allowlist
            )
            _log.debug(
                "set_cluster_columns ws=%s item=%s table=%s.%s cluster_by=%r",
                ws_id,
                entry.id,
                schema,
                table_name,
                cluster_by,
            )
            target = make_sql_target(ws_id, entry, item)
            result = await tables_svc.recluster_table(
                target,
                schema,
                table_name,
                cluster_by=cluster_by or None,
                kind=entry.kind,
                mode=ctx.auth_mode,
            )
        except (ValueError, FabricError) as exc:
            raise tool_err(exc) from exc
        return result.model_dump(mode="json")
