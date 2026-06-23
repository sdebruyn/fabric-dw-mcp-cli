"""Config sub-commands for the fabric-dw CLI.

Mirrors the ``az configure --defaults`` pattern so users don't have to repeat
workspace / warehouse on every command.

Commands
--------
config show                          — print current defaults (JSON or table)
config set workspace                 — persist a workspace default
config set warehouse                 — persist a warehouse default
config set max-429-retries           — persist the max consecutive 429 retry count
config set retry-deadline            — persist the combined 429+5xx wall-clock deadline
config set sql-retry-deadline        — persist the SQL/TDS connect+execute retry budget
config set sql-retry-executes        — persist whether fetch="none" statements are retried
config set sql-pool                  — persist whether SQL connection pooling is enabled
config set auth-mode                 — persist the MCP server credential mode
config set telemetry disabled        — opt in/out of telemetry via config
config set logging level             — set the MCP server log level
config set mcp workspace-allowlist   — set the MCP workspace allowlist (comma-separated)
config unset workspace               — clear the workspace default
config unset warehouse               — clear the warehouse default
config unset max-429-retries         — clear the max consecutive 429 retry count
config unset retry-deadline          — clear the HTTP deadline default
config unset sql-retry-deadline      — clear the SQL retry deadline default
config unset sql-retry-executes      — clear the SQL execute-retry flag
config unset sql-pool                — clear the SQL pool flag
config unset auth-mode               — clear the credential mode (revert to built-in default)
config unset telemetry disabled      — clear the telemetry opt-out (revert to default-on)
config unset logging level           — clear the logging level (revert to built-in INFO)
config unset mcp workspace-allowlist — clear the MCP workspace allowlist (no restriction)
config clear                         — wipe the entire config file
"""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.config import (
    VALID_AUTH_MODES,
    VALID_LOG_LEVELS,
    clear_config,
    set_config,
    set_default,
)


@click.group("config")
def config_group() -> None:
    """Manage fabric-dw CLI configuration defaults."""


# ---------------------------------------------------------------------------
# config show
# ---------------------------------------------------------------------------


@config_group.command("show")
@click.pass_obj
def show_cmd(ctx: CliContext) -> None:
    """Show the current configuration defaults."""
    cfg = ctx.config
    data = {
        "defaults": {
            "workspace": cfg.defaults.workspace,
            "warehouse": cfg.defaults.warehouse,
            "max_429_retries": cfg.defaults.max_429_retries,
            "retry_deadline_s": cfg.defaults.retry_deadline_s,
            "sql_retry_deadline_s": cfg.defaults.sql_retry_deadline_s,
            "sql_retry_executes": cfg.defaults.sql_retry_executes,
            "sql_pool": cfg.defaults.sql_pool,
            "auth_mode": cfg.defaults.auth_mode,
        },
        "telemetry": {
            "disabled": cfg.telemetry.disabled,
        },
        "mcp": {
            "workspace_allowlist": cfg.mcp.workspace_allowlist,
        },
        "logging": {
            "level": cfg.logging.level,
        },
    }
    render(data, json_output=ctx.json_output)


# ---------------------------------------------------------------------------
# config set  (sub-group with workspace / warehouse sub-commands)
# ---------------------------------------------------------------------------


@config_group.group("set")
def set_group() -> None:
    """Set a configuration default."""


@set_group.command("workspace")
@click.argument("value")
def set_workspace_cmd(value: str) -> None:
    """Set the default WORKSPACE (name or GUID)."""
    set_default("workspace", value)
    click.echo(f"Default workspace set to {value!r}.")


@set_group.command("warehouse")
@click.argument("value")
def set_warehouse_cmd(value: str) -> None:
    """Set the default WAREHOUSE / SQL Analytics Endpoint (name or GUID)."""
    set_default("warehouse", value)
    click.echo(f"Default warehouse set to {value!r}.")


@set_group.command("max-429-retries")
@click.argument("value", type=click.IntRange(min=1))
def set_max_429_retries_cmd(value: int) -> None:
    """Set the maximum consecutive 429 responses before raising RateLimitedError."""
    set_default("max_429_retries", str(value))
    click.echo(f"Default max_429_retries set to {value}.")


@set_group.command("retry-deadline")
@click.argument("value", type=click.IntRange(min=1))
def set_retry_deadline_cmd(value: int) -> None:
    """Set the combined 429+5xx retry wall-clock deadline in seconds."""
    set_default("retry_deadline_s", str(value))
    click.echo(f"Default retry_deadline_s set to {value}.")


@set_group.command("sql-retry-deadline")
@click.argument("value", type=click.IntRange(min=1))
def set_sql_retry_deadline_cmd(value: int) -> None:
    """Set the SQL/TDS connect+execute retry wall-clock budget in seconds."""
    set_default("sql_retry_deadline_s", str(value))
    click.echo(f"Default sql_retry_deadline_s set to {value}.")


@set_group.command("sql-retry-executes")
@click.argument("value", type=click.Choice(["true", "false"], case_sensitive=False))
def set_sql_retry_executes_cmd(value: str) -> None:
    """Enable or disable execute-phase retry for fetch=none (non-idempotent) statements.

    WARNING: setting this to true means a transient error on a non-idempotent
    statement (INSERT, UPDATE, DELETE, DDL) may trigger a retry and cause the
    statement to execute more than once.  Only enable when all such statements
    are idempotent.
    """
    set_default("sql_retry_executes", value.lower())
    click.echo(f"Default sql_retry_executes set to {value.lower()}.")


@set_group.command("sql-pool")
@click.argument("value", type=click.Choice(["true", "false"], case_sensitive=False))
def set_sql_pool_cmd(value: str) -> None:
    """Enable or disable SQL connection pooling.

    When set to false, every query opens a fresh TDS connection and closes it
    immediately after use.  Disable pooling only when diagnosing connection
    issues or when running in an environment where persistent connections are
    not supported.
    """
    set_default("sql_pool", value.lower())
    click.echo(f"Default sql_pool set to {value.lower()}.")


_AUTH_MODE_CHOICES = click.Choice(sorted(VALID_AUTH_MODES), case_sensitive=False)


@set_group.command("auth-mode")
@click.argument("value", type=_AUTH_MODE_CHOICES)
def set_auth_mode_cmd(value: str) -> None:
    """Set the MCP server credential mode (default, interactive, or sp).

    This persists the credential mode used by the MCP server.  The
    ``FABRIC_AUTH`` environment variable takes precedence over this setting
    when non-empty.

    Valid modes:

    \b
    default      DefaultAzureCredential chain (Azure CLI, Managed Identity, etc.)
    interactive  Interactive browser sign-in
    sp           Service principal (requires AZURE_TENANT_ID, AZURE_CLIENT_ID,
                 AZURE_CLIENT_SECRET)
    """
    normalised = value.strip().lower()
    set_default("auth_mode", normalised)
    click.echo(f"Default auth_mode set to {normalised!r}.")


@set_group.group("telemetry")
def set_telemetry_group() -> None:
    """Set a telemetry configuration value."""


@set_telemetry_group.command("disabled")
@click.argument("value", type=click.Choice(["true", "false"], case_sensitive=False))
def set_telemetry_disabled_cmd(value: str) -> None:
    """Opt in or out of telemetry via the config file.

    Pass ``true`` to disable telemetry (opt out); ``false`` to re-enable it.
    Setting this to ``false`` does NOT override the env-var opt-out
    (``FABRIC_DW_TELEMETRY_OPT_OUT`` / ``DO_NOT_TRACK`` still take precedence).
    """
    set_config("telemetry", "disabled", value.lower())
    click.echo(f"Telemetry disabled set to {value.lower()}.")


@set_group.group("logging")
def set_logging_group() -> None:
    """Set logging configuration."""


_LOG_LEVEL_CHOICES = click.Choice(sorted(VALID_LOG_LEVELS), case_sensitive=False)


@set_logging_group.command("level")
@click.argument("value", type=_LOG_LEVEL_CHOICES)
def set_logging_level_cmd(value: str) -> None:
    """Set the MCP server log level (DEBUG, INFO, WARNING, ERROR, or CRITICAL)."""
    normalised = value.upper()
    set_config("logging", "level", normalised)
    click.echo(f"Logging level set to {normalised!r}.")


@set_group.group("mcp")
def set_mcp_group() -> None:
    """Set MCP server configuration."""


@set_mcp_group.command("workspace-allowlist")
@click.argument("value")
def set_mcp_workspace_allowlist_cmd(value: str) -> None:
    """Set the MCP workspace allowlist (comma-separated workspace names or GUIDs).

    Restricts the MCP server to the named workspaces only.  The allowlist is
    matched case-insensitively against workspace names and GUIDs.

    This is the config-layer knob: ``FABRIC_MCP_WORKSPACES`` (env var) takes
    precedence when set.

    To remove all workspace restrictions, use the explicit unset command rather
    than passing an empty string:

        fdw config unset mcp workspace-allowlist

    Example::

        fdw config set mcp workspace-allowlist "Sales WS,Finance WS"
    """
    # Reject empty or whitespace-only input explicitly so the operator gets a
    # clear error instead of silently clearing the allowlist via a typo.
    stripped = value.strip()
    if not stripped or not any(e.strip() for e in stripped.split(",")):
        raise click.UsageError(
            "VALUE must not be empty. "
            "To remove all workspace restrictions use: "
            "fdw config unset mcp workspace-allowlist"
        )
    set_config("mcp", "workspace_allowlist", value)
    click.echo(f"MCP workspace allowlist set to {value!r}.")


# ---------------------------------------------------------------------------
# config unset  (sub-group with workspace / warehouse sub-commands)
# ---------------------------------------------------------------------------


@config_group.group("unset")
def unset_group() -> None:
    """Clear a configuration default."""


@unset_group.command("workspace")
def unset_workspace_cmd() -> None:
    """Clear the default workspace."""
    set_default("workspace", None)
    click.echo("Default workspace cleared.")


@unset_group.command("warehouse")
def unset_warehouse_cmd() -> None:
    """Clear the default warehouse."""
    set_default("warehouse", None)
    click.echo("Default warehouse cleared.")


@unset_group.command("max-429-retries")
def unset_max_429_retries_cmd() -> None:
    """Clear the max_429_retries default (revert to built-in 10)."""
    set_default("max_429_retries", None)
    click.echo("Default max_429_retries cleared.")


@unset_group.command("retry-deadline")
def unset_retry_deadline_cmd() -> None:
    """Clear the retry_deadline_s default (revert to built-in 300)."""
    set_default("retry_deadline_s", None)
    click.echo("Default retry_deadline_s cleared.")


@unset_group.command("sql-retry-deadline")
def unset_sql_retry_deadline_cmd() -> None:
    """Clear the sql_retry_deadline_s default (revert to built-in 120)."""
    set_default("sql_retry_deadline_s", None)
    click.echo("Default sql_retry_deadline_s cleared.")


@unset_group.command("sql-retry-executes")
def unset_sql_retry_executes_cmd() -> None:
    """Clear the sql_retry_executes default (revert to built-in false)."""
    set_default("sql_retry_executes", None)
    click.echo("Default sql_retry_executes cleared.")


@unset_group.command("sql-pool")
def unset_sql_pool_cmd() -> None:
    """Clear the sql_pool default (revert to built-in true)."""
    set_default("sql_pool", None)
    click.echo("Default sql_pool cleared.")


@unset_group.command("auth-mode")
def unset_auth_mode_cmd() -> None:
    """Clear the auth_mode default (revert to built-in default credential mode)."""
    set_default("auth_mode", None)
    click.echo("Default auth_mode cleared.")


@unset_group.group("telemetry")
def unset_telemetry_group() -> None:
    """Clear a telemetry configuration value."""


@unset_telemetry_group.command("disabled")
def unset_telemetry_disabled_cmd() -> None:
    """Clear the telemetry opt-out from the config file (revert to default-on).

    Note: env-var opt-outs (``FABRIC_DW_TELEMETRY_OPT_OUT`` / ``DO_NOT_TRACK``)
    still take precedence over this setting.
    """
    set_config("telemetry", "disabled", None)
    click.echo("Telemetry disabled cleared.")


@unset_group.group("logging")
def unset_logging_group() -> None:
    """Clear logging configuration."""


@unset_logging_group.command("level")
def unset_logging_level_cmd() -> None:
    """Clear the logging level (revert to built-in INFO)."""
    set_config("logging", "level", None)
    click.echo("Logging level cleared.")


@unset_group.group("mcp")
def unset_mcp_group() -> None:
    """Clear MCP server configuration."""


@unset_mcp_group.command("workspace-allowlist")
def unset_mcp_workspace_allowlist_cmd() -> None:
    """Clear the MCP workspace allowlist (revert to no restriction — all workspaces allowed)."""
    set_config("mcp", "workspace_allowlist", None)
    click.echo("MCP workspace allowlist cleared.")


# ---------------------------------------------------------------------------
# config clear
# ---------------------------------------------------------------------------


@config_group.command("clear")
@click.pass_obj
def clear_cmd(ctx: CliContext) -> None:
    """Wipe all configuration defaults."""
    confirmed = confirm("Clear all fabric-dw configuration defaults?", yes=ctx.yes)
    if not confirmed:
        click.echo("Aborted.")
        return
    clear_config()
    click.echo("Configuration cleared.")
