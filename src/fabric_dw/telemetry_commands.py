"""Per-command telemetry instrumentation for the fabric-dw CLI and MCP server.

Emits one ``command_invoked`` event per CLI command invocation and per MCP
tool call, with categorical/aggregate attributes only — never identifiers,
SQL text, or flag values.

Public API
----------
- :func:`resolve_domain` — map a CLI group name or MCP tool name to a domain.
- :func:`map_status` — classify an exception (or ``None``) to a status string.
- :func:`duration_bucket` — bucket a float duration in ms to a human label.
- :func:`emit_command_invoked` — fire the ``command_invoked`` event (safe).
- :data:`DOMAIN_MAP` — the authoritative name → domain lookup dict.
"""

from __future__ import annotations

import contextlib
import logging
import time

__all__ = [
    "DOMAIN_MAP",
    "duration_bucket",
    "emit_command_invoked",
    "map_status",
    "resolve_domain",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain map — maps CLI group names AND MCP tool-name prefixes/overrides
# to domain strings.
#
# Rules:
#   1. CLI commands: use the Click group name (e.g. "warehouses").
#   2. MCP tools:    the tool name is the key (exact match), or the leading
#      verb+noun prefix that maps to a domain (fallback: prefix before "_").
# ---------------------------------------------------------------------------

#: Mapping from CLI group name or MCP tool name to a domain string.
#: Add entries here when new commands or tools are introduced.
DOMAIN_MAP: dict[str, str] = {
    # ── CLI group names ───────────────────────────────────────────────────────
    "workspaces": "workspaces",
    "warehouses": "warehouses",
    "sql-endpoints": "sql_endpoints",
    "sql_endpoints": "sql_endpoints",
    "sql": "sql",
    "tables": "tables",
    "views": "views",
    "procedures": "procedures",
    "schemas": "schemas",
    "statistics": "statistics",
    "functions": "functions",
    "snapshots": "snapshots",
    "restore-points": "restore_points",
    "restore_points": "restore_points",
    "audit": "audit",
    "queries": "queries",
    "sql-pools": "sql_pools",
    "sql_pools": "sql_pools",
    "dbt": "dbt",
    "cache": "cache",
    "config": "config",
    "completion": "completion",
    # ── MCP tool names (explicit overrides / multi-domain tools) ─────────────
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
    "get_warehouse_permissions": "warehouses",
    # SQL Endpoints
    "list_sql_endpoints": "sql_endpoints",
    "get_sql_endpoint": "sql_endpoints",
    "refresh_sql_endpoint_metadata": "sql_endpoints",
    "get_sql_endpoint_permissions": "sql_endpoints",
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
    "delete_table": "tables",
    "clear_table": "tables",
    "load_table_from_url": "tables",
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
    # Stored procedures
    "list_procedures": "procedures",
    "get_procedure": "procedures",
    "create_procedure": "procedures",
    "update_procedure": "procedures",
    "drop_procedure": "procedures",
    # Functions
    "list_functions": "functions",
    "get_function": "functions",
    "create_function": "functions",
    "update_function": "functions",
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
    "settings": "settings",
    "get_warehouse_settings": "settings",
    "set_result_set_caching": "settings",
    "set_time_travel_retention": "settings",
    # Tables (load sub-domain)
    "import_table_from_url": "tables",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KNOWN_DOMAINS: frozenset[str] = frozenset(
    {
        "workspaces",
        "warehouses",
        "sql_endpoints",
        "sql",
        "tables",
        "views",
        "procedures",
        "schemas",
        "statistics",
        "functions",
        "snapshots",
        "restore_points",
        "audit",
        "queries",
        "sql_pools",
        "dbt",
        "cache",
        "config",
        "completion",
        "settings",
    }
)


def resolve_domain(name: str) -> str:
    """Return the domain for a CLI group name or MCP tool name.

    Looks up *name* in :data:`DOMAIN_MAP` first.  Falls back to the string
    before the first underscore for MCP tool names whose prefix maps to a
    known domain.  Returns ``"unknown"`` when no mapping is found.

    Args:
        name: A CLI group name (e.g. ``"warehouses"``) or MCP tool name
              (e.g. ``"create_warehouse"``).

    Returns:
        A domain string from the :data:`_KNOWN_DOMAINS` set, or ``"unknown"``.
    """
    domain = DOMAIN_MAP.get(name)
    if domain is not None:
        return domain
    # Fallback: try the first segment before the first underscore.
    prefix = name.split("_", maxsplit=1)[0] if "_" in name else name
    fallback = DOMAIN_MAP.get(prefix)
    if fallback is not None:
        return fallback
    return "unknown"


def _click_exit_code(exc: BaseException) -> int:
    """Extract the exit code from a :class:`click.exceptions.Exit` instance.

    Click stores the exit code in ``exit_code`` (the constructor attribute) or
    ``code`` (the ``SystemExit`` base-class attribute).  The ``or``-chain
    idiom ``getattr(exc, "exit_code", None) or getattr(exc, "code", 0)`` is
    **incorrect** when the exit code is ``0`` (a falsy value): the chain would
    fall through to ``code`` even though ``exit_code`` is explicitly ``0``.

    This helper checks each attribute explicitly, using ``is None`` as the
    sentinel, and defaults to ``0`` only when neither attribute is set.
    """
    v = getattr(exc, "exit_code", None)
    if v is not None:
        return int(v)
    v = getattr(exc, "code", None)
    if v is not None:
        return int(v)
    return 0


def _is_click_user_error(exc: BaseException) -> bool:
    """Return True when *exc* is a Click user-facing error (usage/abort/non-zero exit)."""
    with contextlib.suppress(ImportError):
        import click  # noqa: PLC0415

        if isinstance(exc, (click.exceptions.UsageError, click.exceptions.Abort)):
            return True
        if isinstance(exc, click.exceptions.Exit):
            return _click_exit_code(exc) != 0
        if isinstance(exc, SystemExit):
            code = getattr(exc, "code", None)
            return not (code is None or code == 0)
    return False


def _is_click_exit_ok(exc: BaseException) -> bool:
    """Return True when *exc* is a zero-code Click/System exit (success)."""
    with contextlib.suppress(ImportError):
        import click  # noqa: PLC0415

        if isinstance(exc, click.exceptions.Exit):
            return _click_exit_code(exc) == 0
        if isinstance(exc, SystemExit):
            code = getattr(exc, "code", None)
            return code is None or code == 0
    return False


def map_status(exc: BaseException | None) -> str:
    """Map an exception (or None for success) to a categorical status string.

    Status categories:

    - ``"success"``  -- no exception (normal return) or zero-exit Click/SystemExit.
    - ``"user_error"`` -- validation / usage problems: Click usage errors,
      Abort, ValueError, ConfigError, and NotFoundError
      (which typically means the user referenced a non-existent resource).
    - ``"api_error"`` -- FabricError / HTTP / driver / unexpected exceptions.

    Args:
        exc: The active exception, or ``None`` when the command succeeded.

    Returns:
        One of ``"success"``, ``"user_error"``, or ``"api_error"``.
    """
    if exc is None or _is_click_exit_ok(exc):
        return "success"
    if _is_click_user_error(exc):
        return "user_error"

    with contextlib.suppress(ImportError):
        from fabric_dw.exceptions import ConfigError, FabricError, NotFoundError  # noqa: PLC0415

        if isinstance(exc, (ValueError, ConfigError, NotFoundError)):
            return "user_error"
        if isinstance(exc, FabricError):
            return "api_error"

    with contextlib.suppress(ImportError):
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        if isinstance(exc, ToolError):
            return "user_error"

    return "api_error"


_MS_100 = 100.0
_MS_1S = 1_000.0
_MS_10S = 10_000.0


def duration_bucket(duration_ms: float) -> str:
    """Bucket *duration_ms* (milliseconds) to a human-readable label.

    Buckets:

    - ``"<100ms"`` -- under 100 ms
    - ``"<1s"``    -- 100 ms to 1 s
    - ``"<10s"``   -- 1 s to 10 s
    - ``">10s"``   -- 10 s or more

    Args:
        duration_ms: Wall-clock duration in milliseconds.

    Returns:
        A bucket label string.
    """
    if duration_ms < _MS_100:
        return "<100ms"
    if duration_ms < _MS_1S:
        return "<1s"
    if duration_ms < _MS_10S:
        return "<10s"
    return ">10s"


def emit_command_invoked(
    *,
    name: str,
    status: str,
    duration_ms: float,
    destructive: bool = False,
) -> None:
    """Emit one ``command_invoked`` telemetry event.

    Fire-and-forget: all exceptions are swallowed and nothing is raised.
    When telemetry is disabled this is a guaranteed no-op (no work done).

    Args:
        name: The command name — for CLI: ``"<group>.<subcommand>"``,
              for MCP: the tool name.
        status: One of ``"success"``, ``"user_error"``, ``"api_error"``.
        duration_ms: Wall-clock duration in milliseconds.
        destructive: Whether this is a permanently-destructive operation.
    """
    try:
        from fabric_dw.telemetry import emit_event, telemetry_enabled  # noqa: PLC0415

        if not telemetry_enabled():
            return

        domain = resolve_domain(name.split(".", maxsplit=1)[0] if "." in name else name)
        attrs: dict[str, object] = {
            # ``name`` is kept as a custom dimension for convenience (queryable),
            # even though it is also set as ``ai.operation.name`` (native field).
            "name": name,
            "domain": domain,
            "status": status,
            "duration_ms_bucket": duration_bucket(duration_ms),
            # ai.operation.name → native operation_Name / AppRoleInstance portal column.
            # Set to the command/tool name so it appears in the portal instead of blank.
            "ai.operation.name": name,
        }
        if destructive:
            attrs["destructive_op"] = True

        emit_event("command_invoked", attrs)
    except Exception:
        _log.debug("Failed to emit command_invoked event for %r", name, exc_info=True)


# ---------------------------------------------------------------------------
# Monotonic timer helper (cheap, no imports beyond stdlib)
# ---------------------------------------------------------------------------


def now_ms() -> float:
    """Return the current monotonic time in milliseconds."""
    return time.monotonic() * 1_000
