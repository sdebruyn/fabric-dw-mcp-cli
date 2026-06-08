"""Rich + JSON rendering helpers for CLI output."""

from __future__ import annotations

import json as _json
from collections.abc import Sequence

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

__all__ = [
    "confirm",
    "render",
]

_DEFAULT_CONSOLE = Console()


def render(
    data: object,
    *,
    json_output: bool,
    console: Console | None = None,
    table_title: str | None = None,
) -> None:
    """Print *data* to stdout using JSON or Rich formatting.

    Args:
        data: The data to render. Supported shapes:
            - ``list[dict]`` → Rich Table (or JSON array).
            - ``dict`` → Rich Panel (or JSON object).
            - primitives → ``repr()`` string (or JSON scalar).
        json_output: When *True*, emit indented JSON via ``click.echo``.
            When *False*, use Rich for human-friendly output.
        console: Optional Rich Console instance. When *None* the module-level
            default console (stdout) is used. Ignored when *json_output=True*.
        table_title: Optional title shown above the Rich Table.
            Ignored when *json_output=True* or when *data* is not a list.
    """
    if json_output:
        click.echo(_json.dumps(data, indent=2, default=str))
        return

    con = console if console is not None else _DEFAULT_CONSOLE

    if isinstance(data, list):
        _render_table(data, console=con, title=table_title)
    elif isinstance(data, dict):
        _render_panel({str(k): v for k, v in data.items()}, console=con, title=table_title)
    else:
        click.echo(repr(data))


def _cell(value: object) -> str:
    """Convert a cell value to a Rich-markup string.

    ``None`` is rendered as ``[dim]NULL[/dim]`` so SQL NULLs are visually
    distinct from the literal string ``'None'``.
    """
    if value is None:
        return "[dim]NULL[/dim]"
    return str(value)


def _render_table(rows: Sequence[object], *, console: Console, title: str | None) -> None:
    """Render a list of dicts as a Rich Table."""
    table = Table(title=title, show_header=True, header_style="bold")

    if not rows:
        console.print(table)
        return

    # Collect all column names in insertion order (union of all keys)
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            row_dict: dict[str, object] = {str(k): v for k, v in row.items()}
            for key in row_dict:
                if key not in seen:
                    columns.append(key)
                    seen.add(key)

    for col in columns:
        table.add_column(col)

    for row in rows:
        if isinstance(row, dict):
            row_dict = {str(k): v for k, v in row.items()}
            table.add_row(*[_cell(row_dict.get(col, "")) for col in columns])
        else:
            table.add_row(_cell(row))

    console.print(table)


def _render_panel(data: dict[str, object], *, console: Console, title: str | None) -> None:
    """Render a single dict as a Rich Panel with key: value lines."""
    lines = "\n".join(f"[bold]{k}[/bold]: {_cell(v)}" for k, v in data.items())
    panel = Panel(lines, title=title)
    console.print(panel)


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
