"""Rich + JSON rendering helpers for CLI output."""

from __future__ import annotations

import json as _json
import re
from collections.abc import Sequence

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fabric_dw.models import FABRIC_DEFAULT_COLLATION, ItemAccess, TableSyncStatus

__all__ = [
    "confirm",
    "render",
    "render_permissions_table",
    "render_refresh_table",
    "with_default_collation_for_display",
]

#: API alias for the warehouse / SQL-endpoint collation field.
_COLLATION_KEY = "defaultCollation"


def with_default_collation_for_display(dump: dict[str, object]) -> dict[str, object]:
    """Return a copy of a warehouse/SQL-endpoint dump with the collation filled in.

    When the Fabric REST API returns ``null``/empty for ``defaultCollation``
    (i.e. the item was created without an explicit collation), Fabric still
    applies an effective default — :data:`~fabric_dw.models.FABRIC_DEFAULT_COLLATION`.
    For **human** output we substitute that value, clearly marked as the default
    (``"<value> (default)"``), instead of showing a misleading ``NULL``.

    This helper is intended for the human/Rich rendering path **only**.  The
    ``--json`` path must keep the raw API value (``None``) so machine consumers
    are not fed a fabricated value; the ``(default)`` suffix would also break
    callers that compare the string against a real collation name.

    Args:
        dump: A warehouse/SQL-endpoint dict (``model_dump(by_alias=True)``).

    Returns:
        A shallow copy of *dump*.  If ``defaultCollation`` is present and
        null/empty, it is replaced with ``"<FABRIC_DEFAULT_COLLATION> (default)"``;
        otherwise *dump* is returned unchanged (as a copy).
    """
    result = dict(dump)
    if _COLLATION_KEY in result and not result.get(_COLLATION_KEY):
        result[_COLLATION_KEY] = f"{FABRIC_DEFAULT_COLLATION} (default)"
    return result


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
    """
    if json_output:
        click.echo(_json.dumps(data, indent=2, default=str))
        return

    con = console if console is not None else _DEFAULT_CONSOLE

    if isinstance(data, list):
        _render_table(data, console=con, title=table_title, drop_columns=drop_columns)
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
    """
    if value is None:
        return "[dim]NULL[/dim]"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


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


def _render_table(
    rows: Sequence[object],
    *,
    console: Console,
    title: str | None,
    drop_columns: tuple[str, ...] | list[str] | None = None,
) -> None:
    """Render a list of dicts as a Rich Table.

    Columns whose value is ``None`` in **every** row are omitted from the
    output — they only clutter list views (e.g. ``definition`` for
    ``procedures list``).  A column that is non-null in at least one row is
    kept, and any null cells in that column still render as ``[dim]NULL[/dim]``.

    Args:
        rows: The list of rows (dicts or scalars) to render.
        console: The Rich console to print to.
        title: Optional table title.
        drop_columns: Optional column names to explicitly omit, in addition to
            the automatic all-null pruning.  Used by callers to hide a column
            that is redundant in a given context (e.g. a shared workspace id).
    """
    dropped: frozenset[str] = frozenset(drop_columns or ())
    table = Table(title=title, show_header=True, header_style="bold")

    if not rows:
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

    # Drop columns where every dict-row's value is None (or the key is absent).
    # Non-dict rows (scalars) are never counted as "having" any column value, so
    # a column is only kept if at least one dict-row provides a non-None value.
    all_null = {col for col in columns if _column_is_all_null(col, norm_rows)}
    visible_columns = [col for col in columns if col not in all_null and col not in dropped]

    for col in visible_columns:
        if _is_guid_column(col, norm_rows):
            # Best-effort: GUID columns are prioritised, but a very narrow console
            # with multiple GUID columns may still overflow — Rich cannot fit all
            # of them without truncation in that extreme case.
            table.add_column(col, no_wrap=True, min_width=_GUID_WIDTH)
        else:
            table.add_column(col)

    for row in norm_rows:
        if isinstance(row, dict):
            table.add_row(*[_cell(row.get(col, "")) for col in visible_columns])
        else:
            table.add_row(_cell(row))

    console.print(table)


def _render_panel(data: dict[str, object], *, console: Console, title: str | None) -> None:
    """Render a single dict as a Rich Panel with key: value lines."""
    lines = "\n".join(f"[bold]{k}[/bold]: {_cell(v)}" for k, v in data.items())
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
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Display Name", no_wrap=True)
    table.add_column("UPN / App ID")
    table.add_column("Type")
    table.add_column("Permissions")
    table.add_column("Additional Permissions")

    for entry in accesses:
        p = entry.principal
        display = p.display_name or ""
        identity = p.user_principal_name or (str(p.aad_app_id) if p.aad_app_id else "")
        ptype = p.type
        perms = ", ".join(entry.item_access_details.permissions)
        additional = ", ".join(entry.item_access_details.additional_permissions)
        table.add_row(display, identity, ptype, perms, additional)

    con.print(table)


def render_refresh_table(
    statuses: list[TableSyncStatus], *, console: Console | None = None
) -> None:
    """Render a list of :class:`~fabric_dw.models.TableSyncStatus` as a Rich table."""
    con = console if console is not None else _DEFAULT_CONSOLE
    table = Table(title="Metadata Refresh Results", show_header=True, header_style="bold")
    table.add_column("Table", no_wrap=True)
    table.add_column("Status")
    table.add_column("End Time")
    table.add_column("Error", max_width=ERROR_MAX_LEN)

    for s in statuses:
        status_text = s.status
        style = STATUS_STYLES.get(s.status, "")
        end_dt = s.end_date_time.isoformat() if s.end_date_time else ""

        error_text = ""
        if s.error:
            parts = []
            if s.error.error_code:
                parts.append(s.error.error_code)
            if s.error.message:
                parts.append(s.error.message)
            error_text = ": ".join(parts)

        table.add_row(
            s.table_name,
            f"[{style}]{status_text}[/{style}]" if style else status_text,
            end_dt,
            error_text,
        )

    con.print(table)


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
