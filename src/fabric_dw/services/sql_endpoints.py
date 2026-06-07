"""Service functions for Microsoft Fabric SQL Analytics Endpoint operations."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import Warehouse, WarehouseKind

__all__ = [
    "get_endpoint",
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
        NotFound: If the endpoint does not exist (404).
    """
    resp = await http.request(
        "GET",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/sqlEndpoints/{endpoint_id}",
    )
    return Warehouse.from_api(resp.json(), kind=WarehouseKind.SQL_ENDPOINT)


async def refresh_metadata(
    http: FabricHttpClient, workspace_id: UUID, endpoint_id: UUID
) -> dict[str, Any]:
    """Trigger a metadata refresh for a SQL analytics endpoint.

    Issues ``POST /workspaces/{ws}/sqlEndpoints/{id}/refreshMetadata``, which
    returns a 202 with a ``Location`` header pointing at a long-running
    operation (LRO).  The function polls the LRO to completion and returns
    the final operation body.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint to refresh.

    Returns:
        The final LRO response body (``{"status": "Succeeded", ...}``).

    Raises:
        FabricServerError: If the LRO fails or times out.
        NotFound: If the endpoint does not exist (404).
    """
    resp = await http.request(
        "POST",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/sqlEndpoints/{endpoint_id}/refreshMetadata",
    )
    location: str = resp.headers["Location"]
    return await http.poll_operation(location)
