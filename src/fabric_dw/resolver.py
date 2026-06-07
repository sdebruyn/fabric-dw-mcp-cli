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
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.exceptions import FabricError, NotFound
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

_ITEM_TYPES = frozenset({"Warehouse", "SQLEndpoint", "WarehouseSnapshot"})
_KIND_MAP: dict[str, WarehouseKind] = {
    "Warehouse": WarehouseKind.WAREHOUSE,
    "SQLEndpoint": WarehouseKind.SQL_ENDPOINT,
    "WarehouseSnapshot": WarehouseKind.SNAPSHOT,
}


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
    """Resolves workspace / item names or GUIDs to UUIDs and ItemEntry objects."""

    def __init__(self, http: FabricHttpClient, cache: LookupCache) -> None:
        self._http = http
        self._cache = cache

    # ------------------------------------------------------------------
    # workspace_id
    # ------------------------------------------------------------------

    async def workspace_id(self, value: str) -> UUID:
        """Resolve *value* (name or GUID) to a workspace UUID.

        Args:
            value: A workspace display name or a GUID string.

        Returns:
            The workspace UUID.

        Raises:
            NotFound: If no workspace matches *value*.
            FabricError: If *value* matches more than one workspace.
        """
        # 1. GUID fast-path
        if GUID_RE.match(value):
            return UUID(value)

        # 2. Cache hit
        cached = self._cache.get_workspace(value)
        if cached is not None:
            return cached.id

        # 3. Power BI OData filter
        resp = await self._http.request(
            "GET",
            HttpBase.POWERBI,
            "/groups",
            params={"$filter": f"name eq '{value}'"},
        )
        body: dict[str, Any] = resp.json()
        results: list[dict[str, Any]] = body.get("value", [])

        if not results:
            raise NotFound(f"workspace {value!r} not found")  # noqa: TRY003

        if len(results) > 1:
            ids = ", ".join(str(r.get("id", "?")) for r in results)
            raise FabricError(  # noqa: TRY003
                f"workspace name {value!r} is ambiguous: ids = {ids}"
            )

        ws_id = UUID(str(results[0]["id"]))
        self._cache.put_workspace(value, ws_id)
        return ws_id

    # ------------------------------------------------------------------
    # item
    # ------------------------------------------------------------------

    async def item(self, workspace: str, value: str) -> ItemEntry:
        """Resolve *value* (name or GUID) to an ItemEntry within *workspace*.

        Args:
            workspace: Workspace name or GUID.
            value: Item display name or GUID.

        Returns:
            The resolved ItemEntry with ``connection_string`` populated.

        Raises:
            NotFound: If the item is not found in the workspace.
        """
        ws_id = await self.workspace_id(workspace)

        # 1. GUID fast-path: fetch detail directly
        if GUID_RE.match(value):
            return await self._fetch_item_detail(ws_id, UUID(value))

        # 2. Cache hit
        cached_item = self._cache.get_item(ws_id, value)
        if cached_item is not None:
            return cached_item

        # 3. Page through /v1/workspaces/{ws}/items, filter by kind + name
        async for raw_item in self._http.iter_paginated(
            HttpBase.FABRIC, f"/workspaces/{ws_id}/items"
        ):
            item_type = str(raw_item.get("type", ""))
            if item_type not in _ITEM_TYPES:
                continue
            display_name = str(raw_item.get("displayName", ""))
            if display_name.lower() != value.lower():
                continue
            # Found a name match — fetch full detail to get connection_string
            item_id = UUID(str(raw_item["id"]))
            return await self._fetch_item_detail(ws_id, item_id)

        raise NotFound(f"item {value!r} not found in workspace {ws_id}")  # noqa: TRY003

    async def _fetch_item_detail(self, workspace_id: UUID, item_id: UUID) -> ItemEntry:
        """Fetch a single item's full detail, build ItemEntry, cache and return it."""
        resp = await self._http.request(
            "GET",
            HttpBase.FABRIC,
            f"/workspaces/{workspace_id}/items/{item_id}",
        )
        payload: dict[str, Any] = resp.json()
        item_type = str(payload.get("type", ""))
        kind = _KIND_MAP.get(item_type, WarehouseKind.WAREHOUSE)
        conn = _connection_string_from_detail(payload, kind)
        display_name = str(payload.get("displayName", str(item_id)))
        entry = ItemEntry(
            id=item_id,
            kind=kind,
            connection_string=conn,
            fetched_at=datetime.now(tz=UTC),
        )
        self._cache.put_item(workspace_id, display_name, entry)
        return entry
