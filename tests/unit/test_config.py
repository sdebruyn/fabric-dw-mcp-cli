"""Tests for config load/save/clear."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import filelock
import pytest

from fabric_dw.config import (
    ConfigError,
    Defaults,
    UserConfig,
    clear_config,
    default_path,
    load_config,
    save_config,
    set_default,
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


# ---------------------------------------------------------------------------
# TOML round-trip with hostile workspace / warehouse names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        'Sales "Workspace"',  # embedded double-quote
        "Back\\slash WS",  # backslash
        "Line\nBreak WS",  # newline (LF)
        "Tab\tWS",  # horizontal tab
        "Control\x00WS",  # null byte (control character)
        "Unicode ☃ Snowman",  # BMP unicode
        "Non-BMP \U0001f600 Emoji",  # non-BMP unicode (surrogate pair in UTF-16)
        '"""triple quotes"""',  # triple double-quote
        "null\x00byte\x01ctrl",  # multiple control chars
    ],
)
def test_round_trip_hostile_workspace_name(tmp_path: Path, name: str) -> None:
    """save_config + load_config must survive hostile workspace name strings."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(workspace=name))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == name


@pytest.mark.parametrize(
    "name",
    [
        'Ware"house',
        "DW\\backslash",
        "Line\nBreak-DW",
    ],
)
def test_round_trip_hostile_warehouse_name(tmp_path: Path, name: str) -> None:
    """save_config + load_config must survive hostile warehouse name strings."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(warehouse=name))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.warehouse == name


def test_round_trip_both_hostile(tmp_path: Path) -> None:
    """Both workspace and warehouse with hostile characters round-trip correctly."""
    path = tmp_path / "config.toml"
    ws = 'My "Workspace"\nwith newline'
    wh = "DW\\slash\ttab"
    cfg = UserConfig(defaults=Defaults(workspace=ws, warehouse=wh))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == ws
    assert loaded.defaults.warehouse == wh


# ---------------------------------------------------------------------------
# C18: load_config — narrowed exception handling
# ---------------------------------------------------------------------------


def test_load_config_oserror_returns_empty(tmp_path: Path) -> None:
    """An OSError during file read must return empty defaults, not raise (C18)."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS")), path)
    # Simulate an OSError during read_text (unreadable file)
    with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
        cfg = load_config(path)
    assert cfg == UserConfig(defaults=Defaults())


def test_load_config_lock_timeout_returns_empty(tmp_path: Path) -> None:
    """A filelock.Timeout during lock acquisition must return empty defaults (C18)."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS")), path)
    with patch("filelock.FileLock.acquire", side_effect=filelock.Timeout(str(path) + ".lock")):
        cfg = load_config(path)
    assert cfg == UserConfig(defaults=Defaults())


def test_load_config_corrupt_toml_returns_empty(tmp_path: Path) -> None:
    """A TOMLDecodeError must return empty defaults without raising (C18)."""
    path = tmp_path / "config.toml"
    path.write_text("[[[[this is not valid toml", encoding="utf-8")
    cfg = load_config(path)
    assert cfg == UserConfig(defaults=Defaults())


# ---------------------------------------------------------------------------
# C20: set_default — atomic read-modify-write under one lock
# ---------------------------------------------------------------------------


def test_set_default_workspace_persists(tmp_path: Path) -> None:
    """set_default must persist the given workspace."""
    path = tmp_path / "config.toml"
    set_default("workspace", "SalesWS", path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == "SalesWS"


def test_set_default_preserves_unrelated_key(tmp_path: Path) -> None:
    """set_default must not clear other keys when only one is updated (C20)."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS", warehouse="WH")), path)
    set_default("workspace", "NewWS", path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == "NewWS"
    assert loaded.defaults.warehouse == "WH"


def test_set_default_none_clears_key(tmp_path: Path) -> None:
    """set_default(key, None) must clear the key."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS", warehouse="WH")), path)
    set_default("workspace", None, path)
    loaded = load_config(path)
    assert loaded.defaults.workspace is None
    assert loaded.defaults.warehouse == "WH"


def test_set_default_invalid_key_raises(tmp_path: Path) -> None:
    """set_default with an unrecognised key must raise ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="Unknown config key"):
        set_default("nonexistent_key", "value", path)


def test_set_default_lock_timeout_raises_config_error(tmp_path: Path) -> None:
    """set_default must raise ConfigError (not a raw filelock.Timeout) on lock timeout."""
    path = tmp_path / "config.toml"
    lock_side_effect = filelock.Timeout(str(path) + ".lock")
    with (
        patch("filelock.FileLock.acquire", side_effect=lock_side_effect),
        pytest.raises(ConfigError, match="Could not acquire lock"),
    ):
        set_default("workspace", "SalesWS", path)


def test_set_default_concurrent_no_lost_update(tmp_path: Path) -> None:
    """Concurrent set_default calls must not produce a lost update (C20).

    Two threads each set a different key; both values must appear in the file
    after both threads complete.
    """
    path = tmp_path / "config.toml"
    errors: list[Exception] = []

    def set_workspace() -> None:
        try:
            for _ in range(5):
                set_default("workspace", "ConcurrentWS", path)
        except Exception as exc:
            errors.append(exc)

    def set_warehouse() -> None:
        try:
            for _ in range(5):
                set_default("warehouse", "ConcurrentWH", path)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=set_workspace)
    t2 = threading.Thread(target=set_warehouse)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Thread errors: {errors}"
    loaded = load_config(path)
    # After both threads converge, both keys must be set (no lost update).
    assert loaded.defaults.workspace == "ConcurrentWS"
    assert loaded.defaults.warehouse == "ConcurrentWH"
