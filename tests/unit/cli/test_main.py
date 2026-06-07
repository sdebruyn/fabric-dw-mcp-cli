"""Tests for the Click CLI entry-point — written BEFORE the implementation (TDD)."""

from __future__ import annotations

from click.testing import CliRunner

from fabric_dw.cli._main import cli


class TestCliHelp:
    """Top-level --help is well-formed and lists sub-commands."""

    def test_help_exits_zero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0

    def test_help_mentions_cache(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "cache" in result.output

    def test_global_json_flag_is_listed(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "--json" in result.output

    def test_global_yes_flag_is_listed(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "--yes" in result.output or "-y" in result.output

    def test_global_auth_option_is_listed(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "--auth" in result.output

    def test_global_verbose_flag_is_listed(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "--verbose" in result.output or "-v" in result.output


class TestCliUnknownCommand:
    """Unknown commands return a non-zero exit code."""

    def test_unknown_command_returns_nonzero(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["not-a-real-command"])
        assert result.exit_code != 0


class TestCliVersion:
    """CLI version option (smoke test — just checks it runs)."""

    def test_no_args_shows_help_or_usage(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, [])
        # With invoke_without_command=False, missing subcommand should show usage
        assert result.exit_code != 0 or "Usage" in result.output or "cache" in result.output
