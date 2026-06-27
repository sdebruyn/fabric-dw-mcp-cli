"""SQL Pools sub-commands for the fabric-dw CLI.

.. warning::
   SQL Pools is a **beta / preview** feature.  The underlying API may change
   before general availability.
"""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    AGO_OPTION,
    LIMIT_OPTION,
    SINCE_OPTION,
    UNTIL_OPTION,
    build_http_client,
    build_sql_target,
    coro,
    parse_iso_optional,
    resolve_since,
    resolve_warehouse_arg,
    resolve_workspace,
    resolve_workspace_id,
)
from fabric_dw.exceptions import (
    AlreadyExistsError,
    FabricError,
    NotFoundError,
    PermissionDeniedError,
)
from fabric_dw.models import (
    DEFAULT_NON_SELECT_POOL_NAME,
    DEFAULT_POOL_MAX_RESOURCE_PERCENTAGE,
    DEFAULT_SELECT_POOL_NAME,
    SqlPool,
    SqlPoolClassifier,
)
from fabric_dw.services import query_insights as _qi_svc
from fabric_dw.services import sql_pools as _svc


def _permission_hint(exc: PermissionDeniedError) -> click.ClickException:
    return click.ClickException(f"{exc}  (Hint: the caller must have the workspace admin role.)")


def _default_pool_rows() -> list[dict[str, object]]:
    """Return descriptor rows for the default (autonomous) workload-management pools.

    Used when a workspace has no custom SQL pools.  See the module-level
    constants in :mod:`fabric_dw.models` for the documented values and the
    Microsoft Learn source URLs.
    """
    return [
        {
            "name": DEFAULT_SELECT_POOL_NAME,
            "maxResourcePercentage": DEFAULT_POOL_MAX_RESOURCE_PERCENTAGE,
            "isDefault": True,
            "description": "Handles SELECT (read/analytics) queries.",
        },
        {
            "name": DEFAULT_NON_SELECT_POOL_NAME,
            "maxResourcePercentage": DEFAULT_POOL_MAX_RESOURCE_PERCENTAGE,
            "isDefault": True,
            "description": "Handles non-SELECT (DML/DDL/ETL/ingestion) statements.",
        },
    ]


def _print_default_workload_note() -> None:
    """Print the human-readable note shown when no custom SQL pools exist."""
    click.echo(
        "No custom SQL pools are defined for this workspace. "
        "Fabric Data Warehouse is using the default (autonomous) workload "
        "management, which splits compute 50/50 into two isolated pools:"
    )
    for row in _default_pool_rows():
        click.echo(
            f"  - {row['name']} ({row['maxResourcePercentage']}%) (default) - {row['description']}"
        )


@click.group("sql-pools")
def sql_pools_group() -> None:
    """Manage workspace SQL Pools configuration (beta API)."""


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@sql_pools_group.command("status")
@click.pass_obj
@coro
async def status_cmd(ctx: CliContext) -> None:
    """Show whether custom SQL Pools are enabled for the workspace."""
    ws = resolve_workspace(ctx)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            config = await _svc.get_configuration(http, ws_id)
            render(
                {"customSQLPoolsEnabled": config.custom_sql_pools_enabled},
                json_output=ctx.json_output,
            )
    except PermissionDeniedError as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@sql_pools_group.command("list")
@click.pass_obj
@coro
async def list_cmd(ctx: CliContext) -> None:
    """List all SQL pools in the workspace.

    When no custom SQL pools are defined, Fabric Data Warehouse uses the default
    (autonomous) workload management: compute is split 50/50 into a ``SELECT``
    pool and a ``NON-SELECT`` pool.  This command reports those default pools
    instead of showing an empty list.
    """
    ws = resolve_workspace(ctx)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            config = await _svc.get_configuration(http, ws_id)
    except PermissionDeniedError as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc

    pools = [p.model_dump(by_alias=True, mode="json") for p in config.custom_sql_pools]
    # No custom pools => the default (autonomous) workload management is active.
    if not pools:
        if ctx.json_output:
            # Stay honest: do not fabricate custom pools. Report the real (empty)
            # custom_sql_pools alongside explicit default-workload indicators.
            render(
                {
                    "customSQLPools": pools,
                    "default_workload_active": True,
                    "default_pools": _default_pool_rows(),
                },
                json_output=True,
            )
        else:
            _print_default_workload_note()
        return

    render(pools, json_output=ctx.json_output, prune_null_columns=True)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@sql_pools_group.command("show")
@click.option("--name", required=True, help="Name of the pool to show.")
@click.pass_obj
@coro
async def show_cmd(ctx: CliContext, name: str) -> None:
    """Show details for a single SQL pool in the workspace."""
    ws = resolve_workspace(ctx)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            config = await _svc.get_configuration(http, ws_id)
    except PermissionDeniedError as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
    pool = next((p for p in config.custom_sql_pools if p.name == name), None)
    if pool is None:
        raise click.ClickException(f"pool {name!r} not found")
    render(pool.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@sql_pools_group.command("create")
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
@coro
async def create_cmd(
    ctx: CliContext,
    name: str,
    max_percent: int,
    is_default: bool,
    optimize_for_reads: bool,
    classifier_type: str | None,
    classifier_values: tuple[str, ...],
) -> None:
    """Add a new SQL pool to the workspace."""
    ws = resolve_workspace(ctx)

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
    except AlreadyExistsError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(f"Invalid pool configuration: {exc}") from exc
    except PermissionDeniedError as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@sql_pools_group.command("update")
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
@coro
async def update_cmd(
    ctx: CliContext,
    name: str,
    max_percent: int | None,
    is_default: bool | None,
    optimize_for_reads: bool | None,
    classifier_type: str | None,
    classifier_values: tuple[str, ...],
) -> None:
    """Update an existing SQL pool in the workspace.

    Only the flags you provide are changed; all other fields are preserved.
    """
    ws = resolve_workspace(ctx)
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
    except NotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(f"Invalid pool configuration: {exc}") from exc
    except PermissionDeniedError as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@sql_pools_group.command("delete")
@click.option("--name", required=True, help="Name of the pool to delete.")
@click.pass_obj
@coro
async def delete_cmd(ctx: CliContext, name: str) -> None:
    """Remove an SQL pool from the workspace."""
    ws = resolve_workspace(ctx)
    if not confirm(f"Delete pool {name!r}?", yes=ctx.yes):
        click.echo("Aborted.")
        return
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            result = await _svc.delete_pool(http, ws_id, name)
            render(result.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except NotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except PermissionDeniedError as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


@sql_pools_group.command("enable")
@click.pass_obj
@coro
async def enable_cmd(ctx: CliContext) -> None:
    """Enable custom SQL Pools for the workspace (preserves pool configuration)."""
    ws = resolve_workspace(ctx)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            result = await _svc.enable(http, ws_id)
            render(
                result.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
            )
    except PermissionDeniedError as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_pools_group.command("disable")
@click.pass_obj
@coro
async def disable_cmd(ctx: CliContext) -> None:
    """Disable custom SQL Pools for the workspace (preserves pool configuration).

    Re-enabling with 'sql-pools enable' restores the previously saved configuration.
    """
    ws = resolve_workspace(ctx)
    try:
        async with build_http_client(ctx) as http:
            ws_id = await resolve_workspace_id(http, ws)
            result = await _svc.disable(http, ws_id)
            render(
                result.model_dump(by_alias=True, mode="json"),
                json_output=ctx.json_output,
            )
    except PermissionDeniedError as exc:
        raise _permission_hint(exc) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# insights
# ---------------------------------------------------------------------------


@sql_pools_group.command("insights")
@click.argument("warehouse", required=False, default=None)
@LIMIT_OPTION
@SINCE_OPTION
@UNTIL_OPTION
@AGO_OPTION
@click.pass_obj
@coro
async def insights_cmd(
    ctx: CliContext,
    warehouse: str | None,
    limit: int,
    since: str | None,
    until: str | None,
    ago: str | None,
) -> None:
    """List SQL pool insights from queryinsights.sql_pool_insights."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, warehouse)
    since_dt = resolve_since(since, ago)
    until_dt = parse_iso_optional(until, "--until")
    try:
        async with build_http_client(ctx) as http:
            target, _ = await build_sql_target(http, ws, wh)
            items = await _qi_svc.list_sql_pool_insights(
                target, limit=limit, since=since_dt, until=until_dt, mode=ctx.auth
            )
            render(
                [q.model_dump(by_alias=True, mode="json") for q in items],
                json_output=ctx.json_output,
                table_title="SQL Pool Insights",
                prune_null_columns=True,
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
