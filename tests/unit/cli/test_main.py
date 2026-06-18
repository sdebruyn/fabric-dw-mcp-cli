"""Tests for the Click CLI entry-point — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import logging
import sys
from collections.abc import Generator
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import fabric_dw.cli as _cli_pkg
import fabric_dw.cli._main as _main_mod
import fabric_dw.telemetry as _tel
from fabric_dw.cli._context import CliContext
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

    def test_global_workspace_option_is_listed(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "--workspace" in result.output or "-w" in result.output


class TestCliWorkspaceOption:
    """The root -w / --workspace option populates CliContext.workspace."""

    def _captured_workspace(self, args: list[str]) -> str | None:
        """Invoke *args* and return the workspace stored on the built CliContext.

        Spies on the ``CliContext`` constructor so the workspace passed by the
        root ``cli`` callback can be inspected. ``cache --help`` runs the root
        callback (which builds ctx.obj) without any network access.
        """
        captured: list[CliContext] = []
        original = _main_mod.CliContext

        def _spy(**kwargs: object) -> CliContext:
            obj = original(**kwargs)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            captured.append(obj)
            return obj

        with patch.object(_main_mod, "CliContext", _spy):
            runner = CliRunner()
            runner.invoke(cli, args)
        assert captured, "root callback did not construct a CliContext"
        return captured[-1].workspace

    def test_workspace_long_option_sets_context(self) -> None:
        args = ["--workspace", "Sales WS", "cache", "--help"]
        assert self._captured_workspace(args) == "Sales WS"

    def test_workspace_short_option_sets_context(self) -> None:
        guid = "a1b2c3d4-0000-0000-0000-000000000000"
        assert self._captured_workspace(["-w", guid, "cache", "--help"]) == guid

    def test_workspace_defaults_to_none_when_absent(self) -> None:
        assert self._captured_workspace(["cache", "--help"]) is None


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


class TestCliVerboseFlag:
    """The -v / --verbose flag must wire setup_logging with DEBUG level."""

    def test_verbose_flag_calls_setup_logging_with_debug(self) -> None:
        """When -v is passed, setup_logging should be called with logging.DEBUG."""
        runner = CliRunner()
        with patch("fabric_dw.cli._main.setup_logging") as mock_setup:
            # Use cache --help to trigger the group callback without network calls
            result = runner.invoke(cli, ["-v", "cache", "--help"])
            assert result.exit_code == 0
            mock_setup.assert_called_once_with(logging.DEBUG)

    def test_no_verbose_flag_calls_setup_logging_with_info(self) -> None:
        """Without -v, setup_logging should be called with logging.INFO."""
        runner = CliRunner()
        with patch("fabric_dw.cli._main.setup_logging") as mock_setup:
            # Use cache --help to trigger the group callback without network calls
            result = runner.invoke(cli, ["cache", "--help"])
            assert result.exit_code == 0
            mock_setup.assert_called_once_with(logging.INFO)


class TestHelpTelemetrySuppression:
    """Help invocations must suppress all telemetry (no SDK init, no network flush)."""

    @pytest.fixture(autouse=True)
    def _reset_suppress_flag(self) -> Generator[None, None, None]:
        """Reset the suppress_telemetry flag after every test to avoid state leakage."""
        _tel.suppress_telemetry(value=False)
        yield
        _tel.suppress_telemetry(value=False)

    @pytest.mark.parametrize("help_flag", ["-h", "--help"])
    def test_main_suppresses_telemetry_when_help_flag_in_argv(
        self, help_flag: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() must suppress telemetry when a help flag appears in sys.argv.

        We verify by checking that _SUPPRESSED is True after main() detects
        the help flag, without needing to let the real cli() execute.
        """
        monkeypatch.setattr(sys, "argv", ["fdw", "config", help_flag])

        # Start unsuppressed.
        _tel.suppress_telemetry(value=False)

        # Patch cli() in the __init__ module so main() doesn't actually run Click.
        with patch.object(_cli_pkg, "cli", autospec=False):
            _cli_pkg.main()

        # After main(), the suppress flag must be set.
        assert _tel._SUPPRESSED is True, (  # type: ignore[attr-defined]
            f"_SUPPRESSED must be True after main() when {help_flag!r} is in argv"
        )

    def test_main_does_not_suppress_telemetry_for_normal_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() must NOT suppress telemetry for a normal (non-help) invocation."""
        monkeypatch.setattr(sys, "argv", ["fdw", "config", "get"])

        _tel.suppress_telemetry(value=False)  # start unsuppressed

        with patch.object(_cli_pkg, "cli", autospec=False):
            _cli_pkg.main()

        assert _tel._SUPPRESSED is False, (  # type: ignore[attr-defined]
            "_SUPPRESSED must remain False when no help flag is present"
        )

    def test_get_tracer_not_called_on_subcommand_help(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a subcommand is invoked with --help, _get_tracer must not be called."""
        monkeypatch.setattr(sys, "argv", ["fdw", "config", "--help"])

        # Suppress from the start (as main() would do).
        _tel.suppress_telemetry()

        tracer_calls: list[str] = []
        with patch.object(_tel, "_get_tracer", side_effect=lambda: tracer_calls.append("called")):  # type: ignore[attr-defined]
            runner = CliRunner()
            result = runner.invoke(cli, ["config", "--help"])

        assert result.exit_code == 0
        assert tracer_calls == [], "_get_tracer must not be called when telemetry is suppressed"

    def test_subcommand_help_exits_without_telemetry_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After suppress_telemetry(), telemetry_enabled() must return False."""
        monkeypatch.setattr(sys, "argv", ["fdw", "config", "-h"])
        _tel.suppress_telemetry()

        assert _tel.telemetry_enabled() is False, (
            "telemetry_enabled() must return False when suppress_telemetry() has been called"
        )
