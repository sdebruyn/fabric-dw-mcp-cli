"""Tests for cache sub-commands — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import cli

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WS_UUID = UUID(WS_GUID)


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect XDG_CACHE_HOME to a temp dir so cache files are isolated."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


class TestCacheShow:
    """cache show prints the current cache contents."""

    def test_show_empty_cache_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        result = runner.invoke(cli, ["cache", "show"])
        assert result.exit_code == 0

    def test_show_empty_cache_json_flag(self, runner: CliRunner, cache_env: Path) -> None:
        result = runner.invoke(cli, ["--json", "cache", "show"])
        assert result.exit_code == 0
        # With --json, output must be parseable JSON
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_show_displays_cache_structure(self, runner: CliRunner, cache_env: Path) -> None:
        # Pre-populate cache
        cache_file = cache_env / "fabric-dw" / "lookup.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({
                "version": 1,
                "workspaces": {"myws": {"id": WS_GUID, "fetched_at": "2026-01-01T00:00:00+00:00"}},
                "items": {},
            })
        )
        result = runner.invoke(cli, ["--json", "cache", "show"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "workspaces" in parsed


class TestCacheClear:
    """cache clear wipes the cache file when confirmed."""

    def test_clear_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        result = runner.invoke(cli, ["--yes", "cache", "clear"])
        assert result.exit_code == 0

    def test_clear_yes_writes_empty_cache(self, runner: CliRunner, cache_env: Path) -> None:
        cache_file = cache_env / "fabric-dw" / "lookup.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({
                "version": 1,
                "workspaces": {"myws": {"id": WS_GUID, "fetched_at": "2026-01-01T00:00:00+00:00"}},
                "items": {},
            })
        )
        runner.invoke(cli, ["--yes", "cache", "clear"])
        data = json.loads(cache_file.read_text())
        assert data["workspaces"] == {}

    def test_clear_decline_does_not_clear(self, runner: CliRunner, cache_env: Path) -> None:
        cache_file = cache_env / "fabric-dw" / "lookup.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({
                "version": 1,
                "workspaces": {"myws": {"id": WS_GUID, "fetched_at": "2026-01-01T00:00:00+00:00"}},
                "items": {},
            })
        )
        # Simulate user declining; inject 'n' as input
        runner.invoke(cli, ["cache", "clear"], input="n\n")
        data = json.loads(cache_file.read_text())
        # Cache should still have the workspace entry
        assert "myws" in data["workspaces"]


class TestCacheInvalidate:
    """cache invalidate removes a workspace entry from the cache."""

    def test_invalidate_guid_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        result = runner.invoke(cli, ["cache", "invalidate", WS_GUID])
        assert result.exit_code == 0

    def test_invalidate_guid_removes_workspace(self, runner: CliRunner, cache_env: Path) -> None:
        cache_file = cache_env / "fabric-dw" / "lookup.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({
                "version": 1,
                "workspaces": {"myws": {"id": WS_GUID, "fetched_at": "2026-01-01T00:00:00+00:00"}},
                "items": {WS_GUID: {"someitem": {"id": "x", "kind": "Warehouse"}}},
            })
        )
        runner.invoke(cli, ["cache", "invalidate", WS_GUID])
        data = json.loads(cache_file.read_text())
        assert "myws" not in data["workspaces"]
        assert WS_GUID not in data.get("items", {})

    def test_invalidate_missing_guid_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        # GUID not in cache — should still exit cleanly
        result = runner.invoke(cli, ["cache", "invalidate", WS_GUID])
        assert result.exit_code == 0
