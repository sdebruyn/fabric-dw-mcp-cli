"""SQL sub-commands for the fabric-dw CLI.

Commands
--------
- ``sql exec <ws> <item>`` — execute an arbitrary SQL statement or file.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import click

from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    _coro,
    build_http_client,
    build_sql_target,
    resolve_warehouse_arg,
    resolve_workspace_arg,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import sql_exec as _sql_exec_svc

if TYPE_CHECKING:
    from fabric_dw.cli._context import CliContext

_log = logging.getLogger(__name__)


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
@click.pass_obj
@_coro
async def exec_cmd(
    ctx: CliContext,
    workspace: str | None,
    item: str | None,
    query_text: str | None,
    query_file: str | None,
) -> None:
    """Execute a SQL statement against ITEM (warehouse or SQL endpoint) in WORKSPACE.

    Provide the query via -q/--query or -f/--file (not both).
    Multi-statement batches are supported; only the last result set is returned.
    DDL/DML statements return empty columns and rows.

    Output defaults to a Rich table (rows/columns).  Pass --json on the root command
    for machine-readable JSON ({columns: [...], rows: [...], rowcount: N}).
    """
    if query_text is not None and query_file is not None:
        raise click.UsageError("Use -q/--query OR -f/--file, not both.")
    if query_text is None and query_file is None:
        raise click.UsageError("Provide a query via -q/--query or -f/--file.")

    if query_file is not None:
        query_text = Path(query_file).read_text(encoding="utf-8-sig")

    assert query_text is not None  # noqa: S101 — satisfied by guards above

    ws = resolve_workspace_arg(ctx, workspace)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            result = await _sql_exec_svc.execute(target, query_text, mode=ctx.auth)

            if ctx.json_output:
                render(
                    result.model_dump(by_alias=True, mode="json"),
                    json_output=True,
                )
            elif result.rows:
                rows_as_dicts = [dict(zip(result.columns, row, strict=True)) for row in result.rows]
                render(rows_as_dicts, json_output=False, table_title="SQL Result")
            else:
                click.echo(f"Query executed successfully. rowcount={result.rowcount}")
    except FabricError as exc:
        raise click.ClickException(str(exc)) from exc
