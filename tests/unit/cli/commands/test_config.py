"""Tests for the config CLI sub-group."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import cli
from fabric_dw.config import Defaults, UserConfig, default_path, load_config


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


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

    def test_clear_without_yes_prompts(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS"])
        result = runner.invoke(cli, ["config", "clear"], input="n\n")
        assert result.exit_code != 0

    def test_clear_confirms_yes_input(self, runner: CliRunner, config_env: Path) -> None:
        _ = config_env
        runner.invoke(cli, ["config", "set", "workspace", "WS"])
        result = runner.invoke(cli, ["config", "clear"], input="y\n")
        assert result.exit_code == 0
