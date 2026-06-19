"""Tests that --help output is not truncated to 80 columns.

Click's CliRunner forces FORCED_WIDTH=80 during isolation, so we cannot
observe wider output through runner.invoke.  Instead we verify:

1. The module constant _HELP_MAX_WIDTH is >= 80 and <= 160.
2. The root CLI context_settings includes max_content_width == _HELP_MAX_WIDTH.
3. A HelpFormatter built at a wide width does NOT truncate the long audit
   description when the column budget is ample.
4. Narrow / no-tty environments do not crash (the shutil.get_terminal_size
   fallback handles missing terminal info).
5. -h and --help aliases continue to work (regression guard).
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import _HELP_MAX_WIDTH, cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Module-level constant sanity
# ---------------------------------------------------------------------------


def test_help_max_width_at_least_80() -> None:
    """The computed width must be at least 80 (the floor value)."""
    assert _HELP_MAX_WIDTH >= 80


def test_help_max_width_at_most_160() -> None:
    """The computed width must not exceed the 160-col cap."""
    assert _HELP_MAX_WIDTH <= 160


# ---------------------------------------------------------------------------
# context_settings introspection
# ---------------------------------------------------------------------------


def test_context_settings_has_max_content_width() -> None:
    """Root CLI group carries max_content_width in its context_settings."""
    assert "max_content_width" in cli.context_settings


def test_context_settings_max_content_width_matches_constant() -> None:
    """context_settings['max_content_width'] equals _HELP_MAX_WIDTH."""
    assert cli.context_settings["max_content_width"] == _HELP_MAX_WIDTH


def test_context_settings_preserves_help_option_names() -> None:
    """Adding max_content_width must not drop the -h alias."""
    assert cli.context_settings.get("help_option_names") == ["-h", "--help"]


# ---------------------------------------------------------------------------
# Formatter-level: long description is not truncated at the wide width
# ---------------------------------------------------------------------------

# This is the short help that was truncated in the issue report.
_FULL_AUDIT_HELP = "Manage SQL audit settings for Data Warehouses and SQL Analytics Endpoints."


def test_formatter_does_not_truncate_at_max_content_width() -> None:
    """At a wide terminal the audit short-help is rendered in full.

    We bypass CliRunner (which forces width=80) and exercise Click's
    HelpFormatter directly at an ample width (160 columns).

    Click's ``Group.format_commands`` computes the description column budget as::

        limit = formatter.width - 6 - max(len(name) for name in commands)

    The longest registered command name is ``restore-points`` (14 chars), so at
    160 columns the limit is 140 — well above the 63-char audit description.
    At a narrow terminal (e.g. 80 cols) the limit drops to 60, which would
    truncate the description; this test deliberately uses a wide width to confirm
    the feature works when the terminal provides enough room.
    """
    # Use an explicit wide width so the test is independent of the running
    # terminal size (HelpFormatter(max_width=...) caps at the *current*
    # terminal width, which may be narrow in CI).
    wide_width = 160
    formatter = click.HelpFormatter(width=wide_width)
    ctx = click.Context(cli)
    commands = [
        name
        for name in cli.list_commands(ctx)
        if (cmd := cli.get_command(ctx, name)) is not None and not cmd.hidden
    ]
    longest_cmd = max(len(name) for name in commands)
    limit = formatter.width - 6 - longest_cmd
    audit_cmd = cli.get_command(ctx, "audit")  # type: ignore[attr-defined]
    assert audit_cmd is not None, "Expected 'audit' sub-command to exist on the CLI"
    rendered = audit_cmd.get_short_help_str(limit)
    assert rendered == _FULL_AUDIT_HELP, (
        f"Expected full help text but got: {rendered!r}\n"
        f"(formatter.width={formatter.width}, longest_cmd={longest_cmd}, limit={limit})"
    )


# ---------------------------------------------------------------------------
# Smoke: -h / --help still work; no crash on narrow/no-tty
# ---------------------------------------------------------------------------


def test_root_help_flag(runner: CliRunner) -> None:
    """--help on the root group produces a help page."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_root_short_help_flag(runner: CliRunner) -> None:
    """-h on the root group also produces a help page."""
    result = runner.invoke(cli, ["-h"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_subgroup_help_smoke(runner: CliRunner) -> None:
    """Sub-group --help does not crash."""
    result = runner.invoke(cli, ["audit", "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_leaf_help_smoke(runner: CliRunner) -> None:
    """Leaf command --help does not crash."""
    result = runner.invoke(cli, ["warehouses", "list", "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_help_no_tty_does_not_crash() -> None:
    """shutil.get_terminal_size fallback is exercised: no AttributeError.

    The _HELP_MAX_WIDTH constant is computed at import time with
    fallback=(120, 24), so importing the module without a tty must not raise.
    This test is vacuous when the module is already imported, but it documents
    the contract and guards against regressions.
    """
    # Already imported above — if it didn't crash, the fallback worked.
    assert _HELP_MAX_WIDTH >= 80
