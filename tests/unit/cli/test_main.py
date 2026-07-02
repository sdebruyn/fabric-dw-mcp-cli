"""Tests for the Click CLI entry-point — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import logging
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import fabric_dw
import fabric_dw.cli as _cli_pkg
import fabric_dw.cli._main as _main_mod
import fabric_dw.telemetry as _tel
import fabric_dw.telemetry as _telemetry_mod
from fabric_dw.auth import CredentialMode
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._main import cli
from fabric_dw.config import Defaults, UserConfig, save_config
from fabric_dw.logging import setup_logging


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

    def test_no_args_shows_help_or_usage(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, [])
        # With invoke_without_command=False, missing subcommand should show usage
        assert result.exit_code != 0 or "Usage" in result.output or "cache" in result.output


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


class TestCliTopLevelErrorGuard:
    """fabric_dw.cli.main() must never let an unexpected exception surface as a
    raw Python traceback (#972).

    Click's own ``BaseCommand.main()`` only cleanly handles ``ClickException``/
    ``UsageError``/``Abort``/EPIPE ``OSError``; anything else (e.g. a raw
    driver error that slipped past a connect-retry-exhaustion path) propagates
    unchanged.  ``main()`` wraps ``cli()`` with a defense-in-depth guard; these
    tests exercise that guard directly by replacing ``cli()`` with a callable
    that raises, mirroring the existing ``TestHelpTelemetrySuppression`` pattern.
    """

    def test_unexpected_exception_prints_clean_single_line_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["fdw", "sql", "-q", "SELECT 1"])

        def _boom() -> None:
            raise RuntimeError(
                "Driver Error: Client unable to establish connection; "
                "DDBC Error: [Microsoft]TCP Provider: Error code 0x102"
            )

        with patch.object(_cli_pkg, "cli", _boom), pytest.raises(SystemExit) as exc_info:
            _cli_pkg.main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.err.startswith("Error:")
        assert "TCP Provider" in captured.err
        assert "Traceback" not in captured.err
        assert captured.err.strip().count("\n") == 0, "error output must be a single line"
        assert captured.out == ""

    def test_unexpected_exception_omits_traceback_by_default(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["fdw", "sql", "-q", "SELECT 1"])

        def _boom() -> None:
            raise RuntimeError("boom")

        with patch.object(_cli_pkg, "cli", _boom), pytest.raises(SystemExit):
            _cli_pkg.main()

        assert "Traceback" not in capsys.readouterr().err

    def test_unexpected_exception_includes_traceback_when_debug_logging_enabled(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The full traceback stays available behind the existing -v / --verbose convention.

        Verbose logging is enabled via ``setup_logging(DEBUG)`` on the
        ``fabric_dw`` logger, never shown by default, since it may contain
        driver internals.
        """
        monkeypatch.setattr(sys, "argv", ["fdw", "sql", "-q", "SELECT 1"])
        setup_logging(logging.DEBUG)  # mirrors what the -v flag does

        def _boom() -> None:
            raise RuntimeError("boom")

        with patch.object(_cli_pkg, "cli", _boom), pytest.raises(SystemExit):
            _cli_pkg.main()

        assert "Traceback (most recent call last)" in capsys.readouterr().err

    def test_click_exception_from_real_command_is_unaffected(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A Click usage error from a real invocation still renders via Click itself.

        Goes through the actual guarded entry point (fabric_dw.cli.main()), not
        CliRunner (which bypasses main() and never exercises the guard), so this
        genuinely proves the new top-level guard does not interfere with Click's
        own error rendering.
        """
        monkeypatch.setattr(sys, "argv", ["fdw", "not-a-real-command"])

        with pytest.raises(SystemExit) as exc_info:
            _cli_pkg.main()

        # Click's own UsageError for an unknown command exits 2, unaffected by
        # the guard's own exit code (_UNEXPECTED_ERROR_EXIT_CODE = 1).
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "No such command" in captured.err
        assert "Traceback" not in captured.err
        assert "unexpected error" not in captured.err

    def test_keyboard_interrupt_is_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A raw KeyboardInterrupt that reaches main() propagates untouched.

        It must not be caught and rendered as "Error: unexpected error: ..." by
        the new guard -- Ctrl+C exits silently (via Python's default handling),
        exactly like the pre-#972 behaviour.
        """
        monkeypatch.setattr(sys, "argv", ["fdw", "sql", "-q", "SELECT 1"])

        def _boom() -> None:
            raise KeyboardInterrupt

        with patch.object(_cli_pkg, "cli", _boom), pytest.raises(KeyboardInterrupt):
            _cli_pkg.main()

        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""

    def test_system_exit_from_cli_propagates_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["fdw", "sql", "-q", "SELECT 1"])

        def _exit_5() -> None:
            raise SystemExit(5)

        with patch.object(_cli_pkg, "cli", _exit_5), pytest.raises(SystemExit) as exc_info:
            _cli_pkg.main()

        assert exc_info.value.code == 5


class TestCliVersion:
    """--version / -V flag: output format, exit code, and telemetry short-circuit."""

    def test_version_flag_exits_zero(self) -> None:
        """--version must exit 0."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0

    def test_version_flag_output_format(self) -> None:
        """--version output must be in the form 'fabric-dw <version>'."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.output.strip() == f"fabric-dw {fabric_dw.__version__}"

    def test_short_version_flag_exits_zero(self) -> None:
        """-V must exit 0."""
        runner = CliRunner()
        result = runner.invoke(cli, ["-V"])
        assert result.exit_code == 0

    def test_short_version_flag_output_format(self) -> None:
        """-V output must match --version: 'fabric-dw <version>'."""
        runner = CliRunner()
        result = runner.invoke(cli, ["-V"])
        assert result.output.strip() == f"fabric-dw {fabric_dw.__version__}"

    def test_version_flag_emits_no_telemetry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--version must short-circuit before any telemetry path is entered.

        The autouse ``_disable_telemetry_globally`` fixture sets
        ``FABRIC_DISABLE_TELEMETRY=1`` for every test in this file, which means
        telemetry calls are silently skipped at the ``telemetry_enabled()`` guard
        before they reach ``record_app_started`` / ``_InstrumentedGroup.invoke``.
        That makes a plain ``assert_not_called()`` check vacuous — it would pass
        even if the eager ``--version`` option were removed.

        To make the assertion meaningful, this test *enables* telemetry for its
        own scope (by removing all opt-out env vars) and mocks
        ``configure_azure_monitor`` so no real SDK initialisation or egress can
        occur.  It then asserts three independent invariants that can only hold
        simultaneously when the eager option genuinely short-circuits:

        1. ``_InstrumentedGroup.invoke`` was never entered.
        2. ``record_app_started`` was never called.
        3. ``fabric_dw.telemetry._sdk_initialised`` is still ``False`` (the SDK
           was never initialised — no ``configure_azure_monitor`` side-effects).
        """
        # Enable telemetry for this test only by removing all opt-out signals.
        monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
        monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
        monkeypatch.delenv("DO_NOT_TRACK", raising=False)
        # Prevent real SDK init / egress by pointing at a localhost dummy endpoint.
        monkeypatch.setenv(
            "FABRIC_TELEMETRY_CONNECTION_STRING",
            "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
            "IngestionEndpoint=https://localhost/",
        )
        # Reset _sdk_initialised so the module is in a clean state.
        _telemetry_mod._sdk_initialised = False  # type: ignore[attr-defined]

        runner = CliRunner()
        with (
            patch("azure.monitor.opentelemetry.configure_azure_monitor") as mock_configure,
            patch.object(
                _main_mod._InstrumentedGroup,
                "invoke",
                wraps=_main_mod._InstrumentedGroup.invoke,
            ) as mock_invoke,
            patch("fabric_dw.cli._main.record_app_started") as mock_started,
        ):
            result = runner.invoke(cli, ["--version"])

        assert result.exit_code == 0
        # The eager option must short-circuit before any of these are reached.
        mock_invoke.assert_not_called()
        mock_started.assert_not_called()
        mock_configure.assert_not_called()
        assert _telemetry_mod._sdk_initialised is False  # type: ignore[attr-defined]

    def test_version_flag_listed_in_help(self) -> None:
        """--version must appear in the root --help output."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "--version" in result.output


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


# ---------------------------------------------------------------------------
# CLI auth-mode resolution — 4-layer precedence (flag > env > config > default)
# ---------------------------------------------------------------------------


class TestCliAuthModeResolution:
    """The CLI must honor [defaults] auth_mode and FABRIC_AUTH with the correct precedence.

    Precedence (highest → lowest):
      1. --auth flag (only when EXPLICITLY passed by the user)
      2. FABRIC_AUTH environment variable (non-empty/non-whitespace)
      3. [defaults] auth_mode in config.toml
      4. Built-in default: CredentialMode.DEFAULT
    """

    def _captured_auth(
        self,
        args: list[str],
        env: dict[str, str] | None = None,
        config_path: Path | None = None,
    ) -> CredentialMode:
        """Invoke *args* and return the auth mode stored on the built CliContext.

        Spies on the CliContext constructor to capture the ``auth`` kwarg.
        ``cache --help`` triggers the root callback without any network access.
        """
        captured: list[CliContext] = []
        original = _main_mod.CliContext

        def _spy(**kwargs: object) -> CliContext:
            obj = original(**kwargs)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            captured.append(obj)
            return obj

        # Optionally point load_config at a temp config file to test config layer.
        with patch.object(_main_mod, "CliContext", _spy):
            runner = CliRunner(env=env or {})
            if config_path is not None:
                loaded = _main_mod.load_config(config_path)
                with patch("fabric_dw.cli._main.load_config", return_value=loaded):
                    runner.invoke(cli, args)
            else:
                runner.invoke(cli, args)
        assert captured, "root callback did not construct a CliContext"
        return captured[-1].auth

    def test_builtin_default_when_nothing_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no flag, no env, no config, auth falls back to CredentialMode.DEFAULT."""
        monkeypatch.delenv("FABRIC_AUTH", raising=False)
        auth = self._captured_auth(["cache", "--help"])
        assert auth == CredentialMode.DEFAULT

    def test_explicit_flag_wins_over_env_and_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--auth flag overrides FABRIC_AUTH env var."""
        monkeypatch.setenv("FABRIC_AUTH", "sp")
        auth = self._captured_auth(["--auth", "interactive", "cache", "--help"])
        assert auth == CredentialMode.INTERACTIVE

    def test_env_wins_over_builtin_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_AUTH env var is used when --auth is absent."""
        monkeypatch.setenv("FABRIC_AUTH", "sp")
        auth = self._captured_auth(["cache", "--help"])
        assert auth == CredentialMode.SERVICE_PRINCIPAL

    def test_config_wins_over_builtin_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """[defaults] auth_mode is used when --auth and FABRIC_AUTH are absent."""
        monkeypatch.delenv("FABRIC_AUTH", raising=False)
        cfg_path = tmp_path / "config.toml"
        save_config(
            UserConfig(defaults=Defaults(auth_mode="interactive")),
            path=cfg_path,
        )
        auth = self._captured_auth(["cache", "--help"], config_path=cfg_path)
        assert auth == CredentialMode.INTERACTIVE

    def test_env_wins_over_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FABRIC_AUTH env var overrides [defaults] auth_mode in config."""
        monkeypatch.setenv("FABRIC_AUTH", "sp")
        cfg_path = tmp_path / "config.toml"
        save_config(
            UserConfig(defaults=Defaults(auth_mode="interactive")),
            path=cfg_path,
        )
        auth = self._captured_auth(["cache", "--help"], config_path=cfg_path)
        assert auth == CredentialMode.SERVICE_PRINCIPAL

    def test_flag_wins_over_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Explicit --auth flag overrides [defaults] auth_mode in config."""
        monkeypatch.delenv("FABRIC_AUTH", raising=False)
        cfg_path = tmp_path / "config.toml"
        save_config(
            UserConfig(defaults=Defaults(auth_mode="interactive")),
            path=cfg_path,
        )
        auth = self._captured_auth(["--auth", "sp", "cache", "--help"], config_path=cfg_path)
        assert auth == CredentialMode.SERVICE_PRINCIPAL

    def test_empty_env_falls_through_to_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Empty/whitespace FABRIC_AUTH falls through to config, not an error."""
        monkeypatch.setenv("FABRIC_AUTH", "")
        cfg_path = tmp_path / "config.toml"
        save_config(
            UserConfig(defaults=Defaults(auth_mode="sp")),
            path=cfg_path,
        )
        auth = self._captured_auth(["cache", "--help"], config_path=cfg_path)
        assert auth == CredentialMode.SERVICE_PRINCIPAL

    def test_empty_env_falls_through_to_builtin_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty FABRIC_AUTH with no config yields the built-in default."""
        monkeypatch.setenv("FABRIC_AUTH", "")
        auth = self._captured_auth(["cache", "--help"])
        assert auth == CredentialMode.DEFAULT

    def test_invalid_env_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unrecognised FABRIC_AUTH surfaces a clear error (non-zero exit)."""
        monkeypatch.setenv("FABRIC_AUTH", "not-a-mode")
        runner = CliRunner()
        result = runner.invoke(cli, ["cache", "--help"])
        assert result.exit_code != 0
        assert "not-a-mode" in (result.output or "")

    def test_flag_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--auth value is case-insensitive (e.g. 'SP' is accepted)."""
        monkeypatch.delenv("FABRIC_AUTH", raising=False)
        auth = self._captured_auth(["--auth", "SP", "cache", "--help"])
        assert auth == CredentialMode.SERVICE_PRINCIPAL

    def test_env_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_AUTH value is case-insensitive (e.g. 'INTERACTIVE' is accepted)."""
        monkeypatch.setenv("FABRIC_AUTH", "INTERACTIVE")
        auth = self._captured_auth(["cache", "--help"])
        assert auth == CredentialMode.INTERACTIVE
