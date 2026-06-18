"""CLI entrypoint for fabric-dw."""

from __future__ import annotations

import sys

from fabric_dw.cli._main import cli
from fabric_dw.telemetry import suppress_telemetry

# Help-option names declared on the root group's context_settings.
_HELP_FLAGS: frozenset[str] = frozenset({"-h", "--help"})


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
    cli()
