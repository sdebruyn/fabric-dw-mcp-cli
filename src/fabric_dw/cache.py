"""Persistent 24-hour filesystem name<->ID lookup cache.

Stores workspace and item (Warehouse / SQLEndpoint / WarehouseSnapshot)
name-to-UUID mappings in a single JSON file, protected by a FileLock so
multiple concurrent CLI or MCP processes can share the cache safely.

JSON shape::

    {
        "version": 1,
        "workspaces": {
            "<name_lower>": {"id": "<guid>", "fetched_at": "<iso8601>"}
        },
        "items": {
            "<ws_uuid>": {
                "<name_lower_or_guid_lower>": {
                    "id": "<guid>",
                    "kind": "<WarehouseKind>",
                    "connection_string": "<str | null>",
                    "fetched_at": "<iso8601>"
                }
            }
        }
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

    def _read(self) -> dict[str, Any]:
        """Read and parse the cache file; return empty skeleton on missing/corrupt."""
        if not self._path.exists():
            return self._empty()
        try:
            raw = self._path.read_text(encoding="utf-8")
            data: dict[str, Any] = json.loads(raw)
        except Exception:
            _log.warning("Cache file %s is missing or corrupt; treating as empty", self._path)
            return self._empty()
        else:
            if not isinstance(data, dict):
                _log.warning("Cache file %s has unexpected shape; treating as empty", self._path)
                return self._empty()
            return data

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
        return (now - fetched_at) < self._ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_workspace(self, name: str) -> WorkspaceEntry | None:
        """Return a fresh cached workspace entry or *None* on miss/expiry."""
        with self._lock:
            data = self._read()
        workspaces: dict[str, Any] = data.get("workspaces", {})
        record = workspaces.get(name.strip().lower())
        if not isinstance(record, dict):
            return None
        if not self._is_fresh(record.get("fetched_at", "")):
            return None
        try:
            return WorkspaceEntry(
                id=UUID(record["id"]),
                fetched_at=datetime.fromisoformat(record["fetched_at"]),
            )
        except (KeyError, ValueError, TypeError):
            return None

    def put_workspace(self, name: str, id: UUID) -> None:
        """Store a workspace name→UUID mapping with the current timestamp."""
        key = name.strip().lower()
        with self._lock:
            data = self._read()
            workspaces: dict[str, Any] = data.setdefault("workspaces", {})
            workspaces[key] = {
                "id": str(id),
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
        ws_items: dict[str, Any] = items.get(str(workspace_id), {})
        record = ws_items.get(name.strip().lower())
        if not isinstance(record, dict):
            return None
        if not self._is_fresh(record.get("fetched_at", "")):
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
            ws_items[key] = {
                "id": str(entry.id),
                "kind": str(entry.kind),
                "connection_string": entry.connection_string,
                "fetched_at": entry.fetched_at.isoformat(),
                "display_name": entry.display_name,
            }
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

    def clear(self) -> None:
        """Erase all cached entries by writing an empty skeleton file."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write(self._empty())
