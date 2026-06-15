"""Tests for global options (--json, -y/--yes, -v/--verbose) accepted after subcommand.

Covers:
- Before-position (existing behaviour, regression test)
- After the leaf command (new behaviour)
- After/with a sub-group
- Help still renders global flags in command help
- -h / --help still work
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# --json: BEFORE (regression) and AFTER the leaf command
# ---------------------------------------------------------------------------


class TestJsonFlag:
    """--json can appear before or after the subcommand."""

    @pytest.fixture
    def patched_list(self) -> object:
        mock_ws = MagicMock()
        mock_ws.model_dump.return_value = {"id": "ws-1", "displayName": "Test"}
        with patch(
            "fabric_dw.cli.commands.workspaces._workspaces_svc.list_all",
            new=AsyncMock(return_value=[mock_ws]),
        ) as p:
            yield p

    def test_json_before_subcommand(self, runner: CliRunner, patched_list: object) -> None:  # noqa: ARG002
        """--json BEFORE subcommand still works (regression)."""
        result = runner.invoke(cli, ["--json", "workspaces", "list"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_json_after_leaf_command(self, runner: CliRunner, patched_list: object) -> None:  # noqa: ARG002
        """--json AFTER the leaf command produces JSON output."""
        result = runner.invoke(cli, ["workspaces", "list", "--json"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_json_after_subgroup(self, runner: CliRunner, patched_list: object) -> None:  # noqa: ARG002
        """--json between sub-group and leaf command also works."""
        result = runner.invoke(cli, ["workspaces", "--json", "list"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# -y / --yes: BEFORE (regression) and AFTER the leaf command
# ---------------------------------------------------------------------------


class TestYesFlag:
    """--yes / -y can appear before or after the subcommand."""

    @pytest.fixture
    def patched_set_collation(self) -> object:
        with (
            patch(
                "fabric_dw.cli.commands.workspaces._workspaces_svc.set_collation",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "fabric_dw.cli.commands.workspaces.resolve_workspace_id",
                new=AsyncMock(return_value="ws-id-1"),
            ),
            patch(
                "fabric_dw.cli.commands.workspaces.build_http_client",
            ) as mock_ctx_mgr,
        ):
            mock_http = AsyncMock()
            mock_ctx_mgr.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_ctx_mgr.return_value.__aexit__ = AsyncMock(return_value=None)
            yield

    def test_yes_before_subcommand(
        self,
        runner: CliRunner,
        patched_set_collation: object,  # noqa: ARG002
    ) -> None:
        """--yes BEFORE skips confirmation (regression)."""
        result = runner.invoke(
            cli,
            ["-y", "workspaces", "set-collation", "my-ws", "Latin1_General_100_BIN2_UTF8"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        # Prompt was skipped; command succeeded without interaction
        assert "Aborted" not in result.output

    def test_yes_after_leaf_command(
        self,
        runner: CliRunner,
        patched_set_collation: object,  # noqa: ARG002
    ) -> None:
        """--yes AFTER the leaf command skips confirmation."""
        result = runner.invoke(
            cli,
            ["workspaces", "set-collation", "my-ws", "Latin1_General_100_BIN2_UTF8", "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Aborted" not in result.output

    def test_yes_short_after_leaf_command(
        self,
        runner: CliRunner,
        patched_set_collation: object,  # noqa: ARG002
    ) -> None:
        """-y AFTER the leaf command skips confirmation."""
        result = runner.invoke(
            cli,
            ["workspaces", "set-collation", "my-ws", "Latin1_General_100_BIN2_UTF8", "-y"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Aborted" not in result.output


# ---------------------------------------------------------------------------
# -v / --verbose: BEFORE (regression) and AFTER the leaf command
# ---------------------------------------------------------------------------


class TestVerboseFlag:
    """--verbose / -v can appear before or after the subcommand."""

    @pytest.fixture(autouse=True)
    def _restore_log_level(self) -> object:
        """Capture and restore the fabric_dw logger level after each test.

        setup_logging(DEBUG) mutates the global logger for the process
        lifetime.  This fixture ensures that verbose tests are hermetically
        isolated regardless of execution order.
        """
        pkg_logger = logging.getLogger("fabric_dw")
        original = pkg_logger.level
        yield
        pkg_logger.setLevel(original)

    @pytest.fixture
    def patched_list(self) -> object:
        mock_ws = MagicMock()
        mock_ws.model_dump.return_value = {"id": "ws-1", "displayName": "Test"}
        with patch(
            "fabric_dw.cli.commands.workspaces._workspaces_svc.list_all",
            new=AsyncMock(return_value=[mock_ws]),
        ):
            yield

    def test_verbose_before_subcommand(
        self,
        runner: CliRunner,
        patched_list: object,  # noqa: ARG002
    ) -> None:
        """--verbose BEFORE subcommand enables DEBUG logging (regression)."""
        result = runner.invoke(cli, ["-v", "workspaces", "list"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        pkg_logger = logging.getLogger("fabric_dw")
        assert pkg_logger.level == logging.DEBUG

    def test_verbose_after_leaf_command(
        self,
        runner: CliRunner,
        patched_list: object,  # noqa: ARG002
    ) -> None:
        """--verbose AFTER the leaf command enables DEBUG logging."""
        result = runner.invoke(cli, ["workspaces", "list", "--verbose"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        pkg_logger = logging.getLogger("fabric_dw")
        assert pkg_logger.level == logging.DEBUG

    def test_verbose_short_after_leaf_command(
        self,
        runner: CliRunner,
        patched_list: object,  # noqa: ARG002
    ) -> None:
        """-v AFTER the leaf command enables DEBUG logging."""
        result = runner.invoke(cli, ["workspaces", "list", "-v"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        pkg_logger = logging.getLogger("fabric_dw")
        assert pkg_logger.level == logging.DEBUG


# ---------------------------------------------------------------------------
# Help output: global flags appear in command help
# ---------------------------------------------------------------------------


class TestHelpOutput:
    """Global flags are visible in command help and help still works."""

    def test_help_still_works_on_leaf(self, runner: CliRunner) -> None:
        """--help / -h still works on a leaf command after injection."""
        result = runner.invoke(cli, ["workspaces", "list", "--help"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output

    def test_short_help_still_works_on_leaf(self, runner: CliRunner) -> None:
        """-h still works on a leaf command."""
        result = runner.invoke(cli, ["workspaces", "list", "-h"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output

    def test_json_flag_in_leaf_help(self, runner: CliRunner) -> None:
        """--json appears in leaf command help."""
        result = runner.invoke(cli, ["workspaces", "list", "--help"])
        assert result.exit_code == 0, result.output
        assert "--json" in result.output

    def test_yes_flag_in_leaf_help(self, runner: CliRunner) -> None:
        """-y / --yes appears in leaf command help."""
        result = runner.invoke(cli, ["workspaces", "list", "--help"])
        assert result.exit_code == 0, result.output
        assert "--yes" in result.output

    def test_verbose_flag_in_leaf_help(self, runner: CliRunner) -> None:
        """-v / --verbose appears in leaf command help."""
        result = runner.invoke(cli, ["workspaces", "list", "--help"])
        assert result.exit_code == 0, result.output
        assert "--verbose" in result.output

    def test_help_on_subgroup_still_works(self, runner: CliRunner) -> None:
        """--help still works on a sub-group."""
        result = runner.invoke(cli, ["workspaces", "--help"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output

    def test_root_help_still_works(self, runner: CliRunner) -> None:
        """--help still works on the root command."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output

    def test_auth_not_in_leaf_help(self, runner: CliRunner) -> None:
        """--auth is intentionally NOT injected into leaf commands."""
        result = runner.invoke(cli, ["workspaces", "list", "--help"])
        assert result.exit_code == 0, result.output
        # --auth only appears at the root level; not injected into leaves
        assert "--auth" not in result.output


# ---------------------------------------------------------------------------
# Idempotency: no collision / double-injection
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Global flags are injected only once even if both root and leaf set them."""

    def test_json_both_positions(self, runner: CliRunner) -> None:
        """--json before AND after the subcommand doesn't cause an error."""
        mock_ws = MagicMock()
        mock_ws.model_dump.return_value = {"id": "ws-1", "displayName": "Test"}
        with patch(
            "fabric_dw.cli.commands.workspaces._workspaces_svc.list_all",
            new=AsyncMock(return_value=[mock_ws]),
        ):
            result = runner.invoke(
                cli, ["--json", "workspaces", "list", "--json"], catch_exceptions=False
            )
        # Should not raise "Got multiple values for option" or similar
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
