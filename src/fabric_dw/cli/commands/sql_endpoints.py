"""SQL Analytics Endpoint sub-commands for the fabric-dw CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import (
    render,
    render_permissions_table,
    render_refresh_table,
    with_default_collation_for_display,
)
from fabric_dw.cli.commands._utils import (
    build_http_client,
    coro,
    make_resolver,
    resolve_item,
    resolve_warehouse_arg,
    resolve_workspace_arg,
    validate_workspace_or_all_workspaces,
)
from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.services import permissions as _permissions_svc
from fabric_dw.services import sql_endpoints as _sql_endpoints_svc

if TYPE_CHECKING:
    from uuid import UUID

    from fabric_dw.cache import ItemEntry
    from fabric_dw.http_client import FabricHttpClient

# Per-row keys that carry no information in the single-workspace human table:
# every SQL endpoint row has kind == "SQLEndpoint", and (without -A) a single
# workspace id.  They are stripped from the dicts handed to ``render`` for the
# table path only — the ``--json`` path keeps the complete payload.
_REDUNDANT_TABLE_KEYS = ("kind", "workspaceId")


def _strip_table_keys(
    rows: list[dict[str, object]], *, all_workspaces: bool
) -> list[dict[str, object]]:
    """Return *rows* with table-redundant keys removed (human/table path only).

    Always drops ``kind`` (every row is a SQL endpoint).  Drops ``workspaceId``
    too unless *all_workspaces* is set, in which case rows span workspaces and
    the column carries real information.
    """
    drop = {"kind"} if all_workspaces else set(_REDUNDANT_TABLE_KEYS)
    return [{k: v for k, v in row.items() if k not in drop} for row in rows]


async def _resolve_endpoint_or_hint(
    http: FabricHttpClient,
    ws: str,
    endpoint: str,
    *,
    endpoint_explicit: bool,
) -> tuple[UUID, ItemEntry]:
    """Resolve *endpoint* in workspace *ws*, turning a stale-default 404 into a clear error.

    When the endpoint argument was NOT supplied explicitly it has been taken
    from a configured default (env / config file).  A configured default that
    belongs to a *different* workspace makes ``resolve_item`` issue
    ``GET /workspaces/{ws}/items/{default}`` which 404s with a cryptic
    ``EntityNotFound``.  Translate that into an actionable message instead.

    Raises:
        click.ClickException: When the (defaulted) endpoint is not found in *ws*.
        NotFoundError: When the endpoint was passed explicitly but not found —
            the caller's ``except FabricError`` surfaces the original message.
    """
    try:
        return await resolve_item(http, ws, endpoint)
    except NotFoundError:
        if endpoint_explicit:
            raise
        raise click.ClickException(
            f"SQL endpoint {endpoint!r} (the configured default) was not found in "
            f"workspace {ws!r}. The default endpoint likely belongs to a different "
            "workspace. Pass the endpoint explicitly as the second argument "
            "('fabric-dw sql-endpoints <command> <workspace> <endpoint>'), or set a "
            "default that belongs to this workspace with "
            "'fabric-dw config set warehouse <name|id>' (accepts a warehouse or "
            "SQL Analytics Endpoint)."
        ) from None


@click.group("sql-endpoints")
def sql_endpoints_group() -> None:
    """Manage Microsoft Fabric SQL Analytics Endpoints."""


@sql_endpoints_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.option(
    "-A",
    "--all-workspaces",
    "all_workspaces",
    is_flag=True,
    default=False,
    help="Scan all visible workspaces and aggregate results.",
)
@click.pass_obj
@coro
async def list_cmd(ctx: CliContext, workspace: str | None, all_workspaces: bool) -> None:
    """List all SQL analytics endpoints in WORKSPACE (name or GUID).

    Pass -A / --all-workspaces to scan every visible workspace instead.
    WORKSPACE and --all-workspaces are mutually exclusive; exactly one is required.
    """
    # Resolve the workspace default before the XOR validation so that a
    # configured default-workspace (env / config file) is honoured when no
    # positional arg is passed but --all-workspaces is also absent.
    resolved_workspace = None if all_workspaces else resolve_workspace_arg(ctx, workspace)
    validate_workspace_or_all_workspaces(resolved_workspace, all_workspaces)
    try:
        async with build_http_client(ctx) as http:
            if all_workspaces:
                items = await _sql_endpoints_svc.list_all_workspaces(http)
            else:
                # resolved_workspace is guaranteed non-None by validate_workspace_or_all_workspaces
                if resolved_workspace is None:  # pragma: no cover — defensive
                    raise click.UsageError("Provide WORKSPACE or pass --all-workspaces / -A.")
                resolver, _ = make_resolver(http)
                ws_id = await resolver.workspace_id(resolved_workspace)
                items = await _sql_endpoints_svc.list_endpoints(http, ws_id)
            rows = [ep.model_dump(by_alias=True, mode="json") for ep in items]
            # The --json path stays COMPLETE; only the human/table path drops the
            # always-redundant columns (kind, and workspaceId when single-workspace).
            if not ctx.json_output:
                rows = _strip_table_keys(rows, all_workspaces=all_workspaces)
            render(
                rows,
                json_output=ctx.json_output,
                table_title="SQL Analytics Endpoints",
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_endpoints_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@coro
async def get_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """Get details for ITEM (SQL analytics endpoint) in WORKSPACE (both accept name or GUID)."""
    ws = resolve_workspace_arg(ctx, workspace)
    endpoint_explicit = item is not None
    ep = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_endpoint_or_hint(
                http, ws, ep, endpoint_explicit=endpoint_explicit
            )
            obj = await _sql_endpoints_svc.get_endpoint(http, ws_id, entry.id)
            dump = obj.model_dump(by_alias=True, mode="json")
            # Human output substitutes Fabric's effective default collation when
            # the API returns null; --json keeps the raw API value.
            if not ctx.json_output:
                dump = with_default_collation_for_display(dump)
            render(dump, json_output=ctx.json_output)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_endpoints_group.command("refresh")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option(
    "--recreate-tables",
    "recreate_tables",
    is_flag=True,
    default=False,
    help=(
        "Drop and recreate all tables during the refresh. "
        "Use to resolve inconsistencies or force a clean rebuild. "
        "DESTRUCTIVE — use with caution."
    ),
)
@click.pass_obj
@coro
async def refresh_cmd(
    ctx: CliContext, workspace: str | None, item: str | None, recreate_tables: bool
) -> None:
    """Refresh metadata for ITEM (SQL endpoint) in WORKSPACE (both accept name or GUID).

    Triggers a metadata sync from the underlying Lakehouse delta tables.
    This is a long-running operation (LRO) that is polled to completion.

    By default, results are shown as a Rich table.  Pass --json (on the root
    command) to emit raw JSON instead.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    endpoint_explicit = item is not None
    ep = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_endpoint_or_hint(
                http, ws, ep, endpoint_explicit=endpoint_explicit
            )
            statuses = await _sql_endpoints_svc.refresh_metadata(
                http, ws_id, entry.id, recreate_tables=recreate_tables
            )
            if ctx.json_output:
                render(
                    [s.model_dump(by_alias=True, mode="json") for s in statuses],
                    json_output=True,
                )
            else:
                render_refresh_table(statuses)
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc


@sql_endpoints_group.command("permissions")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.pass_obj
@coro
async def permissions_cmd(ctx: CliContext, workspace: str | None, item: str | None) -> None:
    """List principals with access to ITEM (SQL endpoint) in WORKSPACE (both accept name or GUID).

    Requires Fabric Administrator role.
    See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin for details.
    """
    ws = resolve_workspace_arg(ctx, workspace)
    endpoint_explicit = item is not None
    ep = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await _resolve_endpoint_or_hint(
                http, ws, ep, endpoint_explicit=endpoint_explicit
            )
            items = await _permissions_svc.list_item_access(http, ws_id, entry.id)
            render_permissions_table(
                items,
                title="SQL Analytics Endpoint Permissions",
                json_output=ctx.json_output,
            )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
