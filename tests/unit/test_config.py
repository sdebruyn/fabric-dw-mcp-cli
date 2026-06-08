"""Tests for config load/save/clear."""

from __future__ import annotations

from pathlib import Path

import pytest

from fabric_dw.config import (
    Defaults,
    UserConfig,
    clear_config,
    default_path,
    load_config,
    save_config,
)

# ---------------------------------------------------------------------------
# default_path
# ---------------------------------------------------------------------------


def test_default_path_uses_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    path = default_path()
    assert path == tmp_path / "fabric-dw" / "config.toml"


def test_default_path_falls_back_to_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    path = default_path()
    assert path == tmp_path / ".config" / "fabric-dw" / "config.toml"


# ---------------------------------------------------------------------------
# load_config — missing file
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "no-such-dir" / "config.toml"
    cfg = load_config(path)
    assert cfg == UserConfig(defaults=Defaults())


def test_load_corrupt_toml_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[[[[invalid", encoding="utf-8")
    cfg = load_config(path)
    assert cfg == UserConfig(defaults=Defaults())


# ---------------------------------------------------------------------------
# Round-trip: save then load
# ---------------------------------------------------------------------------


def test_round_trip_workspace(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(workspace="SalesWS"))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == "SalesWS"
    assert loaded.defaults.warehouse is None


def test_round_trip_both(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(workspace="MyWS", warehouse="MyWH"))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == "MyWS"
    assert loaded.defaults.warehouse == "MyWH"


def test_round_trip_empty_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults())
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded == UserConfig(defaults=Defaults())


# ---------------------------------------------------------------------------
# save_config: atomic write creates parent dirs
# ---------------------------------------------------------------------------


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS")), path)
    assert path.exists()


def test_save_no_tmp_files_left(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS")), path)
    # Only the config file and the .lock file should remain (no .config_tmp_* files).
    leftovers = [f for f in tmp_path.iterdir() if f != path and not f.name.endswith(".lock")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# clear_config
# ---------------------------------------------------------------------------


def test_clear_removes_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS")), path)
    assert path.exists()
    clear_config(path)
    assert not path.exists()


def test_clear_no_error_when_file_missing(tmp_path: Path) -> None:
    path = tmp_path / "nonexistent.toml"
    clear_config(path)  # should not raise


# ---------------------------------------------------------------------------
# load_config after clear returns empty
# ---------------------------------------------------------------------------


def test_load_after_clear_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS", warehouse="WH")), path)
    clear_config(path)
    cfg = load_config(path)
    assert cfg == UserConfig(defaults=Defaults())
