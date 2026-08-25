"""Domain classification for the MCP tool surface.

``list_capabilities`` groups the registered tools by domain so a client can see
what the server can do without reading every tool description.  The grouping is
an explicit table rather than something derived from the tool name, because the
name alone does not carry it: ``restore_warehouse_in_place`` belongs to restore
points, not warehouses, and ``list_masked_columns`` belongs to permissions, not
tables.

Every registered tool must have an entry.  Two guards in
``tests/unit/mcp/test_contract.py`` keep the table and the live server in step:
one fails when a registered tool has no domain, the other when the table names a
tool that no longer exists.
"""

from __future__ import annotations

__all__ = ["TOOL_DOMAINS", "domain_for_tool"]

#: Mapping from MCP tool name to its domain.  Add an entry here when a tool is
#: added; the contract tests fail otherwise.
TOOL_DOMAINS: dict[str, str] = {
    # Workspaces
    "assign_workspace_to_capacity": "workspaces",
    "list_capacities": "workspaces",
    "list_workspaces": "workspaces",
    "get_workspace": "workspaces",
    "set_workspace_collation": "workspaces",
    # Warehouses
    "list_warehouses": "warehouses",
    "get_warehouse": "warehouses",
    "create_warehouse": "warehouses",
    "rename_warehouse": "warehouses",
    "delete_warehouse": "warehouses",
    "takeover_warehouse": "warehouses",
    # SQL Endpoints
    "list_sql_endpoints": "sql_endpoints",
    "get_sql_endpoint": "sql_endpoints",
    "refresh_sql_endpoint_metadata": "sql_endpoints",
    "list_item_permissions": "permissions",
    "list_sql_permissions": "permissions",
    "list_database_principals": "permissions",
    "my_permissions": "permissions",
    "grant_permission": "permissions",
    "deny_permission": "permissions",
    "revoke_permission": "permissions",
    # RLS
    "list_security_policies": "permissions",
    "create_security_policy": "permissions",
    "add_security_predicate": "permissions",
    "drop_security_predicate": "permissions",
    "set_security_policy_state": "permissions",
    "drop_security_policy": "permissions",
    # Dynamic data masking
    "list_masked_columns": "permissions",
    "set_column_mask": "permissions",
    "drop_column_mask": "permissions",
    # Audit
    "get_audit_settings": "audit",
    "enable_audit": "audit",
    "disable_audit": "audit",
    "set_audit_action_groups": "audit",
    "add_audit_group": "audit",
    "remove_audit_group": "audit",
    "set_audit_retention": "audit",
    # Queries / running sessions
    "list_running_queries": "queries",
    "kill_session": "queries",
    "list_connections": "queries",
    "list_request_history": "queries",
    "list_session_history": "queries",
    "list_frequent_queries": "queries",
    "list_long_running_queries": "queries",
    "list_locks": "queries",
    "get_request_detail": "queries",
    # SQL execution
    "execute_sql": "sql",
    "get_query_plan": "sql",
    # Snapshots
    "list_snapshots": "snapshots",
    "create_snapshot": "snapshots",
    "rename_snapshot": "snapshots",
    "delete_snapshot": "snapshots",
    "roll_snapshot_timestamp": "snapshots",
    # Restore points
    "list_restore_points": "restore_points",
    "get_restore_point": "restore_points",
    "create_restore_point": "restore_points",
    "update_restore_point": "restore_points",
    "delete_restore_point": "restore_points",
    "restore_warehouse_in_place": "restore_points",
    # Schemas
    "list_schemas": "schemas",
    "create_schema": "schemas",
    "delete_schema": "schemas",
    # Tables
    "get_table_columns": "tables",
    "get_table_health_metrics": "tables",
    "list_tables": "tables",
    "read_table": "tables",
    "count_table_rows": "tables",
    "get_cluster_columns": "tables",
    "set_cluster_columns": "tables",
    "create_table": "tables",
    "create_empty_table": "tables",
    "clone_table": "tables",
    "rename_table": "tables",
    "transfer_table": "tables",
    "delete_table": "tables",
    "clear_table": "tables",
    "load_table_from_url": "tables",
    "import_table_from_url": "tables",
    # Views
    "get_view_columns": "views",
    "list_views": "views",
    "read_view": "views",
    "count_view_rows": "views",
    "get_view": "views",
    "create_view": "views",
    "update_view": "views",
    "drop_view": "views",
    "rename_view": "views",
    "transfer_view": "views",
    # Stored procedures
    "list_procedures": "procedures",
    "get_procedure": "procedures",
    "create_procedure": "procedures",
    "update_procedure": "procedures",
    "drop_procedure": "procedures",
    "transfer_procedure": "procedures",
    # Functions
    "list_functions": "functions",
    "get_function": "functions",
    "create_function": "functions",
    "update_function": "functions",
    "transfer_function": "functions",
    "drop_function": "functions",
    # Statistics
    "list_statistics": "statistics",
    "show_statistics": "statistics",
    "create_statistics": "statistics",
    "update_statistics": "statistics",
    "delete_statistics": "statistics",
    # SQL Pools
    "get_sql_pools_status": "sql_pools",
    "list_sql_pools": "sql_pools",
    "get_sql_pool": "sql_pools",
    "create_sql_pool": "sql_pools",
    "update_sql_pool": "sql_pools",
    "delete_sql_pool": "sql_pools",
    "enable_sql_pools": "sql_pools",
    "disable_sql_pools": "sql_pools",
    "list_sql_pool_insights": "sql_pools",
    # DBT
    "generate_dbt_profile": "dbt",
    # Cache
    "clear_cache": "cache",
    # Settings (server-side warehouse settings)
    "get_warehouse_settings": "settings",
    "set_result_set_caching": "settings",
    "set_time_travel_retention": "settings",
    "set_data_lake_log_publishing": "settings",
    # Server meta
    "list_capabilities": "server",
}


def domain_for_tool(name: str) -> str:
    """Return the domain *name* belongs to.

    Args:
        name: A registered MCP tool name, e.g. ``"create_warehouse"``.

    Returns:
        The tool's domain, or ``"unknown"`` when the tool has no entry in
        :data:`TOOL_DOMAINS`.
    """
    return TOOL_DOMAINS.get(name, "unknown")
