"""Click group definition for the fabric-dw CLI."""

from __future__ import annotations

import importlib
import logging
import shutil
import sys
import time
from typing import Any

import click

from fabric_dw.auth import CredentialMode
from fabric_dw.cli._context import CliContext
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

_logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Lazy command registry
# ---------------------------------------------------------------------------
# Maps the CLI name of each command group to the module and group-object name
# where it lives.  Format: "module.path:group_object_name".
# No module is imported at startup; _LazyGroup does it on demand.
# ---------------------------------------------------------------------------

_COMMAND_MAP: dict[str, str] = {
    "audit": "fabric_dw.cli.commands.audit:audit_group",
    "cache": "fabric_dw.cli.commands.cache:cache_group",
    "completion": "fabric_dw.cli.commands.completion:completion_group",
    "config": "fabric_dw.cli.commands.config:config_group",
    "dbt": "fabric_dw.cli.commands.dbt:dbt_group",
    "functions": "fabric_dw.cli.commands.functions:functions_group",
    "procedures": "fabric_dw.cli.commands.procedures:procedures_group",
    "queries": "fabric_dw.cli.commands.queries:queries_group",
    "restore-points": "fabric_dw.cli.commands.restore_points:restore_points_group",
    "schemas": "fabric_dw.cli.commands.schemas:schemas_group",
    "settings": "fabric_dw.cli.commands.settings:settings_group",
    "snapshots": "fabric_dw.cli.commands.snapshots:snapshots_group",
    "sql": "fabric_dw.cli.commands.sql:sql_group",
    "sql-endpoints": "fabric_dw.cli.commands.sql_endpoints:sql_endpoints_group",
    "sql-pools": "fabric_dw.cli.commands.sql_pools:sql_pools_group",
    "statistics": "fabric_dw.cli.commands.statistics:statistics_group",
    "tables": "fabric_dw.cli.commands.tables:tables_group",
    "views": "fabric_dw.cli.commands.views:views_group",
    "warehouses": "fabric_dw.cli.commands.warehouses:warehouses_group",
    "workspaces": "fabric_dw.cli.commands.workspaces:workspaces_group",
}

# One-line help text per group — shown in root --help WITHOUT importing modules.
_SHORT_HELP_MAP: dict[str, str] = {
    "audit": "Manage SQL audit settings for Data Warehouses and SQL Analytics Endpoints.",
    "cache": "Manage the local name-to-UUID lookup cache.",
    "completion": "Manage shell completion scripts.",
    "config": "Manage fabric-dw CLI configuration defaults.",
    "dbt": "Scaffold and manage dbt projects for Fabric Data Warehouses.",
    "functions": (
        "Manage T-SQL user-defined functions on Fabric warehouses and SQL Analytics Endpoints."
    ),
    "procedures": "Manage stored procedures on Fabric warehouses and SQL Analytics Endpoints.",
    "queries": (
        "Inspect and manage running queries on Fabric warehouses and SQL Analytics Endpoints."
    ),
    "restore-points": "Manage Microsoft Fabric Warehouse restore points.",
    "schemas": "Manage SQL schemas on Fabric warehouses.",
    "settings": "Manage server-side database settings on Fabric Data Warehouses.",
    "snapshots": "Manage Microsoft Fabric Data Warehouse snapshots.",
    "sql": (
        "SQL execution and query-plan capture for Fabric warehouses and SQL Analytics Endpoints."
    ),
    "sql-endpoints": "Manage Microsoft Fabric SQL Analytics Endpoints.",
    "sql-pools": "Manage workspace SQL Pools configuration (beta API).",
    "statistics": (
        "Manage user-defined statistics on Fabric Data Warehouses and SQL Analytics Endpoints."
    ),
    "tables": "Manage SQL tables on Fabric warehouses and SQL Analytics Endpoints.",
    "views": "Manage SQL views on Fabric warehouses and SQL Analytics Endpoints.",
    "warehouses": "Manage Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.",
    "workspaces": "Manage Microsoft Fabric workspaces.",
}

# Guard: both maps must cover exactly the same set of command names.  A command
# added to _COMMAND_MAP but not _SHORT_HELP_MAP (or vice-versa) would silently
# show an empty description or fail to load.  This check fires at import time
# so the mistake is caught by the first test run, not at runtime.
if _COMMAND_MAP.keys() != _SHORT_HELP_MAP.keys():
    _missing_help = _COMMAND_MAP.keys() - _SHORT_HELP_MAP.keys()
    _missing_cmd = _SHORT_HELP_MAP.keys() - _COMMAND_MAP.keys()
    raise ValueError(
        f"_COMMAND_MAP and _SHORT_HELP_MAP must cover the same commands.  "
        f"Missing from _SHORT_HELP_MAP: {_missing_help!r}.  "
        f"Missing from _COMMAND_MAP: {_missing_cmd!r}."
    )


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
    ``command_invoked`` event is enqueued in the ``BatchLogRecordProcessor`` queue
    before shutdown starts.  ``shutdown_telemetry()`` calls ``force_flush`` on the
    log provider first (to export queued records) then ``shutdown()`` (to release
    the connection pool).  Click's ``call_on_close`` callbacks run INSIDE
    ``super().invoke()``, completing before this ``finally`` block executes.  For
    this reason the ``_on_close`` callback does NOT call ``shutdown_telemetry()`` —
    the teardown is performed here, after emission, by the root group only.
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
                    status=status,
                    duration_ms=duration,
                )
            # Shut down the provider AFTER emission so the command_invoked event
            # is enqueued before teardown begins.  shutdown_telemetry() calls
            # force_flush on the log provider first (ensuring command_invoked and
            # app_exited records are exported to App Insights) then shuts down the
            # provider (releases the urllib3 connection pool, preventing the GC
            # finaliser from triggering "AttributeError: 'NoneType' object has no
            # attribute 'Empty'" at interpreter exit).  Runs in a daemon thread
            # with a ≤8 s join so it cannot hang the process indefinitely (B2).
            shutdown_telemetry()


def _build_command_name(root_ctx: click.Context) -> str | None:
    """Build the fully-qualified command name from accumulated path segments.

    Reads the ``_segments`` list written by patched sub-group invoke wrappers,
    sorts segments by nesting depth (shallowest first), and joins them to form
    a path like ``warehouses.list``, ``sql.exec``, or ``config.set.workspace``.

    For direct leaf commands registered on the root group (not currently used —
    all commands are now groups), no sub-group invoke wrapper writes a segment,
    so ``_segments`` is empty.  In that case ``root_ctx.invoked_subcommand``
    holds the command name and we return it directly.

    Returns ``None`` when no segments were accumulated (e.g. root ``--help``).
    """
    segments: list[tuple[int, str, str]] = root_ctx.meta.get(_CLI_SEGMENTS_KEY, [])
    if not segments:
        # Direct leaf command on the root group (e.g. ``fdw sql -q "SELECT 1"``).
        return root_ctx.invoked_subcommand or None

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

    Idempotent: the ``_telemetry_patched`` sentinel prevents double-patching
    when a lazily-loaded group is resolved more than once via :meth:`get_command`.
    """
    if getattr(group, "_telemetry_patched", False):
        return
    group._telemetry_patched = True  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

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


class _LazyGroup(_InstrumentedGroup):
    """A lazy-loading :class:`_InstrumentedGroup` that defers command module imports.

    Command groups are registered as strings in :data:`_COMMAND_MAP` (CLI name
    → ``"module.path:group_object"``).  The module is only imported when the
    group is actually invoked or its own ``--help`` is requested — never on
    startup or for the root ``--help``.

    Root ``--help`` is rendered from :data:`_SHORT_HELP_MAP` so no modules are
    imported at all.  The full telemetry/global-options patching that
    :class:`_InstrumentedGroup` performs via :meth:`add_command` is replicated
    in :meth:`get_command` after the lazy import.
    """

    def list_commands(self, ctx: click.Context) -> list[str]:  # type: ignore[override]  # noqa: ARG002
        """Return all registered command names in alphabetical order."""
        return sorted(_COMMAND_MAP)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:  # noqa: ARG002
        """Import and return the command group for *cmd_name*, or ``None``."""
        spec = _COMMAND_MAP.get(cmd_name)
        if spec is None:
            return None
        module_path, attr_name = spec.rsplit(":", 1)
        try:
            module = importlib.import_module(module_path)
            cmd: click.Command = getattr(module, attr_name)
        except (ImportError, AttributeError) as exc:
            _logger.warning("Failed to load command %r: %s", cmd_name, exc)
            return None
        # Replicate what _InstrumentedGroup.add_command does so that telemetry
        # and global-options injection are applied on the lazily-loaded group.
        if isinstance(cmd, click.Group):
            _patch_group_for_telemetry(cmd)
        _patch_command_for_global_options(cmd)
        return cmd

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        """Resolve a command name, supplying the lazy command list for suggestions.

        Click's base :meth:`resolve_command` passes ``self.commands`` (the
        eagerly-registered dict) to the "Did you mean?" resolver.  Since this
        group never calls :meth:`add_command`, that dict is always empty and
        typo suggestions are permanently suppressed.  Override to pass the
        lazy command names instead.
        """
        # Temporarily populate self.commands with stubs so Click's resolver can
        # compute "Did you mean?" possibilities without triggering real imports.
        # We restore the empty dict immediately after resolution.
        dummy_cmds = {name: click.Command(name) for name in _COMMAND_MAP}
        original_commands = self.commands  # type: ignore[attr-defined]
        self.commands = dummy_cmds  # type: ignore[attr-defined]
        try:
            return super().resolve_command(ctx, args)
        finally:
            self.commands = original_commands  # type: ignore[attr-defined]

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Render the command list from :data:`_SHORT_HELP_MAP` without importing modules."""
        commands: list[tuple[str, str]] = []
        max_name_len = max(len(n) for n in _COMMAND_MAP)
        for name in self.list_commands(ctx):
            help_text = _SHORT_HELP_MAP.get(name, "")
            # Truncate to the available width.  Clamp to 0 to prevent negative
            # slice indices (which slice from the tail) on very narrow terminals.
            limit = max(0, formatter.width - 6 - max_name_len) if formatter.width else 45
            short_help = help_text[:limit] if limit and len(help_text) > limit else help_text
            commands.append((name, short_help))
        if commands:
            with formatter.section("Commands"):
                formatter.write_dl(commands)


@click.group(
    invoke_without_command=False,
    cls=_LazyGroup,
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
    "-w",
    "--workspace",
    "workspace",
    metavar="NAME|GUID",
    default=None,
    help="Target workspace (name or GUID). Falls back to the configured default.",
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
@click.option(
    "--max-429-retries",
    "max_429_retries",
    type=click.IntRange(min=1),
    default=None,
    metavar="N",
    help=(
        "Maximum consecutive 429 responses before raising RateLimitedError "
        "(default: 10, or as configured by FABRIC_DW_MAX_429_RETRIES / config file)."
    ),
)
@click.option(
    "--retry-deadline",
    "retry_deadline",
    type=click.FloatRange(min=0.1),
    default=None,
    metavar="SECONDS",
    help=(
        "Combined wall-clock deadline in seconds for the 429-loop and 5xx-retry budget "
        "(default: 300.0, or as configured by FABRIC_DW_RETRY_DEADLINE_S / config file)."
    ),
)
@click.pass_context
def cli(
    ctx: click.Context,
    json_output: bool,
    auth_mode: str,
    workspace: str | None,
    yes: bool,
    verbose: bool,
    max_429_retries: int | None,
    retry_deadline: float | None,
) -> None:
    """Microsoft Fabric Data Warehouse CLI & MCP Server."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    ctx.obj = CliContext(
        json_output=json_output,
        yes=yes,
        auth=CredentialMode(auth_mode),
        workspace=workspace,
        max_429_retries=max_429_retries,
        retry_deadline_s=retry_deadline,
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
