"""SQL sub-commands for the fabric-dw CLI.

Commands
--------
- ``sql exec <ws> <item>`` — execute an arbitrary SQL statement or file.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import click

from fabric_dw import auth as _auth
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    _coro,
    _resolve_item,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.services import sql_exec as _sql_exec_svc
from fabric_dw.sql import SqlTarget

if TYPE_CHECKING:
    from fabric_dw.cli._context import CliContext

_log = logging.getLogger(__name__)


@asynccontextmanager
async def _build_http_client(ctx: CliContext) -> AsyncIterator[FabricHttpClient]:
    credential = _auth.get_credential(ctx.auth)
    async with FabricHttpClient(credential) as http:
        yield http


@click.group("sql")
def sql_group() -> None:
    """Execute SQL statements against Fabric warehouses and SQL Analytics Endpoints."""


@sql_group.command("exec")
@click.argument("workspace", required=False, default=None)
@click.argument("item", required=False, default=None)
@click.option(
    "-q",
    "--query",
    "query_text",
    default=None,
    help="SQL statement or batch to execute.",
)
@click.option(
    "-f",
    "--file",
    "query_file",
    default=None,
    type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True),
    help="Path to a .sql file to execute.",
)
@click.option(
    "--table",
    "table_output",
    is_flag=True,
    default=False,
    help="Render results as a Rich table instead of JSON.",
)
@click.pass_obj
@_coro
async def exec_cmd(  # noqa: PLR0913
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    query_text: str | None,
    query_file: str | None,
    table_output: bool,
) -> None:
    """Execute a SQL statement against ITEM (warehouse or SQL endpoint) in WORKSPACE.

    Provide the query via -q/--query or -f/--file (not both).
    Multi-statement batches are supported; only the last result set is returned.
    DDL/DML statements return empty columns and rows.

    Output defaults to JSON ({columns: [...], rows: [...], rowcount: N}).
    Use --table for a human-readable Rich table.
    """
    if query_text is not None and query_file is not None:
        raise click.UsageError("Use -q/--query OR -f/--file, not both.")  # noqa: TRY003
    if query_text is None and query_file is None:
        raise click.UsageError("Provide a query via -q/--query or -f/--file.")  # noqa: TRY003

    if query_file is not None:
        query_text = Path(query_file).read_text(encoding="utf-8-sig")

    assert query_text is not None  # noqa: S101 — satisfied by guards above

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
            result = await _sql_exec_svc.execute(target, query_text, mode=ctx.auth)

            if table_output:
                if result.rows:
                    rows_as_dicts = [
                        dict(zip(result.columns, row, strict=True)) for row in result.rows
                    ]
                    render(rows_as_dicts, json_output=False, table_title="SQL Result")
                else:
                    click.echo(f"Query executed successfully. rowcount={result.rowcount}")
            else:
                render(
                    result.model_dump(mode="json"),
                    json_output=True,
                )
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
