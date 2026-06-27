"""Persistent 24-hour filesystem name<->ID lookup cache.

Stores workspace and item (Warehouse / SQLEndpoint / WarehouseSnapshot)
name-to-UUID mappings in a single JSON file, protected by a FileLock so
multiple concurrent CLI or MCP processes can share the cache safely.

JSON shape::

    {
        "version": 1,
        "workspaces": {"<name_lower>": {"id": "<guid>", "fetched_at": "<iso8601>"}},
        "items": {
            "<ws_uuid>": {
                "<name_lower_or_guid_lower>": {
                    "id": "<guid>",
                    "kind": "<WarehouseKind>",
                    "connection_string": "<str | null>",
                    "fetched_at": "<iso8601>",
                }
            }
        },
    }

Names are stripped of leading/trailing whitespace and lower-cased at the
cache boundary.  GUID keys are stored lower-cased (the canonical UUID
string form is lower-case hex with hyphens).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import filelock

from fabric_dw.models import WarehouseKind

__all__ = [
    "ItemEntry",
    "LookupCache",
    "WorkspaceEntry",
]

_log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class WorkspaceEntry:
    """A cached workspace name→UUID mapping."""

    id: UUID
    fetched_at: datetime


@dataclass(frozen=True)
class ItemEntry:
    """A cached item (Warehouse / SQLEndpoint / Snapshot) name→detail mapping."""

    id: UUID
    kind: WarehouseKind
    connection_string: str | None
    fetched_at: datetime
    display_name: str = ""


class LookupCache:
    """Persistent filesystem name<->UUID cache with TTL and file locking.

    All name keys are normalised to lower-case at read and write time so
    that lookups are case-insensitive.
    """

    def __init__(
        self,
        path: Path | None = None,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if path is None:
            xdg = os.environ.get("XDG_CACHE_HOME")
            base = Path(xdg) if xdg else Path.home() / ".cache"
            path = base / "fabric-dw" / "lookup.json"
        self._path = path
        self._ttl = ttl
        self._lock = filelock.FileLock(str(path) + ".lock", timeout=5)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty() -> dict[str, Any]:
        """Return a fresh empty cache skeleton (never share the same inner dicts)."""
        return {"version": _SCHEMA_VERSION, "workspaces": {}, "items": {}}

    def _validate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate *data* shape and return it, or an empty skeleton on any violation."""
        if not isinstance(data, dict):
            _log.warning("Cache file %s has unexpected shape; treating as empty", self._path)
            return self._empty()
        if data.get("version") != _SCHEMA_VERSION:
            _log.info(
                "Cache schema version mismatch (found %r); starting empty",
                data.get("version"),
            )
            return self._empty()
        # Validate inner collections so callers can safely call .get() on them.
        # A corrupt-but-decodable file that has non-dict inner fields would otherwise
        # cause AttributeError/TypeError when callers invoke .get() on those values.
        if not isinstance(data.get("workspaces"), dict) or not isinstance(data.get("items"), dict):
            _log.warning(
                "Cache file %s has non-dict 'workspaces' or 'items' field; treating as empty",
                self._path,
            )
            return self._empty()
        # Validate each per-workspace bucket inside 'items' is also a dict (C24).
        # A corrupt bucket (e.g. a list or string) would raise AttributeError when
        # callers invoke .get() on it, bypassing the "treat-as-empty" guarantee.
        # Partial recovery: drop only the bad bucket(s) so healthy workspace
        # entries are preserved and do not force unnecessary API round-trips.
        items: Any = data["items"]
        bad_buckets = [k for k, v in items.items() if not isinstance(v, dict)]
        if bad_buckets:
            _log.warning(
                "Cache file %s has non-dict per-workspace bucket(s) %r; dropping corrupt bucket(s)",
                self._path,
                bad_buckets,
            )
            for k in bad_buckets:
                del items[k]
        return data

    def _read(self) -> dict[str, Any]:
        """Read and parse the cache file; return empty skeleton on missing/corrupt."""
        if not self._path.exists():
            return self._empty()
        try:
            raw = self._path.read_text(encoding="utf-8")
            data: dict[str, Any] = json.loads(raw)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            _log.warning(
                "Cache file %s is missing or corrupt; treating as empty",
                self._path,
                exc_info=True,
            )
            return self._empty()
        return self._validate(data)

    def _write(self, data: dict[str, Any]) -> None:
        """Atomically write *data* to the cache file, creating parent dirs as needed.

        Uses a temp file + os.replace() so readers always see either the old or
        the new complete file — a crash during write can never leave a truncated
        or partially-written JSON file on disk.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".lookup_tmp_",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, indent=None))
            os.replace(tmp_name, self._path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    def _is_fresh(self, fetched_at_str: str) -> bool:
        """Return True when *fetched_at_str* is within the TTL window."""
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
        except (ValueError, TypeError):
            return False
        now = datetime.now(tz=UTC)
        # Handle naive datetimes by assuming UTC
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        # A slightly-future fetched_at (clock skew) yields a negative age, which is
        # less than TTL, so it is correctly treated as fresh.
        return (now - fetched_at) < self._ttl

    def _get_record(self, section: dict[str, Any], key: str) -> dict[str, Any] | None:
        """Return a fresh, validated record dict from *section* under *key*, or None.

        Handles the lock-read, isinstance-guard, and freshness check that is
        shared between :meth:`get_workspace` and :meth:`get_item` (C09).
        """
        record = section.get(key)
        if not isinstance(record, dict):
            return None
        if not self._is_fresh(record.get("fetched_at", "")):
            return None
        return record

    @staticmethod
    def _entry_to_dict(entry: ItemEntry) -> dict[str, Any]:
        """Serialise *entry* to a JSON-compatible dict (C08).

        Single source of truth for the item cache schema so that
        :meth:`put_item` and :meth:`put_items` cannot diverge.
        """
        return {
            "id": str(entry.id),
            "kind": str(entry.kind),
            "connection_string": entry.connection_string,
            "fetched_at": entry.fetched_at.isoformat(),
            "display_name": entry.display_name,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_workspace(self, name: str) -> WorkspaceEntry | None:
        """Return a fresh cached workspace entry or *None* on miss/expiry."""
        with self._lock:
            data = self._read()
        workspaces: dict[str, Any] = data.get("workspaces", {})
        record = self._get_record(workspaces, name.strip().lower())
        if record is None:
            return None
        try:
            return WorkspaceEntry(
                id=UUID(record["id"]),
                fetched_at=datetime.fromisoformat(record["fetched_at"]),
            )
        except (KeyError, ValueError, TypeError):
            return None

    def put_workspace(self, name: str, workspace_id: UUID) -> None:
        """Store a workspace name→UUID mapping with the current timestamp."""
        key = name.strip().lower()
        with self._lock:
            data = self._read()
            workspaces: dict[str, Any] = data.setdefault("workspaces", {})
            workspaces[key] = {
                "id": str(workspace_id),
                "fetched_at": datetime.now(tz=UTC).isoformat(),
            }
            self._write(data)

    def get_item(self, workspace_id: UUID, name: str) -> ItemEntry | None:
        """Return a fresh cached item entry or *None* on miss/expiry.

        *name* may be a display name or a GUID string; both are normalised to
        lower-case (and stripped) before lookup so the same key is found
        regardless of how the entry was stored.
        """
        with self._lock:
            data = self._read()
        items: dict[str, Any] = data.get("items", {})
        ws_items: Any = items.get(str(workspace_id), {})
        # Guard: per-workspace bucket must be a dict (C24 — _validate catches this
        # for stored files, but in-memory mutations could bypass it).
        if not isinstance(ws_items, dict):
            return None
        record = self._get_record(ws_items, name.strip().lower())
        if record is None:
            return None
        try:
            conn = record.get("connection_string")
            dn = record.get("display_name", "")
            return ItemEntry(
                id=UUID(record["id"]),
                kind=WarehouseKind(record["kind"]),
                connection_string=conn if isinstance(conn, str) else None,
                fetched_at=datetime.fromisoformat(record["fetched_at"]),
                display_name=dn if isinstance(dn, str) else "",
            )
        except (KeyError, ValueError, TypeError):
            return None

    def put_item(self, workspace_id: UUID, name: str, entry: ItemEntry) -> None:
        """Store an item entry under *workspace_id* / *name*.

        *name* is stripped and lower-cased.  Pass either the display name or
        the GUID string to store an alias entry under the GUID key.
        """
        key = name.strip().lower()
        with self._lock:
            data = self._read()
            items: dict[str, Any] = data.setdefault("items", {})
            ws_items: dict[str, Any] = items.setdefault(str(workspace_id), {})
            ws_items[key] = self._entry_to_dict(entry)
            self._write(data)

    def put_items(self, workspace_id: UUID, entries: Iterable[tuple[str, ItemEntry]]) -> None:
        """Store multiple item entries under *workspace_id* in a single lock+read+write cycle.

        Each *(name, entry)* pair is treated identically to :meth:`put_item`:
        *name* is stripped and lower-cased before storage.  Passing aliases
        (e.g. display name + GUID string) in the same call avoids the two
        separate lock cycles that consecutive :meth:`put_item` calls would
        incur.
        """
        pairs = [(name.strip().lower(), entry) for name, entry in entries]
        if not pairs:
            return
        with self._lock:
            data = self._read()
            items: dict[str, Any] = data.setdefault("items", {})
            ws_items: dict[str, Any] = items.setdefault(str(workspace_id), {})
            for key, entry in pairs:
                ws_items[key] = self._entry_to_dict(entry)
            self._write(data)

    def evict_item(self, workspace_id: UUID, name: str) -> None:
        """Remove the item entry for *workspace_id* / *name* from the cache.

        *name* is stripped and lower-cased before key lookup.  A missing key is
        silently ignored so callers do not need to check for existence first.
        """
        key = name.strip().lower()
        with self._lock:
            data = self._read()
            items: dict[str, Any] = data.get("items", {})
            ws_items: dict[str, Any] = items.get(str(workspace_id), {})
            if key in ws_items:
                del ws_items[key]
                self._write(data)

    def evict_workspace(self, name_or_id: str) -> None:
        """Remove the workspace entry for *name_or_id* and all its item entries.

        *name_or_id* is stripped and lower-cased before workspace key lookup.
        If *name_or_id* looks like a UUID the items section is also purged by
        UUID key; if it is a display name, the stored UUID is looked up first
        so that the per-workspace items bucket can be removed too.

        Missing keys are silently ignored so callers do not need to check for
        existence first.
        """
        key = name_or_id.strip().lower()
        with self._lock:
            data = self._read()
            workspaces: dict[str, Any] = data.get("workspaces", {})
            items: dict[str, Any] = data.get("items", {})
            changed = False

            # Determine the UUID string for the workspace items bucket.
            # The workspace dict stores the id under key "id".
            ws_record = workspaces.get(key)
            ws_uuid_str: str | None = None
            if isinstance(ws_record, dict):
                ws_uuid_str = ws_record.get("id")
                del workspaces[key]
                changed = True
            elif key in items:
                # name_or_id was a UUID string; no workspace name entry present.
                # Also scan workspaces for any display-name entry pointing to this UUID
                # and remove it, so a subsequent get_workspace(name) also returns None.
                ws_uuid_str = key
                stale_keys = [
                    k
                    for k, v in workspaces.items()
                    if isinstance(v, dict) and v.get("id") == ws_uuid_str
                ]
                for stale in stale_keys:
                    del workspaces[stale]
                    changed = True

            if ws_uuid_str is not None and ws_uuid_str in items:
                del items[ws_uuid_str]
                changed = True

            if changed:
                self._write(data)

    def clear(self) -> None:
        """Erase all cached entries by writing an empty skeleton file."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write(self._empty())

    def counts(self) -> tuple[int, int]:
        """Return ``(workspace_count, item_workspace_bucket_count)`` from the cache file.

        Reads the current on-disk state and returns the number of workspace
        entries and the number of per-workspace item buckets.  Returns
        ``(0, 0)`` when the file is missing, corrupt, or unreadable.

        .. note:: TOCTOU window
            ``counts()`` acquires the file lock, reads, then releases it.
            A caller that uses the returned values to report what was cleared
            by a subsequent :meth:`clear_scope` or :meth:`clear` call has a
            small window between the two operations during which another writer
            (e.g. a concurrent MCP request) can populate or evict entries.
            The reported counts therefore reflect the state *immediately before*
            the clear, not the exact set of entries removed.  Under normal
            (non-concurrent) usage the counts are precise; the discrepancy is
            acceptable and represents a net improvement over the previous
            implementation, which masked the race entirely by swallowing
            exceptions.
        """
        with self._lock:
            data = self._read()
        ws_count = len(data.get("workspaces", {}))
        items_count = len(data.get("items", {}))
        return ws_count, items_count

    def clear_scope(self, scope: str) -> None:
        """Clear only workspace or item entries from the cache file.

        Args:
            scope: ``"workspaces"`` to erase only workspace name→UUID entries;
                ``"items"`` to erase only per-workspace item buckets.

        Raises:
            OSError: If the underlying file write fails.
            ValueError: If *scope* is not ``"workspaces"`` or ``"items"``.
        """
        if scope not in ("workspaces", "items"):
            msg = f"scope must be 'workspaces' or 'items', got {scope!r}"
            raise ValueError(msg)
        with self._lock:
            data = self._read()
            data[scope] = {}
            self._write(data)
