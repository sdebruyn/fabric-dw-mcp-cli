"""Tests for global options accepted after the subcommand.

Covers:
- Before-position (existing behaviour, regression test)
- After the leaf command (new behaviour)
- After/with a sub-group
- Help still renders global flags in command help
- -h / --help still work
- Collision: dbt init --auth is dbt's own option (global injection skipped)
"""

from __future__ import annotations

import contextlib
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from fabric_dw.auth import CredentialMode
from fabric_dw.cli._context import CliContext
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

    def test_auth_in_leaf_help(self, runner: CliRunner) -> None:
        """--auth is injected into leaf commands that do not already declare it."""
        result = runner.invoke(cli, ["workspaces", "list", "--help"])
        assert result.exit_code == 0, result.output
        assert "--auth" in result.output

    def test_workspace_flag_in_leaf_help(self, runner: CliRunner) -> None:
        """-w / --workspace appears in leaf command help."""
        result = runner.invoke(cli, ["workspaces", "list", "--help"])
        assert result.exit_code == 0, result.output
        assert "--workspace" in result.output

    def test_retry_flags_in_leaf_help(self, runner: CliRunner) -> None:
        """--max-429-retries and --retry-deadline appear in leaf command help."""
        result = runner.invoke(cli, ["workspaces", "list", "--help"])
        assert result.exit_code == 0, result.output
        assert "--max-429-retries" in result.output
        assert "--retry-deadline" in result.output


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


# ---------------------------------------------------------------------------
# -w / --workspace: BEFORE (regression) and AFTER the leaf command
# ---------------------------------------------------------------------------


class TestWorkspaceFlag:
    """-w / --workspace can appear before or after the subcommand."""

    @pytest.fixture(autouse=True)
    def _mock_warehouses_list(self) -> object:
        """Patch the service layer so warehouses list runs without live HTTP."""
        with (
            patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
            ) as mock_http_cm,
            patch(
                "fabric_dw.cli.commands.warehouses.make_resolver",
            ) as mock_make_resolver,
            patch(
                "fabric_dw.cli.commands.warehouses._warehouses_svc.list_warehouses",
                new=AsyncMock(return_value=[]),
            ),
        ):
            mock_http = AsyncMock()
            mock_http_cm.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http_cm.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_resolver = MagicMock()
            mock_resolver.workspace_id = AsyncMock(return_value="fake-ws-uuid")
            mock_make_resolver.return_value = (mock_resolver, MagicMock())
            yield

    def test_workspace_before_subcommand(self, runner: CliRunner) -> None:
        """-w BEFORE the subcommand sets the workspace (regression)."""
        result = runner.invoke(cli, ["-w", "myws", "warehouses", "list"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

    def test_workspace_short_after_leaf(self, runner: CliRunner) -> None:
        """-w AFTER the leaf command sets ctx.obj.workspace identically.

        warehouses list calls resolve_workspace(ctx), which raises UsageError
        if ctx.workspace is None and no default is configured.  Passing -w in
        trailing position must set ctx.obj.workspace so the command succeeds.
        """
        captured: dict[str, str | None] = {}

        def _capture_resolve_workspace(ctx: CliContext) -> str:
            captured["workspace"] = ctx.workspace
            return "fake-ws-name"

        with patch(
            "fabric_dw.cli.commands.warehouses.resolve_workspace",
            side_effect=_capture_resolve_workspace,
        ):
            result = runner.invoke(
                cli, ["warehouses", "list", "-w", "myws"], catch_exceptions=False
            )
        assert result.exit_code == 0, result.output
        assert captured["workspace"] == "myws"

    def test_workspace_long_after_leaf(self, runner: CliRunner) -> None:
        """--workspace AFTER the leaf command resolves identically to the before form."""
        captured: dict[str, str | None] = {}

        def _capture_resolve_workspace(ctx: CliContext) -> str:
            captured["workspace"] = ctx.workspace
            return "fake-ws-name"

        with patch(
            "fabric_dw.cli.commands.warehouses.resolve_workspace",
            side_effect=_capture_resolve_workspace,
        ):
            result = runner.invoke(
                cli, ["warehouses", "list", "--workspace", "myws"], catch_exceptions=False
            )
        assert result.exit_code == 0, result.output
        assert captured["workspace"] == "myws"

    def test_workspace_both_positions(self, runner: CliRunner) -> None:
        """-w before AND after the subcommand does not cause a parse error."""
        with patch(
            "fabric_dw.cli.commands.warehouses.resolve_workspace",
            return_value="fake-ws-name",
        ):
            result = runner.invoke(
                cli,
                ["-w", "first", "warehouses", "list", "-w", "second"],
                catch_exceptions=False,
            )
        # Trailing value wins over the root-level value (last-writer semantics).
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# --auth: BEFORE (regression) and AFTER the leaf command
# ---------------------------------------------------------------------------


class TestAuthFlag:
    """--auth can appear before or after the subcommand (except on dbt init)."""

    @pytest.fixture(autouse=True)
    def _mock_list(self) -> object:
        mock_ws = MagicMock()
        mock_ws.model_dump.return_value = {"id": "ws-1", "displayName": "Test"}
        with patch(
            "fabric_dw.cli.commands.workspaces._workspaces_svc.list_all",
            new=AsyncMock(return_value=[mock_ws]),
        ):
            yield

    def test_auth_before_subcommand(self, runner: CliRunner) -> None:
        """--auth BEFORE the subcommand resolves the auth mode (regression)."""
        result = runner.invoke(
            cli, ["--auth", "default", "workspaces", "list"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output

    def test_auth_after_leaf_command(self, runner: CliRunner) -> None:
        """--auth AFTER the leaf command sets ctx.obj.auth identically.

        The resolved CredentialMode must match what the before-position form
        produces.
        """
        ctx_holder: list[CliContext] = []

        @contextlib.asynccontextmanager
        async def _fake_build_http_client(ctx: CliContext) -> object:  # type: ignore[misc]
            ctx_holder.append(ctx)
            yield AsyncMock()

        with patch(
            "fabric_dw.cli.commands.workspaces.build_http_client",
            side_effect=_fake_build_http_client,
        ):
            result = runner.invoke(
                cli, ["workspaces", "list", "--auth", "default"], catch_exceptions=False
            )

        assert result.exit_code == 0, result.output
        assert len(ctx_holder) == 1
        assert ctx_holder[0].auth == CredentialMode.DEFAULT

    def test_auth_invalid_value_after_leaf(self, runner: CliRunner) -> None:
        """--auth with an invalid value after the subcommand yields a usage error."""
        result = runner.invoke(cli, ["workspaces", "list", "--auth", "notamode"])
        # Click rejects invalid Choice values with exit code 2.
        assert result.exit_code == 2, result.output


# ---------------------------------------------------------------------------
# --max-429-retries / --retry-deadline: BEFORE (regression) and AFTER
# ---------------------------------------------------------------------------


class TestRetryOptionsFlag:
    """--max-429-retries and --retry-deadline can appear after the subcommand."""

    @pytest.fixture(autouse=True)
    def _mock_list(self) -> object:
        mock_ws = MagicMock()
        mock_ws.model_dump.return_value = {"id": "ws-1", "displayName": "Test"}
        with patch(
            "fabric_dw.cli.commands.workspaces._workspaces_svc.list_all",
            new=AsyncMock(return_value=[mock_ws]),
        ):
            yield

    @staticmethod
    def _make_capturing_http(ctx_holder: list[CliContext]) -> object:
        """Return an async-context-manager factory that records the CliContext."""

        @contextlib.asynccontextmanager
        async def _fake(ctx: CliContext) -> object:  # type: ignore[misc]
            ctx_holder.append(ctx)
            yield AsyncMock()

        return _fake

    def test_max_429_retries_before_subcommand(self, runner: CliRunner) -> None:
        """--max-429-retries BEFORE the subcommand sets the retry count (regression)."""
        result = runner.invoke(
            cli, ["--max-429-retries", "3", "workspaces", "list"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output

    def test_max_429_retries_after_leaf(self, runner: CliRunner) -> None:
        """--max-429-retries AFTER the leaf command sets ctx.obj.max_429_retries."""
        ctx_holder: list[CliContext] = []
        with patch(
            "fabric_dw.cli.commands.workspaces.build_http_client",
            side_effect=self._make_capturing_http(ctx_holder),
        ):
            result = runner.invoke(
                cli, ["workspaces", "list", "--max-429-retries", "7"], catch_exceptions=False
            )
        assert result.exit_code == 0, result.output
        assert len(ctx_holder) == 1
        assert ctx_holder[0].max_429_retries == 7

    def test_retry_deadline_before_subcommand(self, runner: CliRunner) -> None:
        """--retry-deadline BEFORE the subcommand sets the deadline (regression)."""
        result = runner.invoke(
            cli, ["--retry-deadline", "60", "workspaces", "list"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output

    def test_retry_deadline_after_leaf(self, runner: CliRunner) -> None:
        """--retry-deadline AFTER the leaf command sets ctx.obj.retry_deadline_s."""
        ctx_holder: list[CliContext] = []
        with patch(
            "fabric_dw.cli.commands.workspaces.build_http_client",
            side_effect=self._make_capturing_http(ctx_holder),
        ):
            result = runner.invoke(
                cli, ["workspaces", "list", "--retry-deadline", "120"], catch_exceptions=False
            )
        assert result.exit_code == 0, result.output
        assert len(ctx_holder) == 1
        assert ctx_holder[0].retry_deadline_s == 120


# ---------------------------------------------------------------------------
# Collision: dbt init --auth is dbt's own option, not the global auth flag
# ---------------------------------------------------------------------------


class TestDbtAuthCollision:
    """The global --auth is not injected into dbt init (collision with dbt's own --auth).

    dbt init declares "--auth" with destination "dbt_auth_override" (a dbt-
    specific auth mode selector with a different Choice set).  The injection
    logic skips "--auth" for that command.  Users who need to set the global
    auth mode for dbt commands must use the pre-subcommand position:
    "fabric-dw --auth <mode> dbt init ...".
    """

    def test_dbt_auth_not_injected_as_global(self, runner: CliRunner) -> None:
        """dbt init --help shows its own --auth only -- global injection is skipped.

        The dbt-local --auth shows choices like [auto|cli|serviceprincipal|...].
        If the global --auth were also injected, a second option block would
        appear with the global CredentialMode choices.  Exactly one --auth
        option block must appear in the output.
        """
        result = runner.invoke(cli, ["dbt", "init", "--help"])
        assert result.exit_code == 0, result.output
        # Count option-header occurrences: "--auth [" marks an option block line.
        # The dbt help text also contains "--auth mode" in the description text,
        # but that does not start a new option block and does not include "[".
        assert result.output.count("--auth [") == 1

    def test_global_auth_before_dbt_init(self, runner: CliRunner) -> None:
        """The global --auth in pre-subcommand position still works for dbt init."""
        # We only verify that Click does not reject the argument; we do not
        # actually run the dbt scaffold (no mocks for the full dbt init flow).
        result = runner.invoke(cli, ["--auth", "default", "dbt", "--help"])
        assert result.exit_code == 0, result.output
