"""dbt sub-commands for the fabric-dw CLI.

Commands
--------
- ``dbt init <item> <folder>`` — scaffold a new dbt-fabric project.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from fabric_dw.auth import CredentialMode
from fabric_dw.cli._context import CliContext
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    coro,
    resolve_warehouse_arg,
    resolve_workspace,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services.dbt_scaffold import (
    DbtAuthMode,
    DbtScaffoldConfig,
    ProfilesDir,
    auth_mode_to_dbt,
    sanitize_project_name,
    scaffold,
)


@click.group("dbt")
def dbt_group() -> None:
    """Scaffold and manage dbt projects for Fabric Data Warehouses."""


@dbt_group.command("init")
@click.argument("item", required=False, default=None)
@click.argument("folder", required=True)
@click.option(
    "--project-name",
    "project_name",
    default=None,
    help="dbt project name (default: sanitized folder name).",
)
@click.option(
    "--profile-name",
    "profile_name",
    default=None,
    help="dbt profile name (default: same as project name).",
)
@click.option(
    "--schema",
    "schema",
    default="dbo",
    show_default=True,
    help="Default target schema.",
)
@click.option(
    "--target",
    "target",
    default="dev",
    show_default=True,
    help="dbt target name.",
)
@click.option(
    "--threads",
    "threads",
    default=4,
    show_default=True,
    type=click.IntRange(1, 64),
    help="Number of dbt threads.",
)
@click.option(
    "--auth",
    "dbt_auth_override",
    type=click.Choice(
        [DbtAuthMode.AUTO, DbtAuthMode.CLI, DbtAuthMode.SERVICE_PRINCIPAL, "interactive", "sp"],
        case_sensitive=False,
    ),
    default=None,
    help=(
        "dbt-fabric authentication mode. "
        "Defaults to the CLI's current --auth mode "
        "(default→auto, interactive→CLI, sp→ServicePrincipal). "
        "Pass 'auto', 'CLI', or 'ServicePrincipal' to override."
    ),
)
@click.option(
    "--profiles-dir",
    "profiles_dir",
    type=click.Choice([ProfilesDir.PROJECT, ProfilesDir.HOME], case_sensitive=False),
    default=ProfilesDir.PROJECT,
    show_default=True,
    help=(
        "'project' writes profiles.yml in the project folder; "
        "'home' merges into ~/.dbt/profiles.yml (backs up existing)."
    ),
)
@click.option(
    "--with-sources",
    "with_sources",
    is_flag=True,
    default=False,
    help="Generate models/staging/_sources.yml from the warehouse's actual schemas and tables.",
)
@click.option(
    "--force",
    "force",
    is_flag=True,
    default=False,
    help="Scaffold into a non-empty folder without refusing.",
)
@click.pass_obj
@coro
async def init_cmd(
    ctx: CliContext,
    item: str | None,
    folder: str,
    project_name: str | None,
    profile_name: str | None,
    schema: str,
    target: str,
    threads: int,
    dbt_auth_override: str | None,
    profiles_dir: str,
    with_sources: bool,
    force: bool,
) -> None:
    """Scaffold a new dbt-fabric project in FOLDER linked to ITEM.

    Writes dbt_project.yml, profiles.yml (or merges into ~/.dbt/profiles.yml),
    requirements.txt, .gitignore, standard dbt folders, a sample model, and
    optionally a _sources.yml derived from the warehouse's actual schemas/tables.

    If git is on PATH and FOLDER has no .git directory, ``git init`` is run.
    """
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)

    target_folder = Path(folder)

    # Derive project name from folder name if not supplied.
    raw_name = project_name or target_folder.name
    try:
        safe_project_name = sanitize_project_name(raw_name)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    # Resolve dbt auth from the override or fall back to the CLI auth mode.
    dbt_auth = _resolve_dbt_auth(dbt_auth_override, ctx.auth)

    try:
        async with build_http_client(ctx) as http:
            target_obj, _entry = await build_sql_target(http, ws, wh)

            # When --with-sources, fetch schemas, tables, and columns (bulk) before scaffolding.
            schemas = []
            tables = []
            columns: dict[tuple[str, str], list[dict[str, object]]] = {}
            if with_sources:
                from fabric_dw.services import schemas as schemas_svc  # noqa: PLC0415
                from fabric_dw.services import tables as tables_svc  # noqa: PLC0415
                from fabric_dw.services.columns import get_columns_for_schemas  # noqa: PLC0415

                schemas, tables, columns = await asyncio.gather(
                    schemas_svc.list_schemas(target_obj, mode=ctx.auth),
                    tables_svc.list_tables(target_obj, mode=ctx.auth),
                    get_columns_for_schemas(target_obj, mode=ctx.auth),
                )

            cfg = DbtScaffoldConfig(
                host=target_obj.connection_string,
                database=target_obj.database,
                project_name=safe_project_name,
                profile_name=profile_name or safe_project_name,
                schema=schema,
                target=target,
                threads=threads,
                dbt_auth=dbt_auth,
                profiles_dir=profiles_dir,
                with_sources=with_sources,
                schemas=schemas,
                tables=tables,
                columns=columns,
            )

            try:
                written = scaffold(cfg, target_folder, force=force)
            except FileExistsError as exc:
                raise click.ClickException(str(exc)) from exc

    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Scaffolded dbt project {safe_project_name!r} in {target_folder.resolve()}")
    click.echo(f"  Host:     {target_obj.connection_string}")
    click.echo(f"  Database: {target_obj.database}")
    click.echo(f"  Auth:     {dbt_auth}")
    click.echo(f"  Files written: {len(written)}")

    if profiles_dir == ProfilesDir.HOME:
        click.echo(
            "  profiles.yml merged into ~/.dbt/profiles.yml (backup created if a file existed)."
        )
    else:
        click.echo("  Run dbt from the project folder (or set DBT_PROFILES_DIR).")


def _resolve_dbt_auth(override: str | None, credential_mode: CredentialMode) -> str:
    """Resolve the final dbt-fabric authentication string.

    Args:
        override: Explicit --auth value from the command line, or ``None``.
        credential_mode: The CLI's global :class:`~fabric_dw.auth.CredentialMode`.

    Returns:
        A dbt-fabric authentication string.
    """
    if override is None:
        return auth_mode_to_dbt(credential_mode)

    # Normalize aliases accepted by --auth.
    alias_map = {
        "interactive": DbtAuthMode.CLI,
        "sp": DbtAuthMode.SERVICE_PRINCIPAL,
        # Pass-through for canonical names.
        DbtAuthMode.AUTO: DbtAuthMode.AUTO,
        DbtAuthMode.CLI: DbtAuthMode.CLI,
        DbtAuthMode.SERVICE_PRINCIPAL: DbtAuthMode.SERVICE_PRINCIPAL,
    }
    return alias_map.get(override, DbtAuthMode.AUTO)
