"""Name-or-GUID resolver for Fabric workspaces and warehouse items.

Every CLI / MCP command accepts either a human-readable name or a raw GUID
for workspace and item (warehouse / SQL endpoint / snapshot) arguments.

Resolution order:

1. If the value already looks like a GUID, skip the API and return immediately.
2. If the name is in the local 24-hour cache (not expired), return from cache.
3. Otherwise hit the API, populate the cache, and return.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, NamedTuple
from uuid import UUID

from fabric_dw._fabric_api import resolve_lakehouse_connection_string
from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.exceptions import FabricError, NotFoundError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import WarehouseKind
from fabric_dw.sql import tenant_from_connection_string_host

_logger = logging.getLogger("fabric_dw.resolver")

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

    Uses the persistent filesystem cache (``LookupCache``) for positive results.
    A lookup of a genuinely-missing item hits the API each time, which keeps
    the resolver simple and avoids masking newly created resources.
    """

    def __init__(self, http: FabricHttpClient, cache: LookupCache) -> None:
        self._http = http
        self._cache = cache

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

        # 2. Cache hit
        cached = await self._cache.async_get_workspace(value)
        if cached is not None:
            return cached.id

        # 3. Power BI OData filter — escape single quotes per OData spec
        resp = await self._http.request(
            "GET",
            HttpBase.POWERBI,
            "/groups",
            params={"$filter": f"name eq '{_odata_escape(value)}'"},
        )
        body: dict[str, Any] = resp.json()
        results: list[dict[str, Any]] = body.get("value", [])

        if not results:
            raise NotFoundError(f"workspace {value!r} not found")

        if len(results) > 1:
            ids = ", ".join(str(r.get("id", "?")) for r in results)
            raise FabricError(f"workspace name {value!r} is ambiguous: ids = {ids}")

        ws_id = UUID(str(results[0]["id"]))
        await self._cache.async_put_workspace(value, ws_id)
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

        # 1. GUID fast-path: check cache first, then fetch detail
        if GUID_RE.match(value):
            item_uuid = UUID(value)
            cached_item = await self._cache.async_get_item(ws_id, value)
            if cached_item is not None:
                return cached_item
            return await self._fetch_item_detail(ws_id, item_uuid)

        # 2. Cache hit
        cached_item = await self._cache.async_get_item(ws_id, value)
        if cached_item is not None:
            return cached_item

        # 3. Page through /v1/workspaces/{ws}/items, filter by kind + name.
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
            return await self._fetch_item_detail(ws_id, item_id)

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
        # connection string is absent, fall back to scanning /lakehouses for the
        # parent Lakehouse whose sqlEndpointProperties.id matches this endpoint's
        # ID (see issue #347 / #471).  The shared helper lives in the low-level
        # fabric_dw._fabric_api module so it can be imported here without an
        # upward-layering dependency on the service package.
        if kind == WarehouseKind.SQL_ENDPOINT and not conn:
            _logger.debug(
                "SQL endpoint %s has empty connectionString on /sqlEndpoints resource; "
                "falling back to lakehouse scan for workspace %s",
                item_id,
                workspace_id,
            )
            conn = await resolve_lakehouse_connection_string(self._http, workspace_id, item_id)

        # Decode the tenant ID from the connection-string hostname (zero-cost,
        # no network request) and forward it to telemetry so subsequent events
        # carry the correct tenant even before the first token round-trip.
        # Fail-safe: tenant_from_connection_string_host never raises; ignore None.
        #
        # Token tid (identity tenant) takes precedence; the connection-string host
        # (resource tenant) is a fallback only.  For B2B guest scenarios the two
        # differ, and the JWT tid is the authoritative identity-plane value.
        # cache_tenant_id_from_token() early-exits when _tenant_id_override is
        # already set, so we only fill from the host when no token has been seen yet.
        if conn is not None:
            tenant_id = tenant_from_connection_string_host(conn)
            if tenant_id is not None:
                import fabric_dw.telemetry as _tel  # noqa: PLC0415

                if _tel._tenant_id_override is None:
                    _tel.set_tenant_id(tenant_id)

        display_name = str(generic_payload.get("displayName", str(item_id)))
        entry = ItemEntry(
            id=item_id,
            kind=kind,
            connection_string=conn,
            fetched_at=datetime.now(tz=UTC),
            display_name=display_name,
        )

        # Persist to the 24-hour cache unless this is a SQL endpoint whose
        # connection string is still unresolved.  Lakehouse-derived endpoints
        # expose their connectionString only once provisioning completes; caching
        # an interim None would serve that stale value for the full TTL and lock
        # the caller out of SQL-over-endpoint commands with "has no connection
        # string" for up to a day (issue #471).  Skipping the write means the next
        # lookup re-fetches and picks up the connection string once it appears.
        # Warehouses and snapshots are always cached — a Warehouse with no
        # connection string is a genuine (cacheable) state, and snapshots never
        # carry one.
        should_cache = not (kind == WarehouseKind.SQL_ENDPOINT and conn is None)
        if should_cache:
            # Store under display name and GUID string in a single lock+read+write cycle
            await self._cache.async_put_items(
                workspace_id,
                [
                    (display_name, entry),
                    (str(item_id), entry),
                ],
            )
        return entry
