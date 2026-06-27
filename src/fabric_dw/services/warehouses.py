"""Service functions for Microsoft Fabric Warehouse and SQL Analytics Endpoint operations."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.exceptions import FabricServerError, NotFoundError, PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import Warehouse, WarehouseKind
from fabric_dw.services._helpers import compact, scan_all_workspaces
from fabric_dw.services._lro import extract_operation_id, resolve_lro_item_id
from fabric_dw.services.capacities import get_capacity_states
from fabric_dw.services.workspaces import SUPPORTED_COLLATIONS
from fabric_dw.services.workspaces import list_all as _list_all_workspaces

_logger = logging.getLogger("fabric_dw.warehouses")

# Backoff schedule for transient empty-2xx responses (seconds).
_EMPTY_2XX_BACKOFF = (2, 6, 18)


async def _post_create_with_retry(
    http: FabricHttpClient,
    workspace_id: UUID,
    body: dict[str, object],
) -> tuple[str | None, dict[str, object] | None]:
    """POST the create-warehouse request, retrying on transient empty-2xx responses.

    Returns either the ``Location`` header string (LRO path) or the warehouse
    body dict (synchronous 201 path), or raises :class:`FabricServerError` if
    all retries are exhausted.

    Returns:
        ``(location, None)`` when a ``Location`` header is present, or
        ``(None, body_dict)`` when a 201 with a usable body is returned.

    Raises:
        FabricServerError: If all attempts return 2xx with no Location and no
            usable body (transient data-plane not ready, issue #204).
    """
    for attempt, backoff in enumerate([None, *_EMPTY_2XX_BACKOFF], start=0):
        if backoff is not None:
            _logger.warning(
                "create warehouse: 2xx response had no Location header and no usable body "
                "(retry %d/3) — waiting %ss before retry (see issue #204)",
                attempt,
                backoff,
            )
            await asyncio.sleep(backoff)

        resp = await http.request(
            "POST", HttpBase.FABRIC, f"/workspaces/{workspace_id}/warehouses", json=body
        )

        location = resp.headers.get("Location")
        if location is not None:
            return location, None

        # Fabric occasionally returns 201 with the new warehouse directly in the body.
        resp_body = resp.json()
        resp_id = resp_body.get("id")
        resp_name = resp_body.get("displayName") or resp_body.get("name")
        if resp_id and resp_name:
            return None, resp_body

        # 2xx + no Location + no usable body: transient Fabric data-plane not ready.

    msg = (
        "create warehouse: Fabric returned 2xx with no Location header and no usable body "
        "after 4 attempts. The capacity data plane may not be ready yet. "
        "See issue #204 for tracking frequency."
    )
    raise FabricServerError(msg)


def _resolve_warehouse_id_from_lro(lro_result: dict[str, object]) -> UUID | None:
    """Extract the new warehouse UUID from a completed LRO status body, if available.

    Per the Fabric LRO contract, ``resourceLocation`` is NOT guaranteed to be present in
    the operation status body.  When it is absent, the caller should fall back to
    :meth:`~fabric_dw.http_client.FabricHttpClient.get_operation_result`.

    Args:
        lro_result: The dict returned by :meth:`FabricHttpClient.poll_operation`.

    Returns:
        The UUID of the newly created warehouse if ``resourceLocation`` is present and
        non-empty, or ``None`` if the field is absent/null/empty.
    """
    resource_location = lro_result.get("resourceLocation")
    if isinstance(resource_location, str) and resource_location:
        return UUID(resource_location.rsplit("/", 1)[-1])
    return None


__all__ = [
    "create",
    "delete",
    "get_warehouse",
    "list_all_workspaces",
    "list_warehouses",
    "rename",
]


async def list_warehouses(
    http: FabricHttpClient, workspace_id: UUID, *, warehouses_only: bool = False
) -> list[Warehouse]:
    """Return warehouses (and, by default, SQL analytics endpoints) in a workspace.

    Combines results from ``GET /workspaces/{ws}/warehouses`` and
    ``GET /workspaces/{ws}/sqlEndpoints``, both followed through pagination.
    Warehouses are listed first, followed by SQL analytics endpoints.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace to query.
        warehouses_only: When ``True``, list only Warehouses
            (kind=WAREHOUSE) and skip the ``GET /workspaces/{ws}/sqlEndpoints``
            call entirely (saving an API request).  Defaults to ``False``,
            which lists both Warehouses and SQL Analytics Endpoints.

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
    if warehouses_only:
        return result
    result += [
        Warehouse.from_api(item, kind=WarehouseKind.SQL_ENDPOINT)
        async for item in http.iter_paginated(
            HttpBase.FABRIC, f"/workspaces/{workspace_id}/sqlEndpoints"
        )
    ]
    return result


async def list_all_workspaces(
    http: FabricHttpClient, *, warehouses_only: bool = False
) -> list[Warehouse]:
    """Scan every visible workspace and collect its warehouses.

    Iterates all workspaces returned by :func:`~fabric_dw.services.workspaces.list_all`
    and aggregates their warehouses using bounded concurrency (up to 8 workspaces
    in parallel).

    Workspaces whose capacity is not ``"Active"`` are skipped **before** the
    data-plane call (proactive filter via ``GET /v1/capacities``), avoiding the
    ~22s hang that paused-capacity workspaces incur.  If the caller lacks the
    capacity-read permission, the proactive filter is unavailable and the
    defensive fallback applies: a non-retriable 5xx per workspace is silently
    skipped at ``DEBUG`` level.

    Workspaces that raise :class:`~fabric_dw.exceptions.PermissionDeniedError`
    or :class:`~fabric_dw.exceptions.NotFoundError` are skipped with a
    per-workspace ``WARNING`` log; a summary ``WARNING`` is logged after the scan.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        warehouses_only: When ``True``, list only Warehouses (kind=WAREHOUSE)
            per workspace and skip the SQL-endpoints fetch entirely.  Defaults
            to ``False`` (both Warehouses and SQL Analytics Endpoints).

    Returns:
        A flat list of :class:`~fabric_dw.models.Warehouse` instances from all
        accessible, active-capacity workspaces.
    """

    # Fetch workspaces and capacity states concurrently.  Capacity-state
    # fetching is best-effort: if GET /v1/capacities fails for any reason
    # other than 403 (which get_capacity_states already handles internally),
    # degrade to capacity_states=None and continue the scan via the defensive
    # per-workspace fallback.  The workspace listing must never abort just
    # because the capacity endpoint is unavailable.
    async def _get_capacity_states_safe() -> dict[str, str] | None:
        try:
            return await get_capacity_states(http)
        except Exception as exc:
            _logger.debug(
                "GET /v1/capacities failed (%s) — proactive capacity filtering unavailable; "
                "falling back to defensive per-workspace error handling",
                exc,
            )
            return None

    workspaces, capacity_states = await asyncio.gather(
        _list_all_workspaces(http),
        _get_capacity_states_safe(),
    )
    return await scan_all_workspaces(
        workspaces,
        lambda ws: list_warehouses(http, ws.id, warehouses_only=warehouses_only),
        logger=_logger,
        skip_errors=(PermissionDeniedError, NotFoundError),
        capacity_states=capacity_states,
    )


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
        "displayName": name,
    }
    if collation is not None:
        body["creationPayload"] = {"collationType": collation}

    # Use the type-specific endpoint (POST /workspaces/{id}/warehouses).
    # Any 4xx is raised by the HTTP client as BadRequestError before reaching here,
    # so the only non-202/201 responses that arrive are genuine 2xx with missing data.
    location, resp_body = await _post_create_with_retry(http, workspace_id, body)

    if resp_body is not None:
        # 201 synchronous path: body contains the new warehouse (without connectionString).
        # Follow up with a GET to return a fully-populated Warehouse.
        new_id = UUID(str(resp_body["id"]))
        return await get_warehouse(http, workspace_id, new_id)

    # 202 LRO path: poll until complete, then resolve the new warehouse ID.
    # Per the Fabric LRO contract, the operation status body is NOT guaranteed to
    # include ``resourceLocation``.  We try it first for backward compatibility, and
    # fall back to the shared resolve_lro_item_id helper (Path B: GET /result) when
    # it is absent.  The helper also handles the transient 404 race on the /result
    # endpoint with a bounded retry.
    # location is non-None here because _post_create_with_retry only returns (None, body)
    # for the synchronous 201 path (handled above) or (location_str, None) for LRO.
    if location is None:
        raise RuntimeError(
            "LRO location is unexpectedly None; internal error in _post_create_with_retry"
        )
    lro_result = await http.poll_operation(location)
    new_id = _resolve_warehouse_id_from_lro(lro_result)
    if new_id is None:
        # resourceLocation absent — delegate to the shared helper which encapsulates
        # Path B (GET /operations/{id}/result) with 404 retry and UUID validation.
        result_id = await resolve_lro_item_id(
            http,
            operation_result=lro_result,
            location=location,
            # Include "id" here: /result returns the created resource, not the operation.
            result_id_keys=("resourceId", "createdItemId", "itemId", "id"),
        )
        if result_id is None:
            operation_id = extract_operation_id(location)
            msg = (
                f"create warehouse: LRO {operation_id} completed but neither resourceLocation "
                f"nor operation result returned an id: lro={lro_result}"
            )
            raise FabricServerError(msg)
        new_id = UUID(result_id)
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
        NotFoundError: If the warehouse does not exist (404).
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
