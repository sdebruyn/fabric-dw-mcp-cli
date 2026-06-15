"""Click group definition for the fabric-dw CLI."""

from __future__ import annotations

import logging
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
    flush_telemetry,
    maybe_print_first_run_notice,
    record_app_exited,
    record_app_started,
)
from fabric_dw.telemetry_commands import (
    emit_command_invoked,
    map_status,
    now_ms,
)

_CLI_TELEMETRY_KEY = "fabric_dw_telemetry_command_name"
_CLI_SEGMENTS_KEY = _CLI_TELEMETRY_KEY + "_segments"


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

    Flush ordering
    --------------
    ``flush_telemetry()`` must run AFTER ``emit_command_invoked`` so that the
    ``command_invoked`` span is enqueued before the exporter is flushed.
    Click's ``call_on_close`` callbacks run INSIDE ``super().invoke()``, so they
    complete before this ``finally`` block executes.  For this reason the
    ``_on_close`` callback registered on the CLI group context does NOT call
    ``flush_telemetry()`` — the flush is performed here, after emission, by the
    root group only.
    """

    def add_command(self, cmd: click.Command, name: str | None = None) -> None:
        """Add *cmd* and patch its ``invoke`` to capture the command path."""
        if isinstance(cmd, click.Group):
            _patch_group_for_telemetry(cmd)
        super().add_command(cmd, name)

    def invoke(self, ctx: click.Context) -> object:
        """Invoke the root group, emit ``command_invoked``, then flush telemetry."""
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
            # Flush AFTER emission so command_invoked is included in the batch.
            # app_started and app_exited are emitted before this point (via
            # record_app_started in the group callback and record_app_exited in
            # _on_close which runs inside super().invoke()), so all three events
            # are enqueued before this bounded flush runs.
            flush_telemetry()


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


@click.group(invoke_without_command=False, cls=_InstrumentedGroup)
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
    """Microsoft Fabric Data Warehouse CLI."""
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
        # NOTE: flush_telemetry() is NOT called here.  It is called in
        # _InstrumentedGroup.invoke() AFTER emit_command_invoked() so that
        # the command_invoked span is enqueued before the flush runs.
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
