"""Tests for LookupCache - written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.models import WarehouseKind

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
WS_ID_2 = UUID("b2c3d4e5-f6a7-8901-bcde-f01234567891")
ITEM_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")


def _make_cache(tmp_path: Path, ttl: timedelta = timedelta(hours=24)) -> LookupCache:
    return LookupCache(path=tmp_path / "lookup.json", ttl=ttl)


def _make_item_entry(
    item_id: UUID = ITEM_ID,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    conn: str | None = "srv.fabric.microsoft.com",
    fetched_at: datetime | None = None,
) -> ItemEntry:
    return ItemEntry(
        id=item_id,
        kind=kind,
        connection_string=conn,
        fetched_at=fetched_at or datetime.now(tz=UTC),
    )


# ---------------------------------------------------------------------------
# Round-trip: workspace
# ---------------------------------------------------------------------------


def test_put_get_workspace_round_trip(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    cache.put_workspace("MyWorkspace", WS_ID)
    result = cache.get_workspace("MyWorkspace")
    assert result is not None
    assert result.id == WS_ID


def test_get_workspace_missing_returns_none(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    assert cache.get_workspace("NonExistent") is None


# ---------------------------------------------------------------------------
# Round-trip: item
# ---------------------------------------------------------------------------


def test_put_get_item_round_trip(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    entry = _make_item_entry()
    cache.put_item(WS_ID, "SalesWarehouse", entry)
    result = cache.get_item(WS_ID, "SalesWarehouse")
    assert result is not None
    assert result.id == ITEM_ID
    assert result.kind == WarehouseKind.WAREHOUSE
    assert result.connection_string == "srv.fabric.microsoft.com"


def test_get_item_missing_returns_none(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    assert cache.get_item(WS_ID, "NonExistent") is None


def test_put_item_no_connection_string(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    entry = _make_item_entry(conn=None, kind=WarehouseKind.SNAPSHOT)
    cache.put_item(WS_ID, "snap", entry)
    result = cache.get_item(WS_ID, "snap")
    assert result is not None
    assert result.connection_string is None
    assert result.kind == WarehouseKind.SNAPSHOT


# ---------------------------------------------------------------------------
# Case-insensitive lookups
# ---------------------------------------------------------------------------


def test_workspace_lookup_case_insensitive(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    cache.put_workspace("MyWorkspace", WS_ID)
    assert cache.get_workspace("myworkspace") is not None
    assert cache.get_workspace("MYWORKSPACE") is not None
    assert cache.get_workspace("MyWorkspace") is not None


def test_item_lookup_case_insensitive(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    entry = _make_item_entry()
    cache.put_item(WS_ID, "SalesWarehouse", entry)
    assert cache.get_item(WS_ID, "saleswarehouse") is not None
    assert cache.get_item(WS_ID, "SALESWAREHOUSE") is not None
    assert cache.get_item(WS_ID, "SalesWarehouse") is not None


def test_put_uppercase_get_lowercase(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    cache.put_workspace("Foo", WS_ID)
    result = cache.get_workspace("foo")
    assert result is not None
    assert result.id == WS_ID


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


def test_workspace_expired_returns_none(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path, ttl=timedelta(hours=1))
    past = datetime.now(tz=UTC) - timedelta(hours=2)
    # Write the entry directly with a past fetched_at via put then overwrite JSON
    cache_file = tmp_path / "lookup.json"
    cache.put_workspace("ws", WS_ID)
    # Overwrite fetched_at to be in the past
    data = json.loads(cache_file.read_text())
    data["workspaces"]["ws"]["fetched_at"] = past.isoformat()
    cache_file.write_text(json.dumps(data))
    assert cache.get_workspace("ws") is None


def test_item_expired_returns_none(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path, ttl=timedelta(hours=1))
    past = datetime.now(tz=UTC) - timedelta(hours=2)
    entry = _make_item_entry(fetched_at=past)
    cache_file = tmp_path / "lookup.json"
    cache.put_item(WS_ID, "wh", entry)
    # Overwrite fetched_at in the JSON
    data = json.loads(cache_file.read_text())
    ws_key = str(WS_ID)
    data["items"][ws_key]["wh"]["fetched_at"] = past.isoformat()
    cache_file.write_text(json.dumps(data))
    assert cache.get_item(WS_ID, "wh") is None


def test_item_within_ttl_returned(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path, ttl=timedelta(hours=24))
    entry = _make_item_entry()
    cache.put_item(WS_ID, "wh", entry)
    result = cache.get_item(WS_ID, "wh")
    assert result is not None


# ---------------------------------------------------------------------------
# LookupCache.clear
# ---------------------------------------------------------------------------


def test_clear_empties_file(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    cache.put_workspace("ws", WS_ID)
    cache.put_item(WS_ID, "item", _make_item_entry())
    cache.clear()
    raw = json.loads((tmp_path / "lookup.json").read_text())
    assert raw == {"version": 1, "workspaces": {}, "items": {}}


def test_clear_then_get_returns_none(tmp_path: Path) -> None:
    cache = _make_cache(tmp_path)
    cache.put_workspace("ws", WS_ID)
    cache.clear()
    assert cache.get_workspace("ws") is None


# ---------------------------------------------------------------------------
# Concurrent writes (file locking)
# ---------------------------------------------------------------------------


def test_concurrent_writes_produce_valid_json(tmp_path: Path) -> None:
    """Two threads writing simultaneously must not corrupt the file."""
    cache_file = tmp_path / "lookup.json"
    errors: list[Exception] = []

    def writer_a() -> None:
        try:
            c = LookupCache(path=cache_file)
            for i in range(5):
                c.put_workspace(f"ws_a_{i}", WS_ID)
        except Exception as exc:
            errors.append(exc)

    def writer_b() -> None:
        try:
            c = LookupCache(path=cache_file)
            for i in range(5):
                c.put_workspace(f"ws_b_{i}", WS_ID_2)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=writer_a)
    t2 = threading.Thread(target=writer_b)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Thread errors: {errors}"
    # File must be valid JSON after concurrent writes
    raw = json.loads(cache_file.read_text())
    assert "workspaces" in raw
    assert "items" in raw
    # Both sets of keys must be present
    for i in range(5):
        assert f"ws_a_{i}" in raw["workspaces"]
        assert f"ws_b_{i}" in raw["workspaces"]


# ---------------------------------------------------------------------------
# Corrupt-file resilience
# ---------------------------------------------------------------------------


def test_corrupt_file_get_returns_none(tmp_path: Path) -> None:
    cache_file = tmp_path / "lookup.json"
    cache_file.write_text("not json")
    cache = LookupCache(path=cache_file)
    assert cache.get_workspace("anything") is None
    assert cache.get_item(WS_ID, "anything") is None


def test_corrupt_file_then_put_overwrites_cleanly(tmp_path: Path) -> None:
    cache_file = tmp_path / "lookup.json"
    cache_file.write_text("not json")
    cache = LookupCache(path=cache_file)
    cache.put_workspace("ws", WS_ID)
    # File should now be valid JSON
    raw = json.loads(cache_file.read_text())
    assert raw["workspaces"]["ws"]["id"] == str(WS_ID)


# ---------------------------------------------------------------------------
# Default path resolution (does NOT touch ~/.cache in tests)
# ---------------------------------------------------------------------------


def test_custom_path_used(tmp_path: Path) -> None:
    custom = tmp_path / "subdir" / "cache.json"
    cache = LookupCache(path=custom)
    cache.put_workspace("ws", WS_ID)
    assert custom.exists()


def test_parent_directory_created_lazily(tmp_path: Path) -> None:
    deep_path = tmp_path / "a" / "b" / "c" / "lookup.json"
    cache = LookupCache(path=deep_path)
    cache.put_workspace("ws", WS_ID)
    assert deep_path.exists()


# ---------------------------------------------------------------------------
# XDG_CACHE_HOME path resolution (finding 5)
# ---------------------------------------------------------------------------


def test_xdg_cache_home_used_for_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LookupCache without explicit path should place the file under XDG_CACHE_HOME."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache = LookupCache()
    cache.put_workspace("ws", WS_ID)
    expected = tmp_path / "fabric-dw" / "lookup.json"
    assert expected.exists(), f"Expected cache file at {expected}"
    assert cache.get_workspace("ws") is not None


# ---------------------------------------------------------------------------
# Name whitespace stripping (finding 6)
# ---------------------------------------------------------------------------


def test_put_workspace_strips_whitespace(tmp_path: Path) -> None:
    """put_workspace should strip leading/trailing whitespace before storing."""
    cache = _make_cache(tmp_path)
    cache.put_workspace("  Foo  ", WS_ID)
    # Lookup without whitespace should work
    result = cache.get_workspace("foo")
    assert result is not None
    assert result.id == WS_ID


def test_get_workspace_strips_whitespace(tmp_path: Path) -> None:
    """get_workspace should strip whitespace in the query key."""
    cache = _make_cache(tmp_path)
    cache.put_workspace("Foo", WS_ID)
    result = cache.get_workspace("  foo  ")
    assert result is not None
    assert result.id == WS_ID


def test_put_item_strips_whitespace(tmp_path: Path) -> None:
    """put_item should strip whitespace from name before storing."""
    cache = _make_cache(tmp_path)
    entry = _make_item_entry()
    cache.put_item(WS_ID, "  SalesWarehouse  ", entry)
    result = cache.get_item(WS_ID, "saleswarehouse")
    assert result is not None
    assert result.id == ITEM_ID


def test_get_item_strips_whitespace(tmp_path: Path) -> None:
    """get_item should strip whitespace in the query key."""
    cache = _make_cache(tmp_path)
    entry = _make_item_entry()
    cache.put_item(WS_ID, "SalesWarehouse", entry)
    result = cache.get_item(WS_ID, "  SalesWarehouse  ")
    assert result is not None
    assert result.id == ITEM_ID


# ---------------------------------------------------------------------------
# evict_item
# ---------------------------------------------------------------------------


def test_evict_item_removes_existing_entry(tmp_path: Path) -> None:
    """evict_item must remove an existing item entry so subsequent get returns None."""
    cache = _make_cache(tmp_path)
    cache.put_item(WS_ID, "SalesWarehouse", _make_item_entry())
    cache.evict_item(WS_ID, "SalesWarehouse")
    assert cache.get_item(WS_ID, "SalesWarehouse") is None


def test_evict_item_is_case_insensitive(tmp_path: Path) -> None:
    """evict_item must evict regardless of case in the name argument."""
    cache = _make_cache(tmp_path)
    cache.put_item(WS_ID, "SalesWarehouse", _make_item_entry())
    cache.evict_item(WS_ID, "SALESWAREHOUSE")
    assert cache.get_item(WS_ID, "saleswarehouse") is None


def test_evict_item_strips_whitespace(tmp_path: Path) -> None:
    """evict_item must strip whitespace from the name before lookup."""
    cache = _make_cache(tmp_path)
    cache.put_item(WS_ID, "SalesWarehouse", _make_item_entry())
    cache.evict_item(WS_ID, "  SalesWarehouse  ")
    assert cache.get_item(WS_ID, "SalesWarehouse") is None


def test_evict_item_missing_key_is_noop(tmp_path: Path) -> None:
    """evict_item must silently ignore a missing key."""
    cache = _make_cache(tmp_path)
    cache.put_item(WS_ID, "SalesWarehouse", _make_item_entry())
    cache.evict_item(WS_ID, "NonExistent")  # must not raise
    assert cache.get_item(WS_ID, "SalesWarehouse") is not None


def test_evict_item_only_removes_target_workspace(tmp_path: Path) -> None:
    """evict_item must not affect entries in other workspaces."""
    cache = _make_cache(tmp_path)
    cache.put_item(WS_ID, "SalesWarehouse", _make_item_entry())
    cache.put_item(WS_ID_2, "SalesWarehouse", _make_item_entry())
    cache.evict_item(WS_ID, "SalesWarehouse")
    assert cache.get_item(WS_ID, "SalesWarehouse") is None
    assert cache.get_item(WS_ID_2, "SalesWarehouse") is not None


def test_evict_item_missing_workspace_section_is_noop(tmp_path: Path) -> None:
    """evict_item must silently handle the case where the workspace has no entries at all."""
    cache = _make_cache(tmp_path)
    cache.evict_item(WS_ID, "SalesWarehouse")  # must not raise
