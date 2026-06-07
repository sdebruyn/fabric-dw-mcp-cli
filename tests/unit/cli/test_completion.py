"""Tests for completion sub-commands — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestCompletionInstallPrint:
    """completion install --print outputs a non-empty shell snippet."""

    def test_bash_print_exits_zero(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "bash", "--print"])
        assert result.exit_code == 0

    def test_bash_print_contains_complete(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "bash", "--print"])
        assert result.exit_code == 0
        assert "complete" in result.output
        assert len(result.output.strip()) > 0

    def test_zsh_print_exits_zero(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "zsh", "--print"])
        assert result.exit_code == 0

    def test_zsh_print_nonempty(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "zsh", "--print"])
        assert result.exit_code == 0
        assert len(result.output.strip()) > 0

    def test_fish_print_exits_zero(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "fish", "--print"])
        assert result.exit_code == 0

    def test_fish_print_contains_complete_c_fabric_dw(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "fish", "--print"])
        assert result.exit_code == 0
        assert "complete" in result.output
        assert "fabric-dw" in result.output

    def test_unknown_shell_exits_nonzero(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "powershell"])
        assert result.exit_code != 0


class TestCompletionHelp:
    """completion --help is well-formed."""

    def test_completion_help_exits_zero(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "--help"])
        assert result.exit_code == 0

    def test_completion_install_help_exits_zero(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "--help"])
        assert result.exit_code == 0
