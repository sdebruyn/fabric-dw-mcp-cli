"""Tests for the config CLI sub-group."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import cli
from fabric_dw.config import Defaults, UserConfig, default_path, load_config
from fabric_dw.telemetry import telemetry_enabled


@pytest.fixture
def config_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XDG_CONFIG_HOME to a temp dir so tests are isolated."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# config show
# ---------------------------------------------------------------------------


class TestConfigShow:
    def test_show_empty_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0

    def test_show_empty_defaults_renders_none(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "show"])
        assert "workspace" in result.output.lower() or result.exit_code == 0

    def test_show_json_empty(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "defaults" in data

    def test_show_after_set(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "TestWS"])
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "TestWS" in result.output


# ---------------------------------------------------------------------------
# config set
# ---------------------------------------------------------------------------


class TestConfigSet:
    def test_set_workspace_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "workspace", "MyWS"])
        assert result.exit_code == 0

    def test_set_workspace_writes_file(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "SalesWS"])
        cfg = load_config(default_path())
        assert cfg.defaults.workspace == "SalesWS"

    def test_set_warehouse_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "warehouse", "MyWH"])
        assert result.exit_code == 0

    def test_set_warehouse_writes_file(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "warehouse", "SalesWH"])
        cfg = load_config(default_path())
        assert cfg.defaults.warehouse == "SalesWH"

    def test_set_workspace_preserves_warehouse(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "warehouse", "WH1"])
        runner.invoke(cli, ["config", "set", "workspace", "WS1"])
        cfg = load_config(default_path())
        assert cfg.defaults.workspace == "WS1"
        assert cfg.defaults.warehouse == "WH1"


# ---------------------------------------------------------------------------
# config unset
# ---------------------------------------------------------------------------


class TestConfigUnset:
    def test_unset_workspace_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS"])
        result = runner.invoke(cli, ["config", "unset", "workspace"])
        assert result.exit_code == 0

    def test_unset_warehouse_clears_key_only(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS1"])
        runner.invoke(cli, ["config", "set", "warehouse", "WH1"])
        runner.invoke(cli, ["config", "unset", "warehouse"])
        cfg = load_config(default_path())
        assert cfg.defaults.warehouse is None
        assert cfg.defaults.workspace == "WS1"

    def test_unset_workspace_clears_key_only(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS1"])
        runner.invoke(cli, ["config", "set", "warehouse", "WH1"])
        runner.invoke(cli, ["config", "unset", "workspace"])
        cfg = load_config(default_path())
        assert cfg.defaults.workspace is None
        assert cfg.defaults.warehouse == "WH1"

    def test_unset_nonexistent_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "unset", "workspace"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# config clear
# ---------------------------------------------------------------------------


class TestConfigClear:
    def test_clear_yes_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS"])
        result = runner.invoke(cli, ["--yes", "config", "clear"])
        assert result.exit_code == 0

    def test_clear_yes_wipes_file(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS"])
        runner.invoke(cli, ["config", "set", "warehouse", "WH"])
        runner.invoke(cli, ["--yes", "config", "clear"])
        cfg = load_config(default_path())
        assert cfg == UserConfig(defaults=Defaults())

    def test_clear_declined_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        """Declining config clear is a clean no-op (exit 0, policy: decline != error)."""
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS"])
        result = runner.invoke(cli, ["config", "clear"], input="n\n")
        assert result.exit_code == 0
        assert "Aborted." in result.output

    def test_clear_confirms_yes_input(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS"])
        result = runner.invoke(cli, ["config", "clear"], input="y\n")
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# config set / unset max-429-retries and retry-deadline
# ---------------------------------------------------------------------------


class TestConfigSetRetryBudget:
    def test_set_max_429_retries_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "max-429-retries", "15"])
        assert result.exit_code == 0

    def test_set_max_429_retries_writes_file(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "max-429-retries", "20"])
        cfg = load_config(default_path())
        assert cfg.defaults.max_429_retries == 20

    def test_set_max_429_retries_invalid_rejected(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "max-429-retries", "0"])
        assert result.exit_code != 0

    def test_set_retry_deadline_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "retry-deadline", "600"])
        assert result.exit_code == 0

    def test_set_retry_deadline_writes_file(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "retry-deadline", "450"])
        cfg = load_config(default_path())
        assert cfg.defaults.retry_deadline_s == 450

    def test_set_retry_deadline_invalid_rejected(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "retry-deadline", "0"])
        assert result.exit_code != 0

    def test_set_retries_preserves_workspace(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "MyWS"])
        runner.invoke(cli, ["config", "set", "max-429-retries", "12"])
        cfg = load_config(default_path())
        assert cfg.defaults.max_429_retries == 12
        assert cfg.defaults.workspace == "MyWS"


class TestConfigUnsetRetryBudget:
    def test_unset_max_429_retries_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "max-429-retries", "8"])
        result = runner.invoke(cli, ["config", "unset", "max-429-retries"])
        assert result.exit_code == 0

    def test_unset_max_429_retries_clears_key(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "max-429-retries", "8"])
        runner.invoke(cli, ["config", "set", "retry-deadline", "200"])
        runner.invoke(cli, ["config", "unset", "max-429-retries"])
        cfg = load_config(default_path())
        assert cfg.defaults.max_429_retries is None
        assert cfg.defaults.retry_deadline_s == 200  # preserved

    def test_unset_retry_deadline_clears_key(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "max-429-retries", "6"])
        runner.invoke(cli, ["config", "set", "retry-deadline", "300"])
        runner.invoke(cli, ["config", "unset", "retry-deadline"])
        cfg = load_config(default_path())
        assert cfg.defaults.retry_deadline_s is None
        assert cfg.defaults.max_429_retries == 6  # preserved


class TestConfigShowRetryBudget:
    def test_show_includes_retry_budget_fields(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "max-429-retries", "10"])
        runner.invoke(cli, ["config", "set", "retry-deadline", "300"])
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["defaults"]["max_429_retries"] == 10
        assert data["defaults"]["retry_deadline_s"] == 300

    def test_show_null_when_not_set(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["defaults"]["max_429_retries"] is None
        assert data["defaults"]["retry_deadline_s"] is None


# ---------------------------------------------------------------------------
# config set / unset / show for SQL retry knobs
# ---------------------------------------------------------------------------


class TestConfigSetSqlRetry:
    def test_set_sql_retry_deadline_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "sql-retry-deadline", "300"])
        assert result.exit_code == 0

    def test_set_sql_retry_deadline_writes_file(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-retry-deadline", "250"])
        cfg = load_config(default_path())
        assert cfg.defaults.sql_retry_deadline_s == 250

    def test_set_sql_retry_deadline_invalid_rejected(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "sql-retry-deadline", "0"])
        assert result.exit_code != 0

    def test_set_sql_retry_deadline_preserves_workspace(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "MyWS"])
        runner.invoke(cli, ["config", "set", "sql-retry-deadline", "180"])
        cfg = load_config(default_path())
        assert cfg.defaults.sql_retry_deadline_s == 180
        assert cfg.defaults.workspace == "MyWS"

    def test_set_sql_retry_executes_true_exits_zero(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "sql-retry-executes", "true"])
        assert result.exit_code == 0

    def test_set_sql_retry_executes_true_writes_file(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-retry-executes", "true"])
        cfg = load_config(default_path())
        assert cfg.defaults.sql_retry_executes is True

    def test_set_sql_retry_executes_false_writes_file(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-retry-executes", "false"])
        cfg = load_config(default_path())
        assert cfg.defaults.sql_retry_executes is False

    def test_set_sql_retry_executes_case_insensitive(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "sql-retry-executes", "True"])
        assert result.exit_code == 0
        cfg = load_config(default_path())
        assert cfg.defaults.sql_retry_executes is True


class TestConfigUnsetSqlRetry:
    def test_unset_sql_retry_deadline_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-retry-deadline", "200.0"])
        result = runner.invoke(cli, ["config", "unset", "sql-retry-deadline"])
        assert result.exit_code == 0

    def test_unset_sql_retry_deadline_clears_key(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-retry-deadline", "200.0"])
        runner.invoke(cli, ["config", "set", "sql-retry-executes", "true"])
        runner.invoke(cli, ["config", "unset", "sql-retry-deadline"])
        cfg = load_config(default_path())
        assert cfg.defaults.sql_retry_deadline_s is None
        assert cfg.defaults.sql_retry_executes is True  # preserved

    def test_unset_sql_retry_executes_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-retry-executes", "true"])
        result = runner.invoke(cli, ["config", "unset", "sql-retry-executes"])
        assert result.exit_code == 0

    def test_unset_sql_retry_executes_clears_key(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-retry-deadline", "150"])
        runner.invoke(cli, ["config", "set", "sql-retry-executes", "true"])
        runner.invoke(cli, ["config", "unset", "sql-retry-executes"])
        cfg = load_config(default_path())
        assert cfg.defaults.sql_retry_executes is None
        assert cfg.defaults.sql_retry_deadline_s == 150  # preserved


class TestConfigShowSqlRetry:
    def test_show_includes_sql_retry_fields(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-retry-deadline", "300"])
        runner.invoke(cli, ["config", "set", "sql-retry-executes", "true"])
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["defaults"]["sql_retry_deadline_s"] == 300
        assert data["defaults"]["sql_retry_executes"] is True

    def test_show_null_when_sql_retry_not_set(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["defaults"]["sql_retry_deadline_s"] is None
        assert data["defaults"]["sql_retry_executes"] is None


# ---------------------------------------------------------------------------
# config set / unset / show for telemetry disabled
# ---------------------------------------------------------------------------


class TestConfigSetTelemetryDisabled:
    def test_set_telemetry_disabled_true_exits_zero(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "telemetry", "disabled", "true"])
        assert result.exit_code == 0

    def test_set_telemetry_disabled_false_exits_zero(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "telemetry", "disabled", "false"])
        assert result.exit_code == 0

    def test_set_telemetry_disabled_true_writes_file(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "telemetry", "disabled", "true"])
        cfg = load_config(default_path())
        assert cfg.telemetry.disabled is True

    def test_set_telemetry_disabled_false_writes_file(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "telemetry", "disabled", "false"])
        cfg = load_config(default_path())
        assert cfg.telemetry.disabled is False

    def test_set_telemetry_disabled_case_insensitive(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "telemetry", "disabled", "True"])
        assert result.exit_code == 0
        cfg = load_config(default_path())
        assert cfg.telemetry.disabled is True

    def test_set_telemetry_disabled_preserves_other_keys(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS1"])
        runner.invoke(cli, ["config", "set", "telemetry", "disabled", "true"])
        cfg = load_config(default_path())
        assert cfg.telemetry.disabled is True
        assert cfg.defaults.workspace == "WS1"

    def test_set_telemetry_disabled_invalid_rejected(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "telemetry", "disabled", "maybe"])
        assert result.exit_code != 0


class TestConfigUnsetTelemetryDisabled:
    def test_unset_telemetry_disabled_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "telemetry", "disabled", "true"])
        result = runner.invoke(cli, ["config", "unset", "telemetry", "disabled"])
        assert result.exit_code == 0

    def test_unset_telemetry_disabled_clears_key(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "telemetry", "disabled", "true"])
        runner.invoke(cli, ["config", "unset", "telemetry", "disabled"])
        cfg = load_config(default_path())
        assert cfg.telemetry.disabled is None

    def test_unset_telemetry_disabled_preserves_other_keys(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS1"])
        runner.invoke(cli, ["config", "set", "telemetry", "disabled", "true"])
        runner.invoke(cli, ["config", "unset", "telemetry", "disabled"])
        cfg = load_config(default_path())
        assert cfg.telemetry.disabled is None
        assert cfg.defaults.workspace == "WS1"

    def test_unset_telemetry_disabled_nonexistent_exits_zero(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "unset", "telemetry", "disabled"])
        assert result.exit_code == 0


class TestConfigShowTelemetry:
    def test_show_includes_telemetry_section(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "telemetry", "disabled", "true"])
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "telemetry" in data
        assert data["telemetry"]["disabled"] is True

    def test_show_null_when_telemetry_not_set(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["telemetry"]["disabled"] is None


class TestConfigTelemetryEndToEnd:
    """End-to-end: CLI set → telemetry_enabled() reflects the change."""

    def test_set_telemetry_disabled_true_disables_telemetry(
        self,
        runner: CliRunner,
        config_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """config set telemetry disabled true → telemetry_enabled() is False."""
        _ = config_env
        # Run the CLI while the autouse env-var guard (_disable_telemetry_globally)
        # is still active so no real socket connection is attempted.
        result = runner.invoke(cli, ["config", "set", "telemetry", "disabled", "true"])
        assert result.exit_code == 0

        # Strip the env-var opt-out AFTER the CLI call so telemetry_enabled()
        # reflects only the config-file layer.
        monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
        monkeypatch.delenv("DO_NOT_TRACK", raising=False)

        assert telemetry_enabled() is False

    def test_set_telemetry_disabled_false_enables_telemetry(
        self,
        runner: CliRunner,
        config_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """config set telemetry disabled false → telemetry_enabled() is True."""
        _ = config_env
        # Run CLI invocations while the env-var guard is still active.
        runner.invoke(cli, ["config", "set", "telemetry", "disabled", "true"])
        runner.invoke(cli, ["config", "set", "telemetry", "disabled", "false"])

        # Only strip the env-var guard for the final assertion.
        monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
        monkeypatch.delenv("DO_NOT_TRACK", raising=False)

        assert telemetry_enabled() is True

    def test_unset_telemetry_disabled_enables_telemetry(
        self,
        runner: CliRunner,
        config_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """config unset telemetry disabled → telemetry_enabled() is True."""
        _ = config_env
        # Run CLI invocations while the env-var guard is still active.
        runner.invoke(cli, ["config", "set", "telemetry", "disabled", "true"])
        runner.invoke(cli, ["config", "unset", "telemetry", "disabled"])

        # Only strip the env-var guard for the final assertion.
        monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
        monkeypatch.delenv("DO_NOT_TRACK", raising=False)

        assert telemetry_enabled() is True


# ---------------------------------------------------------------------------
# config set/unset sql-pool
# ---------------------------------------------------------------------------


class TestConfigSetSqlPool:
    def test_set_sql_pool_false_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "sql-pool", "false"])
        assert result.exit_code == 0

    def test_set_sql_pool_false_writes_file(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-pool", "false"])
        cfg = load_config(default_path())
        assert cfg.defaults.sql_pool is False

    def test_set_sql_pool_true_writes_file(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-pool", "true"])
        cfg = load_config(default_path())
        assert cfg.defaults.sql_pool is True

    def test_set_sql_pool_case_insensitive(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "sql-pool", "False"])
        assert result.exit_code == 0
        cfg = load_config(default_path())
        assert cfg.defaults.sql_pool is False

    def test_set_sql_pool_preserves_other_keys(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "MyWS"])
        runner.invoke(cli, ["config", "set", "sql-retry-executes", "true"])
        runner.invoke(cli, ["config", "set", "sql-pool", "false"])
        cfg = load_config(default_path())
        assert cfg.defaults.sql_pool is False
        assert cfg.defaults.workspace == "MyWS"
        assert cfg.defaults.sql_retry_executes is True  # preserved


class TestConfigUnsetSqlPool:
    def test_unset_sql_pool_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-pool", "false"])
        result = runner.invoke(cli, ["config", "unset", "sql-pool"])
        assert result.exit_code == 0

    def test_unset_sql_pool_clears_key(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-pool", "false"])
        runner.invoke(cli, ["config", "set", "sql-retry-executes", "true"])
        runner.invoke(cli, ["config", "unset", "sql-pool"])
        cfg = load_config(default_path())
        assert cfg.defaults.sql_pool is None
        assert cfg.defaults.sql_retry_executes is True  # preserved


class TestConfigShowSqlPool:
    def test_show_includes_sql_pool_field(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "sql-pool", "false"])
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["defaults"]["sql_pool"] is False

    def test_show_null_when_sql_pool_not_set(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["defaults"]["sql_pool"] is None


# ---------------------------------------------------------------------------
# config set / unset / show for auth-mode
# ---------------------------------------------------------------------------


class TestConfigSetAuthMode:
    def test_set_auth_mode_default_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "auth-mode", "default"])
        assert result.exit_code == 0

    def test_set_auth_mode_interactive_exits_zero(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "auth-mode", "interactive"])
        assert result.exit_code == 0

    def test_set_auth_mode_sp_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "auth-mode", "sp"])
        assert result.exit_code == 0

    def test_set_auth_mode_writes_file(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "auth-mode", "interactive"])
        cfg = load_config(default_path())
        assert cfg.defaults.auth_mode == "interactive"

    def test_set_auth_mode_sp_writes_file(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "auth-mode", "sp"])
        cfg = load_config(default_path())
        assert cfg.defaults.auth_mode == "sp"

    def test_set_auth_mode_case_insensitive(self, runner: CliRunner, config_env: Path) -> None:
        """auth-mode choice is case-insensitive; stored as lowercase."""
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "auth-mode", "INTERACTIVE"])
        assert result.exit_code == 0
        cfg = load_config(default_path())
        assert cfg.defaults.auth_mode == "interactive"

    def test_set_auth_mode_invalid_rejected(self, runner: CliRunner, config_env: Path) -> None:
        """An invalid auth-mode value must be rejected by click.Choice."""
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "auth-mode", "managed_identity"])
        assert result.exit_code != 0

    def test_set_auth_mode_preserves_workspace(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "MyWS"])
        runner.invoke(cli, ["config", "set", "auth-mode", "interactive"])
        cfg = load_config(default_path())
        assert cfg.defaults.auth_mode == "interactive"
        assert cfg.defaults.workspace == "MyWS"


class TestConfigUnsetAuthMode:
    def test_unset_auth_mode_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "auth-mode", "interactive"])
        result = runner.invoke(cli, ["config", "unset", "auth-mode"])
        assert result.exit_code == 0

    def test_unset_auth_mode_clears_key(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "auth-mode", "interactive"])
        runner.invoke(cli, ["config", "set", "workspace", "WS1"])
        runner.invoke(cli, ["config", "unset", "auth-mode"])
        cfg = load_config(default_path())
        assert cfg.defaults.auth_mode is None
        assert cfg.defaults.workspace == "WS1"  # preserved


class TestConfigShowAuthMode:
    def test_show_includes_auth_mode_field(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "auth-mode", "interactive"])
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["defaults"]["auth_mode"] == "interactive"

    def test_show_null_when_auth_mode_not_set(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["defaults"]["auth_mode"] is None


# ---------------------------------------------------------------------------
# config set / unset / show for mcp workspace_allowlist
# ---------------------------------------------------------------------------


class TestConfigSetMcpWorkspaceAllowlist:
    def test_set_workspace_allowlist_exits_zero(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(
            cli, ["config", "set", "mcp", "workspace-allowlist", "Sales WS,Finance WS"]
        )
        assert result.exit_code == 0

    def test_set_workspace_allowlist_writes_list(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", "Sales WS,Finance WS"])
        cfg = load_config(default_path())
        assert cfg.mcp.workspace_allowlist == ["Sales WS", "Finance WS"]

    def test_set_workspace_allowlist_trims_whitespace(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(
            cli, ["config", "set", "mcp", "workspace-allowlist", "  Sales WS , Finance WS  "]
        )
        cfg = load_config(default_path())
        assert cfg.mcp.workspace_allowlist == ["Sales WS", "Finance WS"]

    def test_set_workspace_allowlist_single_entry(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", "prod"])
        cfg = load_config(default_path())
        assert cfg.mcp.workspace_allowlist == ["prod"]

    def test_set_workspace_allowlist_preserves_other_keys(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "MyWS"])
        runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", "prod,staging"])
        cfg = load_config(default_path())
        assert cfg.mcp.workspace_allowlist == ["prod", "staging"]
        assert cfg.defaults.workspace == "MyWS"  # preserved


class TestConfigUnsetMcpWorkspaceAllowlist:
    def test_unset_workspace_allowlist_exits_zero(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", "prod"])
        result = runner.invoke(cli, ["config", "unset", "mcp", "workspace-allowlist"])
        assert result.exit_code == 0

    def test_unset_workspace_allowlist_clears_key(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", "prod"])
        runner.invoke(cli, ["config", "unset", "mcp", "workspace-allowlist"])
        cfg = load_config(default_path())
        assert cfg.mcp.workspace_allowlist is None

    def test_unset_workspace_allowlist_preserves_other_keys(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "MyWS"])
        runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", "prod"])
        runner.invoke(cli, ["config", "unset", "mcp", "workspace-allowlist"])
        cfg = load_config(default_path())
        assert cfg.mcp.workspace_allowlist is None
        assert cfg.defaults.workspace == "MyWS"  # preserved


class TestConfigShowMcpWorkspaceAllowlist:
    def test_show_includes_mcp_section(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "mcp" in data

    def test_show_workspace_allowlist_when_set(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", "Sales WS,Finance WS"])
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["mcp"]["workspace_allowlist"] == ["Sales WS", "Finance WS"]

    def test_show_workspace_allowlist_null_when_not_set(
        self, runner: CliRunner, config_env: Path
    ) -> None:
        _ = config_env
        result = runner.invoke(cli, ["--json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["mcp"]["workspace_allowlist"] is None


class TestConfigSetMcpWorkspaceAllowlistValidation:
    """set mcp workspace-allowlist rejects empty / whitespace-only input."""

    def test_empty_string_rejected(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", ""])
        assert result.exit_code != 0

    def test_whitespace_string_rejected(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", "   "])
        assert result.exit_code != 0

    def test_comma_only_rejected(self, runner: CliRunner, config_env: Path) -> None:
        """A value that is all commas has no non-empty entries after splitting."""
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", ",,,"])
        assert result.exit_code != 0

    def test_empty_string_error_mentions_unset(self, runner: CliRunner, config_env: Path) -> None:
        """Error message points operator toward fdw config unset."""
        _ = config_env
        result = runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", ""])
        assert "unset" in result.output.lower()

    def test_empty_string_does_not_write_config(self, runner: CliRunner, config_env: Path) -> None:
        """A rejected empty call must not modify config.toml."""
        _ = config_env
        runner.invoke(cli, ["config", "set", "mcp", "workspace-allowlist", ""])
        cfg = load_config(default_path())
        assert cfg.mcp.workspace_allowlist is None
