"""Shared ``--watch`` loop for CLI commands that support live refresh.

Extracted from ``fabric_dw.cli.commands.queries`` so that ``queries`` and
``sql exec`` run exactly one loop implementation instead of two copies.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

import click

__all__ = ["validate_watch", "watch_loop"]

_T = TypeVar("_T")


def validate_watch(*, json_output: bool, watch: int | None) -> None:
    """Reject streaming JSON before opening a network client."""
    if watch is not None and json_output:
        raise click.UsageError("--watch cannot be used with --json.")


async def watch_loop(
    *,
    interval: int | None,
    command: str,
    fetch: Callable[[], Awaitable[_T]],
    render: Callable[[_T], None],
) -> None:
    """Fetch and render once, then repeat every *interval* seconds if set.

    Each cycle fetches first, then -- only once fresh data is in hand -- clears
    the terminal and prints a watch-style header (``Every Ns: <command>
    <local timestamp>``), then renders. Fetching before clearing keeps the
    previous frame on screen for the full duration of a slow fetch instead of
    showing a blank terminal, and makes the header timestamp reflect when the
    data was read rather than when the tick started.

    When *interval* is ``None``, *fetch*/*render* run exactly once and the
    terminal is left untouched.

    Args:
        interval: Seconds between refreshes, or ``None`` to run once.
        command: Command name shown in the watch header.
        fetch: Async callback that retrieves one cycle's data. Any exception
            it raises propagates to the caller.
        render: Sync callback that renders the fetched data.
    """
    while True:
        item = await fetch()
        if interval is not None:
            click.clear()
            timestamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            click.echo(f"Every {interval}s: {command}    {timestamp}")
            click.echo()
        render(item)
        if interval is None:
            return
        await asyncio.sleep(interval)
