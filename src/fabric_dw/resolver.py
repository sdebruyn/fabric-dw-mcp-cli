"""Name-or-GUID resolver for Fabric workspaces and warehouse items.

Every CLI / MCP command accepts either a human-readable name or a raw GUID
for workspace and item (warehouse / SQL endpoint / snapshot) arguments.

Resolution order:

1. If the value already looks like a GUID, skip the API and return immediately.
2. If the name is in the local 24-hour cache (not expired), return from cache.
3. Otherwise hit the API, populate the cache, and return.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any, NamedTuple
from uuid import UUID

from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import WarehouseKind

__all__ = [
    "GUID_RE",
    "Resolver",
]

GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Maximum length allowed for OData-escaped name values.
# Fabric display names are well below this limit; rejecting oversized inputs
# defends against injection attempts and avoids unexpected server errors.
_ODATA_MAX_LEN = 256

# How long (in seconds) a negative-cache entry is considered valid.
# Kept in-memory only — persisting "not found" results across restarts would
# suppress retries even when the resource reappears after a short outage.
#
# This TTL is intentionally short (5 s): the negative cache exists only to
# suppress rapid repeated misses (typo retry loops, burst lookups within a
# single user gesture).  A longer window would mask newly created resources
# from the MCP singleton, which outlives any single command invocation.
_NEGATIVE_TTL = 5.0


class ItemTypeInfo(NamedTuple):
    """Metadata for a supported Fabric item type."""

    kind: WarehouseKind
    # Type-specific REST endpoint segment, or None when the generic /items/{id}
    # endpoint is sufficient (e.g. WarehouseSnapshot has no dedicated endpoint).
    endpoint: str | None


# Single source of truth for all supported item type mappings.
# Derives the frozenset of valid types and the kind/endpoint lookups.
_ITEM_TYPE_INFO: dict[str, ItemTypeInfo] = {
    "Warehouse": ItemTypeInfo(kind=WarehouseKind.WAREHOUSE, endpoint="warehouses"),
    "SQLEndpoint": ItemTypeInfo(kind=WarehouseKind.SQL_ENDPOINT, endpoint="sqlEndpoints"),
    # WarehouseSnapshot has no type-specific detail endpoint; fall back to generic.
    "WarehouseSnapshot": ItemTypeInfo(kind=WarehouseKind.SNAPSHOT, endpoint=None),
}

_ITEM_TYPES: frozenset[str] = frozenset(_ITEM_TYPE_INFO)


def _odata_escape(value: str) -> str:
    """Escape a string value for use inside an OData single-quoted literal.

    Per the OData specification, a single quote inside a single-quoted string
    must be escaped by doubling it (e.g. ``O'Brien`` → ``O''Brien``).

    Args:
        value: The raw string to escape.

    Returns:
        The escaped string suitable for embedding in an OData filter.

    Raises:
        FabricError: If *value* exceeds ``_ODATA_MAX_LEN`` characters.
            Fabric display names are much shorter; an oversized value most
            likely indicates an injection attempt or a caller bug.
            Raising ``FabricError`` (not ``ValueError``) ensures the existing
            ``except FabricError`` handlers in MCP tools and CLI catch it and
            surface a structured error rather than a raw traceback.
    """
    if len(value) > _ODATA_MAX_LEN:
        msg = (
            "workspace or item name exceeds 256 characters "
            f"({len(value)} chars); Fabric display names cannot exceed this length"
        )
        raise FabricError(msg)
    return value.replace("'", "''")


def _connection_string_from_detail(payload: dict[str, Any], kind: WarehouseKind) -> str | None:
    """Extract the connection string from a raw item detail payload."""
    props: dict[str, Any] = payload.get("properties") or {}
    if kind == WarehouseKind.WAREHOUSE:
        conn = props.get("connectionString")
        return conn if isinstance(conn, str) else None
    if kind == WarehouseKind.SQL_ENDPOINT:
        sql_ep: dict[str, Any] = props.get("sqlEndpointProperties") or {}
        conn = sql_ep.get("connectionString")
        return conn if isinstance(conn, str) else None
    # WarehouseSnapshot has no connection string
    return None


class Resolver:
    """Resolves workspace / item names or GUIDs to UUIDs and ItemEntry objects.

    In addition to the persistent filesystem cache (``LookupCache``), each
    ``Resolver`` instance keeps an **in-memory** negative cache.  When a
    workspace or item lookup raises ``NotFoundError``, the failed key is recorded
    with a monotonic timestamp; subsequent lookups within ``_NEGATIVE_TTL``
    seconds re-raise ``NotFoundError`` without hitting the API.

    The negative cache is intentionally *not* persisted.  "Not found" is a
    transient condition (the resource may be created moments later), and
    persisting it across process restarts would suppress retries even after
    the resource appears.  An in-memory TTL of ``_NEGATIVE_TTL`` seconds
    provides adequate 429-protection without that risk.
    """

    def __init__(self, http: FabricHttpClient, cache: LookupCache) -> None:
        self._http = http
        self._cache = cache
        # Negative cache: maps (scope, key) → monotonic timestamp of the failure.
        # scope is "workspace" or a workspace UUID string (for items).
        self._negative: dict[tuple[str, str], float] = {}

    def _negative_check(self, scope: str, key: str) -> None:
        """Raise NotFoundError immediately if *key* is in the negative cache and still fresh.

        *key* is normalised to lower-case so that negative-cache lookups are
        case-insensitive and consistent with the positive :class:`LookupCache`
        which stores all names as lower-case.
        """
        normalised = key.strip().lower()
        ts = self._negative.get((scope, normalised))
        if ts is not None:
            age = time.monotonic() - ts
            if age < _NEGATIVE_TTL:
                raise NotFoundError(f"{scope}/{normalised!r} not found")
            # Entry has expired; prune it now to keep the dict bounded.
            del self._negative[(scope, normalised)]

    def _negative_record(self, scope: str, key: str) -> None:
        """Record a not-found result in the negative cache.

        *key* is normalised to lower-case (consistent with positive cache).
        """
        self._negative[(scope, key.strip().lower())] = time.monotonic()

    def _negative_clear(self, scope: str, key: str) -> None:
        """Remove a negative-cache entry after a successful put.

        *key* is normalised to lower-case (consistent with positive cache).
        """
        self._negative.pop((scope, key.strip().lower()), None)

    def _negative_clear_scope(self, scope: str) -> None:
        """Remove all negative-cache entries whose first element equals *scope*.

        Used after a successful item detail fetch to ensure that newly created
        (or just-discovered) items become immediately resolvable within the
        ``_NEGATIVE_TTL`` window.
        """
        stale = [k for k in self._negative if k[0] == scope]
        for k in stale:
            del self._negative[k]

    def clear_negative_cache(self) -> None:
        """Clear the entire in-memory negative cache.

        Cheap O(1) operation that discards all recorded "not found" results.
        Mutating frontends (MCP create / rename tools) call this after a
        successful write so that subsequent lookups for the new name succeed
        immediately rather than waiting for ``_NEGATIVE_TTL`` seconds to elapse.
        """
        self._negative.clear()

    # ------------------------------------------------------------------
    # workspace_id
    # ------------------------------------------------------------------

    async def workspace_id(self, value: str) -> UUID:
        """Resolve *value* (name or GUID) to a workspace UUID.

        Leading/trailing whitespace in *value* is stripped before lookup.

        Args:
            value: A workspace display name or a GUID string.

        Returns:
            The workspace UUID.

        Raises:
            NotFoundError: If no workspace matches *value*.
            FabricError: If *value* matches more than one workspace.
        """
        value = value.strip()

        # 1. GUID fast-path
        if GUID_RE.match(value):
            return UUID(value)

        # 2. Negative cache hit — avoid re-hitting the API for known-missing names
        self._negative_check("workspace", value)

        # 3. Cache hit
        cached = self._cache.get_workspace(value)
        if cached is not None:
            return cached.id

        # 4. Power BI OData filter — escape single quotes per OData spec
        resp = await self._http.request(
            "GET",
            HttpBase.POWERBI,
            "/groups",
            params={"$filter": f"name eq '{_odata_escape(value)}'"},
        )
        body: dict[str, Any] = resp.json()
        results: list[dict[str, Any]] = body.get("value", [])

        if not results:
            self._negative_record("workspace", value)
            raise NotFoundError(f"workspace {value!r} not found")

        if len(results) > 1:
            ids = ", ".join(str(r.get("id", "?")) for r in results)
            raise FabricError(f"workspace name {value!r} is ambiguous: ids = {ids}")

        ws_id = UUID(str(results[0]["id"]))
        self._cache.put_workspace(value, ws_id)
        self._negative_clear("workspace", value)
        return ws_id

    # ------------------------------------------------------------------
    # item
    # ------------------------------------------------------------------

    async def item(self, workspace: str, value: str, *, item_type: str | None = None) -> ItemEntry:
        """Resolve *value* (name or GUID) to an ItemEntry within *workspace*.

        Leading/trailing whitespace in *value* is stripped before lookup.

        Args:
            workspace: Workspace name or GUID.
            value: Item display name or GUID.
            item_type: Optional Fabric item type string (e.g. ``"Warehouse"``)
                used to narrow the server-side ``type`` filter when paging
                through items.  Has no effect when *value* is a GUID.

        Returns:
            The resolved ItemEntry with ``connection_string`` populated.

        Raises:
            NotFoundError: If the item is not found in the workspace.
        """
        value = value.strip()
        ws_id = await self.workspace_id(workspace)
        ws_key = str(ws_id)

        # 1. GUID fast-path: check cache first, then fetch detail
        if GUID_RE.match(value):
            item_uuid = UUID(value)
            cached_item = self._cache.get_item(ws_id, value)
            if cached_item is not None:
                return cached_item
            self._negative_check(ws_key, value)
            try:
                result = await self._fetch_item_detail(ws_id, item_uuid)
            except NotFoundError:
                self._negative_record(ws_key, value)
                raise
            self._negative_clear(ws_key, value)
            return result

        # 2. Negative cache hit
        self._negative_check(ws_key, value)

        # 3. Cache hit
        cached_item = self._cache.get_item(ws_id, value)
        if cached_item is not None:
            return cached_item

        # 4. Page through /v1/workspaces/{ws}/items, filter by kind + name.
        # Pass ``type`` parameter when a single item type is known so the
        # server returns fewer items (Fabric items API supports this filter).
        # Reject unknown item_type values immediately (D22) rather than
        # silently ignoring them, which would broaden the search scope.
        if item_type and item_type not in _ITEM_TYPES:
            msg = f"Unknown item_type {item_type!r}. Supported types: {sorted(_ITEM_TYPES)}"
            raise FabricError(msg)
        list_params: dict[str, str] | None = None
        if item_type:
            list_params = {"type": item_type}

        async for raw_item in self._http.iter_paginated(
            HttpBase.FABRIC, f"/workspaces/{ws_id}/items", params=list_params
        ):
            raw_type = str(raw_item.get("type", ""))
            if raw_type not in _ITEM_TYPES:
                continue
            display_name = str(raw_item.get("displayName", ""))
            if display_name.lower() != value.lower():
                continue
            # Found a name match — fetch full detail to get connection_string.
            # Break out of pagination immediately; no need to fetch remaining pages.
            item_id = UUID(str(raw_item["id"]))
            try:
                result = await self._fetch_item_detail(ws_id, item_id)
            except NotFoundError:
                self._negative_record(ws_key, value)
                raise
            self._negative_clear(ws_key, value)
            return result

        self._negative_record(ws_key, value)
        raise NotFoundError(f"item {value!r} not found in workspace {ws_id}")

    async def _fetch_item_detail(self, workspace_id: UUID, item_id: UUID) -> ItemEntry:
        """Fetch a single item's full detail, build ItemEntry, cache and return it.

        Resolution strategy:
        1. GET the generic ``/items/{id}`` endpoint to discover the item's type.
        2. If the type has a dedicated endpoint (Warehouse → /warehouses/{id},
           SQLEndpoint → /sqlEndpoints/{id}), fetch that endpoint for the full
           ``properties`` payload including ``connectionString``.
        3. Store the entry under both the display name and the GUID string in a
           single lock+read+write cycle via :meth:`LookupCache.put_items`.
        4. Raise ``FabricError`` for any unsupported item type.
        """
        # Step 1 — generic discovery
        resp = await self._http.request(
            "GET",
            HttpBase.FABRIC,
            f"/workspaces/{workspace_id}/items/{item_id}",
        )
        generic_payload: dict[str, Any] = resp.json()
        item_type = str(generic_payload.get("type", ""))

        if item_type not in _ITEM_TYPE_INFO:
            raise FabricError(
                f"item {item_id} has unsupported type {item_type!r}; "
                "expected Warehouse, SQLEndpoint or WarehouseSnapshot"
            )

        type_info = _ITEM_TYPE_INFO[item_type]
        kind = type_info.kind

        # Step 2 — type-specific detail fetch (if available)
        if type_info.endpoint is not None:
            detail_resp = await self._http.request(
                "GET",
                HttpBase.FABRIC,
                f"/workspaces/{workspace_id}/{type_info.endpoint}/{item_id}",
            )
            payload: dict[str, Any] = detail_resp.json()
        else:
            payload = generic_payload

        conn = _connection_string_from_detail(payload, kind)

        # Lakehouse-derived SQL endpoints permanently return an empty
        # connectionString from the /sqlEndpoints/{id} resource.  When the
        # connection string is absent, fall back to scanning /lakehouses for
        # the parent Lakehouse whose sqlEndpointProperties.id matches this
        # endpoint's ID (see issue #347 / #471).
        #
        # Import is local to avoid a circular-import layering violation:
        # resolver.py is low-level; services/sql_endpoints.py sits above it
        # and must not be imported at module level here (that would create a
        # cycle because sql_endpoints.py transitively imports from services/).
        # A lazy import at the call-site is the cleanest option — it runs only
        # for lakehouse-derived endpoints and incurs no overhead for Warehouses.
        if kind == WarehouseKind.SQL_ENDPOINT and not conn:
            from fabric_dw.services.sql_endpoints import (  # noqa: PLC0415
                _resolve_lakehouse_connection_string,
            )

            lh_conn = await _resolve_lakehouse_connection_string(self._http, workspace_id, item_id)
            conn = lh_conn or None  # keep None when no matching lakehouse is found

        display_name = str(generic_payload.get("displayName", str(item_id)))
        entry = ItemEntry(
            id=item_id,
            kind=kind,
            connection_string=conn,
            fetched_at=datetime.now(tz=UTC),
            display_name=display_name,
        )
        # Store under display name and GUID string in a single lock+read+write cycle
        self._cache.put_items(
            workspace_id,
            [
                (display_name, entry),
                (str(item_id), entry),
            ],
        )
        # Clear-on-put: drop all negative entries for this workspace scope so
        # that a freshly created item becomes immediately resolvable even within
        # the _NEGATIVE_TTL window (defence-in-depth against create→resolve race).
        self._negative_clear_scope(str(workspace_id))
        return entry
