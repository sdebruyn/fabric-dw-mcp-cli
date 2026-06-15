"""Tests for LookupCache - written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
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


# ---------------------------------------------------------------------------
# _read: narrow exception handling + schema version check
# ---------------------------------------------------------------------------


def test_read_logs_exc_info_on_oserror(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """_read must log with exc_info=True on OSError."""
    cache_file = tmp_path / "lookup.json"
    cache_file.write_text("valid json but will be replaced")
    cache = LookupCache(path=cache_file)

    # Make the file unreadable (simulate OSError during read_text)
    cache_file.chmod(0o000)
    try:
        with caplog.at_level(logging.WARNING, logger="fabric_dw.cache"):
            result = cache.get_workspace("anything")
        assert result is None
        # exc_info=True causes the traceback to appear in the log record
        assert any(r.exc_info is not None for r in caplog.records)
    finally:
        cache_file.chmod(0o644)


def test_read_returns_empty_on_schema_version_mismatch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """_read must return empty skeleton when the stored version != _SCHEMA_VERSION."""
    cache_file = tmp_path / "lookup.json"
    # Write a cache file with a future schema version
    cache_file.write_text(
        json.dumps(
            {
                "version": 99,
                "workspaces": {"ws": {"id": str(WS_ID), "fetched_at": "2099-01-01T00:00:00+00:00"}},
                "items": {},
            }
        )
    )
    cache = LookupCache(path=cache_file)
    with caplog.at_level(logging.INFO, logger="fabric_dw.cache"):
        result = cache.get_workspace("ws")
    assert result is None, "schema-mismatched cache must be treated as empty"
    assert any("schema version mismatch" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Freshness: future fetched_at must be rejected
# ---------------------------------------------------------------------------


def test_future_fetched_at_not_fresh(tmp_path: Path) -> None:
    """An entry with fetched_at in the future must be treated as stale."""
    cache = _make_cache(tmp_path, ttl=timedelta(hours=24))
    cache_file = tmp_path / "lookup.json"
    cache.put_workspace("ws", WS_ID)
    # Overwrite fetched_at to be one hour in the future
    future = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    data = json.loads(cache_file.read_text())
    data["workspaces"]["ws"]["fetched_at"] = future
    cache_file.write_text(json.dumps(data))
    assert cache.get_workspace("ws") is None, "future fetched_at must be rejected"


def test_exact_now_fetched_at_is_fresh(tmp_path: Path) -> None:
    """An entry with fetched_at == now is on the boundary and must be treated as fresh."""
    cache = _make_cache(tmp_path, ttl=timedelta(hours=24))
    # A freshly written entry should always be within TTL
    cache.put_workspace("ws", WS_ID)
    result = cache.get_workspace("ws")
    assert result is not None, "just-written entry must be fresh"


def test_item_future_fetched_at_rejected(tmp_path: Path) -> None:
    """Item entry with fetched_at in the future must be treated as stale."""
    cache = _make_cache(tmp_path, ttl=timedelta(hours=24))
    cache_file = tmp_path / "lookup.json"
    cache.put_item(WS_ID, "wh", _make_item_entry())
    future = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    data = json.loads(cache_file.read_text())
    data["items"][str(WS_ID)]["wh"]["fetched_at"] = future
    cache_file.write_text(json.dumps(data))
    assert cache.get_item(WS_ID, "wh") is None, "future fetched_at in item must be rejected"


# ---------------------------------------------------------------------------
# _read: corrupt inner collections (non-dict workspaces/items fields)
# ---------------------------------------------------------------------------


def test_read_treats_non_dict_workspaces_field_as_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If workspaces field is not a dict, _read returns empty skeleton."""
    cache_file = tmp_path / "lookup.json"
    cache_file.write_text(json.dumps({"version": 1, "workspaces": ["a", "b"], "items": {}}))
    cache = LookupCache(path=cache_file)
    with caplog.at_level(logging.WARNING, logger="fabric_dw.cache"):
        result = cache.get_workspace("anything")
    assert result is None
    assert any("non-dict" in r.message for r in caplog.records), (
        "expected a warning about non-dict field"
    )


def test_read_treats_non_dict_items_field_as_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If items field is not a dict, _read returns empty skeleton."""
    cache_file = tmp_path / "lookup.json"
    cache_file.write_text(json.dumps({"version": 1, "workspaces": {}, "items": "oops"}))
    cache = LookupCache(path=cache_file)
    with caplog.at_level(logging.WARNING, logger="fabric_dw.cache"):
        result = cache.get_item(WS_ID, "anything")
    assert result is None
    assert any("non-dict" in r.message for r in caplog.records), (
        "expected a warning about non-dict field"
    )


# ---------------------------------------------------------------------------
# evict_workspace
# ---------------------------------------------------------------------------


def test_evict_workspace_by_name_removes_entry(tmp_path: Path) -> None:
    """evict_workspace removes the workspace entry by display name."""
    cache = _make_cache(tmp_path)
    cache.put_workspace("MyWS", WS_ID)
    cache.evict_workspace("MyWS")
    assert cache.get_workspace("MyWS") is None


def test_evict_workspace_by_name_also_removes_items(tmp_path: Path) -> None:
    """evict_workspace removes all per-workspace item entries."""
    cache = _make_cache(tmp_path)
    cache.put_workspace("MyWS", WS_ID)
    cache.put_item(WS_ID, "wh1", _make_item_entry())
    cache.evict_workspace("MyWS")
    assert cache.get_workspace("MyWS") is None
    assert cache.get_item(WS_ID, "wh1") is None


def test_evict_workspace_by_uuid_removes_items(tmp_path: Path) -> None:
    """evict_workspace by UUID string removes the items bucket AND the display-name entry."""
    cache = _make_cache(tmp_path)
    cache.put_workspace("MyWS", WS_ID)
    cache.put_item(WS_ID, "wh1", _make_item_entry())
    cache.evict_workspace(str(WS_ID))
    # Items bucket must be gone
    assert cache.get_item(WS_ID, "wh1") is None
    # Display-name entry that pointed to this UUID must also be gone
    assert cache.get_workspace("MyWS") is None


def test_evict_workspace_by_uuid_removes_all_display_name_aliases(tmp_path: Path) -> None:
    """evict_workspace by UUID removes all workspace entries mapping to that UUID."""
    cache = _make_cache(tmp_path)
    # Store two display-name aliases pointing to the same UUID
    cache.put_workspace("Alias1", WS_ID)
    cache.put_workspace("Alias2", WS_ID)
    cache.put_item(WS_ID, "wh1", _make_item_entry())
    cache.evict_workspace(str(WS_ID))
    assert cache.get_workspace("Alias1") is None
    assert cache.get_workspace("Alias2") is None
    assert cache.get_item(WS_ID, "wh1") is None


def test_evict_workspace_case_insensitive(tmp_path: Path) -> None:
    """evict_workspace name lookup must be case-insensitive."""
    cache = _make_cache(tmp_path)
    cache.put_workspace("MyWS", WS_ID)
    cache.put_item(WS_ID, "wh1", _make_item_entry())
    cache.evict_workspace("MYWS")
    assert cache.get_workspace("myws") is None
    assert cache.get_item(WS_ID, "wh1") is None


def test_evict_workspace_does_not_affect_other_workspaces(tmp_path: Path) -> None:
    """evict_workspace must not remove entries belonging to another workspace."""
    cache = _make_cache(tmp_path)
    cache.put_workspace("WS1", WS_ID)
    cache.put_workspace("WS2", WS_ID_2)
    cache.put_item(WS_ID, "wh1", _make_item_entry())
    cache.put_item(WS_ID_2, "wh2", _make_item_entry())
    cache.evict_workspace("WS1")
    assert cache.get_workspace("WS2") is not None
    assert cache.get_item(WS_ID_2, "wh2") is not None


def test_evict_workspace_missing_is_noop(tmp_path: Path) -> None:
    """evict_workspace must not raise when the workspace does not exist."""
    cache = _make_cache(tmp_path)
    cache.evict_workspace("NonExistent")  # must not raise


# ---------------------------------------------------------------------------
# put_items: single lock+read+write for multiple keys
# ---------------------------------------------------------------------------


def test_put_items_stores_all_entries(tmp_path: Path) -> None:
    """put_items must store all provided (name, entry) pairs."""
    cache = _make_cache(tmp_path)
    entry = _make_item_entry()
    cache.put_items(WS_ID, [("SalesWarehouse", entry), (str(ITEM_ID), entry)])
    assert cache.get_item(WS_ID, "SalesWarehouse") is not None
    assert cache.get_item(WS_ID, str(ITEM_ID)) is not None


def test_put_items_single_write_cycle(tmp_path: Path) -> None:
    """put_items must use exactly one write cycle for multiple aliases."""
    cache = _make_cache(tmp_path)
    entry = _make_item_entry()

    write_calls: list[object] = []

    original_write = cache._write

    def tracking_write(data: dict[str, Any]) -> None:
        write_calls.append(data)
        original_write(data)

    with patch.object(cache, "_write", side_effect=tracking_write):
        cache.put_items(WS_ID, [("SalesWarehouse", entry), (str(ITEM_ID), entry)])

    assert len(write_calls) == 1, "put_items must call _write exactly once"


def test_put_items_empty_iterable_is_noop(tmp_path: Path) -> None:
    """put_items with an empty iterable must not write or raise."""
    cache = _make_cache(tmp_path)
    write_calls: list[object] = []

    def _capture(data: object) -> None:
        write_calls.append(data)

    with patch.object(cache, "_write", side_effect=_capture):
        cache.put_items(WS_ID, [])
    assert len(write_calls) == 0, "put_items([]) must not call _write"


def test_put_items_case_insensitive_keys(tmp_path: Path) -> None:
    """put_items must normalise keys to lower-case."""
    cache = _make_cache(tmp_path)
    entry = _make_item_entry()
    cache.put_items(WS_ID, [("SalesWarehouse", entry)])
    assert cache.get_item(WS_ID, "saleswarehouse") is not None


# ---------------------------------------------------------------------------
# C24: _validate checks per-workspace item buckets (inner entries)
# ---------------------------------------------------------------------------


def test_corrupt_inner_item_bucket_treated_as_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If a per-workspace bucket inside 'items' is not a dict, _read returns empty (C24).

    Previously _validate only checked the top-level 'items' dict; a corrupt
    bucket (e.g. a list) would reach get_item and raise AttributeError.  Now
    the whole file is treated as empty and a warning is emitted.
    """
    cache_file = tmp_path / "lookup.json"
    cache_file.write_text(
        json.dumps(
            {
                "version": 1,
                "workspaces": {},
                "items": {str(WS_ID): ["not", "a", "dict"]},
            }
        )
    )
    cache = LookupCache(path=cache_file)
    with caplog.at_level(logging.WARNING, logger="fabric_dw.cache"):
        result = cache.get_item(WS_ID, "anything")
    assert result is None
    assert any("non-dict per-workspace bucket" in r.message for r in caplog.records), (
        f"expected warning about non-dict bucket, got: {[r.message for r in caplog.records]}"
    )


def test_corrupt_inner_bucket_string_treated_as_empty(tmp_path: Path) -> None:
    """A string per-workspace bucket must be treated as empty (not raise AttributeError)."""
    cache_file = tmp_path / "lookup.json"
    cache_file.write_text(
        json.dumps(
            {
                "version": 1,
                "workspaces": {},
                "items": {str(WS_ID): "oops-a-string"},
            }
        )
    )
    cache = LookupCache(path=cache_file)
    # Must not raise AttributeError; must degrade gracefully.
    assert cache.get_item(WS_ID, "anything") is None


def test_valid_file_with_proper_buckets_not_affected(tmp_path: Path) -> None:
    """A valid file with well-formed per-workspace dicts is still returned correctly."""
    cache = _make_cache(tmp_path)
    entry = _make_item_entry()
    cache.put_item(WS_ID, "wh", entry)
    # Re-open (to force a read from disk) and check the entry is still present.
    cache2 = LookupCache(path=tmp_path / "lookup.json")
    result = cache2.get_item(WS_ID, "wh")
    assert result is not None
    assert result.id == ITEM_ID


# ---------------------------------------------------------------------------
# C08/C09: _entry_to_dict / _get_record helpers
# ---------------------------------------------------------------------------


def test_entry_to_dict_round_trips_via_put_get(tmp_path: Path) -> None:
    """Serialization via _entry_to_dict must survive the full put→get cycle."""
    cache = _make_cache(tmp_path)
    entry = ItemEntry(
        id=ITEM_ID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string="srv.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="Sales Warehouse",
    )
    cache.put_item(WS_ID, "sales", entry)
    result = cache.get_item(WS_ID, "sales")
    assert result is not None
    assert result.id == ITEM_ID
    assert result.display_name == "Sales Warehouse"
    assert result.connection_string == "srv.fabric.microsoft.com"


def test_put_item_and_put_items_produce_identical_schema(tmp_path: Path) -> None:
    """put_item and put_items must write identical dict structures (C08).

    Both rely on _entry_to_dict so schema drift between the two paths is
    impossible.  Verify by checking the raw JSON on disk.
    """
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    entry = _make_item_entry()

    cache_a = LookupCache(path=path_a)
    cache_a.put_item(WS_ID, "wh", entry)

    cache_b = LookupCache(path=path_b)
    cache_b.put_items(WS_ID, [("wh", entry)])

    data_a = json.loads(path_a.read_text())
    data_b = json.loads(path_b.read_text())
    ws_key = str(WS_ID)
    assert data_a["items"][ws_key]["wh"].keys() == data_b["items"][ws_key]["wh"].keys()
