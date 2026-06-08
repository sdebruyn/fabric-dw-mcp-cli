"""Click group definition for the fabric-dw CLI."""

from __future__ import annotations

import logging

import click

from fabric_dw.auth import CredentialMode
from fabric_dw.cli._context import CliContext
from fabric_dw.cli.commands.audit import audit_group
from fabric_dw.cli.commands.cache import cache_group
from fabric_dw.cli.commands.completion import completion_group
from fabric_dw.cli.commands.config import config_group
from fabric_dw.cli.commands.queries import queries_group
from fabric_dw.cli.commands.query_insights import query_insights_group
from fabric_dw.cli.commands.restore_points import restore_points_group
from fabric_dw.cli.commands.snapshots import snapshots_group
from fabric_dw.cli.commands.sql_endpoints import sql_endpoints_group
from fabric_dw.cli.commands.sql_pools import sql_pools_group
from fabric_dw.cli.commands.tables import tables_group
from fabric_dw.cli.commands.views import views_group
from fabric_dw.cli.commands.warehouses import warehouses_group
from fabric_dw.cli.commands.workspaces import workspaces_group
from fabric_dw.logging import setup_logging


@click.group(invoke_without_command=False)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of Rich tables.",
)
@click.option(
    "--auth",
    "auth_mode",
    type=click.Choice([m.value for m in CredentialMode], case_sensitive=False),
    default=CredentialMode.DEFAULT.value,
    show_default=True,
    help="Authentication mode.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    is_flag=True,
    default=False,
    help="Skip confirmation prompts.",
)
@click.option(
    "--verbose",
    "-v",
    "verbose",
    is_flag=True,
    default=False,
    help="Enable debug logging.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    json_output: bool,
    auth_mode: str,
    yes: bool,
    verbose: bool,
) -> None:
    """Microsoft Fabric Data Warehouse CLI."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    ctx.obj = CliContext(
        json_output=json_output,
        yes=yes,
        auth=CredentialMode(auth_mode),
        verbose=verbose,
    )


cli.add_command(cache_group)
cli.add_command(completion_group)
cli.add_command(config_group)
cli.add_command(workspaces_group)
cli.add_command(warehouses_group)
cli.add_command(sql_endpoints_group)
cli.add_command(audit_group)
cli.add_command(queries_group)
cli.add_command(query_insights_group)
cli.add_command(restore_points_group)
cli.add_command(snapshots_group)
cli.add_command(sql_pools_group)
cli.add_command(tables_group)
cli.add_command(views_group)
