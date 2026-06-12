"""FastMCP server exposing all fabric-dw service functions as MCP tools.

Architecture
------------
- One ``FastMCP`` instance (``mcp``) is created at module-import time.
- All Fabric dependencies (HTTP client, SQL client, cache, resolver) are
  constructed **lazily** on first use via module-level singletons so that
  importing the module does not open any network connections.
- Each tool catches :class:`~fabric_dw.exceptions.FabricError` and its
  subclasses and re-raises as :class:`~mcp.server.fastmcp.exceptions.ToolError`
  so callers receive a structured MCP error rather than a raw traceback.
- ``workspace`` and ``warehouse`` parameters are always ``str`` (name OR GUID);
  the :class:`~fabric_dw.resolver.Resolver` translates them to UUIDs
  internally.

Security environment variables
-------------------------------
``FABRIC_MCP_READONLY``
    Set to ``1``, ``true``, or ``yes`` to restrict ``execute_sql`` to
    SELECT/WITH statements and block all mutating tools.

``FABRIC_MCP_ALLOW_DESTRUCTIVE``
    Set to ``1``, ``true``, or ``yes`` to enable permanently-destructive
    tools (delete_warehouse, delete_snapshot, delete_restore_point,
    restore_warehouse_in_place, delete_schema, delete_table, clear_table,
    delete_sql_pool, reset_sql_pools).  Defaults to **disabled**.

``FABRIC_MCP_WORKSPACES``
    Comma-separated workspace names or GUIDs the server may touch.
    Unset = all workspaces allowed.

``FABRIC_MCP_ALLOW_REMOTE``
    Set to ``1``, ``true``, or ``yes`` to allow the HTTP transport to bind
    on a non-loopback address.  A prominent WARNING is logged when set.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from fabric_dw import auth as _auth
from fabric_dw.cache import ItemEntry as _ItemEntry
from fabric_dw.cache import LookupCache
from fabric_dw.exceptions import AlreadyExists, ConfigError, FabricError, NotFound
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.logging import setup_logging
from fabric_dw.mcp._guards import (
    _env_flag as _guards_env_flag,
)
from fabric_dw.mcp._guards import (
    assert_destructive_allowed,
    assert_readonly_sql,
    assert_workspace_allowed,
    assert_writes_allowed,
)
from fabric_dw.models import SqlPool, SqlPoolClassifier, WarehouseKind
from fabric_dw.resolver import Resolver
from fabric_dw.services import audit, queries, snapshots, sql_endpoints, warehouses, workspaces
from fabric_dw.services import ownership as ownership_svc
from fabric_dw.services import permissions as _permissions_svc
from fabric_dw.services import query_insights as _qi_svc
from fabric_dw.services import restore as restore_svc
from fabric_dw.services import schemas as schemas_svc
from fabric_dw.services import sql_exec as _sql_exec_svc
from fabric_dw.services import sql_pools as sql_pools_svc
from fabric_dw.services import tables as tables_svc
from fabric_dw.services import views as views_svc
from fabric_dw.sql import SqlTarget
from fabric_dw.sql_io import json_safe as _json_safe_value

__all__ = ["mcp", "run"]

# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

mcp: FastMCP = FastMCP("fabric-dw")

# ---------------------------------------------------------------------------
# Lazy singleton helpers
# ---------------------------------------------------------------------------

_http_client: FabricHttpClient | None = None
_cache_singleton: LookupCache | None = None
_resolver_singleton: Resolver | None = None


def _get_http() -> FabricHttpClient:
    """Return the shared :class:`FabricHttpClient`, creating it on first call."""
    global _http_client  # noqa: PLW0603
    if _http_client is None:
        raw_mode = os.environ.get("FABRIC_AUTH", "default")
        try:
            mode = _auth.CredentialMode(raw_mode)
        except ValueError as exc:
            raise ConfigError(  # noqa: TRY003
                f"invalid FABRIC_AUTH value {raw_mode!r}; "
                f"expected one of {[m.value for m in _auth.CredentialMode]}"
            ) from exc
        credential = _auth.get_credential(mode)
        _http_client = FabricHttpClient(credential=credential)
    return _http_client


def _get_auth_mode() -> _auth.CredentialMode:
    """Return the configured :class:`CredentialMode` from the environment."""
    raw_mode = os.environ.get("FABRIC_AUTH", "default")
    try:
        return _auth.CredentialMode(raw_mode)
    except ValueError as exc:
        raise ConfigError(  # noqa: TRY003
            f"invalid FABRIC_AUTH value {raw_mode!r}; "
            f"expected one of {[m.value for m in _auth.CredentialMode]}"
        ) from exc


def _get_cache() -> LookupCache:
    """Return the shared :class:`LookupCache`, creating it on first call."""
    global _cache_singleton  # noqa: PLW0603
    if _cache_singleton is None:
        _cache_singleton = LookupCache()
    return _cache_singleton


def _get_resolver() -> Resolver:
    """Return the shared :class:`Resolver`, creating it on first call."""
    global _resolver_singleton  # noqa: PLW0603
    if _resolver_singleton is None:
        _resolver_singleton = Resolver(http=_get_http(), cache=_get_cache())
    return _resolver_singleton


# ---------------------------------------------------------------------------
# Helper: wrap FabricError → ToolError
# ---------------------------------------------------------------------------


def _fabric_err(exc: FabricError) -> ToolError:
    """Convert a :class:`FabricError` to a :class:`ToolError`."""
    err_type = type(exc).__name__
    return ToolError(f"{err_type}: {exc}")


_SQL_ENDPOINT_DDL_ERROR = "SQL Analytics Endpoints are read-only; CREATE/DROP SCHEMA not supported"


def _require_warehouse(entry: _ItemEntry, item: str) -> None:
    """Raise ToolError if *entry* is a SQL Analytics Endpoint.

    DDL operations (CREATE SCHEMA, DROP SCHEMA) are not supported on SQL
    Analytics Endpoints, which are read-only views over Lakehouse data.

    Args:
        entry: The resolved item entry.
        item: The item name/GUID as supplied by the caller (used in the error message).

    Raises:
        ToolError: If the resolved item is a SQL Analytics Endpoint.
    """
    if entry.kind == WarehouseKind.SQL_ENDPOINT:
        raise ToolError(f"{item!r}: {_SQL_ENDPOINT_DDL_ERROR}")  # noqa: TRY003


# ---------------------------------------------------------------------------
# Workspace tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_workspaces() -> list[dict[str, Any]]:
    """List all Fabric workspaces the caller has access to."""
    try:
        result = await workspaces.list_all(_get_http())
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [ws.model_dump(by_alias=True, mode="json") for ws in result]


@mcp.tool()
async def get_workspace(workspace: str) -> dict[str, Any]:
    """Return details for a single workspace (name or GUID)."""
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        result = await workspaces.get(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def set_workspace_collation(workspace: str, collation: str) -> dict[str, Any]:
    """Set the default Data Warehouse collation for a workspace."""
    assert_writes_allowed("set_workspace_collation")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        await workspaces.set_collation(_get_http(), ws_id, collation)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"workspace_id": str(ws_id), "collation": collation}


# ---------------------------------------------------------------------------
# Warehouse tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_warehouses(workspace: str, all_workspaces: bool = False) -> list[dict[str, Any]]:  # noqa: FBT001, FBT002
    """List all warehouses and SQL analytics endpoints in a workspace.

    When *all_workspaces* is ``True``, ignore *workspace* and aggregate results
    across every workspace the caller can see.
    """
    _workspaces_allowlist = os.environ.get("FABRIC_MCP_WORKSPACES", "").strip()
    if all_workspaces and _workspaces_allowlist:
        raise ToolError(  # noqa: TRY003
            "all_workspaces=True is not permitted when FABRIC_MCP_WORKSPACES is configured; "
            "specify an individual workspace instead"
        )
    if not all_workspaces:
        assert_workspace_allowed(workspace)
    try:
        if all_workspaces:
            result = await warehouses.list_all_workspaces(_get_http())
        else:
            ws_id = await _get_resolver().workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            result = await warehouses.list_warehouses(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [wh.model_dump(by_alias=True, mode="json") for wh in result]


@mcp.tool()
async def get_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
    """Return details for a single warehouse (name or GUID)."""
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await warehouses.get_warehouse(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def create_warehouse(
    workspace: str,
    name: str,
    collation: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a new Warehouse in a workspace."""
    assert_writes_allowed("create_warehouse")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        result = await warehouses.create(
            _get_http(), ws_id, name, collation=collation, description=description
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def rename_warehouse(
    workspace: str,
    warehouse: str,
    new_name: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Rename a Warehouse (and optionally update its description)."""
    assert_writes_allowed("rename_warehouse")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await warehouses.rename(
            _get_http(), ws_id, item.id, new_name, description=description
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def delete_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
    """Delete a Warehouse."""
    assert_writes_allowed("delete_warehouse")
    assert_destructive_allowed()
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        await warehouses.delete(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"deleted": True, "warehouse_id": str(item.id)}


@mcp.tool()
async def takeover_warehouse(workspace: str, warehouse: str) -> dict[str, Any]:
    """Take ownership of a Warehouse."""
    assert_writes_allowed("takeover_warehouse")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        await ownership_svc.takeover(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"taken_over": True, "warehouse_id": str(item.id)}


@mcp.tool()
async def get_warehouse_permissions(workspace: str, warehouse: str) -> list[dict[str, Any]]:
    """Return principals with access to a Warehouse item.

    Requires Fabric Administrator role (admin API).

    See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
    """
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await _permissions_svc.list_item_access(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [a.model_dump(by_alias=True, mode="json") for a in result]


# ---------------------------------------------------------------------------
# SQL Endpoint tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_sql_endpoints(workspace: str, all_workspaces: bool = False) -> list[dict[str, Any]]:  # noqa: FBT001, FBT002
    """List all SQL analytics endpoints in a workspace.

    When *all_workspaces* is ``True``, ignore *workspace* and aggregate results
    across every workspace the caller can see.
    """
    _workspaces_allowlist = os.environ.get("FABRIC_MCP_WORKSPACES", "").strip()
    if all_workspaces and _workspaces_allowlist:
        raise ToolError(  # noqa: TRY003
            "all_workspaces=True is not permitted when FABRIC_MCP_WORKSPACES is configured; "
            "specify an individual workspace instead"
        )
    if not all_workspaces:
        assert_workspace_allowed(workspace)
    try:
        if all_workspaces:
            result = await sql_endpoints.list_all_workspaces(_get_http())
        else:
            ws_id = await _get_resolver().workspace_id(workspace)
            assert_workspace_allowed(workspace, str(ws_id))
            result = await sql_endpoints.list_endpoints(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [ep.model_dump(by_alias=True, mode="json") for ep in result]


@mcp.tool()
async def get_sql_endpoint(workspace: str, endpoint: str) -> dict[str, Any]:
    """Return details for a single SQL analytics endpoint (name or GUID)."""
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, endpoint)
        result = await sql_endpoints.get_endpoint(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def refresh_sql_endpoint_metadata(
    workspace: str,
    endpoint: str,
    recreate_tables: bool = False,  # noqa: FBT001, FBT002
) -> list[dict[str, Any]]:
    """Refresh metadata for a SQL analytics endpoint (sync from the underlying Lakehouse).

    This is a long-running operation (LRO) that is polled to completion.
    Returns a list of per-table sync results.

    Args:
        workspace: Workspace name or GUID.
        endpoint: SQL analytics endpoint name or GUID.
        recreate_tables: When ``True``, drop and recreate all tables during
            the refresh.  Use to resolve inconsistencies or force a clean
            rebuild.  **Destructive** — use with caution.
    """
    assert_writes_allowed("refresh_sql_endpoint_metadata")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, endpoint)
        statuses = await sql_endpoints.refresh_metadata(
            _get_http(), ws_id, item.id, recreate_tables=recreate_tables
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [s.model_dump(by_alias=True, mode="json") for s in statuses]


@mcp.tool()
async def get_sql_endpoint_permissions(workspace: str, sql_endpoint: str) -> list[dict[str, Any]]:
    """Return principals with access to a SQL Analytics Endpoint item.

    Requires Fabric Administrator role (admin API).

    See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
    """
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, sql_endpoint)
        result = await _permissions_svc.list_item_access(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [a.model_dump(by_alias=True, mode="json") for a in result]


# ---------------------------------------------------------------------------
# Audit tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_audit_settings(workspace: str, warehouse: str) -> dict[str, Any]:
    """Fetch the current SQL audit settings for a warehouse."""
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.get_settings(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def enable_audit(workspace: str, warehouse: str, retention_days: int = 0) -> dict[str, Any]:
    """Enable SQL auditing on a warehouse."""
    assert_writes_allowed("enable_audit")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.enable(_get_http(), ws_id, item.id, retention_days=retention_days)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def disable_audit(workspace: str, warehouse: str) -> dict[str, Any]:
    """Disable SQL auditing on a warehouse."""
    assert_writes_allowed("disable_audit")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.disable(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def set_audit_action_groups(
    workspace: str, warehouse: str, action_groups: list[str]
) -> dict[str, Any]:
    """Replace the audited action groups for a warehouse."""
    assert_writes_allowed("set_audit_action_groups")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.set_action_groups(_get_http(), ws_id, item.id, action_groups)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def add_audit_group(workspace: str, warehouse: str, group: str) -> dict[str, Any]:
    """Add a single audit action group without overwriting the others.

    Idempotent — if *group* is already present the current settings are
    returned unchanged.  Auditing must already be enabled.

    CAUTION: changes take effect immediately on the live audit policy.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse name or GUID.
        group: Action group name, e.g. ``BATCH_COMPLETED_GROUP``.
    """
    assert_writes_allowed("add_audit_group")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.add_action_group(_get_http(), ws_id, item.id, group)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def remove_audit_group(workspace: str, warehouse: str, group: str) -> dict[str, Any]:
    """Remove a single audit action group without overwriting the others.

    Idempotent — if *group* is not present the current settings are returned
    unchanged.  Auditing must already be enabled.

    CAUTION: changes take effect immediately on the live audit policy.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse name or GUID.
        group: Action group name, e.g. ``BATCH_COMPLETED_GROUP``.
    """
    assert_writes_allowed("remove_audit_group")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.remove_action_group(_get_http(), ws_id, item.id, group)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def set_audit_retention(workspace: str, warehouse: str, days: int) -> dict[str, Any]:
    """Update the audit log retention period without changing the audit enabled/disabled state.

    Audit must already be enabled; if disabled, enable it first with ``enable_audit``.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse name or GUID.
        days: Retention period in days (>= 1). The API enforces its own upper bound.
    """
    assert_writes_allowed("set_audit_retention")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await audit.set_retention(_get_http(), ws_id, item.id, days=days)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


# ---------------------------------------------------------------------------
# Query tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_running_queries(workspace: str, warehouse: str) -> list[dict[str, Any]]:
    """Return all currently-executing queries on a warehouse."""
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        if item.connection_string is None:
            msg = f"warehouse {warehouse!r} has no connection string; cannot query DMVs"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=item.display_name,
            connection_string=item.connection_string,
        )
        result = await queries.list_running(target, mode=_get_auth_mode())
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [q.model_dump(by_alias=True, mode="json") for q in result]


@mcp.tool()
async def list_connections(workspace: str, warehouse: str) -> list[dict[str, Any]]:
    """Return all active SQL connections on a warehouse or SQL Analytics Endpoint."""
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        if item.connection_string is None:
            msg = f"warehouse {warehouse!r} has no connection string; cannot query DMVs"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=item.display_name,
            connection_string=item.connection_string,
        )
        result = await queries.list_connections(target, mode=_get_auth_mode())
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [c.model_dump(by_alias=True, mode="json") for c in result]


@mcp.tool()
async def kill_session(workspace: str, warehouse: str, session_id: int) -> dict[str, Any]:
    """Terminate a session on a warehouse by session_id."""
    assert_writes_allowed("kill_session")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        if item.connection_string is None:
            msg = f"warehouse {warehouse!r} has no connection string; cannot kill sessions"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=item.display_name,
            connection_string=item.connection_string,
        )
        await queries.kill(target, session_id, mode=_get_auth_mode())
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"killed": True, "session_id": session_id}


@mcp.tool()
async def execute_sql(
    workspace: str,
    item: str,
    query: str,
    max_rows: Annotated[int, Field(ge=1, le=10000)] = 1000,
) -> dict[str, Any]:
    """Execute an arbitrary SQL statement or batch against a warehouse or SQL Analytics Endpoint.

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
    if _guards_env_flag("FABRIC_MCP_READONLY"):
        assert_readonly_sql(query)
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot execute SQL"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        result = await _sql_exec_svc.execute(
            target, query, mode=_get_auth_mode(), row_limit=max_rows
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    # The service fetches max_rows+1 rows so we can detect truncation without
    # pulling the entire result set over the wire.  Slice back to max_rows here.
    sliced_rows = result.rows[:max_rows]
    out = result.model_dump(mode="json")
    out["rows"] = sliced_rows
    out["row_count_returned"] = len(sliced_rows)
    out["truncated"] = len(result.rows) > max_rows
    return out


# ---------------------------------------------------------------------------
# Query Insights tools
# ---------------------------------------------------------------------------


async def _resolve_qi_target(workspace: str, warehouse: str) -> SqlTarget:
    """Resolve workspace + warehouse to a SqlTarget for Query Insights views."""
    assert_workspace_allowed(workspace)
    ws_id = await _get_resolver().workspace_id(workspace)
    assert_workspace_allowed(workspace, str(ws_id))
    item = await _get_resolver().item(workspace, warehouse)
    if item.connection_string is None:
        msg = f"item {warehouse!r} has no connection string; cannot query Query Insights DMVs"
        raise FabricError(msg)
    return SqlTarget(
        workspace_id=str(ws_id),
        database=item.display_name,
        connection_string=item.connection_string,
    )


def _parse_dt(value: str | None, param: str) -> datetime | None:
    """Parse an ISO-8601 string to datetime, raising ToolError on bad input."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(  # noqa: TRY003
            f"invalid {param} {value!r}: expected ISO-8601"
        ) from exc


@mcp.tool()
async def list_request_history(
    workspace: str,
    warehouse: str,
    limit: int = 100,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Return completed SQL requests from queryinsights.exec_requests_history.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse or SQL Analytics Endpoint name or GUID.
        limit: Maximum rows to return (default 100, cap 10 000).
        since: Optional ISO-8601 lower bound on submit_time.
        until: Optional ISO-8601 upper bound on submit_time.
    """
    since_dt = _parse_dt(since, "since")
    until_dt = _parse_dt(until, "until")
    try:
        target = await _resolve_qi_target(workspace, warehouse)
        result = await _qi_svc.list_request_history(
            target, limit=limit, since=since_dt, until=until_dt, mode=_get_auth_mode()
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [q.model_dump(by_alias=True, mode="json") for q in result]


@mcp.tool()
async def list_session_history(
    workspace: str,
    warehouse: str,
    limit: int = 100,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Return completed sessions from queryinsights.exec_sessions_history.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse or SQL Analytics Endpoint name or GUID.
        limit: Maximum rows to return (default 100, cap 10 000).
        since: Optional ISO-8601 lower bound on session_start_time.
        until: Optional ISO-8601 upper bound on session_start_time.
    """
    since_dt = _parse_dt(since, "since")
    until_dt = _parse_dt(until, "until")
    try:
        target = await _resolve_qi_target(workspace, warehouse)
        result = await _qi_svc.list_session_history(
            target, limit=limit, since=since_dt, until=until_dt, mode=_get_auth_mode()
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [q.model_dump(by_alias=True, mode="json") for q in result]


@mcp.tool()
async def list_frequent_queries(
    workspace: str,
    warehouse: str,
    limit: int = 100,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Return frequently-run queries from queryinsights.frequently_run_queries.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse or SQL Analytics Endpoint name or GUID.
        limit: Maximum rows to return (default 100, cap 10 000).
        since: Optional ISO-8601 lower bound on last_run_start_time.
        until: Optional ISO-8601 upper bound on last_run_start_time.
    """
    since_dt = _parse_dt(since, "since")
    until_dt = _parse_dt(until, "until")
    try:
        target = await _resolve_qi_target(workspace, warehouse)
        result = await _qi_svc.list_frequent_queries(
            target, limit=limit, since=since_dt, until=until_dt, mode=_get_auth_mode()
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [q.model_dump(by_alias=True, mode="json") for q in result]


@mcp.tool()
async def list_long_running_queries(
    workspace: str,
    warehouse: str,
    limit: int = 100,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Return long-running queries from queryinsights.long_running_queries.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse or SQL Analytics Endpoint name or GUID.
        limit: Maximum rows to return (default 100, cap 10 000).
        since: Optional ISO-8601 lower bound on last_run_start_time.
        until: Optional ISO-8601 upper bound on last_run_start_time.
    """
    since_dt = _parse_dt(since, "since")
    until_dt = _parse_dt(until, "until")
    try:
        target = await _resolve_qi_target(workspace, warehouse)
        result = await _qi_svc.list_long_running_queries(
            target, limit=limit, since=since_dt, until=until_dt, mode=_get_auth_mode()
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [q.model_dump(by_alias=True, mode="json") for q in result]


@mcp.tool()
async def list_sql_pool_insights(
    workspace: str,
    warehouse: str,
    limit: int = 100,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Return SQL pool insight events from queryinsights.sql_pool_insights.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse or SQL Analytics Endpoint name or GUID.
        limit: Maximum rows to return (default 100, cap 10 000).
        since: Optional ISO-8601 lower bound on timestamp.
        until: Optional ISO-8601 upper bound on timestamp.
    """
    since_dt = _parse_dt(since, "since")
    until_dt = _parse_dt(until, "until")
    try:
        target = await _resolve_qi_target(workspace, warehouse)
        result = await _qi_svc.list_sql_pool_insights(
            target, limit=limit, since=since_dt, until=until_dt, mode=_get_auth_mode()
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [q.model_dump(by_alias=True, mode="json") for q in result]


# ---------------------------------------------------------------------------
# Snapshot tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_snapshots(workspace: str, warehouse: str) -> list[dict[str, Any]]:
    """Return all snapshots belonging to a warehouse."""
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await snapshots.list_snapshots(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [s.model_dump(by_alias=True, mode="json") for s in result]


@mcp.tool()
async def create_snapshot(
    workspace: str,
    warehouse: str,
    name: str,
    description: str | None = None,
    snapshot_dt: str | None = None,
) -> dict[str, Any]:
    """Create a new warehouse snapshot.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse name or GUID.
        name: Display name for the new snapshot.
        description: Optional description.
        snapshot_dt: Optional ISO-8601 datetime string for the snapshot point-in-time.
    """
    assert_writes_allowed("create_snapshot")
    assert_workspace_allowed(workspace)
    parsed_dt: datetime | None = None
    if snapshot_dt is not None:
        try:
            parsed_dt = datetime.fromisoformat(snapshot_dt)
        except ValueError as exc:
            raise ToolError(  # noqa: TRY003
                f"invalid snapshot_dt {snapshot_dt!r}: expected ISO-8601"
            ) from exc
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await snapshots.create(
            _get_http(),
            ws_id,
            item.id,
            name,
            description=description,
            snapshot_dt=parsed_dt,
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def rename_snapshot(
    workspace: str,
    snapshot: str,
    new_name: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Rename a warehouse snapshot."""
    assert_writes_allowed("rename_snapshot")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        snap_item = await _get_resolver().item(workspace, snapshot)
        result = await snapshots.rename(
            _get_http(),
            ws_id,
            snap_item.id,
            new_name=new_name,
            description=description,
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def delete_snapshot(workspace: str, snapshot: str) -> dict[str, Any]:
    """Delete a warehouse snapshot."""
    assert_writes_allowed("delete_snapshot")
    assert_destructive_allowed()
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        snap_item = await _get_resolver().item(workspace, snapshot)
        await snapshots.delete(_get_http(), ws_id, snap_item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"deleted": True, "snapshot_id": str(snap_item.id)}


@mcp.tool()
async def roll_snapshot_timestamp(
    workspace: str,
    warehouse: str,
    snapshot_name: str,
    new_dt: str | None = None,
) -> dict[str, Any]:
    """Roll a snapshot's timestamp forward (or reset to current).

    Args:
        workspace: Workspace name or GUID.
        warehouse: Parent warehouse name or GUID (used for the SQL connection).
        snapshot_name: The snapshot database name to roll.
        new_dt: Optional ISO-8601 datetime string; defaults to CURRENT_TIMESTAMP.
    """
    assert_writes_allowed("roll_snapshot_timestamp")
    assert_workspace_allowed(workspace)
    parsed_dt: datetime | None = None
    if new_dt is not None:
        try:
            parsed_dt = datetime.fromisoformat(new_dt)
        except ValueError as exc:
            raise ToolError(  # noqa: TRY003
                f"invalid new_dt {new_dt!r}: expected ISO-8601"
            ) from exc
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        if item.connection_string is None:
            msg = f"warehouse {warehouse!r} has no connection string"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=item.display_name,
            connection_string=item.connection_string,
        )
        await snapshots.roll_timestamp(target, snapshot_name, parsed_dt, mode=_get_auth_mode())
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"rolled": True, "snapshot_name": snapshot_name, "new_dt": new_dt}


# ---------------------------------------------------------------------------
# Restore Point tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_restore_points(workspace: str, warehouse: str) -> list[dict[str, Any]]:
    """Return all restore points for a warehouse."""
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await restore_svc.list_points(_get_http(), ws_id, item.id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [rp.model_dump(by_alias=True, mode="json") for rp in result]


@mcp.tool()
async def get_restore_point(
    workspace: str, warehouse: str, restore_point_id: str
) -> dict[str, Any]:
    """Return a single restore point by ID.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse name or GUID.
        restore_point_id: The restore point ID string (e.g. ``"1726617378000"``).
    """
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await restore_svc.get_point(_get_http(), ws_id, item.id, restore_point_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def create_restore_point(
    workspace: str,
    warehouse: str,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a restore point for a warehouse at the current timestamp.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse name or GUID.
        name: Optional display name (max 128 chars).
        description: Optional description (max 512 chars).
    """
    assert_writes_allowed("create_restore_point")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await restore_svc.create_point(
            _get_http(), ws_id, item.id, name=name, description=description
        )
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def update_restore_point(
    workspace: str,
    warehouse: str,
    restore_point_id: str,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Rename and/or update the description of a restore point.

    At least one of *name* or *description* must be provided.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse name or GUID.
        restore_point_id: The restore point ID string.
        name: New display name (max 128 chars).
        description: New description (max 512 chars).
    """
    assert_writes_allowed("update_restore_point")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        result = await restore_svc.update_point(
            _get_http(),
            ws_id,
            item.id,
            restore_point_id,
            name=name,
            description=description,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def delete_restore_point(
    workspace: str, warehouse: str, restore_point_id: str
) -> dict[str, Any]:
    """Delete a user-defined restore point.

    System-created restore points cannot be deleted.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse name or GUID.
        restore_point_id: The restore point ID string.
    """
    assert_writes_allowed("delete_restore_point")
    assert_destructive_allowed()
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        await restore_svc.delete_point(_get_http(), ws_id, item.id, restore_point_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"deleted": True, "restore_point_id": restore_point_id}


@mcp.tool()
async def restore_warehouse_in_place(
    workspace: str, warehouse: str, restore_point_id: str
) -> dict[str, Any]:
    """Restore a warehouse in-place to a restore point.

    WARNING: This is a destructive, long-running operation. The warehouse
    will be unavailable for approximately 10 minutes while the restore
    completes.

    Args:
        workspace: Workspace name or GUID.
        warehouse: Warehouse name or GUID.
        restore_point_id: The restore point ID string to restore to.
    """
    assert_writes_allowed("restore_warehouse_in_place")
    assert_destructive_allowed()
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        item = await _get_resolver().item(workspace, warehouse)
        await restore_svc.restore_in_place(_get_http(), ws_id, item.id, restore_point_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"restored": True, "restore_point_id": restore_point_id}


# ---------------------------------------------------------------------------
# Views tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_views(workspace: str, item: str, schema: str | None = None) -> list[dict[str, Any]]:
    """List SQL views on a warehouse or SQL Analytics Endpoint.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        schema: When provided, only views in this schema are returned.
    """
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot query views"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        result = await views_svc.list_views(target, schema=schema, mode=_get_auth_mode())
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return [v.model_dump(mode="json") for v in result]


@mcp.tool()
async def read_view(
    workspace: str, item: str, qualified_name: str, count: int = 10
) -> dict[str, Any]:
    """Return up to *count* rows from a view as JSON-serialisable columns + rows.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
        count: Maximum number of rows to return (default 10).
    """
    schema, _, view_name = qualified_name.partition(".")
    if not schema or not view_name:
        raise ToolError(  # noqa: TRY003
            f"qualified_name must be <schema>.<view>, got {qualified_name!r}"
        )
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot read views"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        columns, rows = await views_svc.read_view(
            target, schema, view_name, count=count, mode=_get_auth_mode()
        )
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return {
        "columns": columns,
        "rows": [[_json_safe_value(v) for v in row] for row in rows],
    }


@mcp.tool()
async def get_view(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
    """Fetch the full definition of a view (schema.view).

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
    """
    schema, _, view_name = qualified_name.partition(".")
    if not schema or not view_name:
        raise ToolError(  # noqa: TRY003
            f"qualified_name must be <schema>.<view>, got {qualified_name!r}"
        )
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot query views"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        result = await views_svc.get_view(target, schema, view_name, mode=_get_auth_mode())
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return result.model_dump(mode="json")


@mcp.tool()
async def create_view(
    workspace: str, item: str, qualified_name: str, select_body: str
) -> dict[str, Any]:
    """Create a new SQL view.

    CAUTION: ``select_body`` is executed verbatim as DDL. Ensure the body
    matches the user's intent before calling this tool.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
        select_body: The SELECT statement that forms the view body.
    """
    schema, _, view_name = qualified_name.partition(".")
    if not schema or not view_name:
        raise ToolError(  # noqa: TRY003
            f"qualified_name must be <schema>.<view>, got {qualified_name!r}"
        )
    assert_writes_allowed("create_view")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot create views"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        result = await views_svc.create_view(
            target, schema, view_name, select_body, mode=_get_auth_mode()
        )
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return result.model_dump(mode="json")


@mcp.tool()
async def update_view(
    workspace: str, item: str, qualified_name: str, select_body: str
) -> dict[str, Any]:
    """Redefine a SQL view via CREATE OR ALTER VIEW.

    CAUTION: ``select_body`` is executed verbatim as DDL. Ensure the body
    matches the user's intent before calling this tool.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
        select_body: The new SELECT statement.
    """
    schema, _, view_name = qualified_name.partition(".")
    if not schema or not view_name:
        raise ToolError(  # noqa: TRY003
            f"qualified_name must be <schema>.<view>, got {qualified_name!r}"
        )
    assert_writes_allowed("update_view")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot update views"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        result = await views_svc.update_view(
            target, schema, view_name, select_body, mode=_get_auth_mode()
        )
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return result.model_dump(mode="json")


@mcp.tool()
async def drop_view(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
    """Drop a SQL view.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        qualified_name: Dot-separated qualified view name, e.g. ``dbo.vw_sales``.
    """
    schema, _, view_name = qualified_name.partition(".")
    if not schema or not view_name:
        raise ToolError(  # noqa: TRY003
            f"qualified_name must be <schema>.<view>, got {qualified_name!r}"
        )
    assert_writes_allowed("drop_view")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot drop views"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        await views_svc.drop_view(target, schema, view_name, mode=_get_auth_mode())
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return {"dropped": True}


# ---------------------------------------------------------------------------
# Schema tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_schemas(workspace: str, item: str) -> list[dict[str, Any]]:
    """List user-defined SQL schemas on a warehouse or SQL Analytics Endpoint.

    System schemas (``sys``, ``INFORMATION_SCHEMA``, ``db_*`` fixed-role
    schemas, ``guest``) are excluded.  ``dbo`` is included as it is
    user-writable.

    Listing schemas is a read-only operation and works on both Fabric Data
    Warehouses and SQL Analytics Endpoints.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL Analytics Endpoint name or GUID.
    """
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot query schemas"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        result = await schemas_svc.list_schemas(target, mode=_get_auth_mode())
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return [s.model_dump(mode="json") for s in result]


@mcp.tool()
async def create_schema(workspace: str, item: str, name: str) -> dict[str, Any]:
    """Create a new SQL schema on a warehouse.

    Only Fabric Data Warehouses are supported; SQL Analytics Endpoints are
    rejected because they are read-only views over Lakehouse data.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse name or GUID.
        name: The schema name.  Must be a valid SQL identifier.
    """
    assert_writes_allowed("create_schema")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        _require_warehouse(entry, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot create schemas"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        result = await schemas_svc.create_schema(target, name, mode=_get_auth_mode())
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return result.model_dump(mode="json")


@mcp.tool()
async def delete_schema(
    workspace: str,
    item: str,
    name: str,
    cascade: bool = False,  # noqa: FBT001, FBT002
) -> dict[str, Any]:
    """Drop a SQL schema from a warehouse.

    CAUTION: This is a destructive, irreversible operation.  The schema will
    be permanently deleted.  If the schema still contains tables or views,
    the operation will fail unless *cascade* is ``True``.

    CAUTION: When *cascade* is ``True``, **all tables and views in the schema
    are permanently deleted along with their data**.  Confirm explicitly with
    the user before calling with ``cascade=True``.

    Only Fabric Data Warehouses are supported; SQL Analytics Endpoints are
    rejected.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse name or GUID.
        name: The schema name to drop.
        cascade: When ``True``, drop all tables and views in the schema first.
            Defaults to ``False``.
    """
    assert_writes_allowed("delete_schema")
    assert_destructive_allowed()
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        _require_warehouse(entry, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot delete schemas"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        await schemas_svc.delete_schema(target, name, cascade=cascade, mode=_get_auth_mode())
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Tables tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_tables(workspace: str, item: str, schema: str | None = None) -> list[dict[str, Any]]:
    """List SQL tables on a warehouse or SQL Analytics Endpoint.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        schema: When provided, only tables in this schema are returned.
    """
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot query tables"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        result = await tables_svc.list_tables(target, schema=schema, mode=_get_auth_mode())
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return [t.model_dump(mode="json") for t in result]


@mcp.tool()
async def read_table(
    workspace: str, item: str, qualified_name: str, count: int = 10
) -> dict[str, Any]:
    """Return up to *count* rows from a table as JSON-serialisable columns + rows.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
        count: Maximum number of rows to return (default 10).
    """
    schema, _, table_name = qualified_name.partition(".")
    if not schema or not table_name:
        raise ToolError(  # noqa: TRY003
            f"qualified_name must be <schema>.<table>, got {qualified_name!r}"
        )
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot read tables"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        columns, rows = await tables_svc.read_table(
            target, schema, table_name, count=count, mode=_get_auth_mode()
        )
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return {
        "columns": columns,
        "rows": [[_json_safe_value(v) for v in row] for row in rows],
    }


@mcp.tool()
async def create_table(
    workspace: str, item: str, qualified_name: str, select_body: str
) -> dict[str, Any]:
    """Create a new SQL table via CTAS (CREATE TABLE AS SELECT).

    CAUTION: ``select_body`` is executed verbatim as DDL on the warehouse.
    Ensure the body matches the user's intent before calling this tool.
    The first non-comment keyword of ``select_body`` must be SELECT.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
        select_body: The SELECT statement that becomes the CTAS source.
    """
    schema, _, table_name = qualified_name.partition(".")
    if not schema or not table_name:
        raise ToolError(  # noqa: TRY003
            f"qualified_name must be <schema>.<table>, got {qualified_name!r}"
        )
    assert_writes_allowed("create_table")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot create tables"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        result = await tables_svc.create_table(
            target, schema, table_name, select_body, kind=entry.kind, mode=_get_auth_mode()
        )
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return result.model_dump(mode="json")


@mcp.tool()
async def delete_table(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
    """Drop a SQL table.

    CAUTION: This is a destructive, irreversible operation.  The table and all
    its data will be permanently deleted.  Confirm with the user before calling.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
    """
    schema, _, table_name = qualified_name.partition(".")
    if not schema or not table_name:
        raise ToolError(  # noqa: TRY003
            f"qualified_name must be <schema>.<table>, got {qualified_name!r}"
        )
    assert_writes_allowed("delete_table")
    assert_destructive_allowed()
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot delete tables"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        await tables_svc.delete_table(
            target, schema, table_name, kind=entry.kind, mode=_get_auth_mode()
        )
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return {"dropped": True}


@mcp.tool()
async def clear_table(workspace: str, item: str, qualified_name: str) -> dict[str, Any]:
    """Truncate a SQL table (remove all rows, keep structure).

    CAUTION: This is a destructive, irreversible operation.  All rows will be
    permanently deleted.  The table structure and schema are preserved.
    Confirm with the user before calling.

    Args:
        workspace: Workspace name or GUID.
        item: Warehouse or SQL endpoint name or GUID.
        qualified_name: Dot-separated qualified table name, e.g. ``dbo.sales``.
    """
    schema, _, table_name = qualified_name.partition(".")
    if not schema or not table_name:
        raise ToolError(  # noqa: TRY003
            f"qualified_name must be <schema>.<table>, got {qualified_name!r}"
        )
    assert_writes_allowed("clear_table")
    assert_destructive_allowed()
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        entry = await _get_resolver().item(workspace, item)
        if entry.connection_string is None:
            msg = f"item {item!r} has no connection string; cannot clear tables"
            raise FabricError(msg)  # noqa: TRY301
        target = SqlTarget(
            workspace_id=str(ws_id),
            database=entry.display_name,
            connection_string=entry.connection_string,
        )
        await tables_svc.clear_table(
            target, schema, table_name, kind=entry.kind, mode=_get_auth_mode()
        )
    except (ValueError, FabricError) as exc:
        raise _fabric_err(exc) if isinstance(exc, FabricError) else ToolError(str(exc)) from exc
    return {"truncated": True}


# ---------------------------------------------------------------------------
# SQL Pools tools (beta)
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_sql_pools_configuration(workspace: str) -> dict[str, Any]:
    """Fetch the full SQL Pools configuration (enabled flag + pool list) for a workspace.

    Requires workspace admin role.  This tool targets a **beta / preview** API
    endpoint that may change before general availability.
    """
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        result = await sql_pools_svc.get_configuration(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def list_sql_pools(workspace: str) -> list[dict[str, Any]]:
    """Return the list of custom SQL pools for a workspace.

    Requires workspace admin role.  This tool targets a **beta / preview** API.
    """
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        config = await sql_pools_svc.get_configuration(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return [p.model_dump(by_alias=True, mode="json") for p in config.custom_sql_pools]


@mcp.tool()
async def get_sql_pool(workspace: str, pool_name: str) -> dict[str, Any]:
    """Return details for a single SQL pool by name.

    Args:
        workspace: Workspace name or GUID.
        pool_name: The pool name.

    Requires workspace admin role.  This tool targets a **beta / preview** API.
    """
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        config = await sql_pools_svc.get_configuration(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    pool = next((p for p in config.custom_sql_pools if p.name == pool_name), None)
    if pool is None:
        raise ToolError(f"pool {pool_name!r} not found")  # noqa: TRY003
    return pool.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def create_sql_pool(  # noqa: PLR0913
    workspace: str,
    name: str,
    max_percent: int,
    is_default: bool = False,  # noqa: FBT001, FBT002
    optimize_for_reads: bool = True,  # noqa: FBT001, FBT002
    classifier_type: str | None = None,
    classifier_values: list[str] | None = None,
) -> dict[str, Any]:
    """Add a new custom SQL pool to a workspace.

    Args:
        workspace: Workspace name or GUID.
        name: Pool name (must be unique within the workspace).
        max_percent: Max resource percentage (1-100).
        is_default: Whether this pool is the default pool. Defaults to false.
        optimize_for_reads: Enable read optimisation. Defaults to true.
        classifier_type: Classifier type (e.g. ``"Application Name"``).
        classifier_values: List of classifier values (e.g. application names).

    Requires workspace admin role.  This tool targets a **beta / preview** API.
    """
    assert_writes_allowed("create_sql_pool")
    assert_workspace_allowed(workspace)
    classifier: SqlPoolClassifier | None = None
    if classifier_type is not None:
        classifier = SqlPoolClassifier.model_validate(
            {"type": classifier_type, "value": classifier_values or []}
        )

    try:
        pool = SqlPool.model_validate(
            {
                "name": name,
                "isDefault": is_default,
                "maxResourcePercentage": max_percent,
                "optimizeForReads": optimize_for_reads,
                "classifier": (
                    classifier.model_dump(by_alias=True, mode="json") if classifier else None
                ),
            }
        )
    except Exception as exc:
        raise ToolError(f"Invalid pool: {exc}") from exc  # noqa: TRY003

    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        result = await sql_pools_svc.create_pool(_get_http(), ws_id, pool)
    except AlreadyExists as exc:
        raise ToolError(str(exc)) from exc
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    created = next(p for p in result.custom_sql_pools if p.name == name)
    return created.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def update_sql_pool(  # noqa: PLR0913
    workspace: str,
    name: str,
    max_percent: int | None = None,
    is_default: bool | None = None,  # noqa: FBT001
    optimize_for_reads: bool | None = None,  # noqa: FBT001
    classifier_type: str | None = None,
    classifier_values: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing SQL pool.  Only the parameters you supply are changed.

    Args:
        workspace: Workspace name or GUID.
        name: Name of the pool to update.
        max_percent: New max resource percentage (1-100), or omit to keep current.
        is_default: Set or clear the default flag, or omit to keep current.
        optimize_for_reads: Enable/disable read optimisation, or omit to keep current.
        classifier_type: New classifier type, or omit to keep current.
        classifier_values: New classifier value list, or omit to keep current.

    Requires workspace admin role.  This tool targets a **beta / preview** API.
    """
    assert_writes_allowed("update_sql_pool")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        result = await sql_pools_svc.update_pool(
            _get_http(),
            ws_id,
            name,
            max_resource_percentage=max_percent,
            is_default=is_default,
            optimize_for_reads=optimize_for_reads,
            classifier_type=classifier_type,
            classifier_values=classifier_values,
        )
    except NotFound as exc:
        raise ToolError(str(exc)) from exc
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    updated = next(p for p in result.custom_sql_pools if p.name == name)
    return updated.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def delete_sql_pool(workspace: str, pool_name: str) -> dict[str, Any]:
    """Delete an SQL pool from a workspace.

    Args:
        workspace: Workspace name or GUID.
        pool_name: Name of the pool to delete.

    Requires workspace admin role.  This tool targets a **beta / preview** API.
    """
    assert_writes_allowed("delete_sql_pool")
    assert_destructive_allowed()
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        await sql_pools_svc.delete_pool(_get_http(), ws_id, pool_name)
    except NotFound as exc:
        raise ToolError(str(exc)) from exc
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return {"deleted": True, "pool_name": pool_name}


@mcp.tool()
async def reset_sql_pools(workspace: str) -> dict[str, Any]:
    """Clear all SQL pools for a workspace, preserving the enabled/disabled state.

    Requires workspace admin role.  This tool targets a **beta / preview** API.

    CAUTION: this permanently removes ALL pool definitions in the workspace. The
    configuration's enabled-state is preserved, but every pool is wiped. Use only
    when the user explicitly requests a reset.
    """
    assert_writes_allowed("reset_sql_pools")
    assert_destructive_allowed()
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        result = await sql_pools_svc.reset_pools(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    if result is None:
        return {"message": "Workspace has no SQL pools configuration (never provisioned)."}
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def enable_sql_pools(workspace: str) -> dict[str, Any]:
    """Enable custom SQL Pools for a workspace without modifying pool definitions.

    Requires workspace admin role.  This tool targets a **beta / preview** API.
    """
    assert_writes_allowed("enable_sql_pools")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        result = await sql_pools_svc.enable(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


@mcp.tool()
async def disable_sql_pools(workspace: str) -> dict[str, Any]:
    """Disable custom SQL Pools for a workspace, preserving pool configuration.

    Re-enabling with enable_sql_pools restores the previously saved configuration.

    Requires workspace admin role.  This tool targets a **beta / preview** API.
    """
    assert_writes_allowed("disable_sql_pools")
    assert_workspace_allowed(workspace)
    try:
        ws_id = await _get_resolver().workspace_id(workspace)
        assert_workspace_allowed(workspace, str(ws_id))
        result = await sql_pools_svc.disable(_get_http(), ws_id)
    except FabricError as exc:
        raise _fabric_err(exc) from exc
    return result.model_dump(by_alias=True, mode="json")


# ---------------------------------------------------------------------------
# Cache tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def clear_cache() -> dict[str, Any]:
    """Erase all cached workspace and item name to UUID mappings."""
    _get_cache().clear()
    return {"cleared": True}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def run(argv: Sequence[str] | None = None) -> None:
    """Parse CLI arguments and start the FastMCP server.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when ``None``).

    Transport options
    -----------------
    ``--transport stdio`` (default)
        Communicate over stdin/stdout — standard for Claude Desktop and similar.
    ``--transport http``
        Expose a streamable-HTTP endpoint.

    HTTP-transport options
    ----------------------
    ``--host HOST``
        Bind address for HTTP transport (default ``127.0.0.1``).  Binding to
        a non-loopback address requires ``FABRIC_MCP_ALLOW_REMOTE=1``.
    ``--port PORT``
        TCP port for HTTP transport (default ``8000``).
    """
    # Configure structured logging from env var (default INFO)
    raw_level = os.environ.get("FABRIC_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, raw_level, logging.INFO)
    setup_logging(log_level)

    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(
        prog="fabric-dw-mcp",
        description="Microsoft Fabric Data Warehouse MCP server",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport to use: 'stdio' (default) or 'http' (streamable-HTTP).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for HTTP transport (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="TCP port for HTTP transport (default: 8000).",
    )
    args = parser.parse_args(argv)

    transport: Literal["stdio", "streamable-http"] = (
        "streamable-http" if args.transport == "http" else "stdio"
    )

    if transport == "streamable-http":
        if args.host not in _LOOPBACK_HOSTS:
            if not _guards_env_flag("FABRIC_MCP_ALLOW_REMOTE"):
                logger.error(
                    "refusing to bind HTTP transport on %s:%s — this would expose the server "
                    "network-wide without authentication or TLS. "
                    "Set FABRIC_MCP_ALLOW_REMOTE=1 to override (ensure a reverse proxy provides "
                    "authentication and TLS termination).",
                    args.host,
                    args.port,
                )
                sys.exit(1)
            logger.warning(
                "WARNING: HTTP transport is bound on %s:%s (non-loopback). "
                "The MCP protocol has NO built-in authentication or TLS. "
                "Ensure an authenticating reverse proxy fronts this endpoint before "
                "exposing it to untrusted networks.",
                args.host,
                args.port,
            )

        # Update FastMCP settings before run so uvicorn picks them up.
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(transport=transport)
