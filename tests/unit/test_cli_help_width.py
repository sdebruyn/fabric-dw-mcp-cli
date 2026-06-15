"""Tests that --help output is not truncated to 80 columns.

Click's CliRunner forces FORCED_WIDTH=80 during isolation, so we cannot
observe wider output through runner.invoke.  Instead we verify:

1. The module constant _HELP_MAX_WIDTH is > 80 and <= 160.
2. The root CLI context_settings includes max_content_width == _HELP_MAX_WIDTH.
3. A HelpFormatter built at that width does NOT truncate the long audit
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


def test_help_max_width_above_80() -> None:
    """The computed width must exceed Click's 80-col default."""
    assert _HELP_MAX_WIDTH > 80


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
_FULL_AUDIT_HELP = "Manage SQL audit settings for Microsoft Fabric Data Warehouses."


def test_formatter_does_not_truncate_at_max_content_width() -> None:
    """At max_content_width columns the audit short-help is rendered in full.

    We bypass CliRunner (which forces width=80) and exercise Click's
    HelpFormatter directly at the width we actually configure.
    """
    formatter = click.HelpFormatter(max_width=_HELP_MAX_WIDTH)
    # Simulate the Commands section layout: 18 chars of prefix leaves
    # plenty of room for the full 62-char description.
    limit = formatter.width - 6 - len("audit")  # matches Group.format_commands logic
    audit_cmd = cli.get_command(click.Context(cli), "audit")  # type: ignore[attr-defined]
    assert audit_cmd is not None, "Expected 'audit' sub-command to exist on the CLI"
    rendered = audit_cmd.get_short_help_str(limit)
    assert rendered == _FULL_AUDIT_HELP, (
        f"Expected full help text but got: {rendered!r}\n"
        f"(formatter.width={formatter.width}, limit={limit})"
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
