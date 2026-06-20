"""Tables sub-commands for the fabric-dw CLI."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import UUID

import click

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import render
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    confirm_destructive,
    coro,
    load_sql_body,
    parse_iso_datetime,
    parse_qualified_name,
    resolve_item,
    resolve_warehouse_arg,
    resolve_workspace,
)
from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import ColumnSpec, CopyIntoResult
from fabric_dw.services import tables as _tables_svc
from fabric_dw.services.load import (
    CopyIntoCsvOptions,
    IfExistsPolicy,
    copy_into_from_url,
    create_and_load,
    infer_file_format,
    load_local_file,
)
from fabric_dw.sql import SqlTarget
from fabric_dw.sql_io import OutputFormat, columns_rows_to_arrow, write_arrow


@click.group("tables")
def tables_group() -> None:
    """Manage SQL tables on Fabric warehouses and SQL Analytics Endpoints."""


@tables_group.command("list")
@click.argument("item", required=False, default=None)
@click.option("--schema", default=None, help="Filter by schema name.")
@click.pass_obj
@coro
async def list_cmd(ctx: CliContext, item: str | None, schema: str | None) -> None:
    """List tables on ITEM (warehouse or SQL endpoint)."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            items = await _tables_svc.list_tables(target, schema=schema, mode=ctx.auth)
            render(
                [t.model_dump(by_alias=True, mode="json") for t in items],
                json_output=ctx.json_output,
                table_title="Tables",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@tables_group.command("read")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option("--count", default=10, show_default=True, help="Max rows to return.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice([f.value for f in OutputFormat], case_sensitive=False),
    default=OutputFormat.JSON,
    show_default=True,
    help="Output format.",
)
@click.option("--output", default=None, help="Write to this file instead of stdout.")
@click.pass_obj
@coro
async def read_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
    count: int,
    fmt: str,
    output: str | None,
) -> None:
    """Read up to COUNT rows from QUALIFIED_NAME (schema.table) on ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")
    output_path = Path(output) if output else None

    # --format takes precedence when explicitly supplied (i.e. differs from the default
    # JSON value); if --format is omitted (or is the default "json"), the global --json
    # flag selects JSON output.  This means --json --format csv produces CSV.
    _json_fallback = OutputFormat.JSON.value if ctx.json_output else fmt
    effective_fmt = fmt if fmt != OutputFormat.JSON else _json_fallback

    if effective_fmt in (OutputFormat.CSV, OutputFormat.PARQUET) and output_path is None:
        raise click.UsageError(f"--output PATH is required for {effective_fmt!r} format.")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            columns, rows = await _tables_svc.read_table(
                target, schema, table_name, count=count, mode=ctx.auth
            )
            arrow_table = columns_rows_to_arrow(columns, rows)
            write_arrow(arrow_table, effective_fmt, output_path)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


_COLUMN_SPEC_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_]{0,127})"
    r":(?P<type>[^:]+)"
    r"(?::(?P<nullability>null|notnull))?$",
    re.IGNORECASE,
)


def _parse_column_spec(value: str) -> ColumnSpec:
    """Parse a ``name:TYPE[:null|notnull]`` column spec string.

    Args:
        value: The raw spec string, e.g. ``"id:INT:notnull"`` or ``"name:VARCHAR(100)"``.

    Returns:
        A :class:`~fabric_dw.models.ColumnSpec` instance.

    Raises:
        click.UsageError: If *value* does not match the expected format.
    """
    m = _COLUMN_SPEC_RE.match(value.strip())
    if not m:
        raise click.UsageError(
            f"Invalid --column spec {value!r}. "
            "Expected format: name:TYPE or name:TYPE:null or name:TYPE:notnull. "
            "Example: id:INT:notnull or description:VARCHAR(255)"
        )
    name = m.group("name")
    sql_type = m.group("type").strip()
    nullability = (m.group("nullability") or "null").lower()
    nullable = nullability != "notnull"
    return ColumnSpec(name=name, sql_type=sql_type, nullable=nullable)


def _parse_schema_file(path: str) -> list[ColumnSpec]:
    """Load a JSON column spec file as a list of :class:`~fabric_dw.models.ColumnSpec`.

    The file must contain a JSON array of objects, each with ``name`` and ``type``
    keys, and an optional ``nullable`` boolean.

    Args:
        path: Path to the JSON file.

    Returns:
        A list of :class:`~fabric_dw.models.ColumnSpec` instances.

    Raises:
        click.UsageError: If the file does not exist, is not valid JSON,
            or does not match the expected schema.
    """
    p = Path(path)
    if not p.is_file():
        raise click.UsageError(f"Schema file not found: {path}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"Invalid JSON in --from-schema {path!r}: {exc}") from exc
    if not isinstance(raw, list):
        raise click.UsageError(
            f"--from-schema {path!r} must be a JSON array of objects, got {type(raw).__name__}"
        )
    specs: list[ColumnSpec] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise click.UsageError(
                f"--from-schema {path!r}: element {i} must be an object, got {type(item).__name__}"
            )
        name = item.get("name")
        sql_type = item.get("type")
        if not name or not sql_type:
            raise click.UsageError(
                f"--from-schema {path!r}: element {i} must have 'name' and 'type' keys"
            )
        nullable = bool(item.get("nullable", True))
        specs.append(ColumnSpec(name=str(name), sql_type=str(sql_type), nullable=nullable))
    if not specs:
        raise click.UsageError(f"--from-schema {path!r}: schema file contains no columns")
    return specs


@tables_group.command("count")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@coro
async def count_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
) -> None:
    """Count rows in QUALIFIED_NAME (schema.table) on ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")
    try:
        async with build_http_client(ctx) as http:
            target, _entry = await build_sql_target(http, ws, wh)
            row_count = await _tables_svc.count_table_rows(
                target, schema, table_name, mode=ctx.auth
            )
            render(
                {"schema": schema, "name": table_name, "row_count": row_count},
                json_output=ctx.json_output,
                table_title="Row Count",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@tables_group.command("create")
@click.argument("item", required=False, default=None)
@click.option("--name", "qualified_name", required=True, help="Qualified name: schema.table.")
# CTAS path
@click.option("--select", "select_body", default=None, help="Inline SELECT statement for CTAS.")
@click.option("--from-file", default=None, help="Path to a .sql file containing the SELECT body.")
# Empty-table DDL path — sources (mutually exclusive with each other and with CTAS)
@click.option(
    "--from-parquet",
    "parquet_path",
    default=None,
    metavar="PATH",
    help="Create an empty table whose schema is derived from a Parquet file (no data is loaded).",
)
@click.option(
    "--from-csv",
    "csv_path",
    default=None,
    metavar="PATH",
    help="Create an empty table whose schema is derived from a CSV file header.",
)
@click.option(
    "--from-schema",
    "schema_file",
    default=None,
    metavar="PATH",
    help=(
        "Create an empty table from a JSON spec file (array of {name, type, nullable?} objects)."
    ),
)
@click.option(
    "--column",
    "column_specs",
    multiple=True,
    metavar="NAME:TYPE[:null|notnull]",
    help=(
        "Add a column in NAME:TYPE[:null|notnull] format (repeatable). "
        "Can be combined with --from-schema."
    ),
)
# CSV-specific options
@click.option(
    "--all-varchar",
    is_flag=True,
    default=False,
    help="(CSV) Force all columns to VARCHAR; skip type inference.",
)
@click.option(
    "--varchar-length",
    default=8000,
    show_default=True,
    type=click.IntRange(1, 8000),
    help="Default VARCHAR/VARBINARY length for string/binary columns.",
)
@click.option(
    "--delimiter",
    default=",",
    show_default=True,
    help="(CSV) Field delimiter.",
)
@click.option(
    "--encoding",
    default="utf-8-sig",
    show_default=True,
    help="(CSV) File encoding.",
)
@click.option(
    "--sample-rows",
    default=1000,
    show_default=True,
    type=click.IntRange(1, 100_000),
    help="(CSV) Maximum number of rows to sample for type inference.",
)
@click.pass_obj
@coro
async def create_cmd(  # noqa: PLR0912
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
    select_body: str | None,
    from_file: str | None,
    parquet_path: str | None,
    csv_path: str | None,
    schema_file: str | None,
    column_specs: tuple[str, ...],
    all_varchar: bool,
    varchar_length: int,
    delimiter: str,
    encoding: str,
    sample_rows: int,
) -> None:
    """Create a new table on ITEM.

    \b
    Two modes are available:
      CTAS (CREATE TABLE AS SELECT) — supply --select or --from-file.
      Empty DDL — supply one of --from-parquet, --from-csv, --from-schema,
                  or --column (repeatable).  These can be combined:
                  --from-schema adds base columns and --column appends extras.

    \b
    CSV options (only with --from-csv):
      --all-varchar     Force all columns to VARCHAR, skipping type inference.
      --varchar-length  Default VARCHAR length (1-8000, default 8000).
      --delimiter       Field delimiter (default ',').
      --encoding        File encoding (default 'utf-8-sig').
      --sample-rows     Rows to sample for inference (default 1000).
    """
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")

    # Determine which mode the user wants and validate mutual exclusivity.
    has_ctas = bool(select_body or from_file)
    has_parquet = parquet_path is not None
    has_csv = csv_path is not None
    has_explicit = schema_file is not None or bool(column_specs)

    # Count distinct source groups.
    source_count = sum([has_ctas, has_parquet, has_csv, has_explicit])

    if source_count == 0:
        raise click.UsageError(
            "Specify a source: --select/--from-file (CTAS), --from-parquet, "
            "--from-csv, --from-schema, or --column."
        )

    # CTAS cannot be combined with empty-DDL sources.
    if has_ctas and (has_parquet or has_csv or has_explicit):
        raise click.UsageError(
            "--select/--from-file (CTAS) cannot be combined with "
            "--from-parquet, --from-csv, --from-schema, or --column."
        )

    # Parquet, CSV, and explicit schema are mutually exclusive with each other.
    if has_parquet and has_csv:
        raise click.UsageError("--from-parquet and --from-csv are mutually exclusive.")
    if has_parquet and schema_file:
        raise click.UsageError("--from-parquet and --from-schema are mutually exclusive.")
    if has_parquet and column_specs:
        raise click.UsageError("--from-parquet and --column are mutually exclusive.")
    if has_csv and schema_file:
        raise click.UsageError("--from-csv and --from-schema are mutually exclusive.")
    if has_csv and column_specs:
        raise click.UsageError("--from-csv and --column are mutually exclusive.")

    # --all-varchar only makes sense with --from-csv.
    if all_varchar and not has_csv:
        raise click.UsageError("--all-varchar requires --from-csv.")

    # --select and --from-file are mutually exclusive.
    if select_body and from_file:
        raise click.UsageError("Provide either --select or --from-file, not both.")

    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)

            if has_ctas:
                body = load_sql_body(select_body, from_file)
                t = await _tables_svc.create_table(
                    target, schema, table_name, body, kind=entry.kind, mode=ctx.auth
                )

            elif has_parquet:
                t = await _tables_svc.create_table_from_parquet(
                    target,
                    schema,
                    table_name,
                    Path(parquet_path),  # type: ignore[arg-type]
                    kind=entry.kind,
                    mode=ctx.auth,
                    varchar_length=varchar_length,
                )

            elif has_csv:
                t = await _tables_svc.create_table_from_csv(
                    target,
                    schema,
                    table_name,
                    Path(csv_path),  # type: ignore[arg-type]
                    kind=entry.kind,
                    mode=ctx.auth,
                    all_varchar=all_varchar,
                    varchar_length=varchar_length,
                    delimiter=delimiter,
                    encoding=encoding,
                    sample_rows=sample_rows,
                )

            else:
                # Explicit schema via --from-schema and/or --column.
                cols: list[ColumnSpec] = []
                if schema_file:
                    cols.extend(_parse_schema_file(schema_file))
                cols.extend(_parse_column_spec(s) for s in column_specs)
                if not cols:
                    raise click.UsageError(
                        "--from-schema or at least one --column is required for the DDL path."
                    )
                t = await _tables_svc.create_empty_table(
                    target, schema, table_name, cols, kind=entry.kind, mode=ctx.auth
                )

            render(t.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@tables_group.command("delete")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@coro
async def delete_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
) -> None:
    """Drop QUALIFIED_NAME (schema.table) from ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            if not confirm_destructive(
                f"Drop table [{schema}].[{table_name}] from {entry.display_name!r} ({entry.id})?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _tables_svc.delete_table(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth
            )
            if ctx.json_output:
                render(
                    {"status": "dropped", "name": f"[{schema}].[{table_name}]"},
                    json_output=True,
                )
            else:
                click.echo(f"Table [{schema}].[{table_name}] dropped.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@tables_group.command("clear")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@coro
async def clear_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
) -> None:
    """Truncate QUALIFIED_NAME (schema.table) on ITEM."""
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            if not confirm_destructive(
                f"Truncate table [{schema}].[{table_name}] on {entry.display_name!r}?",
                yes=ctx.yes,
            ):
                click.echo("Aborted.")
                return
            await _tables_svc.clear_table(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth
            )
            if ctx.json_output:
                render(
                    {"status": "truncated", "name": f"[{schema}].[{table_name}]"},
                    json_output=True,
                )
            else:
                click.echo(f"Table [{schema}].[{table_name}] truncated.")
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@tables_group.command("cluster-columns")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.pass_obj
@coro
async def cluster_columns_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
) -> None:
    """List the data-clustering columns of QUALIFIED_NAME (schema.table) on ITEM.

    Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).
    Returns an empty table when no clustering is defined.
    """
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            rows = await _tables_svc.get_cluster_columns(
                target, schema, table_name, kind=entry.kind, mode=ctx.auth
            )
            render(
                rows,
                json_output=ctx.json_output,
                table_title="Cluster Columns",
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


@tables_group.command("clone")
@click.argument("item", required=False, default=None)
@click.option("--source", required=True, help="Qualified source table: schema.table.")
@click.option(
    "--name", "new_table", required=True, help="Qualified name for the clone: schema.table."
)
@click.option(
    "--at",
    "at_str",
    default=None,
    help=(
        "Optional point-in-time (UTC) for a historical clone, "
        "e.g. 2024-05-20T14:00:00. Must be within the data-retention window."
    ),
)
@click.pass_obj
@coro
async def clone_cmd(
    ctx: CliContext,
    item: str | None,
    source: str,
    new_table: str,
    at_str: str | None,
) -> None:
    """Clone SOURCE table as a zero-copy clone named NAME on ITEM.

    Creates a new table using ``CREATE TABLE … AS CLONE OF …``.  The optional
    ``--at`` timestamp must be within the warehouse data-retention window (UTC).
    Only supported on Fabric Data Warehouses (not SQL Analytics Endpoints).
    """
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    # Validate both qualified names eagerly so bad input is reported before any I/O.
    parse_qualified_name(source, kind="table")
    parse_qualified_name(new_table, kind="table")
    at = parse_iso_datetime(at_str, "--at") if at_str is not None else None
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            t = await _tables_svc.clone_table(
                target,
                source,
                new_table,
                at=at,
                kind=entry.kind,
                mode=ctx.auth,
            )
            render(t.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


def _make_csv_options(
    has_header: bool,
    delimiter: str | None,
    encoding: str | None,
    field_quote: str | None,
    row_terminator: str | None,
) -> CopyIntoCsvOptions:
    """Build a :class:`CopyIntoCsvOptions` from CLI option values."""
    return CopyIntoCsvOptions(
        delimiter=delimiter,
        first_row=2 if has_header else 1,
        encoding=encoding,
        field_quote=field_quote,
        row_terminator=row_terminator,
    )


def _resolve_url_file_type(fmt: str | None, url: str) -> str:
    """Resolve the COPY INTO FILE_TYPE for a remote URL.

    Raises:
        click.UsageError: If JSON is requested or format cannot be inferred.
    """
    _json_err = (
        "JSON remote URLs are not supported by COPY INTO. "
        "Download the file locally and use --file instead."
    )
    if fmt:
        upper = fmt.upper()
        if upper == "JSON":
            raise click.UsageError(_json_err)
        return upper
    # Try to infer from URL path.
    from urllib.parse import urlparse  # noqa: PLC0415

    try:
        guessed = infer_file_format(Path(urlparse(url).path))
    except ValueError:
        raise click.UsageError(
            "Cannot infer format from URL; pass --format csv or --format parquet."
        ) from None
    if guessed == "json":
        raise click.UsageError(_json_err)
    return guessed.upper()


@tables_group.command("load")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option("--file", "file_path", default=None, help="Path to a local file to load.")
@click.option("--url", "url", default=None, help="Remote URL to COPY INTO from.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["csv", "json", "parquet"], case_sensitive=False),
    default=None,
    help="File format (inferred from extension when omitted).",
)
@click.option("--delimiter", default=None, help="CSV column delimiter.")
@click.option(
    "--header/--no-header",
    "has_header",
    default=True,
    show_default=True,
    help="CSV has a header row.",
)
@click.option("--encoding", default=None, help="CSV file encoding (e.g. UTF8, UTF8BOM).")
@click.option("--field-quote", default=None, help="CSV field-quote character.")
@click.option("--row-terminator", default=None, help="CSV row terminator.")
@click.option(
    "--credential-type",
    "credential_type",
    type=click.Choice(
        ["none", "sas", "managed-identity", "service-principal", "account-key"],
        case_sensitive=False,
    ),
    default="none",
    show_default=True,
    help="Credential type for secured external URLs.",
)
@click.option("--secret", default=None, help="Credential secret (SAS token or account key).")
@click.option(
    "--identity",
    default=None,
    help="Identity for managed-identity or service-principal credentials.",
)
@click.option(
    "--staging-lakehouse",
    "staging_lakehouse_name",
    default=None,
    help="Staging Lakehouse name (auto-generated if omitted).",
)
@click.option(
    "--keep-staging",
    is_flag=True,
    default=False,
    help="Do not delete the staging Lakehouse after loading.",
)
@click.option(
    "--max-errors",
    "max_errors",
    default=None,
    type=int,
    help="Maximum errors before aborting the load.",
)
@click.option(
    "--rejected-row-location",
    "rejected_row_location",
    default=None,
    help="URL for rejected-row output.",
)
# ── Create-and-load options ──────────────────────────────────────────────────
@click.option(
    "--create/--no-create",
    "create",
    default=False,
    help=(
        "Auto-create the target table from the source schema before loading. "
        "Only supported for local files (--file). "
        "Requires pyarrow."
    ),
)
@click.option(
    "--if-exists",
    "if_exists",
    type=click.Choice(["fail", "append", "truncate", "replace"], case_sensitive=False),
    default=None,
    help=(
        "What to do when the target table already exists. "
        "Default: 'fail' with --create; 'append' without --create. "
        "'truncate' and 'replace' are destructive and require confirmation."
    ),
)
@click.option(
    "--all-varchar",
    "all_varchar",
    is_flag=True,
    default=False,
    help="(--create, CSV) Force all columns to VARCHAR; skip type inference.",
)
@click.option(
    "--varchar-length",
    "varchar_length",
    default=8000,
    show_default=True,
    type=click.IntRange(1, 8000),
    help="(--create) Default VARCHAR/VARBINARY length for inferred columns.",
)
@click.option(
    "--sample-rows",
    "sample_rows",
    default=1000,
    show_default=True,
    type=click.IntRange(1, 100_000),
    help="(--create, CSV) Maximum rows to sample for type inference.",
)
@click.option(
    "--cleanup-on-failure",
    "cleanup_on_failure",
    is_flag=True,
    default=False,
    help=(
        "Drop the table if WE created it and the subsequent load fails. "
        "Never drops a pre-existing table."
    ),
)
@click.pass_obj
@coro
async def load_cmd(  # noqa: PLR0912
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
    file_path: str | None,
    url: str | None,
    fmt: str | None,
    delimiter: str | None,
    has_header: bool,
    encoding: str | None,
    field_quote: str | None,
    row_terminator: str | None,
    credential_type: str,
    secret: str | None,
    identity: str | None,
    staging_lakehouse_name: str | None,
    keep_staging: bool,
    max_errors: int | None,
    rejected_row_location: str | None,
    create: bool,
    if_exists: str | None,
    all_varchar: bool,
    varchar_length: int,
    sample_rows: int,
    cleanup_on_failure: bool,
) -> None:
    """Load data into QUALIFIED_NAME (schema.table) on ITEM via COPY INTO.

    Exactly one of --file (local path) or --url (remote URL) must be provided.

    Local files are staged to a temporary Lakehouse in OneLake, then loaded
    via COPY INTO, and the staging Lakehouse is automatically cleaned up.

    JSON files are converted to Parquet client-side before staging.

    \b
    With --create, the target table is auto-created from the source schema
    before loading (local files only, requires pyarrow).
    Use --if-exists to control behaviour when the table already exists:
      fail      Error if table exists (default with --create).
      append    Load into existing table without modifying it.
      truncate  TRUNCATE the existing table, then load.  [DESTRUCTIVE]
      replace   DROP + recreate from inferred schema, then load.  [DESTRUCTIVE]

    \b
    Examples:
      fabric-dw -w myws tables load mywarehouse dbo.sales --file data.csv
      fabric-dw -w myws tables load mywarehouse dbo.sales --url https://... --format parquet
      fabric-dw -w myws tables load mywarehouse dbo.sales --file data.parquet --create
      fabric-dw -w myws tables load mywarehouse dbo.sales \
          --file data.csv --create --if-exists replace -y
    """
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    schema, table_name = parse_qualified_name(qualified_name, kind="table")

    if file_path and url:
        raise click.UsageError("Provide either --file or --url, not both.")
    if not file_path and not url:
        raise click.UsageError("Provide --file (local path) or --url (remote URL).")

    # --create is only supported for local files.
    if create and url:
        raise click.UsageError("--create is only supported for local files (--file).")

    # --all-varchar / --sample-rows only make sense with --create.
    if all_varchar and not create:
        raise click.UsageError("--all-varchar requires --create.")
    # sample_rows != default only matters with --create; silently ignore otherwise.
    if sample_rows != 1000 and not create:  # noqa: PLR2004
        pass  # silently ignore — it only takes effect with --create

    # Resolve --if-exists default.
    effective_if_exists: IfExistsPolicy
    if if_exists is not None:
        effective_if_exists = cast("IfExistsPolicy", if_exists)
    elif create:
        effective_if_exists = "fail"
    else:
        effective_if_exists = "append"

    # truncate/replace are only meaningful on the --create path (local files).
    # For --url there is no schema to infer, and for --file without --create the
    # destructive policies are undefined.  Reject early with a clear message.
    if effective_if_exists in ("truncate", "replace") and not create:
        raise click.UsageError(
            f"--if-exists {effective_if_exists} requires --create "
            "(destructive policies only apply to the auto-create load path)."
        )

    # Destructive confirmation for truncate / replace.
    is_destructive = effective_if_exists in ("truncate", "replace")
    if is_destructive:
        action = "TRUNCATE" if effective_if_exists == "truncate" else "DROP+recreate"
        if not confirm_destructive(
            f"{action} table [{schema}].[{table_name}] before loading?",
            yes=ctx.yes,
        ):
            click.echo("Aborted.")
            return

    csv_kw = {
        "has_header": has_header,
        "delimiter": delimiter,
        "encoding": encoding,
        "field_quote": field_quote,
        "row_terminator": row_terminator,
    }

    try:
        async with build_http_client(ctx) as http:
            ws_id, entry = await resolve_item(http, ws, wh)
            if entry.connection_string is None:
                raise click.ClickException(f"Item {entry.display_name!r} has no connection string.")
            sql_target = SqlTarget(
                workspace_id=str(ws_id),
                database=entry.display_name,
                connection_string=entry.connection_string,
            )

            if file_path:
                if create:
                    result = await _load_cmd_create_and_load(
                        ctx,
                        http,
                        ws_id,
                        sql_target,
                        entry,
                        schema,
                        table_name,
                        file_path,
                        fmt,
                        csv_kw,
                        staging_lakehouse_name,
                        keep_staging,
                        max_errors,
                        rejected_row_location,
                        effective_if_exists,
                        all_varchar,
                        varchar_length,
                        sample_rows,
                        cleanup_on_failure,
                    )
                else:
                    result = await _load_cmd_local(
                        ctx,
                        http,
                        ws_id,
                        sql_target,
                        entry,
                        schema,
                        table_name,
                        file_path,
                        fmt,
                        csv_kw,
                        staging_lakehouse_name,
                        keep_staging,
                        max_errors,
                        rejected_row_location,
                    )
            else:
                assert url is not None  # noqa: S101 — checked above
                result = await _load_cmd_url(
                    ctx,
                    sql_target,
                    entry,
                    schema,
                    table_name,
                    url,
                    fmt,
                    csv_kw,
                    credential_type,
                    secret,
                    identity,
                    max_errors,
                    rejected_row_location,
                )

        if ctx.json_output:
            render(result.model_dump(mode="json"), json_output=True)
        else:
            suffix = f" ({result.rows_rejected} rejected)" if result.rows_rejected else ""
            click.echo(
                f"Loaded {result.rows_loaded} row(s) into [{schema}].[{table_name}]{suffix}."
            )
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc


async def _load_cmd_local(
    ctx: CliContext,
    http: FabricHttpClient,
    ws_id: UUID,
    sql_target: SqlTarget,
    entry: ItemEntry,
    schema: str,
    table_name: str,
    file_path: str,
    fmt: str | None,
    csv_kw: Mapping[str, object],
    staging_lakehouse_name: str | None,
    keep_staging: bool,
    max_errors: int | None,
    rejected_row_location: str | None,
) -> CopyIntoResult:
    """Dispatch the local-file load sub-path."""
    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.services.load import FileFormat  # noqa: PLC0415

    local = Path(file_path)
    if not local.exists():
        raise click.UsageError(f"File not found: {file_path}")

    try:
        raw_format = fmt or infer_file_format(local)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    file_format: FileFormat = cast("FileFormat", raw_format)
    csv_options = (
        _make_csv_options(
            has_header=bool(csv_kw.get("has_header", True)),
            delimiter=cast("str | None", csv_kw.get("delimiter") or None),
            encoding=cast("str | None", csv_kw.get("encoding") or None),
            field_quote=cast("str | None", csv_kw.get("field_quote") or None),
            row_terminator=cast("str | None", csv_kw.get("row_terminator") or None),
        )
        if file_format == "csv"
        else None
    )
    credential = _auth.get_credential(ctx.auth)
    try:
        return await load_local_file(
            http,
            credential,
            ws_id,
            sql_target,
            schema,
            table_name,
            local,
            file_format=file_format,
            staging_lakehouse_name=staging_lakehouse_name,
            keep_staging=keep_staging,
            csv_options=csv_options,
            max_errors=max_errors,
            rejected_row_location=rejected_row_location,
            kind=entry.kind,
            mode=ctx.auth,
        )
    finally:
        # Close the storage-scope credential to release its internal aiohttp
        # session (azure.identity.aio credentials hold one).  Mirror the same
        # robust pattern used in FabricHttpClient.__aexit__: call close(),
        # await if it returns a coroutine, suppress any teardown error.
        _close = getattr(credential, "close", None)
        if callable(_close):
            try:
                result = _close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: S110
                pass


async def _load_cmd_create_and_load(
    ctx: CliContext,
    http: FabricHttpClient,
    ws_id: UUID,
    sql_target: SqlTarget,
    entry: ItemEntry,
    schema: str,
    table_name: str,
    file_path: str,
    fmt: str | None,
    csv_kw: Mapping[str, object],
    staging_lakehouse_name: str | None,
    keep_staging: bool,
    max_errors: int | None,
    rejected_row_location: str | None,
    if_exists: IfExistsPolicy,
    all_varchar: bool,
    varchar_length: int,
    sample_rows: int,
    cleanup_on_failure: bool,
) -> CopyIntoResult:
    """Dispatch the create-and-load sub-path (--create)."""
    from fabric_dw import auth as _auth  # noqa: PLC0415
    from fabric_dw.services.load import FileFormat  # noqa: PLC0415

    local = Path(file_path)
    if not local.exists():
        raise click.UsageError(f"File not found: {file_path}")

    try:
        raw_format = fmt or infer_file_format(local)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    file_format: FileFormat = cast("FileFormat", raw_format)

    # Build CSV load options (for the COPY INTO step).
    csv_options = (
        _make_csv_options(
            has_header=bool(csv_kw.get("has_header", True)),
            delimiter=cast("str | None", csv_kw.get("delimiter") or None),
            encoding=cast("str | None", csv_kw.get("encoding") or None),
            field_quote=cast("str | None", csv_kw.get("field_quote") or None),
            row_terminator=cast("str | None", csv_kw.get("row_terminator") or None),
        )
        if file_format == "csv"
        else None
    )

    # CSV delimiter for schema inference (from csv_kw or default).
    infer_delimiter = cast("str", csv_kw.get("delimiter") or ",")
    infer_encoding = cast("str", csv_kw.get("encoding") or "utf-8-sig")

    credential = _auth.get_credential(ctx.auth)
    return await create_and_load(
        http,
        credential,
        ws_id,
        sql_target,
        schema,
        table_name,
        local,
        if_exists=if_exists,
        file_format=file_format,
        staging_lakehouse_name=staging_lakehouse_name,
        keep_staging=keep_staging,
        csv_options=csv_options,
        max_errors=max_errors,
        rejected_row_location=rejected_row_location,
        kind=entry.kind,
        mode=ctx.auth,
        cleanup_on_failure=cleanup_on_failure,
        all_varchar=all_varchar,
        varchar_length=varchar_length,
        sample_rows=sample_rows,
        csv_delimiter=infer_delimiter,
        csv_encoding=infer_encoding,
    )


async def _load_cmd_url(
    ctx: CliContext,
    sql_target: SqlTarget,
    entry: ItemEntry,
    schema: str,
    table_name: str,
    url: str,
    fmt: str | None,
    csv_kw: Mapping[str, object],
    credential_type: str,
    secret: str | None,
    identity: str | None,
    max_errors: int | None,
    rejected_row_location: str | None,
) -> CopyIntoResult:
    """Dispatch the remote-URL load sub-path."""
    from fabric_dw.services.load import CopyIntoCredentialType  # noqa: PLC0415

    file_type = _resolve_url_file_type(fmt, url)
    csv_options = (
        _make_csv_options(
            has_header=bool(csv_kw.get("has_header", True)),
            delimiter=cast("str | None", csv_kw.get("delimiter") or None),
            encoding=cast("str | None", csv_kw.get("encoding") or None),
            field_quote=cast("str | None", csv_kw.get("field_quote") or None),
            row_terminator=cast("str | None", csv_kw.get("row_terminator") or None),
        )
        if file_type == "CSV"
        else None
    )
    cred_type: CopyIntoCredentialType = cast("CopyIntoCredentialType", credential_type)
    return await copy_into_from_url(
        sql_target,
        schema,
        table_name,
        url,
        file_type=file_type,
        credential_type=cred_type,
        secret=secret,
        identity=identity,
        csv_options=csv_options,
        max_errors=max_errors,
        rejected_row_location=rejected_row_location,
        kind=entry.kind,
        mode=ctx.auth,
    )


@tables_group.command("rename")
@click.argument("item", required=False, default=None)
@click.argument("qualified_name")
@click.option("--new-name", required=True, help="New (unqualified) table name.")
@click.pass_obj
@coro
async def rename_cmd(
    ctx: CliContext,
    item: str | None,
    qualified_name: str,
    new_name: str,
) -> None:
    """Rename QUALIFIED_NAME (schema.table) on ITEM to --new-name.

    ITEM must be a Data Warehouse; SQL Analytics Endpoints are read-only.
    The new name must be unqualified (bare table name) — sp_rename cannot
    move a table to a different schema.
    """
    ws = resolve_workspace(ctx)
    wh = resolve_warehouse_arg(ctx, item)
    parse_qualified_name(qualified_name, kind="table")
    try:
        async with build_http_client(ctx) as http:
            target, entry = await build_sql_target(http, ws, wh)
            t = await _tables_svc.rename_table(
                target, qualified_name, new_name, kind=entry.kind, mode=ctx.auth
            )
            render(t.model_dump(by_alias=True, mode="json"), json_output=ctx.json_output)
    except (ValueError, FabricError) as exc:
        raise click.ClickException(str(exc)) from exc
