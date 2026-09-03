"""SQL command group for the fabric-dw CLI.

Commands
--------
- ``sql exec <item>`` — execute an arbitrary SQL statement or file.
- ``sql plan <item>`` — capture the estimated SHOWPLAN_XML without executing.
"""

from __future__ import annotations

import json as _json
import sys

import click

from fabric_dw.cli._context import CliContext
from fabric_dw.cli._plan_dot import render_plan_dot
from fabric_dw.cli._plan_html import render_plan_html
from fabric_dw.cli._plan_mermaid import render_plan_mermaid
from fabric_dw.cli._plan_parse import parse_showplan
from fabric_dw.cli._plan_render import operator_to_dict, render_plan_tree
from fabric_dw.cli._plan_svg import render_plan_svg
from fabric_dw.cli._render import render, render_result_rows, sanitise_json
from fabric_dw.cli._watch import validate_watch, watch_loop
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    coro,
    load_sql_body,
    resolve_warehouse_arg,
    resolve_workspace,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.models import SqlResult
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
@click.option(
    "--watch",
    type=click.IntRange(min=1),
    metavar="SECONDS",
    help=(
        "Refresh every SECONDS, until interrupted with Ctrl-C. "
        "Terminal clearing is a no-op when stdout is not a TTY, so redirecting to a "
        "file or running in CI appends every refresh instead of redrawing."
    ),
)
@click.pass_obj
@coro
async def sql_exec_cmd(
    ctx: CliContext,
    item: str | None,
    query_text: str | None,
    query_file: str | None,
    watch: int | None,
) -> None:
    """Execute a SQL statement against ITEM (warehouse or SQL endpoint).

    Provide the query via -q/--query or -f/--file (not both).
    Multi-statement batches are supported; the CLI keeps the last result set that
    has a column list (i.e. the last query), not the last statement. A batch that
    ends with DDL/DML after a query, such as SELECT id FROM t; UPDATE t SET x = 1;,
    shows the SELECT result and does not separately report that the UPDATE ran.
    A statement with no result set at all (DDL/DML with nothing else in the batch)
    returns empty columns and rows.

    Output defaults to a Rich table (rows/columns).  Pass --json on the root command
    for machine-readable JSON ({columns: [...], rows: [...], rowcount: N}).

    With --watch SECONDS, the query text is read once and then re-executed and
    redrawn every SECONDS, the same way ``queries running``/``locks``/``connections``
    do.  There is no way for the CLI to tell whether a statement is read-only, so
    --watch simply re-runs whatever it was given, verbatim, DDL and DML included, on
    every tick.  Only use --watch with statements that are safe to run repeatedly.
    The loop runs until interrupted with Ctrl-C; it never stops on its own.  When
    stdout is not a TTY (e.g. redirected to a file, or running in CI), the terminal
    clear is a no-op, so each refresh is appended rather than redrawn.
    """
    validate_watch(json_output=ctx.json_output, watch=watch)
    query = load_sql_body(query_text, query_file, inline_opt="-q/--query", file_opt="-f/--file")

    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)

            async def _fetch() -> SqlResult:
                return await _sql_exec_svc.execute(target, query, mode=ctx.auth)

            def _render(result: SqlResult) -> None:
                _render_sql_result(ctx, result)

            await watch_loop(interval=watch, command="fdw sql exec", fetch=_fetch, render=_render)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


def _render_sql_result(ctx: CliContext, result: SqlResult) -> None:
    """Render one ``sql exec`` result set, for a single run or one watch tick.

    Branches on ``result.columns``, not ``result.rows``: a statement that
    returned a column list is a query and gets the table, even with zero
    rows (e.g. a ``SELECT`` that matched nothing).  Only a statement with no
    columns at all — the genuine DDL/DML case — falls back to the
    ``rowcount=`` banner.
    """
    if ctx.json_output:
        render(
            result.model_dump(by_alias=True, mode="json"),
            json_output=True,
        )
    elif result.columns:
        render_result_rows(
            result.columns,
            result.rows,
            json_output=False,
            table_title="SQL Result",
        )
    else:
        click.echo(f"Query executed successfully. rowcount={result.rowcount}")


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
        "Write the rendered output to this file.  For --raw/--xml and the default "
        "behaviour (no --format), a .sqlplan extension is recommended — the file opens "
        "in SSMS or Azure Data Studio.  For --format mermaid, any text extension works.  "
        "For --format html, a .html extension is recommended; -o/--output is required "
        "for --format html."
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
@click.option(
    "--format",
    "output_format",
    default=None,
    type=click.Choice(["mermaid", "dot", "svg", "html"], case_sensitive=False),
    help=(
        "Export format for the execution plan.  "
        "``mermaid`` emits a Mermaid flowchart TD diagram (plain text).  "
        "``dot`` emits a Graphviz DOT digraph (plain text, no extra dependencies).  "
        "``svg`` renders the plan to an SVG image via the system ``dot`` binary "
        "(requires Graphviz installed; see https://graphviz.org/download/).  "
        "``html`` writes a self-contained HTML file that renders the plan graphically "
        "in any browser (offline, no CDN); requires -o/--output.  "
        "Output goes to stdout, or to -o/--output when given.  "
        "Takes precedence over the default Rich tree; lower priority than --raw/--xml "
        "and the root --json flag."
    ),
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
    output_format: str | None,
) -> None:
    """Capture the estimated SHOWPLAN_XML for ITEM (warehouse or SQL endpoint).

    Provide the query via -q/--query or -f/--file (not both).
    The query is NOT executed — only the estimated execution plan is captured.
    This means DDL/DML query text is safe to plan without modifying any data.

    \b
    Representation (what is produced):
      --raw / --xml          raw SHOWPLAN XML  (highest priority)
      root --json            parsed operator tree as JSON
      --format mermaid       Mermaid flowchart TD diagram
      --format dot           Graphviz DOT digraph
      --format svg           SVG image via system dot binary (requires Graphviz)
      --format html          self-contained HTML file (requires -o/--output)
      (default)              Rich terminal tree

    \b
    Destination (where output goes) — orthogonal to representation:
      -o / --output FILE     write to FILE; no stdout rendering
      (default)              stdout / terminal

    When -o is given with the default (no --raw/--json/--format), the raw
    SHOWPLAN XML is written to the file (opens in SSMS / Azure Data Studio).
    """
    if raw and output_format is not None:
        raise click.UsageError("--raw/--xml and --format cannot be used together.")
    if output_format == "html" and output_path is None:
        raise click.UsageError(
            "--format html requires -o/--output FILE (HTML is not useful on stdout)."
        )

    query = load_sql_body(query_text, query_file, inline_opt="-q/--query", file_opt="-f/--file")

    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            plan_xml = await _sql_exec_svc.get_plan(target, query, mode=ctx.auth)
            _output_plan(ctx, plan_xml, output_path, raw, output_format)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


def _output_plan(
    ctx: CliContext,
    plan_xml: str,
    output_path: str | None,
    raw: bool,
    output_format: str | None,
) -> None:
    """Write or render the execution plan according to the selected mode.

    Representation (what is produced — first matching rule wins):
      raw=True          → raw SHOWPLAN XML
      ctx.json_output   → parsed operator tree as JSON
      format="mermaid"  → Mermaid flowchart TD diagram
      format="dot"      → Graphviz DOT digraph
      format="svg"      → SVG image via system dot binary (requires Graphviz)
      format="html"     → self-contained HTML file (output_path required)
      (default)         → Rich terminal tree

    Destination (where output goes):
      output_path set   → write to file, print confirmation to stdout only
      output_path None  → render/print to stdout / terminal

    When output_path is given with the default (no --raw/--json/--format),
    the raw SHOWPLAN XML is written to the file (opens in SSMS / ADS).
    """
    if raw:
        # --raw/--xml: pipe-friendly raw XML; -o writes to file.
        _write_or_echo(plan_xml, output_path, "Execution plan written to {path}")
    elif ctx.json_output:
        # Global --json: parsed operator tree as JSON; -o writes to file.
        operators = parse_showplan(plan_xml)
        payload = [operator_to_dict(op) for op in operators]
        json_text = _json.dumps(sanitise_json(payload), indent=2, allow_nan=False)
        _write_or_echo(json_text, output_path, "Execution plan JSON written to {path}")
    elif output_format == "mermaid":
        # --format mermaid: Mermaid flowchart diagram; -o writes to file.
        operators = parse_showplan(plan_xml)
        diagram = render_plan_mermaid(operators)
        _write_or_echo(diagram, output_path, "Mermaid diagram written to {path}")
    elif output_format == "dot":
        # --format dot: Graphviz DOT digraph; -o writes to file.
        operators = parse_showplan(plan_xml)
        dot_text = render_plan_dot(operators)
        _write_or_echo(dot_text, output_path, "DOT graph written to {path}")
    elif output_format == "svg":
        # --format svg: SVG image via system dot binary; -o writes to file.
        operators = parse_showplan(plan_xml)
        svg_bytes = render_plan_svg(operators)
        _write_or_echo_bytes(svg_bytes, output_path, "SVG written to {path}")
    elif output_format == "html":
        # --format html: self-contained HTML file; -o is required (validated upstream).
        html_text = render_plan_html(plan_xml)
        _write_or_echo(html_text, output_path, "HTML plan written to {path}")
    elif output_path is not None:
        # Default mode with -o: write raw XML to file, no tree rendered.
        # (Scripts relying on file-only output get no terminal noise.)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(plan_xml)
        click.echo(f"Execution plan written to {output_path}")
    else:
        # Default mode without -o: render Rich terminal tree.
        operators = parse_showplan(plan_xml)
        render_plan_tree(operators)


def _write_or_echo(text: str, output_path: str | None, file_msg_template: str) -> None:
    """Write *text* to *output_path* with a confirmation, or echo to stdout.

    Args:
        text: The content to write or echo.
        output_path: File path to write to; when ``None``, echo to stdout.
        file_msg_template: Message template for the confirmation echo; ``{path}``
            is replaced with the actual path.
    """
    if output_path is not None:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        click.echo(file_msg_template.format(path=output_path))
    else:
        click.echo(text)


def _write_or_echo_bytes(data: bytes, output_path: str | None, file_msg_template: str) -> None:
    """Write binary *data* to *output_path* with a confirmation, or echo to stdout.

    Used for binary output formats such as SVG (which is technically text but
    delivered as ``bytes`` from the subprocess).

    Args:
        data: The binary content to write or echo.
        output_path: File path to write to; when ``None``, the raw bytes are
            written to ``sys.stdout.buffer``.
        file_msg_template: Message template for the confirmation echo; ``{path}``
            is replaced with the actual path.
    """
    if output_path is not None:
        with open(output_path, "wb") as fh:
            fh.write(data)
        click.echo(file_msg_template.format(path=output_path))
    else:
        sys.stdout.buffer.write(data)
