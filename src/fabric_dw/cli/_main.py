"""Click group definition for the fabric-dw CLI."""

from __future__ import annotations

import logging
import shutil
import sys
import time
from typing import Any

import click

from fabric_dw.auth import CredentialMode
from fabric_dw.cli._context import CliContext
from fabric_dw.cli.commands.audit import audit_group
from fabric_dw.cli.commands.cache import cache_group
from fabric_dw.cli.commands.completion import completion_group
from fabric_dw.cli.commands.config import config_group
from fabric_dw.cli.commands.dbt import dbt_group
from fabric_dw.cli.commands.functions import functions_group
from fabric_dw.cli.commands.procedures import procedures_group
from fabric_dw.cli.commands.queries import queries_group
from fabric_dw.cli.commands.restore_points import restore_points_group
from fabric_dw.cli.commands.schemas import schemas_group
from fabric_dw.cli.commands.snapshots import snapshots_group
from fabric_dw.cli.commands.sql import sql_group
from fabric_dw.cli.commands.sql_endpoints import sql_endpoints_group
from fabric_dw.cli.commands.sql_pools import sql_pools_group
from fabric_dw.cli.commands.statistics import statistics_group
from fabric_dw.cli.commands.tables import tables_group
from fabric_dw.cli.commands.views import views_group
from fabric_dw.cli.commands.warehouses import warehouses_group
from fabric_dw.cli.commands.workspaces import workspaces_group
from fabric_dw.logging import setup_logging
from fabric_dw.telemetry import (
    maybe_print_first_run_notice,
    record_app_exited,
    record_app_started,
    shutdown_telemetry,
)
from fabric_dw.telemetry_commands import (
    emit_command_invoked,
    map_status,
    now_ms,
)

_CLI_TELEMETRY_KEY = "fabric_dw_telemetry_command_name"
_CLI_SEGMENTS_KEY = _CLI_TELEMETRY_KEY + "_segments"

# Use actual terminal width so help text adapts to the user's screen.
# Floor of 80 preserves readable wrapping on narrow terminals and in CI
# (where get_terminal_size falls back to the (120, 24) default).
# Cap of 160 prevents absurdly long lines on ultra-wide monitors.
_HELP_MAX_WIDTH = max(80, min(shutil.get_terminal_size(fallback=(120, 24)).columns, 160))

# ---------------------------------------------------------------------------
# Global-options injection
# ---------------------------------------------------------------------------
# These option definitions are injected into every leaf command and sub-group
# so that --json, -y/--yes, and -v/--verbose work regardless of whether they
# appear before or after the subcommand on the command line.
#
# --auth is intentionally excluded: it is consumed in the root group callback
# before any subcommand runs, so positional placement there is load-bearing.
#
# Design note — expose_value=False for all commands (groups and leaves):
#   All injected options use expose_value=False so Click parses the option but
#   does NOT pass it as a keyword argument to any command callback.  Instead,
#   an option callback (see _make_meta_callback) stores the value in ctx.meta
#   when the flag is set.  Before the command body runs, _apply_meta_global_params
#   reads ctx.meta and OR-merges the stored flags into ctx.obj (the shared
#   CliContext).  This uniform approach avoids having to distinguish between
#   group callbacks and leaf-command callbacks.
# ---------------------------------------------------------------------------

_META_KEY_JSON = "fabric_dw_global_json_output"
_META_KEY_YES = "fabric_dw_global_yes"
_META_KEY_VERBOSE = "fabric_dw_global_verbose"


def _make_meta_callback(meta_key: str) -> Any:  # noqa: ANN401
    """Return an option callback that stores the flag value in ``ctx.meta``."""

    def _cb(ctx: click.Context, _param: click.Parameter, value: bool) -> bool:
        if value:
            ctx.meta[meta_key] = True
        return value

    return _cb


def _inject_global_options(cmd: click.Command) -> None:
    """Add the three global options to *cmd*, skipping any that already exist.

    All injected options use ``expose_value=False`` so they are never passed
    as keyword arguments to the command's own callback (which may not declare
    them).  Instead, an option callback stores the value in ``ctx.meta`` so
    the ``_wrapped_invoke`` can read it and fold it into the shared
    :class:`CliContext` before the command body runs.
    """
    existing_names: set[str] = set()
    existing_dests: set[str] = set()
    for param in cmd.params:
        if isinstance(param, click.Option):
            existing_names.update(param.opts)
        if param.name is not None:
            existing_dests.add(param.name)

    # Tuples of (opts, dest, meta_key, help_text).
    _specs: list[tuple[list[str], str, str, str]] = [
        (
            ["--json", "json_output"],
            "json_output",
            _META_KEY_JSON,
            "Emit machine-readable JSON instead of Rich tables.",
        ),
        (["--yes", "-y", "yes"], "yes", _META_KEY_YES, "Skip confirmation prompts."),
        (["--verbose", "-v", "verbose"], "verbose", _META_KEY_VERBOSE, "Enable debug logging."),
    ]

    for opts, dest, meta_key, help_text in _specs:
        # Skip if any declared option string already exists on this command.
        if existing_names.intersection(opts):
            continue
        # Skip if the destination name already exists.
        if dest in existing_dests:
            continue

        option = click.Option(
            opts,
            is_flag=True,
            default=False,
            expose_value=False,
            callback=_make_meta_callback(meta_key),
            help=help_text,
        )
        cmd.params.append(option)
        existing_dests.add(dest)
        # Update existing_names with the actual flag strings (e.g. "--json", "-y")
        # as returned by the constructed option, not the raw opts list which may
        # include the Click destination name (e.g. "json_output").
        existing_names.update(option.opts)


def _apply_meta_global_params(ctx: click.Context) -> None:
    """Apply global flags stored in ``ctx.meta`` to the shared :class:`CliContext`.

    The option callbacks (set up via :func:`_inject_global_options`) store flag
    values in ``ctx.meta`` when the flag is set.  This function merges those
    stored values into ``ctx.obj`` (the shared :class:`CliContext`) before the
    command body runs.

    Merge semantics (OR-merge — the most-permissive position wins):
    - ``json_output`` → ``ctx.obj.json_output = True``
    - ``yes``         → ``ctx.obj.yes = True``
    - ``verbose``     → re-applies ``setup_logging(DEBUG)``
    """
    obj: CliContext | None = ctx.obj
    if obj is None:
        return
    if ctx.meta.get(_META_KEY_JSON):
        obj.json_output = True
    if ctx.meta.get(_META_KEY_YES):
        obj.yes = True
    if ctx.meta.get(_META_KEY_VERBOSE):
        setup_logging(logging.DEBUG)


def _patch_command_for_global_options(cmd: click.Command) -> None:
    """Inject global options and an invoke wrapper into *cmd* in-place.

    Idempotent: the ``_global_opts_patched`` sentinel prevents double-patching.
    All injected options use ``expose_value=False`` so neither group nor leaf
    callbacks receive unexpected keyword arguments.

    Recurses into sub-groups' already-registered commands so that nested
    command trees are fully covered when a sub-group is added to the root.
    """
    if getattr(cmd, "_global_opts_patched", False):
        return
    cmd._global_opts_patched = True  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    _inject_global_options(cmd)

    original_invoke = cmd.invoke

    def _wrapped_invoke(ctx: click.Context) -> Any:  # noqa: ANN401
        _apply_meta_global_params(ctx)
        return original_invoke(ctx)

    cmd.invoke = _wrapped_invoke  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]

    if isinstance(cmd, click.Group):
        for sub in cmd.commands.values():  # type: ignore[attr-defined]
            _patch_command_for_global_options(sub)


class _InstrumentedGroup(click.Group):
    """A :class:`click.Group` subclass that emits one ``command_invoked``
    telemetry event per leaf command invocation.

    The event is fired *after* the subcommand (or the nested group + leaf)
    finishes so that the ``status`` and ``duration_ms`` are accurate.
    The fully-qualified ``name`` attribute uses the format
    ``<group>.<subcommand>`` (e.g. ``warehouses.list``).

    All child groups added via :meth:`add_command` are transparently patched
    to record ``<group>.<leaf>`` in the root context's :attr:`click.Context.meta`
    dict.  The root group (``parent is None``) emits the ``command_invoked``
    event once the full call stack has unwound.

    Shutdown ordering
    -----------------
    ``shutdown_telemetry()`` must run AFTER ``emit_command_invoked`` so that the
    ``command_invoked`` span is enqueued before the provider is shut down.
    ``provider.shutdown()`` flushes all pending spans internally, so no separate
    ``flush_telemetry()`` call is needed.  Click's ``call_on_close`` callbacks
    run INSIDE ``super().invoke()``, completing before this ``finally`` block
    executes.  For this reason the ``_on_close`` callback does NOT call
    ``shutdown_telemetry()`` — the teardown is performed here, after emission,
    by the root group only.
    """

    def add_command(self, cmd: click.Command, name: str | None = None) -> None:
        """Add *cmd*, patch for telemetry, and inject global options."""
        if isinstance(cmd, click.Group):
            _patch_group_for_telemetry(cmd)
        _patch_command_for_global_options(cmd)
        super().add_command(cmd, name)

    def invoke(self, ctx: click.Context) -> object:
        """Invoke the root group, emit ``command_invoked``, then shut down telemetry."""
        start = now_ms()
        exc_seen: BaseException | None = None
        try:
            return super().invoke(ctx)
        except BaseException as exc:
            exc_seen = exc
            raise
        finally:
            command_name = _build_command_name(ctx)
            if command_name:
                duration = now_ms() - start
                status = map_status(exc_seen)
                emit_command_invoked(
                    name=command_name,
                    surface="cli",
                    status=status,
                    duration_ms=duration,
                )
            # Shut down the provider AFTER emission so the command_invoked span
            # is enqueued before teardown begins.  provider.shutdown() flushes
            # all pending spans internally before closing processors/exporters,
            # so a separate force_flush step is not needed (and would add up to
            # an extra 2 s to the total hang budget).  Releasing the exporter's
            # requests/urllib3 connection pool via shutdown() prevents the pool
            # from being finalized by the GC after the queue module globals are
            # torn down — which would trigger:
            #   AttributeError: 'NoneType' object has no attribute 'Empty'
            # shutdown_telemetry runs in a daemon thread with a single ≤2 s join
            # so it cannot hang the process (B2).
            shutdown_telemetry()


def _build_command_name(root_ctx: click.Context) -> str | None:
    """Build the fully-qualified command name from accumulated path segments.

    Reads the ``_segments`` list written by patched sub-group invoke wrappers,
    sorts segments by nesting depth (shallowest first), and joins them to form
    a path like ``warehouses.list`` or ``config.set.workspace``.

    Returns ``None`` when no segments were accumulated (e.g. root ``--help``).
    """
    segments: list[tuple[int, str, str]] = root_ctx.meta.get(_CLI_SEGMENTS_KEY, [])
    if not segments:
        return None

    # Sort by depth (ascending) to get outermost → innermost order.
    segments_sorted = sorted(segments, key=lambda t: t[0])

    # Build path: take the group name from each segment, then append the
    # subcommand name from the deepest segment.
    parts: list[str] = []
    for _depth, group_name, _sub in segments_sorted:
        parts.append(group_name)
    # Append the leaf subcommand name from the deepest segment.
    if segments_sorted:
        parts.append(segments_sorted[-1][2])

    return ".".join(parts)


def _patch_group_for_telemetry(group: click.Group) -> None:
    """Monkey-patch *group*.invoke to record its portion of the command path.

    The strategy is simple and correct for arbitrarily nested groups:

    - Each patched group's ``finally`` block records its own name in the root
      context's ``meta`` as a **list of (depth, name) segments**.
    - After all groups have written their segments, the root
      :class:`_InstrumentedGroup` reads the segments, sorts by depth, and
      joins them to build the full path (e.g. ``config.set.workspace``).

    Recursive patching ensures that sub-groups added before this function
    is called (e.g. ``config.set``) are also patched.
    """
    # Recursively patch any sub-groups already registered.
    for sub_cmd in group.commands.values():  # type: ignore[attr-defined]
        if isinstance(sub_cmd, click.Group):
            _patch_group_for_telemetry(sub_cmd)

    original_invoke = group.invoke

    def _patched_invoke(ctx: click.Context) -> Any:  # noqa: ANN401
        try:
            return original_invoke(ctx)
        finally:
            group_name = ctx.info_name or ""
            sub_name = ctx.invoked_subcommand or ""
            if group_name and sub_name:
                # Calculate nesting depth: count ancestors up to (not including) root.
                depth = 0
                node = ctx
                while node.parent is not None:
                    depth += 1
                    node = node.parent
                root_ctx = node  # node is now the root context

                # Accumulate segments: list of (depth, group_name, sub_name).
                segs: list[tuple[int, str, str]] = root_ctx.meta.get(_CLI_SEGMENTS_KEY, [])
                segs.append((depth, group_name, sub_name))
                root_ctx.meta[_CLI_SEGMENTS_KEY] = segs

    group.invoke = _patched_invoke  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]


@click.group(
    invoke_without_command=False,
    cls=_InstrumentedGroup,
    context_settings={"help_option_names": ["-h", "--help"], "max_content_width": _HELP_MAX_WIDTH},
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of Rich tables.",
)
@click.option(
    "--auth",
    "auth_mode",
    type=click.Choice([m.value for m in CredentialMode], case_sensitive=False),
    default=CredentialMode.DEFAULT.value,
    show_default=True,
    help="Authentication mode.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    is_flag=True,
    default=False,
    help="Skip confirmation prompts.",
)
@click.option(
    "--verbose",
    "-v",
    "verbose",
    is_flag=True,
    default=False,
    help="Enable debug logging.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    json_output: bool,
    auth_mode: str,
    yes: bool,
    verbose: bool,
) -> None:
    """Microsoft Fabric Data Warehouse CLI & MCP Server."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    ctx.obj = CliContext(
        json_output=json_output,
        yes=yes,
        auth=CredentialMode(auth_mode),
    )

    maybe_print_first_run_notice()
    record_app_started("cli")

    start_ms = time.monotonic() * 1000

    def _on_close() -> None:
        duration_ms = time.monotonic() * 1000 - start_ms
        # Map the active exception (if any) to a categorical exit status (B3).
        # call_on_close callbacks run inside Click's ExitStack __exit__, so
        # sys.exc_info() reflects the exception that triggered teardown.
        exc_type, exc_value, _ = sys.exc_info()
        if exc_type is None:
            exit_status = "ok"
        elif exc_type is SystemExit:
            code = getattr(exc_value, "code", None)
            exit_status = "ok" if (code is None or code == 0) else "user_error"
        elif issubclass(exc_type, click.exceptions.Exit):
            code = getattr(exc_value, "code", 0)
            exit_status = "ok" if code == 0 else "user_error"
        elif issubclass(exc_type, (click.exceptions.Abort, click.exceptions.UsageError)):
            exit_status = "user_error"
        else:
            exit_status = "user_error"
        record_app_exited(
            duration_ms=duration_ms,
            exit_status=exit_status,
            error_category=None,
        )
        # NOTE: shutdown_telemetry() is NOT called here.  It is called in
        # _InstrumentedGroup.invoke() AFTER emit_command_invoked() so that
        # the command_invoked span is enqueued before teardown begins.
        # (call_on_close callbacks run inside super().invoke(), which returns
        # before the finally block in _InstrumentedGroup.invoke() executes.)

    ctx.call_on_close(_on_close)


cli.add_command(cache_group)
cli.add_command(completion_group)
cli.add_command(config_group)
cli.add_command(dbt_group)
cli.add_command(workspaces_group)
cli.add_command(warehouses_group)
cli.add_command(sql_endpoints_group)
cli.add_command(audit_group)
cli.add_command(queries_group)
cli.add_command(restore_points_group)
cli.add_command(snapshots_group)
cli.add_command(sql_group)
cli.add_command(sql_pools_group)
cli.add_command(schemas_group)
cli.add_command(tables_group)
cli.add_command(views_group)
cli.add_command(procedures_group)
cli.add_command(statistics_group)
cli.add_command(functions_group)
