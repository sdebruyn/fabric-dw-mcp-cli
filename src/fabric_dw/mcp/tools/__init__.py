"""Per-domain MCP tool registration modules.

Each sub-module exposes a ``register(mcp: FastMCP) -> None`` function that
decorates and registers that domain's tools against the provided
:class:`~mcp.server.fastmcp.FastMCP` instance.

Domains
-------
- :mod:`.workspaces` — workspace listing, detail, collation
- :mod:`.warehouses` — warehouse CRUD, takeover
- :mod:`.sql_endpoints` — SQL Analytics Endpoint listing, detail, refresh
- :mod:`.permissions` — Fabric item-level permissions (REST) and T-SQL GRANT/DENY/REVOKE
- :mod:`.audit` — SQL audit settings management
- :mod:`.queries` — running queries, connections, kill session, query-insights DMVs
- :mod:`.sql_exec` — generic SQL execution (execute_sql)
- :mod:`.snapshots` — warehouse snapshot CRUD, roll timestamp
- :mod:`.restore` — restore points CRUD, in-place restore
- :mod:`.views` — SQL view listing, reading, CRUD
- :mod:`.procedures` — stored procedure listing and CRUD
- :mod:`.functions` — T-SQL user-defined function listing and CRUD
- :mod:`.schemas` — SQL schema listing and DDL
- :mod:`.tables` — SQL table listing, reading, DDL
- :mod:`.load` — ``COPY INTO`` table loading (remote URL)
- :mod:`.sql_pools` — SQL Pools beta API, pool insights DMV
- :mod:`.statistics` — DW statistics listing, inspection, and DDL
- :mod:`.settings` — warehouse database settings (result-set caching, time-travel retention)
- :mod:`.cache` — cache management (clear_cache)
- :mod:`.dbt` — generate dbt-fabric project file contents
- :mod:`.capabilities` — list all available tools grouped by domain
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from fabric_dw.mcp.tools import (
    audit,
    cache,
    capabilities,
    dbt,
    functions,
    load,
    permissions,
    procedures,
    queries,
    restore,
    schemas,
    settings,
    snapshots,
    sql_endpoints,
    sql_exec,
    sql_pools,
    statistics,
    tables,
    views,
    warehouses,
    workspaces,
)

__all__ = ["register_all"]

_DOMAINS = [
    workspaces,
    warehouses,
    sql_endpoints,
    permissions,
    audit,
    queries,
    sql_exec,
    snapshots,
    restore,
    views,
    procedures,
    functions,
    schemas,
    tables,
    load,
    statistics,
    settings,
    sql_pools,
    cache,
    dbt,
    capabilities,
]


def register_all(mcp: FastMCP) -> None:
    """Register all domain tools against *mcp*."""
    for domain in _DOMAINS:
        domain.register(mcp)
