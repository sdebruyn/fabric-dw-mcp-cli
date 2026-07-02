"""Permissions sub-commands for the fabric-dw CLI.

Three distinct permission planes are exposed under the ``permissions`` top-level group:

``permissions item``
    Fabric item-level permissions (REST admin API).  Moved from ``warehouses permissions``
    and ``sql-endpoints permissions``.

``permissions sql``
    T-SQL granular in-database permissions.  Reads from ``sys.database_permissions`` /
    ``sys.database_principals`` and issues ``GRANT`` / ``DENY`` / ``REVOKE`` statements.

``permissions cls``
    Column-level security: ``GRANT`` / ``DENY`` / ``REVOKE`` on specific columns of a
    table, targeting ``minor_id != 0`` rows in ``sys.database_permissions``.
"""

from __future__ import annotations

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render, render_permissions_table
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    confirm_destructive,
    coro,
    resolve_item,
    resolve_warehouse_arg,
    resolve_workspace,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.identifiers import parse_qualified_name as _parse_qualified_name
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

    This is a destructive operation: it removes an existing permission.
    A confirmation prompt is shown unless --yes / -y is passed.
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    scope_class, schema, object_name = _resolve_scope(scope_database, scope_schema, scope_object)
    if not confirm_destructive(
        f"Revoke {permissions!r} on {scope_class} from {principal!r}?",
        yes=ctx.yes,
    ):
        click.echo("Aborted.")
        return
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


# ---------------------------------------------------------------------------
# permissions cls helpers
# ---------------------------------------------------------------------------


def _parse_column_list(columns_str: str) -> list[str]:
    """Split a comma-separated column string into a non-empty list.

    Raises:
        click.UsageError: If the parsed list is empty (e.g. ``--columns ","``).
    """
    columns = [c.strip() for c in columns_str.split(",") if c.strip()]
    if not columns:
        raise click.UsageError("--columns must contain at least one non-empty column name")
    return columns


# ---------------------------------------------------------------------------
# permissions cls sub-group (column-level security)
# ---------------------------------------------------------------------------


@permissions_group.group("cls")
def cls_group() -> None:
    """Column-level security: GRANT / DENY / REVOKE on specific columns of a table."""


@cls_group.command("grant")
@click.argument("item", required=False, default=None)
@click.argument("permissions")
@click.option("--to", "principal", required=True, metavar="PRINCIPAL", help="Grantee principal.")
@click.option(
    "--object",
    "scope_object",
    required=True,
    metavar="SCHEMA.TABLE",
    help="Qualified table name (e.g. dbo.sales).",
)
@click.option(
    "--columns",
    "columns_str",
    required=True,
    metavar="COL1,COL2,...",
    help="Comma-separated list of column names to grant.",
)
@click.option(
    "--with-grant-option",
    "with_grant_option",
    is_flag=True,
    default=False,
    help="Allow the grantee to grant the permission to others.",
)
@click.pass_obj
@coro
async def cls_grant_cmd(
    ctx: CliContext,
    item: str | None,
    permissions: str,
    principal: str,
    scope_object: str,
    columns_str: str,
    with_grant_option: bool,
) -> None:
    """Grant column-level PERMISSIONS on ITEM to PRINCIPAL.

    PERMISSIONS: comma-separated list (e.g. SELECT). Allowed: SELECT, UPDATE, REFERENCES.
    Use --object to specify the qualified table name and --columns for the column list.
    Use --to to specify the grantee principal (Entra UPN, GUID, or role name).
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    columns = _parse_column_list(columns_str)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _permissions_svc.grant_permission(
                target,
                permissions,
                principal,
                "OBJECT",
                object_name=scope_object,
                with_grant_option=with_grant_option,
                columns=columns,
                mode=ctx.auth,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Granted {permissions!r} on {scope_object!r} cols {columns!r} to {principal!r}.")


@cls_group.command("deny")
@click.argument("item", required=False, default=None)
@click.argument("permissions")
@click.option("--to", "principal", required=True, metavar="PRINCIPAL", help="Principal to deny.")
@click.option(
    "--object",
    "scope_object",
    required=True,
    metavar="SCHEMA.TABLE",
    help="Qualified table name (e.g. dbo.sales).",
)
@click.option(
    "--columns",
    "columns_str",
    required=True,
    metavar="COL1,COL2,...",
    help="Comma-separated list of column names to deny.",
)
@click.pass_obj
@coro
async def cls_deny_cmd(
    ctx: CliContext,
    item: str | None,
    permissions: str,
    principal: str,
    scope_object: str,
    columns_str: str,
) -> None:
    """Deny column-level PERMISSIONS on ITEM to PRINCIPAL.

    PERMISSIONS: comma-separated list (e.g. SELECT). Allowed: SELECT, UPDATE, REFERENCES.
    Use --object to specify the qualified table name and --columns for the column list.
    Use --to to specify the principal (Entra UPN, GUID, or role name).
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    columns = _parse_column_list(columns_str)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _permissions_svc.deny_permission(
                target,
                permissions,
                principal,
                "OBJECT",
                object_name=scope_object,
                columns=columns,
                mode=ctx.auth,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Denied {permissions!r} on {scope_object!r} cols {columns!r} to {principal!r}.")


@cls_group.command("revoke")
@click.argument("item", required=False, default=None)
@click.argument("permissions")
@click.option(
    "--from", "principal", required=True, metavar="PRINCIPAL", help="Principal to revoke from."
)
@click.option(
    "--object",
    "scope_object",
    required=True,
    metavar="SCHEMA.TABLE",
    help="Qualified table name (e.g. dbo.sales).",
)
@click.option(
    "--columns",
    "columns_str",
    required=True,
    metavar="COL1,COL2,...",
    help="Comma-separated list of column names to revoke.",
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
@click.pass_obj
@coro
async def cls_revoke_cmd(
    ctx: CliContext,
    item: str | None,
    permissions: str,
    principal: str,
    scope_object: str,
    columns_str: str,
    grant_option_only: bool,
    cascade: bool,
) -> None:
    """Revoke column-level PERMISSIONS on ITEM from PRINCIPAL.

    PERMISSIONS: comma-separated list (e.g. SELECT). Allowed: SELECT, UPDATE, REFERENCES.
    Use --object to specify the qualified table name and --columns for the column list.
    Use --from to specify the principal (Entra UPN, GUID, or role name).

    This is a destructive operation: it removes an existing column-level permission.
    A confirmation prompt is shown unless --yes / -y is passed.
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    # Parse and validate columns before the confirm prompt so invalid input
    # raises UsageError immediately without prompting.
    columns = _parse_column_list(columns_str)
    if not confirm_destructive(
        f"Revoke {permissions!r} on {scope_object!r} columns {columns!r} from {principal!r}?",
        yes=ctx.yes,
    ):
        click.echo("Aborted.")
        return
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _permissions_svc.revoke_permission(
                target,
                permissions,
                principal,
                "OBJECT",
                object_name=scope_object,
                columns=columns,
                grant_option_only=grant_option_only,
                cascade=cascade,
                mode=ctx.auth,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Revoked {permissions!r} on {scope_object!r} cols {columns!r} from {principal!r}.")


@cls_group.command("list")
@click.argument("item", required=False, default=None)
@click.option(
    "--object",
    "scope_object",
    required=True,
    metavar="SCHEMA.TABLE",
    help="Qualified table name to list column-level permissions for.",
)
@click.pass_obj
@coro
async def cls_list_cmd(
    ctx: CliContext,
    item: str | None,
    scope_object: str,
) -> None:
    """List column-level permissions on ITEM for a specific table.

    Shows only rows that apply to specific columns (minor_id != 0 in sys.database_permissions).
    Use --object to specify the qualified table name.
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            perms = await _permissions_svc.list_sql_permissions(
                target,
                object_name=scope_object,
                mode=ctx.auth,
            )
            # Filter to column-level rows only (those where column_name is resolved)
            col_perms = [p for p in perms if p.column_name is not None]
            render(
                [p.model_dump(mode="json") for p in col_perms],
                json_output=ctx.json_output,
                table_title="Column-Level Permissions",
                prune_null_columns=True,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


# ---------------------------------------------------------------------------
# permissions rls helpers
# ---------------------------------------------------------------------------


def _parse_table_ref(table_expr: str, option_name: str) -> tuple[str, str]:
    """Split SCHEMA.TABLE into (schema, table_name).

    Delegates to :func:`fabric_dw.identifiers.parse_qualified_name`, converting
    :class:`ValueError` into a :class:`click.UsageError` prefixed with the
    option name.

    Args:
        table_expr: Qualified table name (e.g. ``"dbo.Sales"``).
        option_name: CLI option name for error messages (e.g. ``"--on"``).

    Returns:
        ``(schema, table_name)`` tuple.

    Raises:
        click.UsageError: If *table_expr* is not a two-part qualified name.
    """
    try:
        return _parse_qualified_name(table_expr, "table")
    except ValueError as exc:
        raise click.UsageError(f"{option_name}: {exc}") from exc


def _parse_fn_ref(fn_expr: str, option_name: str) -> tuple[str | None, str, list[str]]:
    """Parse a structured predicate function reference.

    Accepts the form ``schema.fn_name(col1, col2)`` or ``fn_name(col1)``.
    This is structured user input parsing -- not SQL DDL parsing.

    Args:
        fn_expr: Function reference string from the CLI.
        option_name: CLI option name for error messages (e.g. ``"--filter"``).

    Returns:
        ``(fn_schema, fn_name, cols)`` where ``fn_schema`` may be ``None``
        when no schema prefix is present.

    Raises:
        click.UsageError: On malformed input.
    """
    if "(" not in fn_expr or not fn_expr.endswith(")"):
        raise click.UsageError(
            f"{option_name}: expected SCHEMA.FN(col,...) or FN(col,...), got {fn_expr!r}"
        )
    paren_open = fn_expr.index("(")
    fn_ref = fn_expr[:paren_open].strip()
    args_str = fn_expr[paren_open + 1 : -1]

    if "." in fn_ref:
        dot = fn_ref.index(".")
        fn_schema: str | None = fn_ref[:dot].strip()
        fn_name = fn_ref[dot + 1 :].strip()
    else:
        fn_schema = None
        fn_name = fn_ref.strip()

    if not fn_name:
        raise click.UsageError(f"{option_name}: function name must not be empty")

    cols = [c.strip() for c in args_str.split(",") if c.strip()]
    if not cols:
        raise click.UsageError(
            f"{option_name}: at least one column argument is required, e.g. FN(col)"
        )
    return fn_schema, fn_name, cols


# ---------------------------------------------------------------------------
# permissions rls sub-group (row-level security)
# ---------------------------------------------------------------------------


@permissions_group.group("rls")
def rls_group() -> None:
    """Row-level security: CREATE / ALTER / DROP SECURITY POLICY."""


from fabric_dw.services import rls as _rls_svc  # noqa: E402


@rls_group.command("list")
@click.argument("item", required=False, default=None)
@click.pass_obj
@coro
async def rls_list_cmd(ctx: CliContext, item: str | None) -> None:
    """List security policies on ITEM (warehouse or SQL endpoint).

    Shows all security policies together with their attached predicates.
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            policies = await _rls_svc.list_security_policies(target, mode=ctx.auth)
            rows = [p.model_dump(mode="json") for p in policies]
            render(
                rows,
                json_output=ctx.json_output,
                table_title="Security Policies",
                prune_null_columns=True,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@rls_group.command("create")
@click.argument("item", required=False, default=None)
@click.argument("policy")
@click.option(
    "--filter",
    "filter_fn",
    required=True,
    metavar="SCHEMA.FN(col,...)",
    help="Filter predicate function reference.",
)
@click.option(
    "--on",
    "target_table",
    required=True,
    metavar="SCHEMA.TABLE",
    help="Target table for the predicate (e.g. dbo.Sales).",
)
@click.option(
    "--state",
    type=click.Choice(["on", "off"], case_sensitive=False),
    default="on",
    show_default=True,
    help="Initial policy state.",
)
@click.pass_obj
@coro
async def rls_create_cmd(
    ctx: CliContext,
    item: str | None,
    policy: str,
    filter_fn: str,
    target_table: str,
    state: str,
) -> None:
    """Create a security policy POLICY on ITEM with an initial FILTER predicate.

    Fabric Data Warehouse supports FILTER predicates only (#966): there is no
    --block option and no predicate-type choice to make.

    Use 'permissions rls add-predicate' to add further predicates.

    POLICY may be schema-qualified (e.g. rls.MySalesFilter) or bare (MySalesFilter).
    --on specifies the target table (e.g. dbo.Sales).

    Example:

        fdw -w MyWS permissions rls create MyWH rls.SalesFilter \\
            --filter "rls.fn_filter(SalesRep)" --on dbo.Sales
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)

    fn_schema, fn_name, fn_args = _parse_fn_ref(filter_fn, "--filter")
    table_schema, table_name = _parse_table_ref(target_table, "--on")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _rls_svc.create_security_policy(
                target,
                policy,
                [
                    {
                        "fn_schema": fn_schema,
                        "fn_name": fn_name,
                        "fn_args": fn_args,
                        "table_schema": table_schema,
                        "table_name": table_name,
                    }
                ],
                state=state.lower() == "on",
                mode=ctx.auth,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Created security policy {policy!r} with FILTER predicate on {target_table!r}.")


@rls_group.command("add-predicate")
@click.argument("item", required=False, default=None)
@click.argument("policy")
@click.option(
    "--filter",
    "filter_fn",
    required=True,
    metavar="SCHEMA.FN(col,...)",
    help="Filter predicate function reference.",
)
@click.option(
    "--on",
    "target_table",
    required=True,
    metavar="SCHEMA.TABLE",
    help="Target table for the predicate (e.g. dbo.Sales).",
)
@click.pass_obj
@coro
async def rls_add_predicate_cmd(
    ctx: CliContext,
    item: str | None,
    policy: str,
    filter_fn: str,
    target_table: str,
) -> None:
    """Add a FILTER predicate to an existing security policy POLICY on ITEM.

    Fabric Data Warehouse supports FILTER predicates only (#966): there is no
    --block option and no predicate-type choice to make.

    POLICY may be schema-qualified (e.g. rls.MySalesFilter) or bare (MySalesFilter).
    --on specifies the target table.
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)

    fn_schema, fn_name, fn_args = _parse_fn_ref(filter_fn, "--filter")
    table_schema, table_name = _parse_table_ref(target_table, "--on")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _rls_svc.add_predicate(
                target,
                policy,
                fn_schema,
                fn_name,
                fn_args,
                table_schema,
                table_name,
                mode=ctx.auth,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Added FILTER predicate to policy {policy!r} on {target_table!r}.")


@rls_group.command("drop-predicate")
@click.argument("item", required=False, default=None)
@click.argument("policy")
@click.option(
    "--on",
    "target_table",
    required=True,
    metavar="SCHEMA.TABLE",
    help="Target table whose FILTER predicate to drop.",
)
@click.pass_obj
@coro
async def rls_drop_predicate_cmd(
    ctx: CliContext,
    item: str | None,
    policy: str,
    target_table: str,
) -> None:
    """Drop the FILTER predicate from security policy POLICY on ITEM.

    Fabric Data Warehouse supports FILTER predicates only (#966): there is no
    --block option and no predicate-type choice to make.
    --on specifies the target table.
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)

    table_schema, table_name = _parse_table_ref(target_table, "--on")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _rls_svc.drop_predicate(
                target,
                policy,
                table_schema,
                table_name,
                mode=ctx.auth,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Dropped FILTER predicate from policy {policy!r} on {target_table!r}.")


@rls_group.command("set-state")
@click.argument("item", required=False, default=None)
@click.argument("policy")
@click.option(
    "--enable",
    "set_enable",
    is_flag=True,
    default=False,
    help="Enable the policy (STATE = ON). Mutually exclusive with --disable.",
)
@click.option(
    "--disable",
    "set_disable",
    is_flag=True,
    default=False,
    help="Disable the policy (STATE = OFF). Mutually exclusive with --enable.",
)
@click.pass_obj
@coro
async def rls_set_state_cmd(
    ctx: CliContext,
    item: str | None,
    policy: str,
    set_enable: bool,
    set_disable: bool,
) -> None:
    """Enable or disable security policy POLICY on ITEM.

    Specify exactly one of --enable or --disable.

    This is a mutating but non-destructive operation: no data or policy
    definitions are removed.
    """
    if set_enable == set_disable:
        raise click.UsageError("Specify exactly one of --enable or --disable.")

    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    enabled = set_enable

    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _rls_svc.set_policy_state(target, policy, enabled=enabled, mode=ctx.auth)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    state_label = "enabled" if enabled else "disabled"
    click.echo(f"Security policy {policy!r} {state_label}.")


@rls_group.command("drop")
@click.argument("item", required=False, default=None)
@click.argument("policy")
@click.pass_obj
@coro
async def rls_drop_cmd(ctx: CliContext, item: str | None, policy: str) -> None:
    """Drop security policy POLICY on ITEM.

    This is a destructive operation: the policy and all attached predicates
    are permanently removed.  A confirmation prompt is shown unless --yes / -y
    is passed.
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    if not confirm_destructive(
        f"Drop security policy {policy!r}?",
        yes=ctx.yes,
    ):
        click.echo("Aborted.")
        return
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _rls_svc.drop_security_policy(target, policy, mode=ctx.auth)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Dropped security policy {policy!r}.")


# ---------------------------------------------------------------------------
# permissions mask sub-group (dynamic data masking)
# ---------------------------------------------------------------------------


from fabric_dw.services import mask as _mask_svc  # noqa: E402


@permissions_group.group("mask")
def mask_group() -> None:
    """Dynamic data masking: apply or remove column masks."""


def _parse_mask_table_ref(table_expr: str) -> tuple[str, str]:
    """Split SCHEMA.TABLE into (schema, table_name) for mask commands.

    Args:
        table_expr: Qualified table name (e.g. ``"dbo.Sales"``).

    Returns:
        ``(schema, table_name)`` tuple.

    Raises:
        click.UsageError: If *table_expr* is not a two-part qualified name.
    """
    try:
        return _parse_qualified_name(table_expr, "table")
    except ValueError as exc:
        raise click.UsageError(f"TABLE: {exc}") from exc


@mask_group.command("list")
@click.argument("item", required=False, default=None)
@click.argument("table", required=False, default=None, metavar="[SCHEMA.TABLE]")
@click.pass_obj
@coro
async def mask_list_cmd(ctx: CliContext, item: str | None, table: str | None) -> None:
    """List columns with dynamic data masks on ITEM.

    When TABLE is given (as SCHEMA.TABLE), only masks for that table are shown.
    """
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    table_schema: str | None = None
    table_name: str | None = None
    if table is not None:
        table_schema, table_name = _parse_mask_table_ref(table)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            columns = await _mask_svc.list_masked_columns(
                target,
                table_schema=table_schema,
                table_name=table_name,
                mode=ctx.auth,
            )
            from fabric_dw.cli._render import render  # noqa: PLC0415

            render(
                [c.model_dump(mode="json") for c in columns],
                json_output=ctx.json_output,
                table_title="Masked Columns",
                prune_null_columns=True,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@mask_group.command("set")
@click.argument("item", required=False, default=None)
@click.argument("table", metavar="SCHEMA.TABLE")
@click.option(
    "--column",
    "column_name",
    required=True,
    metavar="COL",
    help="Column to apply the mask to.",
)
@click.option(
    "--function",
    "fn_type",
    required=True,
    type=click.Choice(["default", "email", "random", "partial"], case_sensitive=False),
    help="Mask function type.",
)
@click.option("--start", "start", type=int, default=None, help="Lower bound for random() mask.")
@click.option("--end", "end", type=int, default=None, help="Upper bound for random() mask.")
@click.option(
    "--prefix",
    "prefix",
    type=int,
    default=None,
    help="Leading characters to expose for partial() mask.",
)
@click.option(
    "--padding",
    "padding",
    default=None,
    metavar="STR",
    help="Replacement padding string for partial() mask.",
)
@click.option(
    "--suffix",
    "suffix",
    type=int,
    default=None,
    help="Trailing characters to expose for partial() mask.",
)
@click.pass_obj
@coro
async def mask_set_cmd(
    ctx: CliContext,
    item: str | None,
    table: str,
    column_name: str,
    fn_type: str,
    start: int | None,
    end: int | None,
    prefix: int | None,
    padding: str | None,
    suffix: int | None,
) -> None:
    """Apply or replace a dynamic data mask on a column of ITEM.

    TABLE must be in SCHEMA.TABLE format.  Specify the mask function with
    --function and any required arguments:

    \b
      default  -- no extra args required
      email    -- no extra args required
      random   -- requires --start and --end
      partial  -- requires --prefix, --padding, and --suffix
    """
    table_schema, table_name = _parse_mask_table_ref(table)
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            mask_fn_literal = await _mask_svc.set_column_mask(
                target,
                table_schema,
                table_name,
                column_name,
                fn_type,
                start=start,
                end=end,
                prefix=prefix,
                padding=padding,
                suffix=suffix,
                mode=ctx.auth,
            )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Mask {mask_fn_literal!r} applied to [{table_schema}].[{table_name}].[{column_name}]."
    )


@mask_group.command("drop")
@click.argument("item", required=False, default=None)
@click.argument("table", metavar="SCHEMA.TABLE")
@click.option(
    "--column",
    "column_name",
    required=True,
    metavar="COL",
    help="Column whose mask to remove.",
)
@click.pass_obj
@coro
async def mask_drop_cmd(
    ctx: CliContext,
    item: str | None,
    table: str,
    column_name: str,
) -> None:
    """Remove a dynamic data mask from a column of ITEM.

    TABLE must be in SCHEMA.TABLE format.

    This is a destructive operation: the mask is permanently removed.
    A confirmation prompt is shown unless --yes / -y is passed.
    """
    table_schema, table_name = _parse_mask_table_ref(table)
    if not confirm_destructive(
        f"Remove mask from [{table_schema}].[{table_name}].[{column_name}]?",
        yes=ctx.yes,
    ):
        click.echo("Aborted.")
        return
    ws = resolve_workspace(ctx)
    it = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, it)
            await _mask_svc.drop_column_mask(
                target,
                table_schema,
                table_name,
                column_name,
                mode=ctx.auth,
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Mask removed from [{table_schema}].[{table_name}].[{column_name}].")
