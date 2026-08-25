"""Click group definition for the fabric-dw CLI."""

from __future__ import annotations

import importlib
import logging
import shutil
from typing import Any

import click
from click.core import ParameterSource

from fabric_dw import __version__
from fabric_dw.auth import CredentialMode
from fabric_dw.cli._context import CliContext
from fabric_dw.config import VALID_AUTH_MODES, load_config
from fabric_dw.config_resolve import resolve_auth_mode
from fabric_dw.logging import setup_logging

_logger = logging.getLogger(__name__)

# Use actual terminal width so help text adapts to the user's screen.
# Floor of 80 preserves readable wrapping on narrow terminals and in CI
# (where get_terminal_size falls back to the (120, 24) default).
# Cap of 160 prevents absurdly long lines on ultra-wide monitors.
_HELP_MAX_WIDTH = max(80, min(shutil.get_terminal_size(fallback=(120, 24)).columns, 160))

# ---------------------------------------------------------------------------
# Global-options injection
# ---------------------------------------------------------------------------
# These option definitions are injected into every leaf command and sub-group
# so that all global options work regardless of whether they appear before or
# after the subcommand on the command line.
#
# Collision handling: if a leaf command already declares a flag with the same
# name (e.g. "--auth" in "dbt init" which has its own dbt-specific auth mode),
# injection for that flag is silently skipped on that command.  The pre-
# subcommand form of the global option is unaffected; only the trailing form
# is unavailable for that specific command.
#
# Known collision: "dbt init --auth" is dbt's own auth-mode selector
# (destination: dbt_auth_override).  The global "--auth" is therefore not
# injected into "dbt init"; users who need to set the global auth mode for
# that command must use the pre-subcommand form: "fabric-dw --auth <mode>
# dbt init ...".
#
# Design note -- expose_value=False for all commands (groups and leaves):
#   All injected options use expose_value=False so Click parses the option but
#   does NOT pass it as a keyword argument to any command callback.  Instead,
#   an option callback (see _make_meta_callback / _make_meta_value_callback)
#   stores the value in ctx.meta when the flag is set.  Before the command body
#   runs, _apply_meta_global_params reads ctx.meta and merges the stored values
#   into ctx.obj (the shared CliContext).  This uniform approach avoids having
#   to distinguish between group callbacks and leaf-command callbacks.
# ---------------------------------------------------------------------------

_META_KEY_JSON = "fabric_dw_global_json_output"
_META_KEY_YES = "fabric_dw_global_yes"
_META_KEY_VERBOSE = "fabric_dw_global_verbose"
_META_KEY_WORKSPACE = "fabric_dw_global_workspace"
_META_KEY_AUTH = "fabric_dw_global_auth_mode"
_META_KEY_MAX_429_RETRIES = "fabric_dw_global_max_429_retries"
_META_KEY_RETRY_DEADLINE = "fabric_dw_global_retry_deadline"


def _make_meta_callback(meta_key: str) -> Any:  # noqa: ANN401
    """Return an option callback that stores the flag value in ``ctx.meta``."""

    def _cb(ctx: click.Context, _param: click.Parameter, value: bool) -> bool:
        if value:
            ctx.meta[meta_key] = True
        return value

    return _cb


def _make_meta_value_callback(meta_key: str) -> Any:  # noqa: ANN401
    """Return an option callback that stores a non-None value in ``ctx.meta``."""

    def _cb(ctx: click.Context, _param: click.Parameter, value: Any) -> Any:  # noqa: ANN401
        if value is not None:
            ctx.meta[meta_key] = value
        return value

    return _cb


def _inject_global_options(cmd: click.Command) -> None:
    """Add all global options to *cmd*, skipping any that already exist.

    All injected options use ``expose_value=False`` so they are never passed
    as keyword arguments to the command's own callback (which may not declare
    them).  Instead, an option callback stores the value in ``ctx.meta`` so
    the ``_wrapped_invoke`` can read it and fold it into the shared
    :class:`CliContext` before the command body runs.

    Collision policy: if a command already declares an option with the same
    flag string (e.g. ``--auth`` on ``dbt init``), that option is skipped for
    that command.  See the module-level comment for details.
    """
    existing_names: set[str] = set()
    existing_dests: set[str] = set()
    for param in cmd.params:
        if isinstance(param, click.Option):
            existing_names.update(param.opts)
        if param.name is not None:
            existing_dests.add(param.name)

    # Boolean flag specs: (opts, dest, meta_key, help_text).
    _flag_specs: list[tuple[list[str], str, str, str]] = [
        (
            ["--json", "json_output"],
            "json_output",
            _META_KEY_JSON,
            "Emit machine-readable JSON instead of Rich tables.",
        ),
        (["--yes", "-y", "yes"], "yes", _META_KEY_YES, "Skip confirmation prompts."),
        (["--verbose", "-v", "verbose"], "verbose", _META_KEY_VERBOSE, "Enable debug logging."),
    ]

    for opts, dest, meta_key, help_text in _flag_specs:
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

    # Value option specs: (opts, dest, meta_key, param_type, metavar, help_text).
    # Each is skipped if a flag with the same name already exists on the command
    # (collision policy -- see module-level comment).
    _value_specs: list[tuple[list[str], str, str, Any, str | None, str]] = [
        (
            ["-w", "--workspace", "workspace"],
            "workspace",
            _META_KEY_WORKSPACE,
            str,
            "NAME|GUID",
            "Target workspace (name or GUID). Falls back to the configured default.",
        ),
        (
            ["--auth", "auth_mode"],
            "auth_mode",
            _META_KEY_AUTH,
            click.Choice([m.value for m in CredentialMode], case_sensitive=False),
            None,
            (
                "Authentication mode (default: 'default', or as configured by "
                "FABRIC_AUTH / config file [defaults] auth_mode)."
            ),
        ),
        (
            ["--max-429-retries", "max_429_retries"],
            "max_429_retries",
            _META_KEY_MAX_429_RETRIES,
            click.IntRange(min=1),
            "N",
            (
                "Maximum consecutive 429 responses before raising RateLimitedError "
                "(default: 10, or as configured by FABRIC_DW_MAX_429_RETRIES / config file)."
            ),
        ),
        (
            ["--retry-deadline", "retry_deadline"],
            "retry_deadline",
            _META_KEY_RETRY_DEADLINE,
            click.IntRange(min=1),
            "SECONDS",
            (
                "Combined wall-clock deadline in seconds for the 429-loop and 5xx-retry "
                "budget (default: 300, or as configured by FABRIC_DW_RETRY_DEADLINE_S / "
                "config file)."
            ),
        ),
    ]

    for opts, dest, meta_key, param_type, metavar, help_text in _value_specs:
        if existing_names.intersection(opts):
            continue
        if dest in existing_dests:
            continue

        option = click.Option(
            opts,
            type=param_type,
            default=None,
            expose_value=False,
            callback=_make_meta_value_callback(meta_key),
            metavar=metavar,
            help=help_text,
        )
        cmd.params.append(option)
        existing_dests.add(dest)
        existing_names.update(option.opts)


def _apply_meta_global_params(ctx: click.Context) -> None:
    """Apply global options stored in ``ctx.meta`` to the shared :class:`CliContext`.

    The option callbacks (set up via :func:`_inject_global_options`) store
    values in ``ctx.meta`` when the option is set.  This function merges those
    stored values into ``ctx.obj`` (the shared :class:`CliContext`) before the
    command body runs.

    Merge semantics:
    - ``json_output``     → ``ctx.obj.json_output = True`` (OR-merge)
    - ``yes``             → ``ctx.obj.yes = True`` (OR-merge)
    - ``verbose``         → re-applies ``setup_logging(DEBUG)`` (OR-merge)
    - ``workspace``       → ``ctx.obj.workspace`` (trailing value wins over None)
    - ``auth_mode``       → re-resolves and updates ``ctx.obj.auth``
    - ``max_429_retries`` → ``ctx.obj.max_429_retries`` (trailing value wins over None)
    - ``retry_deadline``  → ``ctx.obj.retry_deadline_s`` (trailing value wins over None)
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
    if (ws := ctx.meta.get(_META_KEY_WORKSPACE)) is not None:
        obj.workspace = ws
    if (auth_val := ctx.meta.get(_META_KEY_AUTH)) is not None:
        cfg = load_config()
        try:
            resolved = resolve_auth_mode(
                cli_value=auth_val,
                config_value=cfg.defaults.auth_mode,
                valid_modes=VALID_AUTH_MODES,
            )
            obj.auth = CredentialMode(resolved)
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
    if (retries := ctx.meta.get(_META_KEY_MAX_429_RETRIES)) is not None:
        obj.max_429_retries = retries
    if (deadline := ctx.meta.get(_META_KEY_RETRY_DEADLINE)) is not None:
        obj.retry_deadline_s = deadline


def _patch_command_for_global_options(cmd: click.Command) -> None:
    """Inject global options and an invoke wrapper into *cmd* in-place.

    Idempotent: the ``_global_opts_patched`` sentinel prevents double-patching.
    All injected options use ``expose_value=False`` so neither group nor leaf
    callbacks receive unexpected keyword arguments.

    Recurses into sub-groups' already-registered commands so that nested
    command trees are fully covered when a sub-group is added to the root.

    For leaf commands, also wraps ``parse_args`` to right-align positional
    values onto declared slots.  This fixes commands that have one or more
    leading optional positionals followed by one or more required positionals:
    Click fills slots strictly left-to-right (ignoring ``required``), so the
    first supplied value normally lands in the optional slot and the required
    slot is reported missing.  The wrapper detects this shape and redistributes
    the supplied values so trailing required slots are filled first, leaving the
    leading optional slot empty when the user omits the warehouse / item.
    """
    if getattr(cmd, "_global_opts_patched", False):
        return
    cmd._global_opts_patched = True  # ty: ignore[unresolved-attribute]

    _inject_global_options(cmd)

    original_invoke = cmd.invoke

    def _wrapped_invoke(ctx: click.Context) -> Any:  # noqa: ANN401
        _apply_meta_global_params(ctx)
        return original_invoke(ctx)

    cmd.invoke = _wrapped_invoke  # ty: ignore[invalid-assignment]

    if isinstance(cmd, click.Group):
        for sub in cmd.commands.values():
            _patch_command_for_global_options(sub)
    else:
        # Right-align positional arguments for leaf commands that have one or
        # more leading optional positionals followed by required positionals.
        _wrap_parse_args_for_right_align(cmd)


_HELP_TOKENS: frozenset[str] = frozenset(("-h", "--help"))


def _wrap_parse_args_for_right_align(cmd: click.Command) -> None:
    """Wrap ``cmd.parse_args`` to right-align supplied positional values.

    When a command declares ``[opt] req1 req2`` and the user omits the
    optional positional, Click fills the optional slot with the first supplied
    value and leaves the required slot empty.  This wrapper detects that shape
    and redistributes the values so required slots are filled first, then calls
    ``Parameter.process_value`` on each relocated slot so that type conversion
    (e.g. Choice case-normalisation, INT casting) is applied correctly.

    Shape requirements (all must hold, otherwise the original parse_args is
    called unmodified):

    * Exactly one leading optional positional whose ``default`` is ``None``,
      followed by one or more required positionals (``[opt] req+``).
    * All positionals have ``nargs == 1`` (no variadic arguments).
    * No trailing optional positional after the required ones.

    Shell completion (``ctx.resilient_parsing=True``) and help-flag invocations
    (``--help`` / ``-h``) bypass the wrapper so completion candidates and help
    text are never perturbed.
    """
    original_parse_args = cmd.parse_args

    def _wrapped_parse_args(ctx: click.Context, args: list[str]) -> list[str]:  # noqa: PLR0911,PLR0912
        # Shell completion must not be perturbed.
        if ctx.resilient_parsing:
            return original_parse_args(ctx, args)

        # Bail out immediately for help tokens so that the relaxation window
        # below does not make required positionals appear optional in the
        # generated help text.  The scan deliberately ignores ``--`` (the
        # end-of-options marker): ``--help`` is not a plausible positional
        # value (schema name, session id, etc.), so "fdw cmd -- --help"
        # produces the same Click error it always would -- no silent
        # mis-binding occurs.
        if any(a in _HELP_TOKENS for a in args):
            return original_parse_args(ctx, args)

        pos_params = [p for p in cmd.params if isinstance(p, click.Argument)]

        # Count consecutive leading optional positionals.
        n_leading_optional = 0
        for p in pos_params:
            if not p.required:
                n_leading_optional += 1
            else:
                break

        n_required = sum(1 for p in pos_params if p.required)

        # Shape guard: exactly one leading optional, all others required, no
        # variadic arguments, no trailing optional after the required ones.
        if n_leading_optional != 1 or n_required == 0:
            return original_parse_args(ctx, args)
        if any(p.nargs != 1 for p in pos_params):
            return original_parse_args(ctx, args)
        if any(not p.required for p in pos_params[1:]):
            return original_parse_args(ctx, args)

        # Guard against a leading optional with a non-None default.  The reset
        # below unconditionally stores None in that slot, which would silently
        # discard any real default.  All current leading optionals declare
        # ``default=None``; bailing out here protects against a future command
        # that uses a different default without realising the impact.
        if pos_params[0].default is not None:
            return original_parse_args(ctx, args)

        # Temporarily relax all positionals (required=False, type=STRING,
        # callback=None) so Click fills every slot with raw strings without
        # raising MissingParameter or triggering callbacks.  The params are
        # module-level singletons, so restoration must happen unconditionally
        # even if parse_args raises (e.g. on over-supply).
        saved_required = [p.required for p in pos_params]
        saved_types = [p.type for p in pos_params]
        saved_callbacks = [p.callback for p in pos_params]
        for p in pos_params:
            p.required = False
            p.type = click.STRING
            p.callback = None
        try:
            result = original_parse_args(ctx, args)
        finally:
            for p, req, typ, cb in zip(
                pos_params, saved_required, saved_types, saved_callbacks, strict=False
            ):
                p.required = req
                p.type = typ
                p.callback = cb

        # Collect raw strings Click assigned from the command line, in
        # declaration order.  Because type=STRING was active, all COMMANDLINE
        # values are plain strings regardless of the original param type.
        cl_values = [
            ctx.params[p.name]
            for p in pos_params
            if ctx.get_parameter_source(p.name) is ParameterSource.COMMANDLINE
        ]
        n_cl = len(cl_values)
        n_pos = len(pos_params)
        opt_param = pos_params[0]
        req_params_list = pos_params[1:]  # all required (shape guard above)

        if n_cl == n_pos:
            # Full form: all slots were provided.  No relocation needed, but
            # types were STRING during the delegated parse so we must call
            # process_value on every slot to apply type conversion and callbacks.
            for p in pos_params:
                if ctx.get_parameter_source(p.name) is ParameterSource.COMMANDLINE:
                    ctx.params[p.name] = p.process_value(ctx, ctx.params[p.name])
            return result

        # Short form: fewer values than slots.  Reset the optional slot and
        # distribute the collected raw values to the required slots, applying
        # type conversion and callbacks via process_value.
        ctx.params[opt_param.name] = None
        ctx.set_parameter_source(opt_param.name, ParameterSource.DEFAULT)

        for idx, p in enumerate(req_params_list):
            if idx < n_cl:
                ctx.params[p.name] = p.process_value(ctx, cl_values[idx])
                ctx.set_parameter_source(p.name, ParameterSource.COMMANDLINE)
            else:
                raise click.MissingParameter(ctx=ctx, param=p)

        return result

    cmd.parse_args = _wrapped_parse_args  # ty: ignore[invalid-assignment]


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
    "permissions": "fabric_dw.cli.commands.permissions:permissions_group",
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
    "permissions": (
        "Manage Fabric item-level and T-SQL in-database permissions (GRANT/DENY/REVOKE)."
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


class _LazyGroup(click.Group):
    """A :class:`click.Group` that defers command module imports until they are used.

    Command groups are registered as strings in :data:`_COMMAND_MAP` (CLI name
    → ``"module.path:group_object"``).  The module is only imported when the
    group is actually invoked or its own ``--help`` is requested — never on
    startup or for the root ``--help``.

    Root ``--help`` is rendered from :data:`_SHORT_HELP_MAP`, so listing the
    commands imports nothing at all.  Because commands arrive through
    :meth:`get_command` rather than ``add_command``, the global-options patching
    that a normally-registered command would get at registration time is applied
    there instead.
    """

    def list_commands(self, ctx: click.Context) -> list[str]:  # noqa: ARG002
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
        # A command reached this way never went through add_command, so the
        # trailing global options have to be injected here.  The patch recurses
        # into sub-groups itself, so a nested tree is covered in one call.
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
        original_commands = self.commands
        self.commands = dummy_cmds
        try:
            return super().resolve_command(ctx, args)
        finally:
            self.commands = original_commands

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
@click.version_option(
    __version__,
    "-V",
    "--version",
    prog_name="fabric-dw",
    message="%(prog)s %(version)s",
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
    default=None,
    show_default=False,
    help=(
        "Authentication mode (default: 'default', or as configured by "
        "FABRIC_AUTH / config file [defaults] auth_mode)."
    ),
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
    help=(
        "Enable debug logging. "
        "DEBUG output may contain SQL and URLs verbatim, "
        "including any embedded credentials (SAS tokens, COPY INTO secrets, "
        "connection strings). Treat -v output as sensitive and do not share it."
    ),
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
    type=click.IntRange(min=1),
    default=None,
    metavar="SECONDS",
    help=(
        "Combined wall-clock deadline in seconds for the 429-loop and 5xx-retry budget "
        "(default: 300, or as configured by FABRIC_DW_RETRY_DEADLINE_S / config file)."
    ),
)
@click.pass_context
def cli(
    ctx: click.Context,
    json_output: bool,
    auth_mode: str | None,
    workspace: str | None,
    yes: bool,
    verbose: bool,
    max_429_retries: int | None,
    retry_deadline: int | None,
) -> None:
    """Microsoft Fabric Data Warehouse CLI & MCP Server."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)

    # Resolve the credential mode via the shared 4-layer helper:
    #   --auth flag (explicit) > FABRIC_AUTH env > [defaults] auth_mode > built-in default.
    # auth_mode is None when the user did NOT pass --auth; a non-None value
    # means the flag was explicitly set on the command line (Click's sentinel
    # default=None guarantees this without needing ParameterSource).
    cfg = load_config()
    try:
        resolved_mode = resolve_auth_mode(
            cli_value=auth_mode,
            config_value=cfg.defaults.auth_mode,
            valid_modes=VALID_AUTH_MODES,
        )
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    ctx.obj = CliContext(
        json_output=json_output,
        yes=yes,
        auth=CredentialMode(resolved_mode),
        workspace=workspace,
        max_429_retries=max_429_retries,
        retry_deadline_s=retry_deadline,
    )
