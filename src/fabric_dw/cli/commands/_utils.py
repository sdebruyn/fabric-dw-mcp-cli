"""Shared helpers used by all per-noun CLI command modules."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast
from uuid import UUID

import anyio
import click

from fabric_dw import auth as _auth
from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.exceptions import ConfigError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.identifiers import parse_qualified_name as _parse_qn
from fabric_dw.resolver import Resolver
from fabric_dw.sql import SqlTarget

if TYPE_CHECKING:
    from fabric_dw.cli._context import CliContext

_P = ParamSpec("_P")
_R = TypeVar("_R")

# ---------------------------------------------------------------------------
# Note on SQL Analytics Endpoint DDL support
# ---------------------------------------------------------------------------
# CREATE/DROP SCHEMA, CREATE/ALTER/DROP VIEW, and CREATE/ALTER/DROP PROCEDURE
# are all explicitly supported on SQL Analytics Endpoints per the Microsoft
# Fabric T-SQL reference (Applies-to: "SQL analytics endpoint in Microsoft
# Fabric").  Only table DDL and DML (CREATE/DROP/TRUNCATE TABLE, INSERT/
# UPDATE/DELETE) are Warehouse-only.  No client-side guard is needed for
# schema or view operations.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Async-command runner
# ---------------------------------------------------------------------------


def coro(f: Callable[_P, Coroutine[None, None, _R]]) -> Callable[_P, _R]:
    """Wrap an async Click command so it runs via anyio.run."""

    @wraps(f)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        async def _inner() -> _R:
            return await f(*args, **kwargs)

        return anyio.run(_inner)

    return wrapper


# Private alias kept for backward compatibility with existing imports.
_coro = coro


# ---------------------------------------------------------------------------
# HTTP client context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def build_http_client(ctx: CliContext) -> AsyncIterator[FabricHttpClient]:
    """Yield an authenticated :class:`FabricHttpClient` for *ctx*.

    Centralises the ``get_credential(ctx.auth)`` + ``FabricHttpClient(credential)``
    pattern that was previously duplicated in every command module.

    Raises:
        click.UsageError: When ``get_credential`` raises :class:`~fabric_dw.exceptions.ConfigError`
            (e.g. missing environment variables or an unrecognised credential mode).
            This ensures the error surfaces as a clean CLI message rather than a raw
            traceback, even though ``ConfigError`` is not a subtype of ``FabricError``.
    """
    try:
        credential = _auth.get_credential(ctx.auth)
    except ConfigError as exc:
        raise click.UsageError(str(exc)) from exc
    async with FabricHttpClient(credential) as http:
        yield http


# ---------------------------------------------------------------------------
# Resolver / LookupCache helpers
# ---------------------------------------------------------------------------


def make_resolver(http: FabricHttpClient) -> tuple[Resolver, LookupCache]:
    """Return a fresh ``(Resolver, LookupCache)`` pair for *http*."""
    cache = LookupCache()
    resolver = Resolver(http=http, cache=cache)
    return resolver, cache


async def resolve_item(
    http: FabricHttpClient,
    workspace: str,
    item: str,
) -> tuple[UUID, ItemEntry]:
    """Resolve workspace and item names/GUIDs to UUIDs + item entry."""
    resolver, _ = make_resolver(http)
    ws_id = await resolver.workspace_id(workspace)
    entry = await resolver.item(workspace, item)
    return ws_id, entry


async def resolve_item_with_cache(
    http: FabricHttpClient,
    workspace: str,
    item: str,
) -> tuple[UUID, ItemEntry, LookupCache]:
    """Resolve workspace and item names/GUIDs and return the shared cache instance.

    Use this variant when the caller needs the cache for subsequent eviction or
    population (e.g. after rename or delete).
    """
    resolver, cache = make_resolver(http)
    ws_id = await resolver.workspace_id(workspace)
    entry = await resolver.item(workspace, item)
    return ws_id, entry, cache


async def resolve_workspace_id(http: FabricHttpClient, workspace: str) -> UUID:
    """Resolve a workspace name or GUID to a UUID."""
    resolver, _ = make_resolver(http)
    return await resolver.workspace_id(workspace)


# Private aliases kept for backward compatibility with existing imports.
_resolve_item = resolve_item
_resolve_item_with_cache = resolve_item_with_cache


# ---------------------------------------------------------------------------
# SqlTarget builder
# ---------------------------------------------------------------------------


async def build_sql_target(
    http: FabricHttpClient,
    workspace: str,
    item: str,
) -> tuple[SqlTarget, ItemEntry]:
    """Resolve workspace + item and build a :class:`SqlTarget`.

    Raises:
        click.ClickException: If the resolved item has no connection string.
    """
    ws_id, entry = await resolve_item(http, workspace, item)
    if entry.connection_string is None:
        raise click.ClickException(f"Item {entry.display_name!r} has no connection string.")
    target = SqlTarget(
        workspace_id=str(ws_id),
        database=entry.display_name,
        connection_string=entry.connection_string,
    )
    return target, entry


# ---------------------------------------------------------------------------
# Qualified-name / SELECT-body helpers
# ---------------------------------------------------------------------------


def parse_qualified_name(qualified_name: str, *, kind: str = "object") -> tuple[str, str]:
    """Split ``<schema>.<object>`` into ``(schema, name)``.

    Wraps :func:`fabric_dw.identifiers.parse_qualified_name` with a
    :class:`click.UsageError` so the CLI shows a friendly message.

    Args:
        qualified_name: The qualified name string to parse.
        kind: Human-readable label used in the error message (default ``"object"``).

    Raises:
        click.UsageError: If *qualified_name* does not contain a dot.
    """
    try:
        return _parse_qn(qualified_name)
    except ValueError:
        raise click.UsageError(f"Expected <schema>.{kind}, got {qualified_name!r}") from None


def load_select_body(select: str | None, from_file: str | None) -> str:
    """Return the SELECT body from the inline option or file option.

    Raises:
        click.UsageError: If neither or both are provided, or file is missing.
    """
    if select and from_file:
        raise click.UsageError("Provide either --select or --from-file, not both.")
    if from_file:
        path = Path(from_file)
        if not path.is_file():
            raise click.UsageError(f"File not found: {from_file}")
        return path.read_text(encoding="utf-8-sig").strip()
    if select:
        return select
    raise click.UsageError("Provide --select or --from-file.")


# ---------------------------------------------------------------------------
# ISO datetime parser
# ---------------------------------------------------------------------------


def parse_iso_datetime(value: str, param_name: str, *, assume_utc: bool = True) -> datetime:
    """Parse an ISO-8601 datetime string, optionally normalising to UTC.

    Args:
        value: The raw string supplied by the user.
        param_name: Flag/option name shown in the error message (e.g. ``"--since"``).
        assume_utc: When *True* (default), naïve datetimes (no tzinfo) are treated
            as UTC.  When *False*, they are returned as-is.

    Raises:
        click.UsageError: If *value* is not a valid ISO-8601 string.
    """
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise click.UsageError(
            f"invalid {param_name} {value!r}: expected ISO-8601 (e.g. 2024-01-01T00:00:00)"
        ) from exc
    if assume_utc:
        dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    _DT_YEAR_MIN = 2000  # noqa: N806
    _DT_YEAR_MAX = 2100  # noqa: N806
    # Sanity range check: reject obviously wrong years (e.g. epoch 0 or year 9999).
    if not (_DT_YEAR_MIN <= dt.year <= _DT_YEAR_MAX):
        raise click.UsageError(
            f"invalid {param_name} {value!r}: year {dt.year} is out of the expected range "
            "(2000-2100). Check the timestamp."
        )
    return dt


# ---------------------------------------------------------------------------
# Destructive-operation confirmation helper
# ---------------------------------------------------------------------------


def confirm_destructive(prompt_text: str, *, yes: bool) -> bool:
    """Prompt the user to confirm a destructive operation.

    Prints a WARNING preamble to *stderr* then asks for confirmation.

    Policy: declining a destructive prompt is NOT an error — the user changed
    their mind.  Callers should treat a ``False`` return as a clean no-op and
    exit 0 (print "Aborted." and return).  Only genuine service/network errors
    should exit non-zero.

    Args:
        prompt_text: The full confirmation prompt string (shown after the preamble).
        yes: When *True*, skip the prompt entirely (non-interactive / ``--yes`` mode).

    Returns:
        ``True`` when the operation should proceed, ``False`` when the user
        declined (never raises).
    """
    if yes:
        return True
    click.echo(f"\nWARNING: {prompt_text}\n", err=True)
    return click.confirm("Proceed?", default=False)


# ---------------------------------------------------------------------------
# Typed context accessor
# ---------------------------------------------------------------------------


def get_ctx(click_ctx: click.Context) -> CliContext:
    """Cast *click_ctx.obj* to :class:`CliContext`.

    Centralises the ``cast(CliContext, ctx.obj)`` pattern so callers that use
    ``@click.pass_context`` can get a typed object without repeating the cast.
    """
    return cast("CliContext", click_ctx.obj)


# ---------------------------------------------------------------------------
# Workspace / warehouse arg resolvers
# ---------------------------------------------------------------------------


def resolve_workspace_arg(ctx: CliContext, value: str | None) -> str:
    """Resolve the workspace argument using the priority order.

    1. Explicit positional arg (*value*).
    2. ``FABRIC_DW_DEFAULT_WORKSPACE`` environment variable.
    3. ``ctx.config.defaults.workspace`` from the config file.
    4. Neither → :class:`click.UsageError`.
    """
    if value is not None:
        return value
    env = os.environ.get("FABRIC_DW_DEFAULT_WORKSPACE")
    if env:
        return env
    cfg_val = ctx.config.defaults.workspace
    if cfg_val is not None:
        return cfg_val
    raise click.UsageError(
        "no workspace specified; pass as argument or run 'fabric-dw config set workspace ...'"
    )


def resolve_warehouse_arg(ctx: CliContext, value: str | None) -> str:
    """Resolve the warehouse argument using the priority order.

    1. Explicit positional arg (*value*).
    2. ``FABRIC_DW_DEFAULT_WAREHOUSE`` environment variable.
    3. ``ctx.config.defaults.warehouse`` from the config file.
    4. Neither → :class:`click.UsageError`.
    """
    if value is not None:
        return value
    env = os.environ.get("FABRIC_DW_DEFAULT_WAREHOUSE")
    if env:
        return env
    cfg_val = ctx.config.defaults.warehouse
    if cfg_val is not None:
        return cfg_val
    raise click.UsageError(
        "no warehouse specified; pass as argument or run 'fabric-dw config set warehouse ...'"
    )


# ---------------------------------------------------------------------------
# --all-workspaces / WORKSPACE mutual-exclusion guard
# ---------------------------------------------------------------------------


def validate_workspace_or_all_workspaces(workspace: str | None, all_workspaces: bool) -> None:
    """Enforce the WORKSPACE / --all-workspaces contract.

    Either an explicit WORKSPACE or --all-workspaces must be given — but not
    both.  Raises :class:`click.UsageError` in both failure cases so callers
    get a consistent, friendly message regardless of which subcommand they
    invoke.

    Args:
        workspace: The positional WORKSPACE argument (or *None* if omitted).
        all_workspaces: Whether ``--all-workspaces`` / ``-A`` was passed.

    Raises:
        click.UsageError: If both or neither are provided.
    """
    if workspace and all_workspaces:
        raise click.UsageError("WORKSPACE and --all-workspaces are mutually exclusive.")
    if not workspace and not all_workspaces:
        raise click.UsageError("Provide WORKSPACE or pass --all-workspaces / -A.")
