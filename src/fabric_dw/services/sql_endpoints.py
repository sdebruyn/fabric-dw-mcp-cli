"""Service functions for Microsoft Fabric SQL Analytics Endpoint operations."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fabric_dw.exceptions import NotFoundError, PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import TableSyncStatus, Warehouse, WarehouseKind
from fabric_dw.services._concurrency import bounded_gather
from fabric_dw.services.workspaces import list_all as _list_all_workspaces

_log = logging.getLogger("fabric_dw.sql_endpoints")

__all__ = [
    "get_endpoint",
    "list_all_workspaces",
    "list_endpoints",
    "refresh_metadata",
]


async def list_endpoints(http: FabricHttpClient, workspace_id: UUID) -> list[Warehouse]:
    """Return all SQL analytics endpoints in a workspace.

    Pages through ``GET /workspaces/{ws}/sqlEndpoints`` and returns each item
    parsed as a :class:`~fabric_dw.models.Warehouse` with
    ``kind=SQL_ENDPOINT``.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace to query.

    Returns:
        A list of :class:`~fabric_dw.models.Warehouse` instances with
        ``kind == WarehouseKind.SQL_ENDPOINT``.
    """
    return [
        Warehouse.from_api(item, kind=WarehouseKind.SQL_ENDPOINT)
        async for item in http.iter_paginated(
            HttpBase.FABRIC, f"/workspaces/{workspace_id}/sqlEndpoints"
        )
    ]


async def list_all_workspaces(http: FabricHttpClient) -> list[Warehouse]:
    """Scan every visible workspace and collect its SQL analytics endpoints.

    Iterates all workspaces returned by :func:`~fabric_dw.services.workspaces.list_all`
    and aggregates their SQL analytics endpoints using bounded concurrency (up to
    8 workspaces in parallel).  Workspaces that raise
    :class:`~fabric_dw.exceptions.PermissionDeniedError` or
    :class:`~fabric_dw.exceptions.NotFoundError` are skipped with a per-workspace
    warning; a summary warning is logged after the scan.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.

    Returns:
        A flat list of :class:`~fabric_dw.models.Warehouse` instances (with
        ``kind == SQL_ENDPOINT``) from all accessible workspaces.
    """
    workspaces = await _list_all_workspaces(http)
    total = len(workspaces)

    raw = await bounded_gather(
        [lambda ws=ws: list_endpoints(http, ws.id) for ws in workspaces],
        return_exceptions=True,
    )

    out: list[Warehouse] = []
    skipped = 0
    for ws, result in zip(workspaces, raw, strict=True):
        if isinstance(result, (PermissionDeniedError, NotFoundError)):
            _log.warning("skipping workspace %s: %s", ws.name, result)
            skipped += 1
        elif isinstance(result, BaseException):
            raise result
        else:
            out.extend(result)

    if skipped:
        _log.warning("skipped %d of %d workspaces due to access errors", skipped, total)

    return out


async def get_endpoint(http: FabricHttpClient, workspace_id: UUID, endpoint_id: UUID) -> Warehouse:
    """Fetch a single SQL analytics endpoint by ID.

    Uses ``GET /workspaces/{ws}/sqlEndpoints/{id}``.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint to retrieve.

    Returns:
        A populated :class:`~fabric_dw.models.Warehouse` instance with
        ``kind == WarehouseKind.SQL_ENDPOINT``.

    Raises:
        NotFoundError: If the endpoint does not exist (404).
    """
    resp = await http.request(
        "GET",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/sqlEndpoints/{endpoint_id}",
    )
    return Warehouse.from_api(resp.json(), kind=WarehouseKind.SQL_ENDPOINT)


async def refresh_metadata(
    http: FabricHttpClient,
    workspace_id: UUID,
    endpoint_id: UUID,
    *,
    recreate_tables: bool = False,
) -> list[TableSyncStatus]:
    """Trigger a metadata refresh for a SQL analytics endpoint.

    Issues ``POST /workspaces/{ws}/sqlEndpoints/{id}/refreshMetadata`` with
    an optional ``recreateTables`` body flag.  The API returns a 202 with a
    ``Location`` header pointing at a long-running operation (LRO).  The
    function polls the LRO to completion and parses the per-table results.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint to refresh.
        recreate_tables: When ``True``, pass ``recreateTables=true`` in the
            request body, instructing Fabric to drop and recreate all tables
            during the refresh.  **Destructive** — use with caution.

    Returns:
        A list of :class:`~fabric_dw.models.TableSyncStatus` objects, one per
        table, describing the outcome of the refresh.

    Raises:
        FabricServerError: If the LRO fails or times out.
        NotFoundError: If the endpoint does not exist (404).
    """
    json_body: dict[str, Any] | None = {"recreateTables": True} if recreate_tables else None

    resp = await http.request(
        "POST",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/sqlEndpoints/{endpoint_id}/refreshMetadata",
        json=json_body,
    )
    location: str = resp.headers["Location"]
    lro_body = await http.poll_operation(location)
    raw_value: Any = lro_body.get("value", []) if isinstance(lro_body, dict) else []
    raw_items: list[Any] = raw_value if isinstance(raw_value, list) else []
    return [TableSyncStatus.model_validate(item) for item in raw_items]
