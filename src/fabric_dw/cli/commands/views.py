"""Views sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import click

from fabric_dw import auth as _auth
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm, render
from fabric_dw.cli.commands._utils import (
    _coro,
    _resolve_item,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.services import views as _views_svc
from fabric_dw.sql import SqlTarget

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_http_client(ctx: CliContext) -> AsyncIterator[FabricHttpClient]:
    """Build and yield an HTTP client for views commands."""
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http


def _parse_qualified_name(qualified_name: str) -> tuple[str, str]:
    """Split ``<schema>.<view>`` into ``(schema, view)``.

    Raises:
        click.UsageError: If the string does not contain exactly one dot.
    """
    schema, _, view = qualified_name.partition(".")
    if not schema or not view:
        raise click.UsageError(  # noqa: TRY003
            f"Expected <schema>.<view>, got {qualified_name!r}"
        )
    return schema, view


def _load_select_body(select: str | None, from_file: str | None) -> str:
    """Return the SELECT body from the inline option or file option.

    Raises:
        click.UsageError: If neither or both are provided.
    """
    if select and from_file:
        raise click.UsageError("Provide either --select or --from-file, not both.")  # noqa: TRY003
    if from_file:
        path = Path(from_file)
        if not path.is_file():
            raise click.UsageError(f"File not found: {from_file}")  # noqa: TRY003
        return path.read_text(encoding="utf-8").strip()
    if select:
        return select
    raise click.UsageError("Provide --select or --from-file.")  # noqa: TRY003


@click.group("views")
def views_group() -> None:
    """Manage SQL views on Fabric warehouses and SQL Analytics Endpoints."""


@views_group.command("list")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option("--schema", default=None, help="Filter by schema name.")
@click.pass_obj
@_coro
async def list_cmd(
    ctx: CliContext, workspace: str | None, item: str | None, schema: str | None
) -> None:
    """List views on ITEM (warehouse or SQL endpoint) in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Item {entry.display_name!r} has no connection string."
                )
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            items = await _views_svc.list_views(target, schema=schema, mode=ctx.auth)
            render(
                [v.model_dump(mode="json") for v in items],
                json_output=ctx.json_output,
                table_title="Views",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("get")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@_coro
async def get_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
) -> None:
    """Fetch the full definition of QUALIFIED_NAME (schema.view) on ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = _parse_qualified_name(qualified_name)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Item {entry.display_name!r} has no connection string."
                )
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            v = await _views_svc.get_view(target, schema, view_name, mode=ctx.auth)
            render(v.model_dump(mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("create")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option("--name", "qualified_name", required=True, help="Qualified name: schema.view.")
@click.option("--select", "select_body", default=None, help="Inline SELECT statement.")
@click.option("--from-file", default=None, help="Path to a .sql file containing the SELECT body.")
@click.pass_obj
@_coro
async def create_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
    select_body: str | None,
    from_file: str | None,
) -> None:
    """Create a new view QUALIFIED_NAME on ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = _parse_qualified_name(qualified_name)
    body = _load_select_body(select_body, from_file)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Item {entry.display_name!r} has no connection string."
                )
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            v = await _views_svc.create_view(target, schema, view_name, body, mode=ctx.auth)
            render(v.model_dump(mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("update")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option("--select", "select_body", default=None, help="Inline SELECT statement.")
@click.option("--from-file", default=None, help="Path to a .sql file containing the SELECT body.")
@click.pass_obj
@_coro
async def update_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
    select_body: str | None,
    from_file: str | None,
) -> None:
    """Redefine QUALIFIED_NAME (schema.view) on ITEM in WORKSPACE via CREATE OR ALTER VIEW."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = _parse_qualified_name(qualified_name)
    body = _load_select_body(select_body, from_file)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Item {entry.display_name!r} has no connection string."
                )
            confirmed = confirm(
                f"Redefine view [{schema}].[{view_name}] on {entry.display_name!r}?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            v = await _views_svc.update_view(target, schema, view_name, body, mode=ctx.auth)
            render(v.model_dump(mode="json"), json_output=ctx.json_output)
    except click.Abort:
        raise
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@views_group.command("drop")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@_coro
async def drop_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    qualified_name: str,
) -> None:
    """Drop QUALIFIED_NAME (schema.view) from ITEM in WORKSPACE."""
    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    schema, view_name = _parse_qualified_name(qualified_name)
    try:
        async with _build_http_client(ctx) as http:
            ws_id, entry = await _resolve_item(http, ws, wh)
            if entry.connection_string is None:
                raise click.ClickException(  # noqa: TRY003
                    f"Item {entry.display_name!r} has no connection string."
                )
            confirmed = confirm(
                f"Drop view [{schema}].[{view_name}] from {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            )
            if not confirmed:
                raise click.Abort()  # noqa: TRY301
            target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )
            await _views_svc.drop_view(target, schema, view_name, mode=ctx.auth)
            click.echo(f"View [{schema}].[{view_name}] dropped.")
    except click.Abort:
        raise
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
