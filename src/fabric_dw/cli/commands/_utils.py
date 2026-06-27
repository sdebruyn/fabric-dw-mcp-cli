"""Shared helpers used by all per-noun CLI command modules."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast
from uuid import UUID

import anyio
import click

from fabric_dw import auth as _auth
from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.config_resolve import resolve_int_knob
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


# ---------------------------------------------------------------------------
# HTTP client context manager
# ---------------------------------------------------------------------------


_logger_utils = logging.getLogger("fabric_dw.cli.utils")

# Minimum accepted value for retry_deadline_s — enforced in both CLI
# (click.IntRange) and env-var / config-file fallback paths.
_MIN_RETRY_DEADLINE_S: int = 1


def _resolve_max_429_retries(ctx: CliContext) -> int | None:
    """Resolve effective max_429_retries with precedence CLI > env > config > None.

    Returns *None* when no source supplies a value, letting the HTTP client use
    its own built-in default (10).  Malformed env/config values are logged and
    skipped so a bad stored value never blocks all requests.
    """
    return resolve_int_knob(
        cli_value=ctx.max_429_retries,
        env_key="FABRIC_DW_MAX_429_RETRIES",
        config_value=ctx.config.defaults.max_429_retries,
        min_val=1,
        knob_name="max_429_retries",
    )


def _resolve_retry_deadline_s(ctx: CliContext) -> int | None:
    """Resolve effective retry_deadline_s with precedence CLI > env > config > None.

    Returns *None* when no source supplies a value, letting the HTTP client use
    its own built-in default (300).  Malformed or out-of-range env/config values
    are logged and skipped.  Float-formatted integers (e.g. ``"300.0"``) from env
    vars are accepted via ``int(float(...))``.
    """
    return resolve_int_knob(
        cli_value=ctx.retry_deadline_s,
        env_key="FABRIC_DW_RETRY_DEADLINE_S",
        config_value=ctx.config.defaults.retry_deadline_s,
        min_val=_MIN_RETRY_DEADLINE_S,
        knob_name="retry-deadline",
    )


@asynccontextmanager
async def build_http_client(ctx: CliContext) -> AsyncIterator[FabricHttpClient]:
    """Yield an authenticated :class:`FabricHttpClient` for *ctx*.

    Centralises the ``get_credential(ctx.auth)`` + ``FabricHttpClient(credential)``
    pattern that was previously duplicated in every command module.

    The retry budget is resolved with precedence CLI option > env var
    (``FABRIC_DW_MAX_429_RETRIES`` / ``FABRIC_DW_RETRY_DEADLINE_S``) >
    config file (``[defaults] max_429_retries`` / ``retry_deadline_s``) >
    built-in default (10 / 300).  Malformed or out-of-range env/config values
    are logged and skipped; the next source in the chain is tried.

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

    retries = _resolve_max_429_retries(ctx)
    deadline = _resolve_retry_deadline_s(ctx)

    # Pass only the kwargs that were explicitly resolved so the HTTP client
    # can apply its own built-in defaults for the rest.
    client = FabricHttpClient(
        credential,
        **({"max_429_retries": retries} if retries is not None else {}),
        **({"combined_deadline_s": deadline} if deadline is not None else {}),
    )
    async with client as http:
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

    Adapter around :func:`fabric_dw.identifiers.parse_qualified_name` that
    converts :class:`ValueError` to :class:`click.UsageError` so the CLI
    shows a friendly message.

    Args:
        qualified_name: The qualified name string to parse.
        kind: Human-readable label for the object type used in the error
            message (default ``"object"``).

    Raises:
        click.UsageError: If *qualified_name* is invalid (missing dot, empty
            or whitespace-only schema/object part).
    """
    try:
        return _parse_qn(qualified_name, kind)
    except ValueError:
        raise click.UsageError(f"Expected <schema>.{kind}, got {qualified_name!r}") from None


def load_sql_body(
    inline: str | None,
    from_file: str | None,
    *,
    inline_opt: str = "--select",
    file_opt: str = "--from-file",
) -> str:
    """Return the SQL body from the inline option or file option.

    Args:
        inline: The inline SQL text (e.g. from ``--select`` or ``--body``).
        from_file: Path to a ``.sql`` file.
        inline_opt: Name of the inline option used in error messages.
        file_opt: Name of the file option used in error messages.

    Raises:
        click.UsageError: If neither or both are provided, or file is missing.
    """
    if inline and from_file:
        raise click.UsageError(f"Provide either {inline_opt} or {file_opt}, not both.")
    if from_file:
        path = Path(from_file)
        if not path.is_file():
            raise click.UsageError(f"File not found: {from_file}")
        return path.read_text(encoding="utf-8-sig").strip()
    if inline:
        return inline
    raise click.UsageError(f"Provide {inline_opt} or {file_opt}.")


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


def parse_iso_optional(value: str | None, param_name: str) -> datetime | None:
    """Parse an optional ISO-8601 string; return *None* when *value* is *None*.

    Convenience wrapper used by ``queries`` and ``sql-pools insights`` commands
    for ``--since``/``--until`` options.

    Raises:
        click.UsageError: If *value* is set but is not a valid ISO-8601 string.
    """
    if value is None:
        return None
    return parse_iso_datetime(value, param_name, assume_utc=False)


# ---------------------------------------------------------------------------
# Relative-duration parser and --ago resolver
# ---------------------------------------------------------------------------

_UNIT_RE = re.compile(r"(\d+)([a-zA-Z]+)")
_VALID_UNITS: frozenset[str] = frozenset("smhdw")
_DURATION_EXAMPLES = "e.g. 1h, 90m, 3600s, 2d, 1w, 1h30m, 2d12h"
_UNIT_SECONDS: dict[str, int] = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(value: str) -> timedelta:
    """Parse a relative duration string into a :class:`~datetime.timedelta`.

    Supported units: ``s`` (seconds), ``m`` (minutes), ``h`` (hours),
    ``d`` (days), ``w`` (weeks).  Both single-unit (``1h``, ``90m``) and
    compound (``1h30m``, ``2d12h``) forms are accepted.

    Args:
        value: The raw duration string supplied by the user.

    Returns:
        A :class:`~datetime.timedelta` representing the duration.

    Raises:
        click.UsageError: If *value* is empty, contains an unknown unit,
            uses a fractional coefficient, is negative, or evaluates to zero.
    """
    stripped = value.strip()
    if not stripped:
        raise click.UsageError(f"--ago requires a non-empty duration ({_DURATION_EXAMPLES}).")
    if stripped.startswith("-"):
        raise click.UsageError(
            f"--ago duration must be positive; got {value!r} ({_DURATION_EXAMPLES})."
        )
    if re.search(r"\d+\.\d+", stripped):
        raise click.UsageError(
            f"--ago does not support fractional values; got {value!r} ({_DURATION_EXAMPLES})."
        )

    tokens = _UNIT_RE.findall(stripped)

    if not tokens:
        raise click.UsageError(
            f"--ago value {value!r} has no recognised unit; use s/m/h/d/w ({_DURATION_EXAMPLES})."
        )

    # Ensure the full string is covered by the recognised (number, unit) pairs.
    reconstructed = "".join(n + u for n, u in tokens)
    if reconstructed != stripped:
        raise click.UsageError(
            f"--ago value {value!r} is not a valid duration ({_DURATION_EXAMPLES})."
        )

    total_seconds = 0
    seen_units: set[str] = set()
    for num_str, unit in tokens:
        if unit not in _VALID_UNITS:
            raise click.UsageError(
                f"--ago unknown unit {unit!r} in {value!r}; use s/m/h/d/w ({_DURATION_EXAMPLES})."
            )
        if unit in seen_units:
            raise click.UsageError(
                f"--ago duplicate unit {unit!r} in {value!r}; each unit may appear at most once "
                f"({_DURATION_EXAMPLES})."
            )
        seen_units.add(unit)
        total_seconds += int(num_str) * _UNIT_SECONDS[unit]

    if total_seconds == 0:
        raise click.UsageError(
            f"--ago duration must be greater than zero; got {value!r} ({_DURATION_EXAMPLES})."
        )

    return timedelta(seconds=total_seconds)


def resolve_since(since: str | None, ago: str | None) -> datetime | None:
    """Resolve the effective lower-bound datetime from ``--since`` and ``--ago``.

    Exactly one of *since* or *ago* may be provided.  When *ago* is given the
    result is a tz-aware UTC datetime equal to ``datetime.now(UTC) - parse_duration(ago)``.

    Args:
        since: Raw ISO-8601 string from ``--since`` (or *None*).
        ago: Raw duration string from ``--ago`` (or *None*).

    Returns:
        A :class:`~datetime.datetime` (tz-aware UTC when *ago* is used;
        tz as supplied by *since* otherwise) or *None* when both are absent.

    Raises:
        click.UsageError: If both *since* and *ago* are set, or if either
            value is invalid.
    """
    if since is not None and ago is not None:
        raise click.UsageError("--since and --ago are mutually exclusive.")
    if ago is not None:
        return datetime.now(UTC) - parse_duration(ago)
    if since is not None:
        return parse_iso_optional(since, "--since")
    return None


#: Shared Click option for ``--ago`` used by query-insights commands.
AGO_OPTION = click.option(
    "--ago",
    default=None,
    metavar="DURATION",
    help=(
        "Relative alternative to --since: return rows newer than now minus this duration "
        "(e.g. 1h, 90m, 3600s, 2d). Mutually exclusive with --since."
    ),
)

#: Shared Click option for ``--limit`` used by query-insights commands.
LIMIT_OPTION = click.option(
    "--limit",
    default=100,
    show_default=True,
    type=click.IntRange(1, 10_000),
    help="Maximum number of rows to return (1-10 000).",
)

#: Shared Click option for ``--since`` used by query-insights commands.
SINCE_OPTION = click.option(
    "--since",
    default=None,
    metavar="ISO8601",
    help="Return rows with timestamp >= this value (ISO-8601).",
)

#: Shared Click option for ``--until`` used by query-insights commands.
UNTIL_OPTION = click.option(
    "--until",
    default=None,
    metavar="ISO8601",
    help="Return rows with timestamp <= this value (ISO-8601).",
)


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


def _workspace_default(ctx: CliContext) -> str | None:
    """Return the configured-default workspace, env var first then config file.

    Shared by :func:`resolve_workspace_arg` and :func:`resolve_workspace`:
    consults ``FABRIC_DW_DEFAULT_WORKSPACE`` then ``ctx.config.defaults.workspace``.
    Returns *None* when neither is set.
    """
    env = os.environ.get("FABRIC_DW_DEFAULT_WORKSPACE")
    if env:
        return env
    return ctx.config.defaults.workspace


def resolve_workspace_arg(ctx: CliContext, value: str | None) -> str:
    """Resolve the workspace argument using the priority order.

    1. Explicit positional arg (*value*).
    2. ``FABRIC_DW_DEFAULT_WORKSPACE`` environment variable.
    3. ``ctx.config.defaults.workspace`` from the config file.
    4. Neither → :class:`click.UsageError`.
    """
    if value is not None:
        return value
    default = _workspace_default(ctx)
    if default is not None:
        return default
    raise click.UsageError(
        "no workspace specified; pass one as an argument, or set a persistent default"
        " with 'fabric-dw config set workspace <name|id>'"
    )


def resolve_workspace(ctx: CliContext) -> str:
    """Resolve the target workspace for the global ``-w/--workspace`` option.

    Precedence:

    1. Explicit ``-w/--workspace`` (``ctx.workspace``) if not *None*.
    2. ``FABRIC_DW_DEFAULT_WORKSPACE`` environment variable.
    3. ``ctx.config.defaults.workspace`` from the config file.
    4. None of the above → :class:`click.UsageError`.

    Raises:
        click.UsageError: If no workspace can be resolved from any source.
    """
    if ctx.workspace is not None:
        return ctx.workspace
    default = _workspace_default(ctx)
    if default is not None:
        return default
    raise click.UsageError(
        "no workspace specified; pass -w/--workspace <name|id>, or set a persistent default"
        " with 'fabric-dw config set workspace <name|id>'"
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
        "no warehouse specified; pass one as an argument, or set a persistent default"
        " with 'fabric-dw config set warehouse <name|id>'"
        " (accepts a warehouse or SQL Analytics Endpoint)"
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


def validate_workspace_option_or_all_workspaces(
    explicit_workspace: str | None, all_workspaces: bool
) -> None:
    """Enforce the ``-w/--workspace`` / ``-A/--all-workspaces`` contract.

    Companion to :func:`validate_workspace_or_all_workspaces` for commands that
    take the global ``-w`` option rather than a positional WORKSPACE.  An
    **explicit** ``-w`` (``ctx.workspace is not None``) is mutually exclusive
    with ``-A``; a configured *default* workspace must NOT conflict with ``-A``
    (so ``-A`` always wins over a default and only the explicit flag clashes).

    Unlike :func:`validate_workspace_or_all_workspaces`, this helper does NOT
    require one of the two to be present: neither explicit ``-w`` nor ``-A`` is
    valid because a configured default may still supply the workspace later.

    Args:
        explicit_workspace: ``ctx.workspace`` — the explicit ``-w`` value, or
            *None* when ``-w`` was not passed.
        all_workspaces: Whether ``--all-workspaces`` / ``-A`` was passed.

    Raises:
        click.UsageError: If both an explicit ``-w`` and ``-A`` are given.
    """
    if explicit_workspace is not None and all_workspaces:
        raise click.UsageError("-w/--workspace and --all-workspaces / -A are mutually exclusive.")
