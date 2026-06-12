"""SQL Pools sub-commands for the fabric-dw CLI.

.. warning::
   SQL Pools is a **beta / preview** feature.  The underlying API may change
   before general availability.
"""

from __future__ import annotations

import logging

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    _coro,
    build_http_client,
    resolve_workspace_arg,
    resolve_workspace_id,
)
from fabric_dw.exceptions import AlreadyExists, FabricError, NotFound, PermissionDenied
from fabric_dw.models import SqlPool, SqlPoolClassifier
from fabric_dw.services import sql_pools as _svc

_log = logging.getLogger(__name__)


def _permission_hint(exc: PermissionDenied) -> click.ClickException:
    return click.ClickException(f"{exc}  (Hint: the caller must have the workspace admin role.)")


@click.group("sql-pools")
def sql_pools_group() -> None:
    """Manage workspace SQL Pools configuration (beta API)."""


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


@sql_pools_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@_coro
async def get_cmd(ctx: CliContext, workspace: str | None) -> None:
    """Fetch the SQL Pools configuration for WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            config = await _svc.get_configuration(http, ws_id)
            render(
                config.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
            )
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@sql_pools_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@_coro
async def list_cmd(ctx: CliContext, workspace: str | None) -> None:
    """List all SQL pools in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            config = await _svc.get_configuration(http, ws_id)
            pools = [p.model_dump(by_alias=True, mode="json") for p in config.custom_sql_pools]
            render(pools, json_output=ctx.json_output)
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@sql_pools_group.command("show")
@click.argument("workspace", required=False, default=None)
@click.option("--name", required=True, help="Name of the pool to show.")
@click.pass_obj
@_coro
async def show_cmd(ctx: CliContext, workspace: str | None, name: str) -> None:
    """Show details for a single SQL pool in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            config = await _svc.get_configuration(http, ws_id)
            pool = next((p for p in config.custom_sql_pools if p.name == name), None)
            if pool is None:
                raise click.ClickException(f"pool {name!r} not found")  # noqa: TRY003, TRY301
            render(pool.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except click.ClickException:
        raise
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@sql_pools_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.option("--name", required=True, help="Pool name.")
@click.option(
    "--max-percent",
    "max_percent",
    required=True,
    type=int,
    help="Max resource percentage (1-100).",
)
@click.option(
    "--default/--no-default",
    "is_default",
    default=False,
    show_default=True,
    help="Mark pool as default.",
)
@click.option(
    "--optimize-for-reads/--no-optimize-for-reads",
    "optimize_for_reads",
    default=True,
    show_default=True,
    help="Enable read optimisation.",
)
@click.option(
    "--classifier-type",
    "classifier_type",
    default=None,
    help="Classifier type (e.g. 'Application Name').",
)
@click.option(
    "--classifier-value",
    "classifier_values",
    multiple=True,
    help="Classifier value(s). Repeat for multiple values.",
)
@click.pass_obj
@_coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    name: str,
    max_percent: int,
    is_default: bool,
    optimize_for_reads: bool,
    classifier_type: str | None,
    classifier_values: tuple[str, ...],
) -> None:
    """Add a new SQL pool to WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)

    classifier: SqlPoolClassifier | None = None
    if classifier_type is not None:
        classifier = SqlPoolClassifier.model_validate(
            {"type": classifier_type, "value": list(classifier_values)}
        )

    pool = SqlPool.model_validate(
        {
            "name": name,
            "isDefault": is_default,
            "maxResourcePercentage": max_percent,
            "optimizeForReads": optimize_for_reads,
            "classifier": classifier.model_dump(by_alias=True, mode="json") if classifier else None,
        }
    )

    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            result = await _svc.create_pool(http, ws_id, pool)
            render(result.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except AlreadyExists as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(f"Invalid pool configuration: {exc}") from exc  # noqa: TRY003
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@sql_pools_group.command("update")
@click.argument("workspace", required=False, default=None)
@click.option("--name", required=True, help="Name of the pool to update.")
@click.option(
    "--max-percent",
    "max_percent",
    default=None,
    type=int,
    help="New max resource percentage (1-100).",
)
@click.option(
    "--default/--no-default",
    "is_default",
    default=None,
    help="Set or clear the default flag.",
)
@click.option(
    "--optimize-for-reads/--no-optimize-for-reads",
    "optimize_for_reads",
    default=None,
    help="Enable or disable read optimisation.",
)
@click.option(
    "--classifier-type",
    "classifier_type",
    default=None,
    help="New classifier type.",
)
@click.option(
    "--classifier-value",
    "classifier_values",
    multiple=True,
    help="New classifier value(s). Repeat for multiple values. Replaces all existing values.",
)
@click.pass_obj
@_coro
async def update_cmd(
    ctx: CliContext,
    workspace: str | None,
    name: str,
    max_percent: int | None,
    is_default: bool | None,
    optimize_for_reads: bool | None,
    classifier_type: str | None,
    classifier_values: tuple[str, ...],
) -> None:
    """Update an existing SQL pool in WORKSPACE.

    Only the flags you provide are changed; all other fields are preserved.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    cv: list[str] | None = list(classifier_values) if classifier_values else None
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            result = await _svc.update_pool(
                http,
                ws_id,
                name,
                max_resource_percentage=max_percent,
                is_default=is_default,
                optimize_for_reads=optimize_for_reads,
                classifier_type=classifier_type,
                classifier_values=cv,
            )
            render(result.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except NotFound as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(f"Invalid pool configuration: {exc}") from exc  # noqa: TRY003
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@sql_pools_group.command("delete")
@click.argument("workspace", required=False, default=None)
@click.option("--name", required=True, help="Name of the pool to delete.")
@click.option("--yes", "yes", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.pass_obj
@_coro
async def delete_cmd(ctx: CliContext, workspace: str | None, name: str, yes: bool) -> None:
    """Remove an SQL pool from WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    if not yes and not ctx.yes and not confirm(f"Delete pool {name!r}?", yes=False):
        raise click.Abort()
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            result = await _svc.delete_pool(http, ws_id, name)
            render(result.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except NotFound as exc:
        raise click.ClickException(str(exc)) from exc
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


@sql_pools_group.command("enable")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@_coro
async def enable_cmd(ctx: CliContext, workspace: str | None) -> None:
    """Enable custom SQL Pools for WORKSPACE (preserves pool configuration)."""
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            result = await _svc.enable(http, ws_id)
            render(
                result.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
            )
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_pools_group.command("disable")
@click.argument("workspace", required=False, default=None)
@click.pass_obj
@_coro
async def disable_cmd(ctx: CliContext, workspace: str | None) -> None:
    """Disable custom SQL Pools for WORKSPACE (preserves pool configuration).

    Re-enabling with 'sql-pools enable' restores the previously saved configuration.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            result = await _svc.disable(http, ws_id)
            render(
                result.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
            )
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


@sql_pools_group.command("reset")
@click.argument("workspace", required=False, default=None)
@click.option("--yes", "yes", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.pass_obj
@_coro
async def reset_cmd(ctx: CliContext, workspace: str | None, yes: bool) -> None:
    """Clear all SQL pools for WORKSPACE (preserves enabled/disabled state)."""
    ws = resolve_workspace_arg(ctx, workspace)
    if not yes and not ctx.yes and not confirm("Clear all SQL pools?", yes=False):
        raise click.Abort()
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            result = await _svc.reset_pools(http, ws_id)
            if result is None:
                click.echo("Workspace has no SQL pools configuration (never provisioned).")
            else:
                render(result.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except PermissionDenied as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
