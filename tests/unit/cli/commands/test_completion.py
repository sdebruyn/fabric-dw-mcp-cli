"""Tests for completion CLI sub-commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import cli
from fabric_dw.cli.commands.completion import (
    _SENTINEL_END,
    _SENTINEL_START,
    _append_idempotent,
    _completion_script,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestCompletionInstallPrintOnly:
    """completion install --print emits script to stdout, no file written."""

    def test_print_bash_exits_zero(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "--print", "bash"])
        assert result.exit_code == 0

    def test_print_zsh_exits_zero(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "--print", "zsh"])
        assert result.exit_code == 0

    def test_print_fish_exits_zero(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "--print", "fish"])
        assert result.exit_code == 0

    def test_print_bash_emits_script(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "--print", "bash"])
        # Click generates a bash completion script; it should contain some shell code
        assert len(result.output) > 0

    def test_unsupported_shell_exits_nonzero(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["completion", "install", "--print", "powershell"])
        assert result.exit_code != 0


class TestCompletionInstallAppendShells:
    """completion install for append-mode shells (bash, zsh)."""

    def test_bash_install_appends_to_rc(self, runner: CliRunner, tmp_path: Path) -> None:
        """Installing bash completion writes to ~/.bashrc."""
        rc_file = tmp_path / ".bashrc"
        rc_file.write_text("# existing rc\n")

        mock_script = "# bash completion script\n_FABRIC_DW_COMPLETE=bash_source fabric-dw\n"

        with (
            patch("fabric_dw.cli.commands.completion.Path.home", return_value=tmp_path),
            patch(
                "fabric_dw.cli.commands.completion._completion_script",
                return_value=mock_script,
            ),
        ):
            result = runner.invoke(cli, ["completion", "install", "bash"])

        assert result.exit_code == 0
        assert "appended" in result.output.lower() or "Reload" in result.output
        content = rc_file.read_text()
        assert mock_script.strip() in content

    def test_zsh_install_appends_to_zshrc(self, runner: CliRunner, tmp_path: Path) -> None:
        """Installing zsh completion writes to ~/.zshrc."""
        rc_file = tmp_path / ".zshrc"
        rc_file.write_text("# existing rc\n")

        mock_script = "# zsh completion script\n_FABRIC_DW_COMPLETE=zsh_source fabric-dw\n"

        with (
            patch("fabric_dw.cli.commands.completion.Path.home", return_value=tmp_path),
            patch(
                "fabric_dw.cli.commands.completion._completion_script",
                return_value=mock_script,
            ),
        ):
            result = runner.invoke(cli, ["completion", "install", "zsh"])

        assert result.exit_code == 0
        content = rc_file.read_text()
        assert mock_script.strip() in content

    def test_bash_install_idempotent_when_script_already_present(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Re-running install when the sentinel block is in ~/.bashrc updates it in-place."""
        mock_script = "# bash completion script\n_FABRIC_DW_COMPLETE=bash_source fabric-dw\n"
        rc_file = tmp_path / ".bashrc"
        # Pre-populate with the sentinel-wrapped block (simulates a previous install).
        rc_file.write_text(
            f"# existing rc\n{_SENTINEL_START}\n{mock_script.strip()}\n{_SENTINEL_END}\n"
        )

        new_script = "# updated bash completion\n_FABRIC_DW_COMPLETE=bash_source fabric-dw\n"

        with (
            patch("fabric_dw.cli.commands.completion.Path.home", return_value=tmp_path),
            patch(
                "fabric_dw.cli.commands.completion._completion_script",
                return_value=new_script,
            ),
        ):
            result = runner.invoke(cli, ["completion", "install", "bash"])

        assert result.exit_code == 0
        # "updated" is the message for in-place replacement.
        assert "updated" in result.output.lower() or "Reload" in result.output
        content = rc_file.read_text()
        # Old script replaced; no duplicate sentinels.
        assert content.count(_SENTINEL_START) == 1
        assert new_script.strip() in content


class TestCompletionInstallWriteShell:
    """completion install for write-mode shells (fish)."""

    def test_fish_install_writes_file(self, runner: CliRunner, tmp_path: Path) -> None:
        """Fish completions are written to ~/.config/fish/completions/fabric-dw.fish."""
        mock_script = "# fish completion\ncomplete -c fabric-dw\n"

        with (
            patch("fabric_dw.cli.commands.completion.Path.home", return_value=tmp_path),
            patch(
                "fabric_dw.cli.commands.completion._completion_script",
                return_value=mock_script,
            ),
        ):
            result = runner.invoke(cli, ["completion", "install", "fish"])

        assert result.exit_code == 0
        expected_path = tmp_path / ".config" / "fish" / "completions" / "fabric-dw.fish"
        assert expected_path.exists()
        assert expected_path.read_text() == mock_script

    def test_fish_install_creates_parent_dirs(self, runner: CliRunner, tmp_path: Path) -> None:
        """Parent directories for fish completion file are created if absent."""
        mock_script = "# fish\n"

        with (
            patch("fabric_dw.cli.commands.completion.Path.home", return_value=tmp_path),
            patch(
                "fabric_dw.cli.commands.completion._completion_script",
                return_value=mock_script,
            ),
        ):
            runner.invoke(cli, ["completion", "install", "fish"])

        parent = tmp_path / ".config" / "fish" / "completions"
        assert parent.is_dir()


class TestAppendIdempotent:
    """Unit tests for the _append_idempotent helper."""

    def test_appends_when_file_does_not_exist(self, tmp_path: Path) -> None:
        target = tmp_path / ".bashrc"
        _append_idempotent(target, "# script\n")
        assert "# script" in target.read_text()

    def test_appends_when_script_not_in_file(self, tmp_path: Path) -> None:
        target = tmp_path / ".bashrc"
        target.write_text("# existing\n")
        _append_idempotent(target, "# newscript\n")
        content = target.read_text()
        assert "# existing" in content
        assert "# newscript" in content

    def test_wraps_script_in_sentinel_markers(self, tmp_path: Path) -> None:
        target = tmp_path / ".bashrc"
        _append_idempotent(target, "# script\n")
        content = target.read_text()
        assert _SENTINEL_START in content
        assert _SENTINEL_END in content

    def test_replaces_block_when_sentinel_already_present(self, tmp_path: Path) -> None:
        """Re-install replaces the existing managed block instead of duplicating it."""
        old_script = "# old completion\n"
        new_script = "# new completion\n"
        target = tmp_path / ".bashrc"
        # Simulate a previous install by writing the sentinel-wrapped block.
        target.write_text(f"# existing\n{_SENTINEL_START}\n{old_script.strip()}\n{_SENTINEL_END}\n")
        _append_idempotent(target, new_script)
        content = target.read_text()
        # Exactly one block, containing the new script.
        assert content.count(_SENTINEL_START) == 1
        assert "# new completion" in content
        assert "# old completion" not in content


class TestCompletionScript:
    """Unit tests for _completion_script helper."""

    def test_returns_string_for_bash(self) -> None:
        script = _completion_script("bash")
        assert isinstance(script, str)
        assert len(script) > 0

    def test_returns_string_for_zsh(self) -> None:
        script = _completion_script("zsh")
        assert isinstance(script, str)
        assert len(script) > 0

    def test_returns_string_for_fish(self) -> None:
        script = _completion_script("fish")
        assert isinstance(script, str)
        assert len(script) > 0
