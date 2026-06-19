"""SQL command group for the fabric-dw CLI.

Commands
--------
- ``sql exec <item>`` — execute an arbitrary SQL statement or file.
- ``sql plan <item>`` — capture the estimated SHOWPLAN_XML without executing.
"""

from __future__ import annotations

import json as _json

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._plan_parse import parse_showplan
from fabric_dw.cli._plan_render import operator_to_dict, render_plan_tree
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    coro,
    load_sql_body,
    resolve_warehouse_arg,
    resolve_workspace,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.services import sql_exec as _sql_exec_svc


@click.group("sql")
def sql_group() -> None:
    """SQL execution and query-plan capture for Fabric warehouses and SQL Analytics Endpoints."""


@sql_group.command("exec")
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
@coro
async def sql_exec_cmd(
    ctx: CliContext,
    item: str | None,
    query_text: str | None,
    query_file: str | None,
) -> None:
    """Execute a SQL statement against ITEM (warehouse or SQL endpoint).

    Provide the query via -q/--query or -f/--file (not both).
    Multi-statement batches are supported; only the last result set is returned.
    DDL/DML statements return empty columns and rows.

    Output defaults to a Rich table (rows/columns).  Pass --json on the root command
    for machine-readable JSON ({columns: [...], rows: [...], rowcount: N}).
    """
    query = load_sql_body(query_text, query_file, inline_opt="-q/--query", file_opt="-f/--file")

    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            result = await _sql_exec_svc.execute(target, query, mode=ctx.auth)

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
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@sql_group.command("plan")
@click.argument("item", required=False, default=None)
@click.option(
    "-q",
    "--query",
    "query_text",
    default=None,
    help="SQL statement to generate an estimated execution plan for.",
)
@click.option(
    "-f",
    "--file",
    "query_file",
    default=None,
    type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True),
    help="Path to a .sql file to plan.",
)
@click.option(
    "-o",
    "--output",
    "output_path",
    default=None,
    type=click.Path(file_okay=True, dir_okay=False, writable=True),
    help=(
        "Write the plan XML to this file (recommend .sqlplan extension). "
        "Opens in SSMS, Azure Data Studio, or pastetheplan.com."
    ),
)
@click.option(
    "--raw",
    "--xml",
    "raw",
    is_flag=True,
    default=False,
    help="Print the raw SHOWPLAN XML to stdout instead of the Rich terminal tree.",
)
@click.pass_obj
@coro
async def sql_plan_cmd(
    ctx: CliContext,
    item: str | None,
    query_text: str | None,
    query_file: str | None,
    output_path: str | None,
    raw: bool,
) -> None:
    """Capture the estimated SHOWPLAN_XML for ITEM (warehouse or SQL endpoint).

    Provide the query via -q/--query or -f/--file (not both).
    The query is NOT executed — only the estimated execution plan is captured.
    This means DDL/DML query text is safe to plan without modifying any data.

    By default the plan is rendered as a Rich terminal tree showing each
    operator with estimated rows, cost percentage, and parallel/warning badges.
    Use --raw/--xml to print the raw SHOWPLAN XML to stdout (e.g. for piping).
    Use -o/--output to save the raw XML to a file (recommended extension:
    .sqlplan); opens in SSMS, Azure Data Studio, or pastetheplan.com.
    Pass the root --json flag for machine-readable JSON of the operator tree.
    """
    query = load_sql_body(query_text, query_file, inline_opt="-q/--query", file_opt="-f/--file")

    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            plan_xml = await _sql_exec_svc.get_plan(target, query, mode=ctx.auth)

            if output_path is not None:
                # Always write raw XML to file, regardless of other flags.
                with open(output_path, "w", encoding="utf-8") as fh:
                    fh.write(plan_xml)
                click.echo(f"Execution plan written to {output_path}")
            elif raw:
                # --raw/--xml: pipe-friendly raw XML to stdout.
                click.echo(plan_xml)
            elif ctx.json_output:
                # Global --json: emit the parsed operator tree as JSON.
                operators = parse_showplan(plan_xml)
                payload = [operator_to_dict(op) for op in operators]
                click.echo(_json.dumps(payload, indent=2))
            else:
                # Default: render a Rich terminal tree.
                operators = parse_showplan(plan_xml)
                render_plan_tree(operators)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
