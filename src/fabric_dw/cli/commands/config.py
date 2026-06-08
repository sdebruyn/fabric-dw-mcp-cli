"""Config sub-commands for the fabric-dw CLI.

Mirrors the ``az configure --defaults`` pattern so users don't have to repeat
workspace / warehouse on every command.

Commands
--------
config show           — print current defaults (JSON or table)
config set workspace  — persist a workspace default
config set warehouse  — persist a warehouse default
config unset workspace — clear the workspace default
config unset warehouse — clear the warehouse default
config clear          — wipe the entire config file
"""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.config import Defaults, UserConfig, clear_config, load_config, save_config


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
        }
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
    cfg = load_config()
    new_cfg = UserConfig(defaults=Defaults(workspace=value, warehouse=cfg.defaults.warehouse))
    save_config(new_cfg)
    click.echo(f"Default workspace set to {value!r}.")


@set_group.command("warehouse")
@click.argument("value")
def set_warehouse_cmd(value: str) -> None:
    """Set the default WAREHOUSE / SQL Analytics Endpoint (name or GUID)."""
    cfg = load_config()
    new_cfg = UserConfig(defaults=Defaults(workspace=cfg.defaults.workspace, warehouse=value))
    save_config(new_cfg)
    click.echo(f"Default warehouse set to {value!r}.")


# ---------------------------------------------------------------------------
# config unset  (sub-group with workspace / warehouse sub-commands)
# ---------------------------------------------------------------------------


@config_group.group("unset")
def unset_group() -> None:
    """Clear a configuration default."""


@unset_group.command("workspace")
def unset_workspace_cmd() -> None:
    """Clear the default workspace."""
    cfg = load_config()
    new_cfg = UserConfig(defaults=Defaults(workspace=None, warehouse=cfg.defaults.warehouse))
    save_config(new_cfg)
    click.echo("Default workspace cleared.")


@unset_group.command("warehouse")
def unset_warehouse_cmd() -> None:
    """Clear the default warehouse."""
    cfg = load_config()
    new_cfg = UserConfig(defaults=Defaults(workspace=cfg.defaults.workspace, warehouse=None))
    save_config(new_cfg)
    click.echo("Default warehouse cleared.")


# ---------------------------------------------------------------------------
# config clear
# ---------------------------------------------------------------------------


@config_group.command("clear")
@click.pass_obj
def clear_cmd(ctx: CliContext) -> None:
    """Wipe all configuration defaults."""
    confirmed = confirm("Clear all fabric-dw configuration defaults?", yes=ctx.yes)
    if not confirmed:
        raise click.Abort()
    clear_config()
    click.echo("Configuration cleared.")
