"""Tests for config load/save/clear."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import filelock
import pytest

from fabric_dw.auth import CredentialMode
from fabric_dw.config import (
    VALID_AUTH_MODES,
    VALID_LOG_LEVELS,
    AuthConfig,
    ConfigError,
    Defaults,
    LoggingConfig,
    McpConfig,
    TelemetryConfig,
    UserConfig,
    clear_config,
    default_path,
    load_config,
    save_config,
    set_config,
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


# ---------------------------------------------------------------------------
# Retry-budget fields — round-trip and set_default numeric coercion
# ---------------------------------------------------------------------------


def test_round_trip_max_429_retries(tmp_path: Path) -> None:
    """max_429_retries is saved and loaded as an int."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(max_429_retries=15))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.max_429_retries == 15
    assert loaded.defaults.retry_deadline_s is None


def test_round_trip_retry_deadline_s(tmp_path: Path) -> None:
    """retry_deadline_s is saved and loaded as a float."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(retry_deadline_s=600.0))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.retry_deadline_s == 600.0
    assert loaded.defaults.max_429_retries is None


def test_round_trip_all_four_defaults(tmp_path: Path) -> None:
    """All four Defaults fields survive a save/load cycle together."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(
        defaults=Defaults(
            workspace="SalesWS",
            warehouse="SalesDW",
            max_429_retries=7,
            retry_deadline_s=180.0,
        )
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == "SalesWS"
    assert loaded.defaults.warehouse == "SalesDW"
    assert loaded.defaults.max_429_retries == 7
    assert loaded.defaults.retry_deadline_s == 180.0


def test_set_default_max_429_retries_persists(tmp_path: Path) -> None:
    """set_default('max_429_retries', '20') stores 20 as int and preserves other keys."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS")), path)
    set_default("max_429_retries", "20", path)
    loaded = load_config(path)
    assert loaded.defaults.max_429_retries == 20
    assert loaded.defaults.workspace == "WS"  # preserved


def test_set_default_retry_deadline_s_persists(tmp_path: Path) -> None:
    """set_default('retry_deadline_s', '450.0') stores 450.0 as float."""
    path = tmp_path / "config.toml"
    set_default("retry_deadline_s", "450.0", path)
    loaded = load_config(path)
    assert loaded.defaults.retry_deadline_s == 450.0


def test_set_default_max_429_retries_none_clears(tmp_path: Path) -> None:
    """set_default('max_429_retries', None) clears the key."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(max_429_retries=5, retry_deadline_s=120.0)), path)
    set_default("max_429_retries", None, path)
    loaded = load_config(path)
    assert loaded.defaults.max_429_retries is None
    assert loaded.defaults.retry_deadline_s == 120.0  # preserved


def test_set_default_retry_deadline_s_none_clears(tmp_path: Path) -> None:
    """set_default('retry_deadline_s', None) clears the key."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(max_429_retries=8, retry_deadline_s=200.0)), path)
    set_default("retry_deadline_s", None, path)
    loaded = load_config(path)
    assert loaded.defaults.retry_deadline_s is None
    assert loaded.defaults.max_429_retries == 8  # preserved


def test_set_default_max_429_retries_bad_value_raises(tmp_path: Path) -> None:
    """set_default('max_429_retries', 'not-a-number') raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="cannot be converted"):
        set_default("max_429_retries", "not-a-number", path)


def test_set_default_retry_deadline_s_bad_value_raises(tmp_path: Path) -> None:
    """set_default('retry_deadline_s', 'bad') raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="cannot be converted"):
        set_default("retry_deadline_s", "bad", path)


# ---------------------------------------------------------------------------
# Blocker 1: set_default must not clobber config on read error
# ---------------------------------------------------------------------------


def test_set_default_read_error_does_not_clobber_existing_config(tmp_path: Path) -> None:
    """set_default must raise (not silently clobber) when the existing file cannot be read."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS", warehouse="WH")), path)
    with (
        patch.object(Path, "read_text", side_effect=OSError("permission denied")),
        pytest.raises(OSError, match="permission denied"),
    ):
        set_default("max_429_retries", "5", path)
    # File must be intact — we did NOT overwrite it.
    loaded = load_config(path)
    assert loaded.defaults.workspace == "WS"
    assert loaded.defaults.warehouse == "WH"


# ---------------------------------------------------------------------------
# Blocker 2: non-finite values must be rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("val", ["inf", "-inf", "nan"])
def test_set_default_retry_deadline_s_non_finite_raises(tmp_path: Path, val: str) -> None:
    """Non-finite values for retry_deadline_s must raise ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="finite"):
        set_default("retry_deadline_s", val, path)


# ---------------------------------------------------------------------------
# Should-fix 3: int("5.0") — float-formatted int env var accepted
# ---------------------------------------------------------------------------


def test_set_default_max_429_retries_float_string_accepted(tmp_path: Path) -> None:
    """'20.0' is a valid value for max_429_retries (Docker YAML float-formatted int)."""
    path = tmp_path / "config.toml"
    set_default("max_429_retries", "20.0", path)
    loaded = load_config(path)
    assert loaded.defaults.max_429_retries == 20


# ---------------------------------------------------------------------------
# Should-fix 4: range validation in set_default
# ---------------------------------------------------------------------------


def test_set_default_max_429_retries_below_minimum_raises(tmp_path: Path) -> None:
    """max_429_retries must be >= 1; 0 raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match=">= 1"):
        set_default("max_429_retries", "0", path)


def test_set_default_retry_deadline_s_below_minimum_raises(tmp_path: Path) -> None:
    """retry_deadline_s must be >= 0.1; 0.0 raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match=r">= 0\.1"):
        set_default("retry_deadline_s", "0.0", path)


# ---------------------------------------------------------------------------
# SQL retry fields — round-trip and set_default validation
# ---------------------------------------------------------------------------


def test_round_trip_sql_retry_deadline_s(tmp_path: Path) -> None:
    """sql_retry_deadline_s is saved and loaded as a float."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(sql_retry_deadline_s=300.0))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_retry_deadline_s == 300.0
    assert loaded.defaults.sql_retry_executes is None


def test_round_trip_sql_retry_executes_true(tmp_path: Path) -> None:
    """sql_retry_executes=True is saved and loaded as a bool."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(sql_retry_executes=True))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_retry_executes is True


def test_round_trip_sql_retry_executes_false(tmp_path: Path) -> None:
    """sql_retry_executes=False is saved and loaded as a bool."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(sql_retry_executes=False))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_retry_executes is False


def test_round_trip_all_six_defaults(tmp_path: Path) -> None:
    """All six Defaults fields survive a save/load cycle together."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(
        defaults=Defaults(
            workspace="SalesWS",
            warehouse="SalesDW",
            max_429_retries=7,
            retry_deadline_s=180.0,
            sql_retry_deadline_s=240.0,
            sql_retry_executes=True,
        )
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == "SalesWS"
    assert loaded.defaults.warehouse == "SalesDW"
    assert loaded.defaults.max_429_retries == 7
    assert loaded.defaults.retry_deadline_s == 180.0
    assert loaded.defaults.sql_retry_deadline_s == 240.0
    assert loaded.defaults.sql_retry_executes is True


def test_set_default_sql_retry_deadline_s_persists(tmp_path: Path) -> None:
    """set_default('sql_retry_deadline_s', '300.0') stores 300.0 and preserves other keys."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS")), path)
    set_default("sql_retry_deadline_s", "300.0", path)
    loaded = load_config(path)
    assert loaded.defaults.sql_retry_deadline_s == 300.0
    assert loaded.defaults.workspace == "WS"  # preserved


def test_set_default_sql_retry_deadline_s_none_clears(tmp_path: Path) -> None:
    """set_default('sql_retry_deadline_s', None) clears the key."""
    path = tmp_path / "config.toml"
    save_config(
        UserConfig(defaults=Defaults(sql_retry_deadline_s=300.0, sql_retry_executes=True)), path
    )
    set_default("sql_retry_deadline_s", None, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_retry_deadline_s is None
    assert loaded.defaults.sql_retry_executes is True  # preserved


def test_set_default_sql_retry_deadline_s_bad_value_raises(tmp_path: Path) -> None:
    """set_default('sql_retry_deadline_s', 'bad') raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="cannot be converted"):
        set_default("sql_retry_deadline_s", "bad", path)


def test_set_default_sql_retry_deadline_s_below_minimum_raises(tmp_path: Path) -> None:
    """sql_retry_deadline_s must be >= 0.1; 0.0 raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match=r">= 0\.1"):
        set_default("sql_retry_deadline_s", "0.0", path)


@pytest.mark.parametrize("val", ["inf", "-inf", "nan"])
def test_set_default_sql_retry_deadline_s_non_finite_raises(tmp_path: Path, val: str) -> None:
    """Non-finite values for sql_retry_deadline_s must raise ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="finite"):
        set_default("sql_retry_deadline_s", val, path)


def test_set_default_sql_retry_executes_true_persists(tmp_path: Path) -> None:
    """set_default('sql_retry_executes', 'true') stores True."""
    path = tmp_path / "config.toml"
    set_default("sql_retry_executes", "true", path)
    loaded = load_config(path)
    assert loaded.defaults.sql_retry_executes is True


def test_set_default_sql_retry_executes_false_persists(tmp_path: Path) -> None:
    """set_default('sql_retry_executes', 'false') stores False."""
    path = tmp_path / "config.toml"
    set_default("sql_retry_executes", "false", path)
    loaded = load_config(path)
    assert loaded.defaults.sql_retry_executes is False


def test_set_default_sql_retry_executes_none_clears(tmp_path: Path) -> None:
    """set_default('sql_retry_executes', None) clears the key."""
    path = tmp_path / "config.toml"
    save_config(
        UserConfig(defaults=Defaults(sql_retry_executes=True, sql_retry_deadline_s=120.0)), path
    )
    set_default("sql_retry_executes", None, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_retry_executes is None
    assert loaded.defaults.sql_retry_deadline_s == 120.0  # preserved


def test_set_default_sql_retry_executes_garbage_raises(tmp_path: Path) -> None:
    """An unrecognised value for sql_retry_executes raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="sql_retry_executes"):
        set_default("sql_retry_executes", "maybe", path)


@pytest.mark.parametrize("truthy", ["true", "True", "TRUE", "1", "yes", "on"])
def test_set_default_sql_retry_executes_truthy_variants(tmp_path: Path, truthy: str) -> None:
    """All truthy string variants are accepted for sql_retry_executes."""
    path = tmp_path / "config.toml"
    set_default("sql_retry_executes", truthy, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_retry_executes is True


@pytest.mark.parametrize("falsy", ["false", "False", "FALSE", "0", "no", "off"])
def test_set_default_sql_retry_executes_falsy_variants(tmp_path: Path, falsy: str) -> None:
    """All falsy string variants are accepted for sql_retry_executes."""
    path = tmp_path / "config.toml"
    set_default("sql_retry_executes", falsy, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_retry_executes is False


# ---------------------------------------------------------------------------
# Backward-compat: defaults-only file loads with new sections at None
# ---------------------------------------------------------------------------


def test_defaults_only_file_loads_with_new_sections_empty(tmp_path: Path) -> None:
    """A [defaults]-only TOML file loads with all new sections defaulting to None."""
    path = tmp_path / "config.toml"
    path.write_text(
        '[defaults]\nworkspace = "SalesWS"\nwarehouse = "SalesDW"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.defaults.workspace == "SalesWS"
    assert cfg.defaults.warehouse == "SalesDW"
    assert cfg.telemetry.disabled is None
    assert cfg.mcp.workspace_allowlist is None
    assert cfg.logging.level is None
    assert cfg.auth.tenant_id is None
    assert cfg.auth.client_id is None


def test_old_style_user_config_equals_fully_defaulted() -> None:
    """UserConfig(defaults=Defaults()) equals a fully-defaulted UserConfig()."""
    old_style = UserConfig(defaults=Defaults())
    new_style = UserConfig()
    assert old_style == new_style


# ---------------------------------------------------------------------------
# TelemetryConfig round-trip
# ---------------------------------------------------------------------------


def test_round_trip_telemetry_disabled_true(tmp_path: Path) -> None:
    """[telemetry] disabled = true survives save/load."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(telemetry=TelemetryConfig(disabled=True))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.telemetry.disabled is True


def test_round_trip_telemetry_disabled_false(tmp_path: Path) -> None:
    """[telemetry] disabled = false survives save/load."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(telemetry=TelemetryConfig(disabled=False))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.telemetry.disabled is False


def test_telemetry_section_absent_when_none(tmp_path: Path) -> None:
    """When telemetry.disabled is None, the [telemetry] section is not written."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(workspace="WS"))
    save_config(cfg, path)
    content = path.read_text(encoding="utf-8")
    assert "[telemetry]" not in content


def test_telemetry_disabled_integer_one_parsed_as_true(tmp_path: Path) -> None:
    """[telemetry] disabled = 1 (integer) is treated as disabled=True."""
    path = tmp_path / "config.toml"
    path.write_text("[telemetry]\ndisabled = 1\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.telemetry.disabled is True


def test_telemetry_disabled_integer_zero_parsed_as_false(tmp_path: Path) -> None:
    """[telemetry] disabled = 0 (integer) is treated as disabled=False."""
    path = tmp_path / "config.toml"
    path.write_text("[telemetry]\ndisabled = 0\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.telemetry.disabled is False


def test_telemetry_disabled_string_true_parsed_as_true(tmp_path: Path) -> None:
    """[telemetry] disabled = \"true\" (string) is treated as disabled=True."""
    path = tmp_path / "config.toml"
    path.write_text('[telemetry]\ndisabled = "true"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.telemetry.disabled is True


def test_telemetry_disabled_string_false_parsed_as_false(tmp_path: Path) -> None:
    """[telemetry] disabled = \"false\" (string) must not opt out (disabled=False)."""
    path = tmp_path / "config.toml"
    path.write_text('[telemetry]\ndisabled = "false"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.telemetry.disabled is False


def test_telemetry_disabled_string_zero_parsed_as_false(tmp_path: Path) -> None:
    """[telemetry] disabled = \"0\" (string) must not opt out (disabled=False)."""
    path = tmp_path / "config.toml"
    path.write_text('[telemetry]\ndisabled = "0"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.telemetry.disabled is False


def test_empty_user_config_writes_empty_file(tmp_path: Path) -> None:
    """A fully-None UserConfig writes an empty TOML file (no sections at all)."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(), path)
    content = path.read_text(encoding="utf-8")
    assert content.strip() == ""


# ---------------------------------------------------------------------------
# McpConfig round-trip
# ---------------------------------------------------------------------------


def test_round_trip_mcp_workspace_allowlist(tmp_path: Path) -> None:
    """[mcp] workspace_allowlist survives save/load."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(mcp=McpConfig(workspace_allowlist=["Sales", "Finance"]))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.mcp.workspace_allowlist == ["Sales", "Finance"]


def test_mcp_section_absent_when_none(tmp_path: Path) -> None:
    """When mcp fields are None, the [mcp] section is not written."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(), path)
    content = path.read_text(encoding="utf-8")
    assert "[mcp]" not in content


# ---------------------------------------------------------------------------
# LoggingConfig round-trip
# ---------------------------------------------------------------------------


def test_round_trip_logging_level(tmp_path: Path) -> None:
    """[logging] level survives save/load."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(logging=LoggingConfig(level="DEBUG"))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.logging.level == "DEBUG"


# ---------------------------------------------------------------------------
# AuthConfig round-trip
# ---------------------------------------------------------------------------


def test_round_trip_auth_config(tmp_path: Path) -> None:
    """[auth] tenant_id and client_id survive save/load."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(
        auth=AuthConfig(
            tenant_id="00000000-0000-0000-0000-000000000001",
            client_id="00000000-0000-0000-0000-000000000002",
        )
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.auth.tenant_id == "00000000-0000-0000-0000-000000000001"
    assert loaded.auth.client_id == "00000000-0000-0000-0000-000000000002"


# ---------------------------------------------------------------------------
# All sections round-trip together
# ---------------------------------------------------------------------------


def test_round_trip_all_sections(tmp_path: Path) -> None:
    """All sections survive a combined save/load cycle."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(
        defaults=Defaults(workspace="WS", max_429_retries=5),
        telemetry=TelemetryConfig(disabled=True),
        mcp=McpConfig(workspace_allowlist=["A", "B"]),
        logging=LoggingConfig(level="WARNING"),
        auth=AuthConfig(tenant_id="tid", client_id="cid"),
    )
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == "WS"
    assert loaded.defaults.max_429_retries == 5
    assert loaded.telemetry.disabled is True
    assert loaded.mcp.workspace_allowlist == ["A", "B"]
    assert loaded.logging.level == "WARNING"
    assert loaded.auth.tenant_id == "tid"
    assert loaded.auth.client_id == "cid"


# ---------------------------------------------------------------------------
# Unknown sections silently ignored
# ---------------------------------------------------------------------------


def test_unknown_section_ignored_on_load(tmp_path: Path) -> None:
    """Unknown TOML sections are silently ignored on load."""
    path = tmp_path / "config.toml"
    path.write_text(
        '[defaults]\nworkspace = "WS"\n\n[unknown_future_section]\nsome_key = "val"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.defaults.workspace == "WS"
    # No error raised, other sections default to None.
    assert cfg.telemetry.disabled is None


# ---------------------------------------------------------------------------
# set_config — parity with set_default + new sections
# ---------------------------------------------------------------------------


def test_set_config_defaults_parity_with_set_default(tmp_path: Path) -> None:
    """set_config('defaults', 'workspace', ...) behaves identically to set_default."""
    path = tmp_path / "config.toml"
    set_config("defaults", "workspace", "SalesWS", path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == "SalesWS"


def test_set_config_unknown_section_raises(tmp_path: Path) -> None:
    """set_config with unknown section raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="Unknown config section/key"):
        set_config("nonexistent", "key", "val", path)


def test_set_config_unknown_key_raises(tmp_path: Path) -> None:
    """set_config with unknown key in known section raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="Unknown config section/key"):
        set_config("defaults", "not_a_real_key", "val", path)


def test_set_config_none_clears_key(tmp_path: Path) -> None:
    """set_config(value=None) removes a key from its section."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS", warehouse="WH")), path)
    set_config("defaults", "workspace", None, path)
    loaded = load_config(path)
    assert loaded.defaults.workspace is None
    assert loaded.defaults.warehouse == "WH"  # preserved


def test_set_config_lock_timeout_raises_config_error(tmp_path: Path) -> None:
    """set_config raises ConfigError on lock acquisition timeout."""
    path = tmp_path / "config.toml"
    lock_side_effect = filelock.Timeout(str(path) + ".lock")
    with (
        patch("filelock.FileLock.acquire", side_effect=lock_side_effect),
        pytest.raises(ConfigError, match="Could not acquire lock"),
    ):
        set_config("defaults", "workspace", "SalesWS", path)


def test_set_config_preserves_other_sections(tmp_path: Path) -> None:
    """set_config on one section does not disturb unrelated sections."""
    path = tmp_path / "config.toml"
    save_config(
        UserConfig(
            defaults=Defaults(workspace="WS"),
            telemetry=TelemetryConfig(disabled=True),
        ),
        path,
    )
    set_config("defaults", "warehouse", "DW", path)
    loaded = load_config(path)
    assert loaded.defaults.workspace == "WS"
    assert loaded.defaults.warehouse == "DW"
    assert loaded.telemetry.disabled is True  # not lost


def test_set_config_telemetry_disabled_true(tmp_path: Path) -> None:
    """set_config can write telemetry.disabled = True."""
    path = tmp_path / "config.toml"
    set_config("telemetry", "disabled", "true", path)
    loaded = load_config(path)
    assert loaded.telemetry.disabled is True


def test_set_config_telemetry_disabled_false(tmp_path: Path) -> None:
    """set_config can write telemetry.disabled = False."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(telemetry=TelemetryConfig(disabled=True)), path)
    set_config("telemetry", "disabled", "false", path)
    loaded = load_config(path)
    assert loaded.telemetry.disabled is False


def test_set_config_concurrent_different_sections_no_lost_update(tmp_path: Path) -> None:
    """Concurrent set_config calls to different sections must not lose updates."""
    path = tmp_path / "config.toml"
    errors: list[Exception] = []

    def set_ws() -> None:
        try:
            for _ in range(5):
                set_config("defaults", "workspace", "ConcurrentWS", path)
        except Exception as exc:
            errors.append(exc)

    def set_telemetry() -> None:
        try:
            for _ in range(5):
                set_config("telemetry", "disabled", "true", path)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=set_ws)
    t2 = threading.Thread(target=set_telemetry)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Thread errors: {errors}"
    loaded = load_config(path)
    assert loaded.defaults.workspace == "ConcurrentWS"
    assert loaded.telemetry.disabled is True


# ---------------------------------------------------------------------------
# set_config logging.level — round-trip, unset, validation, normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", sorted(VALID_LOG_LEVELS))
def test_set_config_logging_level_valid_values(tmp_path: Path, level: str) -> None:
    """All valid log levels can be written and round-trip correctly."""
    path = tmp_path / "config.toml"
    set_config("logging", "level", level, path)
    loaded = load_config(path)
    assert loaded.logging.level == level.upper()


@pytest.mark.parametrize("raw", ["debug", "Debug", "DEBUG"])
def test_set_config_logging_level_normalises_to_upper(tmp_path: Path, raw: str) -> None:
    """Levels written in any case are normalised to upper-case."""
    path = tmp_path / "config.toml"
    set_config("logging", "level", raw, path)
    loaded = load_config(path)
    assert loaded.logging.level == "DEBUG"


def test_set_config_logging_level_unset(tmp_path: Path) -> None:
    """set_config('logging', 'level', None) clears the key."""
    path = tmp_path / "config.toml"
    set_config("logging", "level", "WARNING", path)
    assert load_config(path).logging.level == "WARNING"
    set_config("logging", "level", None, path)
    loaded = load_config(path)
    assert loaded.logging.level is None


def test_set_config_logging_level_invalid_raises(tmp_path: Path) -> None:
    """An unrecognised log level raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match=r"logging\.level"):
        set_config("logging", "level", "VERBOSE", path)


def test_set_config_logging_level_preserves_other_sections(tmp_path: Path) -> None:
    """Writing logging.level does not disturb unrelated sections."""
    path = tmp_path / "config.toml"
    save_config(
        UserConfig(
            defaults=Defaults(workspace="WS"),
            telemetry=TelemetryConfig(disabled=True),
        ),
        path,
    )
    set_config("logging", "level", "DEBUG", path)
    loaded = load_config(path)
    assert loaded.logging.level == "DEBUG"
    assert loaded.defaults.workspace == "WS"
    assert loaded.telemetry.disabled is True


def test_logging_section_absent_when_level_none(tmp_path: Path) -> None:
    """When logging.level is None, the [logging] section is not written."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS")), path)
    content = path.read_text(encoding="utf-8")
    assert "[logging]" not in content


def test_load_config_invalid_logging_level_discarded(tmp_path: Path) -> None:
    """A hand-edited [logging] level with an invalid value is discarded (treated as None)."""
    path = tmp_path / "config.toml"
    path.write_text('[logging]\nlevel = "VERBOSE"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.logging.level is None


def test_load_config_invalid_logging_level_valid_level_preserved(tmp_path: Path) -> None:
    """A valid (but lowercased) [logging] level is normalised to upper-case on load."""
    path = tmp_path / "config.toml"
    path.write_text('[logging]\nlevel = "debug"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.logging.level == "DEBUG"


# ---------------------------------------------------------------------------
# sql_pool — round-trip and set_default coercion
# ---------------------------------------------------------------------------


def test_round_trip_sql_pool_true(tmp_path: Path) -> None:
    """sql_pool=True is saved and loaded as a bool."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(sql_pool=True))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_pool is True


def test_round_trip_sql_pool_false(tmp_path: Path) -> None:
    """sql_pool=False is saved and loaded as a bool."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(sql_pool=False))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_pool is False


def test_round_trip_sql_pool_none(tmp_path: Path) -> None:
    """sql_pool=None is not written to the file."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(workspace="WS", sql_pool=None))
    save_config(cfg, path)
    content = path.read_text(encoding="utf-8")
    assert "sql_pool" not in content
    loaded = load_config(path)
    assert loaded.defaults.sql_pool is None
    assert loaded.defaults.workspace == "WS"


def test_set_default_sql_pool_true_persists(tmp_path: Path) -> None:
    """set_default('sql_pool', 'true') stores True."""
    path = tmp_path / "config.toml"
    set_default("sql_pool", "true", path)
    loaded = load_config(path)
    assert loaded.defaults.sql_pool is True


def test_set_default_sql_pool_false_persists(tmp_path: Path) -> None:
    """set_default('sql_pool', 'false') stores False."""
    path = tmp_path / "config.toml"
    set_default("sql_pool", "false", path)
    loaded = load_config(path)
    assert loaded.defaults.sql_pool is False


def test_set_default_sql_pool_none_clears(tmp_path: Path) -> None:
    """set_default('sql_pool', None) clears the key."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(sql_pool=False, sql_retry_executes=True)), path)
    set_default("sql_pool", None, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_pool is None
    assert loaded.defaults.sql_retry_executes is True  # preserved


def test_set_default_sql_pool_garbage_raises(tmp_path: Path) -> None:
    """An unrecognised value for sql_pool raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="sql_pool"):
        set_default("sql_pool", "maybe", path)


@pytest.mark.parametrize("truthy", ["true", "True", "TRUE", "1", "yes", "on"])
def test_set_default_sql_pool_truthy_variants(tmp_path: Path, truthy: str) -> None:
    """All truthy string variants are accepted for sql_pool."""
    path = tmp_path / "config.toml"
    set_default("sql_pool", truthy, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_pool is True


@pytest.mark.parametrize("falsy", ["false", "False", "FALSE", "0", "no", "off"])
def test_set_default_sql_pool_falsy_variants(tmp_path: Path, falsy: str) -> None:
    """All falsy string variants are accepted for sql_pool."""
    path = tmp_path / "config.toml"
    set_default("sql_pool", falsy, path)
    loaded = load_config(path)
    assert loaded.defaults.sql_pool is False


def test_set_default_sql_pool_preserves_other_keys(tmp_path: Path) -> None:
    """set_default('sql_pool', ...) does not clear unrelated keys."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS", sql_retry_executes=True)), path)
    set_default("sql_pool", "false", path)
    loaded = load_config(path)
    assert loaded.defaults.sql_pool is False
    assert loaded.defaults.workspace == "WS"
    assert loaded.defaults.sql_retry_executes is True


# ---------------------------------------------------------------------------
# auth_mode — round-trip, set_default, validation, case normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", sorted(VALID_AUTH_MODES))
def test_round_trip_auth_mode(tmp_path: Path, mode: str) -> None:
    """Each valid auth_mode value survives a save/load cycle."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(auth_mode=mode))
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.defaults.auth_mode == mode


def test_auth_mode_none_not_written(tmp_path: Path) -> None:
    """When auth_mode is None, the key is absent from the file."""
    path = tmp_path / "config.toml"
    cfg = UserConfig(defaults=Defaults(workspace="WS", auth_mode=None))
    save_config(cfg, path)
    content = path.read_text(encoding="utf-8")
    assert "auth_mode" not in content
    loaded = load_config(path)
    assert loaded.defaults.auth_mode is None


@pytest.mark.parametrize("mode", sorted(VALID_AUTH_MODES))
def test_set_default_auth_mode_persists(tmp_path: Path, mode: str) -> None:
    """set_default('auth_mode', mode) stores the mode and preserves other keys."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS")), path)
    set_default("auth_mode", mode, path)
    loaded = load_config(path)
    assert loaded.defaults.auth_mode == mode
    assert loaded.defaults.workspace == "WS"  # preserved


def test_set_default_auth_mode_none_clears(tmp_path: Path) -> None:
    """set_default('auth_mode', None) clears the key."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(auth_mode="interactive", workspace="WS")), path)
    set_default("auth_mode", None, path)
    loaded = load_config(path)
    assert loaded.defaults.auth_mode is None
    assert loaded.defaults.workspace == "WS"  # preserved


def test_set_default_auth_mode_invalid_raises(tmp_path: Path) -> None:
    """An unrecognised auth_mode raises ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="auth_mode"):
        set_default("auth_mode", "managed_identity", path)


def test_set_default_auth_mode_invalid_gibberish_raises(tmp_path: Path) -> None:
    """Gibberish auth_mode values raise ValueError."""
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="auth_mode"):
        set_default("auth_mode", "notamode", path)


@pytest.mark.parametrize("raw", ["DEFAULT", "Default", "default"])
def test_set_default_auth_mode_normalises_to_lower(tmp_path: Path, raw: str) -> None:
    """auth_mode is normalised to lowercase when set."""
    path = tmp_path / "config.toml"
    set_default("auth_mode", raw, path)
    loaded = load_config(path)
    assert loaded.defaults.auth_mode == "default"


@pytest.mark.parametrize("raw", ["INTERACTIVE", "Interactive"])
def test_set_default_auth_mode_interactive_normalised(tmp_path: Path, raw: str) -> None:
    """'interactive' auth_mode variant cases are normalised."""
    path = tmp_path / "config.toml"
    set_default("auth_mode", raw, path)
    loaded = load_config(path)
    assert loaded.defaults.auth_mode == "interactive"


@pytest.mark.parametrize("raw", ["SP", "Sp"])
def test_set_default_auth_mode_sp_normalised(tmp_path: Path, raw: str) -> None:
    """'sp' auth_mode variant cases are normalised."""
    path = tmp_path / "config.toml"
    set_default("auth_mode", raw, path)
    loaded = load_config(path)
    assert loaded.defaults.auth_mode == "sp"


def test_load_config_invalid_auth_mode_discarded(tmp_path: Path) -> None:
    """A hand-edited [defaults] auth_mode with an invalid value is discarded (treated as None)."""
    path = tmp_path / "config.toml"
    path.write_text('[defaults]\nauth_mode = "not_valid"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.defaults.auth_mode is None


def test_load_config_auth_mode_valid_normalised(tmp_path: Path) -> None:
    """A valid but uppercased [defaults] auth_mode is normalised to lowercase on load."""
    path = tmp_path / "config.toml"
    path.write_text('[defaults]\nauth_mode = "DEFAULT"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg.defaults.auth_mode == "default"


def test_set_default_auth_mode_preserves_other_keys(tmp_path: Path) -> None:
    """set_default('auth_mode', ...) does not clear unrelated keys."""
    path = tmp_path / "config.toml"
    save_config(UserConfig(defaults=Defaults(workspace="WS", warehouse="WH", sql_pool=True)), path)
    set_default("auth_mode", "interactive", path)
    loaded = load_config(path)
    assert loaded.defaults.auth_mode == "interactive"
    assert loaded.defaults.workspace == "WS"
    assert loaded.defaults.warehouse == "WH"
    assert loaded.defaults.sql_pool is True


# ---------------------------------------------------------------------------
# Drift guard — VALID_AUTH_MODES must mirror CredentialMode enum values
# ---------------------------------------------------------------------------


def test_valid_auth_modes_mirrors_credential_mode_enum() -> None:
    """VALID_AUTH_MODES must be exactly the set of CredentialMode values.

    This test guards against VALID_AUTH_MODES drifting out of sync with
    :class:`~fabric_dw.auth.CredentialMode`.  If a new mode is added to the
    enum but not mirrored here (or vice-versa), this test will fail fast
    instead of silently rejecting/accepting the wrong modes at runtime.
    """
    assert frozenset(m.value for m in CredentialMode) == VALID_AUTH_MODES
