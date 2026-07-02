"""CLI entrypoint for fabric-dw."""

from __future__ import annotations

import contextlib
import logging
import sys
import traceback

import click

from fabric_dw.cli._main import cli
from fabric_dw.telemetry import suppress_telemetry

# Help-option names declared on the root group's context_settings.
_HELP_FLAGS: frozenset[str] = frozenset({"-h", "--help"})

# Exit code for an unexpected/internal error that escaped every other
# error-handling layer.  Grouped with the generic non-zero-failure code
# documented in docs/reference/exit-codes.md (usage error / aborted prompt /
# Fabric API error all exit 1); a dedicated code is not warranted since the
# guard below is defense-in-depth, not the primary error-reporting path.
_UNEXPECTED_ERROR_EXIT_CODE = 1


def main() -> None:
    """Entrypoint registered in pyproject.toml [project.scripts]."""
    # Suppress all telemetry for help invocations at any level.  Click's eager
    # --help for the ROOT group short-circuits before the group callback runs, so
    # it never pays any telemetry cost.  Subcommand help (e.g. ``fdw config -h``)
    # DOES run the root callback (to dispatch toward the subcommand) and then
    # exits, which would otherwise trigger a full telemetry init + network flush.
    # Detecting the help flag here and calling suppress_telemetry() before cli()
    # ensures every help invocation at any depth is a guaranteed no-op for
    # telemetry, bringing subcommand help latency from ~2-4 s to <0.5 s.
    if _HELP_FLAGS.intersection(sys.argv[1:]):
        suppress_telemetry()
    try:
        cli()
    except (SystemExit, KeyboardInterrupt):
        # SystemExit: Click's own main() already caught and cleanly rendered
        # ClickException/UsageError/Abort and translated them into this,
        # nothing more to do.  KeyboardInterrupt: never swallow Ctrl+C.
        raise
    except BaseException as exc:
        _render_unexpected_error(exc)


def _render_unexpected_error(exc: BaseException) -> None:
    """Render an exception that escaped every other error-handling layer.

    Click's own ``main()`` only catches ``ClickException`` / ``UsageError`` /
    ``Abort`` / EPIPE ``OSError``; anything else (e.g. a raw driver error that
    slipped past a connect-retry-exhaustion path, see #972) propagates
    unchanged.  This is the last line of defense so the CLI never shows a raw
    Python traceback by default: a clean, single-line message goes to stderr
    and the process exits non-zero.

    The full traceback is still available for debugging, but only when the
    ``fabric_dw`` logger is at ``DEBUG`` level, the existing ``-v`` /
    ``--verbose`` convention (see ``fabric_dw.cli._main.cli``), never by
    default, since it may contain internal stack frames and driver internals.

    This function must never itself raise: it is the last line of defense, so
    a broken ``exc.__str__`` or a write failure on a closed/broken stderr must
    not prevent the process from exiting with the correct non-zero code.
    """
    try:
        message = str(exc).replace("\r", " ").replace("\n", " ").strip() or type(exc).__name__
    except Exception:
        # exc.__str__ itself failed (e.g. a buggy custom exception): fall back
        # to the type name, which cannot raise.
        message = type(exc).__name__
    # Stderr write failures (e.g. a broken pipe) must not block the exit below.
    with contextlib.suppress(Exception):
        click.echo(f"Error: unexpected error: {message}", err=True)
    if logging.getLogger("fabric_dw").isEnabledFor(logging.DEBUG):
        # Guarded too: a broken stderr must not prevent sys.exit() from firing.
        with contextlib.suppress(Exception):
            traceback.print_exc()
    sys.exit(_UNEXPECTED_ERROR_EXIT_CODE)
