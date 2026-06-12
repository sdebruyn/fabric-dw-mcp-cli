"""Service functions for Microsoft Fabric Warehouse and SQL Analytics Endpoint operations."""

from __future__ import annotations

import asyncio
import http as _http
import logging
from datetime import UTC, datetime
from uuid import UUID

from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.exceptions import FabricServerError, NotFound, PermissionDenied
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import Warehouse, WarehouseKind
from fabric_dw.services._concurrency import bounded_gather
from fabric_dw.services._helpers import compact
from fabric_dw.services.workspaces import SUPPORTED_COLLATIONS
from fabric_dw.services.workspaces import list_all as _list_all_workspaces

_logger = logging.getLogger("fabric_dw.warehouses")

# Backoff schedule for transient empty-2xx responses (seconds).
_EMPTY_2XX_BACKOFF = (2, 6, 18)

_HTTP_400_BAD_REQUEST = _http.HTTPStatus.BAD_REQUEST
_HTTP_500_INTERNAL_SERVER_ERROR = _http.HTTPStatus.INTERNAL_SERVER_ERROR

__all__ = [
    "create",
    "delete",
    "get_warehouse",
    "list_all_workspaces",
    "list_warehouses",
    "rename",
]


async def list_warehouses(http: FabricHttpClient, workspace_id: UUID) -> list[Warehouse]:
    """Return all warehouses and SQL analytics endpoints in a workspace.

    Combines results from ``GET /workspaces/{ws}/warehouses`` and
    ``GET /workspaces/{ws}/sqlEndpoints``, both followed through pagination.
    Warehouses are listed first, followed by SQL analytics endpoints.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace to query.

    Returns:
        A list of :class:`~fabric_dw.models.Warehouse` instances with their
        respective :class:`~fabric_dw.models.WarehouseKind`.
    """
    result: list[Warehouse] = [
        Warehouse.from_api(item, kind=WarehouseKind.WAREHOUSE)
        async for item in http.iter_paginated(
            HttpBase.FABRIC, f"/workspaces/{workspace_id}/warehouses"
        )
    ]
    result += [
        Warehouse.from_api(item, kind=WarehouseKind.SQL_ENDPOINT)
        async for item in http.iter_paginated(
            HttpBase.FABRIC, f"/workspaces/{workspace_id}/sqlEndpoints"
        )
    ]
    return result


async def list_all_workspaces(http: FabricHttpClient) -> list[Warehouse]:
    """Scan every visible workspace and collect its warehouses.

    Iterates all workspaces returned by :func:`~fabric_dw.services.workspaces.list_all`
    and aggregates their warehouses using bounded concurrency (up to 8 workspaces
    in parallel).  Workspaces that raise
    :class:`~fabric_dw.exceptions.PermissionDenied` or
    :class:`~fabric_dw.exceptions.NotFound` are skipped with a per-workspace
    warning; a summary warning is logged after the scan.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.

    Returns:
        A flat list of :class:`~fabric_dw.models.Warehouse` instances from all
        accessible workspaces.
    """
    workspaces = await _list_all_workspaces(http)
    total = len(workspaces)

    raw = await bounded_gather(
        [lambda ws=ws: list_warehouses(http, ws.id) for ws in workspaces],
        return_exceptions=True,
    )

    out: list[Warehouse] = []
    skipped = 0
    for ws, result in zip(workspaces, raw, strict=True):
        if isinstance(result, (PermissionDenied, NotFound)):
            _logger.warning("skipping workspace %s: %s", ws.name, result)
            skipped += 1
        elif isinstance(result, BaseException):
            raise result
        else:
            out.extend(result)

    if skipped:
        _logger.warning("skipped %d of %d workspaces due to access errors", skipped, total)

    return out


async def get_warehouse(
    http: FabricHttpClient, workspace_id: UUID, warehouse_id: UUID
) -> Warehouse:
    """Fetch a single warehouse by ID.

    Uses the type-specific ``GET /workspaces/{ws}/warehouses/{wh}`` endpoint.
    SQL analytics endpoints should be discovered via :func:`list_warehouses`.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the warehouse.
        warehouse_id: The UUID of the warehouse to retrieve.

    Returns:
        A populated :class:`~fabric_dw.models.Warehouse` instance.
    """
    resp = await http.request(
        "GET", HttpBase.FABRIC, f"/workspaces/{workspace_id}/warehouses/{warehouse_id}"
    )
    return Warehouse.from_api(resp.json(), kind=WarehouseKind.WAREHOUSE)


async def create(
    http: FabricHttpClient,
    workspace_id: UUID,
    name: str,
    *,
    collation: str | None = None,
    description: str | None = None,
) -> Warehouse:
    """Create a new Warehouse in a workspace.

    Validates *name* and *collation* before issuing any HTTP requests. The
    create call returns a 202 with a ``Location`` header; this function polls
    the LRO to completion and then fetches and returns the populated Warehouse.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace in which to create the warehouse.
        name: Display name for the new warehouse. Must be a non-empty string.
        collation: Optional default collation. Must be one of
            :data:`~fabric_dw.services.workspaces.SUPPORTED_COLLATIONS` if provided.
        description: Optional description for the new warehouse.

    Returns:
        A populated :class:`~fabric_dw.models.Warehouse` instance.

    Raises:
        ValueError: If *name* is empty/whitespace, or *collation* is unsupported.
    """
    if not name or not name.strip():
        msg = "Warehouse name must be a non-empty string"
        raise ValueError(msg)

    if collation is not None and collation not in SUPPORTED_COLLATIONS:
        msg = f"Unsupported collation {collation!r}. Allowed values: {sorted(SUPPORTED_COLLATIONS)}"
        raise ValueError(msg)

    body: dict[str, object] = {
        **compact({"description": description}),
        "type": "Warehouse",
        "displayName": name,
    }
    if collation is not None:
        body["creationPayload"] = {"defaultCollation": collation}

    for attempt, backoff in enumerate(
        [None, *_EMPTY_2XX_BACKOFF], start=0
    ):  # attempt 0 = initial; attempts 1-3 = retries
        if backoff is not None:
            _logger.warning(
                "create warehouse: 2xx response had no Location header and no usable body "
                "(attempt %d/3) — waiting %ss before retry (see issue #204)",
                attempt,
                backoff,
            )
            await asyncio.sleep(backoff)

        resp = await http.request(
            "POST", HttpBase.FABRIC, f"/workspaces/{workspace_id}/items", json=body
        )

        location = resp.headers.get("Location")

        if location is None:
            # Fabric occasionally returns 201 with the new warehouse directly in the body
            # (no LRO / no Location header). The body does NOT include properties.connectionString,
            # so we must do a follow-up GET to return a fully-populated Warehouse.
            resp_body = resp.json()
            resp_id = resp_body.get("id")
            resp_name = resp_body.get("displayName") or resp_body.get("name")
            if resp_id and resp_name:
                new_id = UUID(str(resp_id))
                return await get_warehouse(http, workspace_id, new_id)

            # Do not retry on 4xx — those are definitive client errors.
            if (
                resp.status_code >= _HTTP_400_BAD_REQUEST
                and resp.status_code < _HTTP_500_INTERNAL_SERVER_ERROR
            ):
                msg = (
                    f"create warehouse: {resp.status_code} error response "
                    f"with no Location header and no usable body"
                )
                raise FabricServerError(msg)

            # 2xx + no Location + no usable body: transient Fabric data-plane not ready.
            # Continue the retry loop (will raise below if exhausted).
            continue

        # Location header present → poll the LRO then fetch the new warehouse.
        break

    else:
        # Exhausted all attempts (initial + 3 retries) without a usable response.
        msg = (
            "create warehouse: Fabric returned 2xx with no Location header and no usable body "
            "after 4 attempts. The capacity data plane may not be ready yet. "
            "See issue #204 for tracking frequency."
        )
        raise FabricServerError(msg)

    # 202 = LRO initiated; poll the operation then fetch the new warehouse
    lro_result = await http.poll_operation(location)

    # Extract the new warehouse ID from resourceLocation.
    # str(None) == "None" (truthy) so we must use isinstance instead of truthiness.
    resource_location = lro_result.get("resourceLocation")
    if isinstance(resource_location, str) and resource_location:
        new_id = UUID(resource_location.rsplit("/", 1)[-1])
    else:
        msg = f"create warehouse LRO completed but no resourceLocation returned: {lro_result}"
        raise FabricServerError(msg)

    return await get_warehouse(http, workspace_id, new_id)


async def rename(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    new_name: str,
    *,
    description: str | None = None,
    cache: LookupCache | None = None,
    old_name: str | None = None,
) -> Warehouse:
    """Rename a Warehouse (and optionally update its description).

    After a successful rename the stale (workspace_id, old_name) cache entry is
    evicted and a new entry under (workspace_id, new_name) is populated.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the warehouse.
        warehouse_id: The UUID of the warehouse to rename.
        new_name: The new display name. Must be a non-empty string.
        description: Optional new description. Omitted from the body if ``None``.
        cache: Optional :class:`~fabric_dw.cache.LookupCache` for stale-entry eviction.
        old_name: The current display name; used to evict the stale cache entry.

    Returns:
        The updated :class:`~fabric_dw.models.Warehouse` as returned by the API.

    Raises:
        ValueError: If *new_name* is empty/whitespace.
    """
    if not new_name or not new_name.strip():
        msg = "Warehouse name must be a non-empty string"
        raise ValueError(msg)

    body: dict[str, object] = {
        **compact({"description": description}),
        "displayName": new_name,
    }

    resp = await http.request(
        "PATCH",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/warehouses/{warehouse_id}",
        json=body,
    )
    result = Warehouse.from_api(resp.json(), kind=WarehouseKind.WAREHOUSE)

    if cache is not None:
        if old_name is not None:
            cache.evict_item(workspace_id, old_name)
        new_entry = ItemEntry(
            id=warehouse_id,
            kind=WarehouseKind.WAREHOUSE,
            connection_string=result.connection_string,
            fetched_at=datetime.now(tz=UTC),
            display_name=new_name,
        )
        cache.put_item(workspace_id, new_name, new_entry)
        cache.put_item(workspace_id, str(warehouse_id), new_entry)

    return result


async def delete(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    *,
    cache: LookupCache | None = None,
    name: str | None = None,
) -> None:
    """Delete a Warehouse.

    After a successful delete the (workspace_id, name) and
    (workspace_id, warehouse_id) cache entries are evicted.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the warehouse.
        warehouse_id: The UUID of the warehouse to delete.
        cache: Optional :class:`~fabric_dw.cache.LookupCache` for stale-entry eviction.
        name: The display name of the warehouse; used to evict the name-keyed entry.

    Returns:
        ``None`` on success (204 No Content).

    Raises:
        NotFound: If the warehouse does not exist (404).
    """
    await http.request(
        "DELETE",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/warehouses/{warehouse_id}",
    )

    if cache is not None:
        if name is not None:
            cache.evict_item(workspace_id, name)
        cache.evict_item(workspace_id, str(warehouse_id))
