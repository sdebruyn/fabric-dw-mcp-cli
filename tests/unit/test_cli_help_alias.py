"""Tests that -h is accepted as a short alias for --help at every CLI level.

Covers: root group, a sub-group (warehouses), and a nested leaf command
(warehouses list).  Propagation must work through the custom
_InstrumentedGroup class and the telemetry patching applied to sub-groups.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _invoke_help(runner: CliRunner, args: list[str]) -> None:
    """Assert that *args* produces a successful help page."""
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, (
        f"Expected exit_code 0 for {args!r}, got {result.exit_code}.\nOutput:\n{result.output}"
    )
    assert "Usage:" in result.output, (
        f"Expected 'Usage:' in output for {args!r}.\nOutput:\n{result.output}"
    )


def test_root_short_help(runner: CliRunner) -> None:
    """-h on the root group shows help."""
    _invoke_help(runner, ["-h"])


def test_root_long_help(runner: CliRunner) -> None:
    """--help on the root group still works."""
    _invoke_help(runner, ["--help"])


def test_subgroup_short_help(runner: CliRunner) -> None:
    """-h on a sub-group shows help."""
    _invoke_help(runner, ["warehouses", "-h"])


def test_subgroup_long_help(runner: CliRunner) -> None:
    """--help on a sub-group still works."""
    _invoke_help(runner, ["warehouses", "--help"])


def test_leaf_short_help(runner: CliRunner) -> None:
    """-h on a nested leaf command shows help."""
    _invoke_help(runner, ["warehouses", "list", "-h"])


def test_leaf_long_help(runner: CliRunner) -> None:
    """--help on a nested leaf command still works."""
    _invoke_help(runner, ["warehouses", "list", "--help"])
