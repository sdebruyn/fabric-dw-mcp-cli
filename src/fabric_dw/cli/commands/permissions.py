"""Permissions sub-commands for the fabric-dw CLI.

Two distinct permission planes are exposed under the ``permissions`` top-level group:

``permissions item``
    Fabric item-level permissions (REST admin API).  Moved from ``warehouses permissions``
    and ``sql-endpoints permissions``.

``permissions sql``
    T-SQL granular in-database permissions.  Reads from ``sys.database_permissions`` /
    ``sys.database_principals`` and issues ``GRANT`` / ``DENY`` / ``REVOKE`` statements.
"""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render, render_permissions_table
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    coro,
    resolve_item,
    resolve_warehouse_arg,
    resolve_workspace,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import permissions as _permissions_svc

# ---------------------------------------------------------------------------
# Top-level group
# ---------------------------------------------------------------------------


@click.group("permissions")
def permissions_group() -> None:
    """Manage Fabric item-level and T-SQL in-database permissions."""


# ---------------------------------------------------------------------------
# permissions item sub-group
# ---------------------------------------------------------------------------


@permissions_group.group("item")
def item_group() -> None:
    """Fabric item-level permissions (REST admin API)."""


@item_group.command("list")
@click.argument("item", required=False, default=None)
@click.pass_obj
@coro
async def item_list_cmd(ctx: CliContext, item: str | None) -> None:
    """List principals with access to ITEM (warehouse or SQL endpoint, name or GUID).

    Accepts both Data Warehouses and SQL Analytics Endpoints.
    Requires Fabric Administrator role.
    See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, it)
            items = await _permissions_svc.list_item_access(http, ws_id, entry.id)
            render_permissions_table(
                items,
                title="Item Permissions",
                json_output=ctx.json_output,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# permissions sql sub-group
# ---------------------------------------------------------------------------


@permissions_group.group("sql")
def sql_group() -> None:
    """T-SQL granular in-database permissions (GRANT / DENY / REVOKE)."""


def _resolve_scope(
    scope_database: bool,
    scope_schema: str | None,
    scope_object: str | None,
) -> tuple[str, str | None, str | None]:
    """Resolve the three scope flags to (scope_class, schema, object_name).

    Enforces mutual exclusivity: at most one scope option may be set.
    Defaults to DATABASE when nothing is given.

    Returns:
        ``(scope_class, schema, object_name)`` where *scope_class* is one of
        ``"DATABASE"``, ``"SCHEMA"``, or ``"OBJECT"``.

    Raises:
        click.UsageError: If more than one scope option is set.
    """
    active = sum(
        [
            bool(scope_database),
            scope_schema is not None,
            scope_object is not None,
        ]
    )
    if active > 1:
        raise click.UsageError(
            "--database, --schema, and --object are mutually exclusive; specify at most one."
        )
    if scope_schema is not None:
        return "SCHEMA", scope_schema, None
    if scope_object is not None:
        return "OBJECT", None, scope_object
    # --database flag or default
    return "DATABASE", None, None


@sql_group.command("list")
@click.argument("item", required=False, default=None)
@click.option("--principal", default=None, metavar="NAME", help="Filter by principal name.")
@click.option("--schema", "filter_schema", default=None, metavar="NAME", help="Filter by schema.")
@click.option(
    "--object",
    "filter_object",
    default=None,
    metavar="SCHEMA.NAME",
    help="Filter by qualified object name (e.g. dbo.sales).",
)
@click.pass_obj
@coro
async def sql_list_cmd(
    ctx: CliContext,
    item: str | None,
    principal: str | None,
    filter_schema: str | None,
    filter_object: str | None,
) -> None:
    """List T-SQL permissions on ITEM (warehouse or SQL endpoint)."""
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            perms = await _permissions_svc.list_sql_permissions(
                target,
                principal=principal,
                schema=filter_schema,
                object_name=filter_object,
                mode=ctx.auth,
            )
            render(
                [p.model_dump(mode="json") for p in perms],
                json_output=ctx.json_output,
                table_title="SQL Permissions",
                prune_null_columns=True,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@sql_group.command("principals")
@click.argument("item", required=False, default=None)
@click.option(
    "--type",
    "principal_type",
    type=click.Choice(["user", "role", "all"], case_sensitive=False),
    default="all",
    show_default=True,
    help="Filter principal type.",
)
@click.pass_obj
@coro
async def sql_principals_cmd(
    ctx: CliContext,
    item: str | None,
    principal_type: str,
) -> None:
    """List database principals on ITEM (warehouse or SQL endpoint)."""
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            principals = await _permissions_svc.list_database_principals(
                target,
                principal_type=principal_type,
                mode=ctx.auth,
            )
            render(
                [p.model_dump(mode="json") for p in principals],
                json_output=ctx.json_output,
                table_title="Database Principals",
                prune_null_columns=True,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@sql_group.command("mine")
@click.argument("item", required=False, default=None)
@click.option(
    "--scope",
    default=None,
    metavar="SCOPE",
    help=("Securable scope: 'database' (default), 'schema:<name>', or 'object:<schema>.<object>'."),
)
@click.pass_obj
@coro
async def sql_mine_cmd(
    ctx: CliContext,
    item: str | None,
    scope: str | None,
) -> None:
    """Show permissions for the current connection on ITEM.

    Use --scope to target a specific securable: 'database' (default),
    'schema:<name>', or 'object:<schema>.<object>'.
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            rows = await _permissions_svc.my_permissions(target, scope=scope, mode=ctx.auth)
            render(
                rows,
                json_output=ctx.json_output,
                table_title="My Permissions",
                prune_null_columns=True,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@sql_group.command("grant")
@click.argument("item", required=False, default=None)
@click.argument("permissions")
@click.option("--to", "principal", required=True, metavar="PRINCIPAL", help="Grantee principal.")
@click.option(
    "--with-grant-option",
    "with_grant_option",
    is_flag=True,
    default=False,
    help="Allow the grantee to grant the permission to others.",
)
@click.option(
    "--database",
    "scope_database",
    is_flag=True,
    default=False,
    help="Target the DATABASE scope (default when no scope option is given).",
)
@click.option(
    "--schema",
    "scope_schema",
    default=None,
    metavar="NAME",
    help="Target a SCHEMA scope (provide schema name).",
)
@click.option(
    "--object",
    "scope_object",
    default=None,
    metavar="SCHEMA.NAME",
    help="Target an OBJECT scope (provide qualified name, e.g. dbo.sales).",
)
@click.pass_obj
@coro
async def sql_grant_cmd(
    ctx: CliContext,
    item: str | None,
    permissions: str,
    principal: str,
    with_grant_option: bool,
    scope_database: bool,
    scope_schema: str | None,
    scope_object: str | None,
) -> None:
    """Grant PERMISSIONS on ITEM to PRINCIPAL.

    PERMISSIONS: comma-separated list (e.g. SELECT,INSERT).
    Use --to to specify the grantee principal (Entra UPN, GUID, or role name).
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    scope_class, schema, object_name = _resolve_scope(scope_database, scope_schema, scope_object)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _permissions_svc.grant_permission(
                target,
                permissions,
                principal,
                scope_class,
                schema=schema,
                object_name=object_name,
                with_grant_option=with_grant_option,
                mode=ctx.auth,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Granted {permissions!r} on {scope_class} to {principal!r}.")


@sql_group.command("deny")
@click.argument("item", required=False, default=None)
@click.argument("permissions")
@click.option("--to", "principal", required=True, metavar="PRINCIPAL", help="Principal to deny.")
@click.option(
    "--database",
    "scope_database",
    is_flag=True,
    default=False,
    help="Target the DATABASE scope (default when no scope option is given).",
)
@click.option(
    "--schema",
    "scope_schema",
    default=None,
    metavar="NAME",
    help="Target a SCHEMA scope (provide schema name).",
)
@click.option(
    "--object",
    "scope_object",
    default=None,
    metavar="SCHEMA.NAME",
    help="Target an OBJECT scope (provide qualified name, e.g. dbo.sales).",
)
@click.pass_obj
@coro
async def sql_deny_cmd(
    ctx: CliContext,
    item: str | None,
    permissions: str,
    principal: str,
    scope_database: bool,
    scope_schema: str | None,
    scope_object: str | None,
) -> None:
    """Deny PERMISSIONS on ITEM to PRINCIPAL.

    PERMISSIONS: comma-separated list (e.g. SELECT).
    Use --to to specify the principal (Entra UPN, GUID, or role name).
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    scope_class, schema, object_name = _resolve_scope(scope_database, scope_schema, scope_object)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _permissions_svc.deny_permission(
                target,
                permissions,
                principal,
                scope_class,
                schema=schema,
                object_name=object_name,
                mode=ctx.auth,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Denied {permissions!r} on {scope_class} to {principal!r}.")


@sql_group.command("revoke")
@click.argument("item", required=False, default=None)
@click.argument("permissions")
@click.option(
    "--from", "principal", required=True, metavar="PRINCIPAL", help="Principal to revoke from."
)
@click.option(
    "--grant-option-only",
    "grant_option_only",
    is_flag=True,
    default=False,
    help="Revoke only the GRANT OPTION, not the base permission.",
)
@click.option(
    "--cascade",
    "cascade",
    is_flag=True,
    default=False,
    help="Cascade the revocation to principals the grantee has granted to.",
)
@click.option(
    "--database",
    "scope_database",
    is_flag=True,
    default=False,
    help="Target the DATABASE scope (default when no scope option is given).",
)
@click.option(
    "--schema",
    "scope_schema",
    default=None,
    metavar="NAME",
    help="Target a SCHEMA scope (provide schema name).",
)
@click.option(
    "--object",
    "scope_object",
    default=None,
    metavar="SCHEMA.NAME",
    help="Target an OBJECT scope (provide qualified name, e.g. dbo.sales).",
)
@click.pass_obj
@coro
async def sql_revoke_cmd(
    ctx: CliContext,
    item: str | None,
    permissions: str,
    principal: str,
    grant_option_only: bool,
    cascade: bool,
    scope_database: bool,
    scope_schema: str | None,
    scope_object: str | None,
) -> None:
    """Revoke PERMISSIONS on ITEM from PRINCIPAL.

    PERMISSIONS: comma-separated list (e.g. SELECT,INSERT).
    Use --from to specify the principal (Entra UPN, GUID, or role name).
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    scope_class, schema, object_name = _resolve_scope(scope_database, scope_schema, scope_object)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _permissions_svc.revoke_permission(
                target,
                permissions,
                principal,
                scope_class,
                schema=schema,
                object_name=object_name,
                grant_option_only=grant_option_only,
                cascade=cascade,
                mode=ctx.auth,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Revoked {permissions!r} on {scope_class} from {principal!r}.")
