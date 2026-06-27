"""Rich + JSON rendering helpers for CLI output."""

from __future__ import annotations

import json as _json
import math
import re
from collections.abc import Sequence

import click
from rich.console import Console
from rich.markup import escape as _escape_markup
from rich.panel import Panel
from rich.table import Table

from fabric_dw.models import ItemAccess, TableSyncStatus

__all__ = [
    "confirm",
    "render",
    "render_permissions_table",
    "render_refresh_table",
    "render_result_rows",
    "sanitise_json",
]


# ---------------------------------------------------------------------------
# JSON sanitisation
# ---------------------------------------------------------------------------


def sanitise_json(obj: object) -> object:
    """Recursively replace non-finite floats with ``None`` for strict JSON.

    ``json.dumps`` with the default ``allow_nan=True`` emits the non-standard
    tokens ``Infinity``, ``-Infinity``, and ``NaN``, which strict JSON parsers
    reject.  This function walks the value tree and coerces any non-finite
    ``float`` to ``None`` (serialised as JSON ``null``) so the payload is
    always RFC 8259 compliant before being passed to ``json.dumps``.

    Handles ``dict``, ``list``, and scalar types.  All other types are returned
    unchanged for the ``default`` fallback in ``json.dumps`` to handle.

    Args:
        obj: The value to sanitise (can be any JSON-representable Python value).

    Returns:
        A new value with non-finite floats replaced by ``None``.
    """
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: sanitise_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitise_json(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# GUID detection
# ---------------------------------------------------------------------------

#: Compiled regex that matches a canonical UUID/GUID string (bare, 36 chars).
#: No ``^``/``$`` anchors — use ``fullmatch()`` so a trailing newline (which
#: Python ``$`` would accept) is correctly rejected.
_GUID_RE: re.Pattern[str] = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

#: Fixed width for GUID columns — a GUID is exactly 36 characters.
_GUID_WIDTH = 36

#: Estimated width for secondary (non-primary) GUID columns in the fit
#: heuristic.  Secondary GUIDs are allowed to truncate (no forced min_width),
#: but they still occupy at least this many chars in practice before wrapping.
_GUID_SECONDARY_WIDTH = 10

#: Cap applied to non-GUID header names when estimating required table width.
#: Headers longer than this are assumed to truncate gracefully (Rich ellipsis)
#: rather than needing their full length to be legible.
_HEADER_MAX_WIDTH = 24


def _is_guid_column(col: str, norm_rows: list[dict[str, object] | object]) -> bool:
    """Return *True* when *col* is a GUID column.

    A column is considered a GUID column when:

    * It has at least one non-``None`` value in a dict row, **and**
    * Every non-``None`` cell value (converted to ``str``) matches the
      canonical GUID regex ``_GUID_RE``.

    Non-dict rows are ignored entirely.  An all-``None`` column returns
    ``False`` (no evidence that the column contains GUIDs).
    """
    found_non_null = False
    for row in norm_rows:
        if not isinstance(row, dict):
            continue
        val = row.get(col)
        if val is None:
            continue
        found_non_null = True
        if not _GUID_RE.fullmatch(str(val)):
            return False
    return found_non_null


# ---------------------------------------------------------------------------
# Presentation constants (kept here so re-skinning touches one file)
# ---------------------------------------------------------------------------

#: Status label → Rich colour mapping used in metadata refresh result tables.
STATUS_STYLES: dict[str, str] = {
    "Success": "green",
    "Failure": "red",
    "NotRun": "yellow",
}

#: Maximum character width for error text in the metadata refresh result table.
ERROR_MAX_LEN = 60

_DEFAULT_CONSOLE = Console()


def render(
    data: object,
    *,
    json_output: bool,
    console: Console | None = None,
    table_title: str | None = None,
    drop_columns: tuple[str, ...] | list[str] | None = None,
    prune_null_columns: bool = False,
) -> None:
    """Print *data* to stdout using JSON or Rich formatting.

    Args:
        data: The data to render. Supported shapes:
            - ``list[dict]`` → Rich Table (or JSON array).
            - ``dict`` → Rich Panel (or JSON object).
            - other → ``str()`` conversion via ``click.echo`` (or JSON scalar).
        json_output: When *True*, emit indented JSON via ``click.echo``.
            When *False*, use Rich for human-friendly output.
        console: Optional Rich Console instance. When *None* the module-level
            default console (stdout) is used. Ignored when *json_output=True*.
        table_title: Optional title shown above the Rich Table.
            Ignored when *json_output=True* or when *data* is not a list.
        drop_columns: Optional column names to omit from the **human-readable
            table only**.  Must be a ``tuple[str, ...]`` or ``list[str]`` (not
            a bare ``str``).  Useful for hiding redundant columns (e.g. a
            workspace-id column when every row shares the same workspace).
            Ignored when *json_output=True* (machine-readable output is never
            pruned) and when *data* is not a list.
        prune_null_columns: When *True*, columns whose value is ``None`` in
            every row are omitted from the rendered table.  Defaults to
            *False* so that raw query results (e.g. ``sql exec``) always show
            every column the query returned, including all-``NULL`` ones.
            Set to *True* for metadata renders (list commands) where optional
            model fields that happen to be unpopulated in a given response
            should be hidden to reduce visual clutter.
            Ignored when *json_output=True*.
    """
    if json_output:
        click.echo(_json.dumps(sanitise_json(data), indent=2, default=str, allow_nan=False))
        return

    con = console if console is not None else _DEFAULT_CONSOLE

    if isinstance(data, list):
        _render_table(
            data,
            console=con,
            title=table_title,
            drop_columns=drop_columns,
            prune_null_columns=prune_null_columns,
        )
    elif isinstance(data, dict):
        _render_panel({str(k): v for k, v in data.items()}, console=con, title=table_title)
    else:
        click.echo(str(data))


def _cell(value: object) -> str:
    """Convert a cell value to a Rich-markup string.

    ``None`` is rendered as ``[dim]NULL[/dim]`` so SQL NULLs are visually
    distinct from the literal string ``'None'``.

    Whole-number ``float`` values (e.g. ``1500.0``) are rendered without the
    spurious ``.0`` suffix (e.g. ``"1500"``), matching the appearance of the
    underlying integer.  Fractional floats (e.g. ``1234.5``) are rendered
    as-is via ``str()``.

    All data-derived string values are escaped with :func:`rich.markup.escape`
    so that bracket characters (``[``, ``]``) in the data are rendered
    verbatim and never interpreted as Rich markup tags.
    """
    if value is None:
        return "[dim]NULL[/dim]"
    if isinstance(value, float) and value.is_integer():
        return _escape_markup(str(int(value)))
    return _escape_markup(str(value))


def _format_nested(value: object, *, _depth: int = 0) -> str:
    """Recursively format a value for panel display.

    Scalars are rendered via ``_cell()``.  Dicts and lists are expanded into
    indented multi-line blocks so nested structures appear readable rather than
    as Python ``repr`` strings (e.g. ``{'workspace': None, ...}``).

    Empty dict renders as ``{}``.  Empty list renders as ``[]``.
    """
    indent = "  " * (_depth + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        parts = [
            f"{indent}[bold]{_escape_markup(str(k))}[/bold]: {_format_nested(v, _depth=_depth + 1)}"
            for k, v in value.items()
        ]
        return "\n" + "\n".join(parts)
    if isinstance(value, list):
        if not value:
            return "[]"
        parts = []
        for item in value:
            child = _format_nested(item, _depth=_depth + 1)
            # When item is itself a dict/list, child starts with "\n"; strip
            # it so the indent is applied to the first content line rather
            # than producing a blank line of trailing spaces before the content.
            parts.append(f"{indent}{child.lstrip(chr(10))}")
        return "\n" + "\n".join(parts)
    return _cell(value)


def _column_is_all_null(col: str, norm_rows: list[dict[str, object] | object]) -> bool:
    """Return *True* when every dict-row in *norm_rows* has a ``None`` value for *col*.

    Non-dict rows (scalars) are never considered to "have" a value for any
    column, so they are skipped.  A column is kept as soon as one dict-row
    provides a non-``None`` value.
    """
    for row in norm_rows:
        if not isinstance(row, dict):
            continue
        if row.get(col) is not None:
            return False
    return True


def _add_columns(
    table: Table,
    visible_columns: list[str],
    norm_rows: list[dict[str, object] | object],
) -> None:
    """Add columns to *table*, applying GUID-specific width constraints.

    Only the *first* GUID column gets ``no_wrap=True, min_width=_GUID_WIDTH``
    so it is never truncated.  Additional GUID columns are rendered without a
    forced ``min_width``: they can yield space to human-readable columns
    (``displayName``, ``description``, …) rather than starving them to zero
    width on a narrow terminal (e.g. the 80-col default for piped/non-TTY
    output).

    Column names are escaped with :func:`rich.markup.escape` so that bracket
    characters (e.g. ``FileRowCount[avg]``) are rendered verbatim instead of
    being silently stripped as markup tags.

    .. note::
        "First" is determined by insertion order of *visible_columns*, which
        mirrors API-response field order.  All current Fabric API models place
        ``id`` first, making it the consistent primary GUID column.  If a
        future model reorders fields such that a different GUID appears first,
        the heuristic will silently shift — keep model field order stable.
    """
    primary_guid_assigned = False
    for col in visible_columns:
        escaped_col = _escape_markup(col)
        if _is_guid_column(col, norm_rows) and not primary_guid_assigned:
            table.add_column(escaped_col, no_wrap=True, min_width=_GUID_WIDTH)
            primary_guid_assigned = True
        else:
            table.add_column(escaped_col)


def _table_fits(
    visible_columns: list[str],
    norm_rows: list[dict[str, object] | object],
    console_width: int,
) -> bool:
    """Return *True* when *visible_columns* can fit legibly in *console_width*.

    The heuristic mirrors what :func:`_add_columns` actually does at render time:

    * The **primary GUID column** (first column whose every non-null value is a
      canonical GUID) needs its full ``_GUID_WIDTH`` (36) chars — it is rendered
      ``no_wrap`` so it is never truncated.
    * **Secondary GUID columns** are given a floor of ``_GUID_SECONDARY_WIDTH``
      (10) chars — they can truncate, but they still consume visible space.
    * **Non-GUID columns** need ``min(len(header), _HEADER_MAX_WIDTH)`` chars —
      a 20-char header like ``PotentialAnomalyType`` needs its full length to be
      readable; a very long header (>24) is assumed to truncate gracefully.
    * **Rich border overhead**: 2 chars padding per column (1 space each side)
      plus 1 separator char between each pair of columns (``len(cols) - 1``)
      plus 2 outer border chars.  Total: ``3 * len(cols) + 1``.

    Calibration examples at ``console_width = 80``:
    * #743 shape — ``id``(GUID/36) + ``workspaceId``(GUID/10) +
      ``displayName``(11) + ``kind``(4) + borders ≈ 36+10+11+4 + 3*4+1 = 74 ≤ 80
      → **horizontal** ✓
    * 12-col queries shape — headers avg ~10 chars → 12*10 + 3*12+1 = 157 > 80
      → **vertical** ✓
    * 16-col health-check shape — headers avg ~15 chars → 16*15 + 3*16+1 = 289 > 80
      → **vertical** ✓
    """
    if not visible_columns:
        return True

    primary_guid_assigned = False
    content_width = 0
    for col in visible_columns:
        if _is_guid_column(col, norm_rows):
            if not primary_guid_assigned:
                content_width += _GUID_WIDTH
                primary_guid_assigned = True
            else:
                content_width += _GUID_SECONDARY_WIDTH
        else:
            content_width += min(len(col), _HEADER_MAX_WIDTH)

    # Rich border: 1 left-padding + 1 right-padding per column + 1 separator
    # between each adjacent pair + 2 outer frame chars → 3 * N + 1.
    border_chars = 3 * len(visible_columns) + 1
    return content_width + border_chars <= console_width


def _render_table(
    rows: Sequence[object],
    *,
    console: Console,
    title: str | None,
    drop_columns: tuple[str, ...] | list[str] | None = None,
    prune_null_columns: bool = False,
) -> None:
    """Render a list of dicts as a Rich Table (or vertical fallback).

    When the estimated minimum width required to show all visible column headers
    legibly exceeds the current console width, the renderer switches to a
    vertical, record-oriented layout — one panel per row listing ``key: value``
    pairs.  This keeps wide-schema tables (e.g. ``tables health-check`` with
    ~16 metrics) fully legible on an 80-column terminal.  Tables that fit
    normally keep the horizontal layout and the existing GUID-column width
    behaviour from PR #743.  See :func:`_table_fits` for the exact criterion.

    When *prune_null_columns* is *True*, columns whose value is ``None`` in
    every row are omitted from the output.  This is appropriate for metadata
    list views (e.g. ``procedures list``) where optional model fields that are
    unpopulated in a given response only add visual clutter.  A column that is
    non-null in at least one row is always kept, and any null cells in that
    column still render as ``[dim]NULL[/dim]``.

    When *prune_null_columns* is *False* (the default), every column that
    appears in the data is rendered, including all-``NULL`` ones.  Raw query
    results (``sql exec``) must use this default so that the user sees every
    column their query selected.

    Args:
        rows: The list of rows (dicts or scalars) to render.
        console: The Rich console to print to.
        title: Optional table title.
        drop_columns: Optional column names to explicitly omit.  Used by callers
            to hide a column that is redundant in a given context (e.g. a
            shared workspace id).
        prune_null_columns: When *True*, drop columns whose value is ``None``
            in every row before rendering.  Defaults to *False*.
    """
    dropped: frozenset[str] = frozenset(drop_columns or ())

    if not rows:
        if title:
            console.print(f"[bold]{_escape_markup(title)}[/bold]")
        table = Table(show_header=True, header_style="bold")
        console.print(table)
        return

    # Collect all column names in insertion order (union of all keys)
    columns: list[str] = []
    seen: set[str] = set()
    # Normalise each row to dict[str, object] once so we can reuse below.
    norm_rows: list[dict[str, object] | object] = []
    for row in rows:
        if isinstance(row, dict):
            row_dict: dict[str, object] = {str(k): v for k, v in row.items()}
            norm_rows.append(row_dict)
            for key in row_dict:
                if key not in seen:
                    columns.append(key)
                    seen.add(key)
        else:
            norm_rows.append(row)

    # Optionally drop columns where every dict-row's value is None (or absent).
    # Non-dict rows (scalars) are never counted as "having" any column value, so
    # a column is only kept if at least one dict-row provides a non-None value.
    # Pruning is opt-in: raw query results must show every column they returned.
    all_null: set[str] = (
        {col for col in columns if _column_is_all_null(col, norm_rows)}
        if prune_null_columns
        else set()
    )
    visible_columns = [col for col in columns if col not in all_null and col not in dropped]

    # Wide-table fallback: when the estimated minimum width for all visible
    # columns exceeds the console width, switch to a vertical key: value layout
    # per row so that every field name and value is fully readable.
    if not _table_fits(visible_columns, norm_rows, console.width):
        _render_vertical(norm_rows, visible_columns, console=console, title=title)
        return

    if title:
        console.print(f"[bold]{_escape_markup(title)}[/bold]")
    table = Table(show_header=True, header_style="bold")
    _add_columns(table, visible_columns, norm_rows)

    for row in norm_rows:
        if isinstance(row, dict):
            table.add_row(*[_cell(row.get(col, "")) for col in visible_columns])
        else:
            table.add_row(_cell(row))

    console.print(table)


def _render_vertical(
    norm_rows: list[dict[str, object] | object],
    visible_columns: list[str],
    *,
    console: Console,
    title: str | None,
) -> None:
    """Render *norm_rows* as a sequence of vertical key: value panels.

    Used as a fallback when the horizontal table would be too wide for the
    console.  Each dict row is rendered as a ``_render_panel``-style block;
    scalar rows are printed as plain text.  The overall title is printed once
    before the first row.
    """
    if title:
        console.print(f"[bold]{_escape_markup(title)}[/bold]")

    for i, row in enumerate(norm_rows):
        if i > 0:
            # Light separator between rows
            console.rule(style="dim")
        if isinstance(row, dict):
            panel_data = {col: row.get(col) for col in visible_columns}
            _render_panel(panel_data, console=console, title=None)
        else:
            console.print(_cell(row))


def _render_panel(data: dict[str, object], *, console: Console, title: str | None) -> None:
    """Render a single dict as a Rich Panel with key: value lines.

    Nested dict and list values are expanded recursively via
    :func:`_format_nested` so they appear as readable indented blocks rather
    than raw Python repr strings.  Scalar values are rendered via
    :func:`_cell` as before.
    """
    lines = "\n".join(
        f"[bold]{_escape_markup(k)}[/bold]: {_format_nested(v)}" for k, v in data.items()
    )
    panel = Panel(lines, title=title)
    console.print(panel)


def render_permissions_table(
    accesses: Sequence[ItemAccess],
    *,
    title: str,
    json_output: bool = False,
    console: Console | None = None,
) -> None:
    """Render a sequence of :class:`~fabric_dw.models.ItemAccess` objects.

    Routes through the central rendering infrastructure so both JSON and table
    output share a single entry point.

    Args:
        accesses: The list of item access records to display.
        title: Table title shown in the Rich header.
        json_output: When *True*, emit indented JSON via ``click.echo`` using
            :func:`render`.  When *False*, render a Rich table.
        console: Optional Rich console; ignored when *json_output=True*.
    """
    if json_output:
        render(
            [a.model_dump(by_alias=True, mode="json") for a in accesses],
            json_output=True,
        )
        return

    con = console if console is not None else _DEFAULT_CONSOLE
    if title:
        con.print(f"[bold]{_escape_markup(title)}[/bold]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Display Name", no_wrap=True)
    table.add_column("UPN / App ID")
    table.add_column("Type")
    table.add_column("Permissions")
    table.add_column("Additional Permissions")

    for entry in accesses:
        p = entry.principal
        display = _escape_markup(p.display_name or "")
        identity = _escape_markup(
            p.user_principal_name or (str(p.aad_app_id) if p.aad_app_id else "")
        )
        ptype = _escape_markup(p.type)
        perms = _escape_markup(", ".join(entry.item_access_details.permissions))
        additional = _escape_markup(", ".join(entry.item_access_details.additional_permissions))
        table.add_row(display, identity, ptype, perms, additional)

    con.print(table)


def render_refresh_table(
    statuses: list[TableSyncStatus], *, console: Console | None = None
) -> None:
    """Render a list of :class:`~fabric_dw.models.TableSyncStatus` as a Rich table."""
    con = console if console is not None else _DEFAULT_CONSOLE
    con.print("[bold]Metadata Refresh Results[/bold]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Table", no_wrap=True)
    table.add_column("Status")
    table.add_column("End Time")
    table.add_column("Error", max_width=ERROR_MAX_LEN)

    for s in statuses:
        status_text = _escape_markup(s.status)
        style = STATUS_STYLES.get(s.status, "")
        end_dt = _escape_markup(s.end_date_time.isoformat() if s.end_date_time else "")

        error_text = ""
        if s.error:
            parts = []
            if s.error.error_code:
                parts.append(s.error.error_code)
            if s.error.message:
                parts.append(s.error.message)
            error_text = _escape_markup(": ".join(parts))

        table.add_row(
            _escape_markup(s.table_name),
            f"[{style}]{status_text}[/{style}]" if style else status_text,
            end_dt,
            error_text,
        )

    con.print(table)


def _is_guid_column_values(values: list[object]) -> bool:
    """Return *True* when all non-``None`` entries in *values* are GUIDs.

    Used by :func:`_render_positional_table` to apply the same GUID-column
    width heuristic as :func:`_is_guid_column`, but working directly on a
    pre-collected list of values rather than through dict row lookups.
    """
    found = False
    for val in values:
        if val is None:
            continue
        found = True
        if not _GUID_RE.fullmatch(str(val)):
            return False
    return found


def _render_positional_vertical(
    rows: Sequence[Sequence[object]],
    columns: list[str],
    visible: list[int],
    *,
    console: Console,
    title: str | None,
) -> None:
    """Render *rows* as a sequence of vertical key-value panels, index-based.

    Extracted from :func:`_render_positional_table` so that the parent
    function stays within the branch-count budget.  Duplicate column names
    are preserved because cell values are looked up by position, not by key.
    """
    if title:
        console.print(f"[bold]{_escape_markup(title)}[/bold]")
    for j, row in enumerate(rows):
        if j > 0:
            console.rule(style="dim")
        lines = "\n".join(
            f"[bold]{_escape_markup(columns[i])}[/bold]: {_cell(row[i] if i < len(row) else None)}"
            for i in visible
        )
        console.print(Panel(lines))


def _render_positional_table(
    columns: list[str],
    rows: Sequence[Sequence[object]],
    *,
    console: Console,
    title: str | None,
    prune_null_columns: bool = True,
) -> None:
    """Render *columns* and *rows* as a Rich table using positional access.

    Unlike :func:`_render_table`, column headers come directly from *columns*
    and cell values are accessed by index, so duplicate column names
    (e.g. ``SELECT 1 AS id, 2 AS id``) each keep their own header and value
    instead of being collapsed by dict keying.

    When *prune_null_columns* is *True* (default), columns whose value is
    ``None`` in every row are omitted.  Pass *False* to keep all columns,
    e.g. for raw SQL output where every column must appear regardless of
    nullability.

    The wide-table vertical fallback uses the same heuristic as
    :func:`_render_table`.
    """
    if not rows:
        if title:
            console.print(f"[bold]{_escape_markup(title)}[/bold]")
        console.print(Table(show_header=True, header_style="bold"))
        return

    n = len(columns)

    # Pre-collect values per column position for GUID detection and null pruning.
    col_values: list[list[object]] = [
        [row[i] if i < len(row) else None for row in rows] for i in range(n)
    ]

    # Drop positions where every row has None, unless the caller opts out.
    all_null_idx: set[int] = (
        {i for i, vals in enumerate(col_values) if all(v is None for v in vals)}
        if prune_null_columns
        else set()
    )
    visible: list[int] = [i for i in range(n) if i not in all_null_idx]

    # Estimate horizontal fit using the same heuristic as _table_fits().
    primary_guid_assigned = False
    content_width = 0
    for i in visible:
        if _is_guid_column_values(col_values[i]):
            # Primary GUID gets full width; additional GUIDs get a smaller floor.
            content_width += _GUID_WIDTH if not primary_guid_assigned else _GUID_SECONDARY_WIDTH
            primary_guid_assigned = True
        else:
            content_width += min(len(columns[i]), _HEADER_MAX_WIDTH)
    border_chars = 3 * len(visible) + 1

    if content_width + border_chars > console.width:
        _render_positional_vertical(rows, columns, visible, console=console, title=title)
        return

    if title:
        console.print(f"[bold]{_escape_markup(title)}[/bold]")
    table = Table(show_header=True, header_style="bold")

    primary_guid_assigned = False
    for i in visible:
        escaped_col = _escape_markup(columns[i])
        if _is_guid_column_values(col_values[i]) and not primary_guid_assigned:
            table.add_column(escaped_col, no_wrap=True, min_width=_GUID_WIDTH)
            primary_guid_assigned = True
        else:
            table.add_column(escaped_col)

    for row in rows:
        table.add_row(*[_cell(row[i] if i < len(row) else None) for i in visible])

    console.print(table)


def render_result_rows(
    columns: list[str],
    rows: Sequence[Sequence[object]],
    *,
    json_output: bool,
    console: Console | None = None,
    table_title: str | None = None,
    prune_null_columns: bool = False,
) -> None:
    """Render SQL result columns and rows, preserving duplicate column names.

    Unlike :func:`render`, this function takes *columns* and *rows*
    separately and renders them positionally for the Rich table path, so
    duplicate column names (e.g. ``SELECT 1 AS id, 2 AS id``) each keep
    their own header and value.

    For JSON output the rows are zipped into per-row dicts; duplicate column
    names follow standard dict semantics (last value wins), which matches the
    behaviour of the previous dict-based rendering.

    Args:
        columns: Ordered list of column names as returned by the query.
        rows: Ordered list of rows; each row is a sequence of values aligned
            positionally with *columns*.
        json_output: When *True*, emit indented JSON.  When *False*, render a
            Rich table via :func:`_render_positional_table`.
        console: Optional Rich Console instance.  Ignored when
            *json_output=True*.
        table_title: Optional title shown above the Rich table.  Ignored when
            *json_output=True*.
        prune_null_columns: When *True*, columns whose value is ``None`` in
            every row are omitted from the human-readable table.  Defaults to
            *False* so raw SQL output retains every column regardless of
            nullability.  Ignored when *json_output=True*.
    """
    if json_output:
        click.echo(
            _json.dumps(
                [dict(zip(columns, row, strict=False)) for row in rows],
                indent=2,
                default=str,
            )
        )
        return

    con = console if console is not None else _DEFAULT_CONSOLE
    _render_positional_table(
        columns,
        list(rows),
        console=con,
        title=table_title,
        prune_null_columns=prune_null_columns,
    )


def confirm(message: str, *, yes: bool) -> bool:
    """Ask the user for confirmation, skipping the prompt when *yes=True*.

    Args:
        message: The confirmation message shown to the user.
        yes: When *True*, return ``True`` immediately without prompting.

    Returns:
        ``True`` if the action should proceed, ``False`` otherwise.
    """
    if yes:
        return True
    return click.confirm(message, default=False)
