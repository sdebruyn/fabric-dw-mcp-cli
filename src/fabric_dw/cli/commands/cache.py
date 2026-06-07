"""Cache sub-commands: clear."""

from __future__ import annotations

import click

from fabric_dw.cache import LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._render import confirm


@click.group("cache")
def cache_group() -> None:
    """Manage the local name-to-UUID lookup cache."""


@cache_group.command("clear")
@click.pass_obj
def clear(ctx: CliContext) -> None:
    """Clear all cached entries."""
    if confirm("Clear the entire lookup cache?", yes=ctx.yes):
        cache = LookupCache()
        cache.clear()
        click.echo("Cache cleared.")
    else:
        click.echo("Aborted.")
