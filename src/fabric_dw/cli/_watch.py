"""Shared ``--watch`` loop for CLI commands that support live refresh.

Extracted from ``fabric_dw.cli.commands.queries`` so that ``queries`` and
``sql exec`` run exactly one loop implementation instead of two copies.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import click

from fabric_dw.cli._context import CliContext

__all__ = ["validate_watch", "watch_loop"]


def validate_watch(ctx: CliContext, watch: int | None) -> None:
    """Reject streaming JSON before opening a network client."""
    if watch is not None and ctx.json_output:
        raise click.UsageError("--watch cannot be used with --json.")


async def watch_loop(
    *,
    interval: int | None,
    command: str,
    tick: Callable[[], Awaitable[None]],
) -> None:
    """Run *tick* once, then repeat every *interval* seconds if set.

    Each cycle clears the terminal and prints a watch-style header
    (``Every Ns: <command>    <local timestamp>``) before invoking *tick*,
    which is responsible for fetching and rendering that cycle's output.
    When *interval* is ``None``, *tick* runs exactly once and the terminal
    is left untouched.

    Args:
        interval: Seconds between refreshes, or ``None`` to run *tick* once.
        command: Command name shown in the watch header.
        tick: Async callback that fetches and renders one cycle's output.
            Any exception it raises propagates to the caller.
    """
    while True:
        if interval is not None:
            click.clear()
            timestamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            click.echo(f"Every {interval}s: {command}    {timestamp}")
            click.echo()
        await tick()
        if interval is None:
            return
        await asyncio.sleep(interval)
