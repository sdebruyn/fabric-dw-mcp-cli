"""Service functions for Microsoft Fabric Warehouse and SQL Analytics Endpoint operations."""

from __future__ import annotations

import builtins
from uuid import UUID

from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import Warehouse, WarehouseKind
from fabric_dw.services.workspaces import SUPPORTED_COLLATIONS

__all__ = [
    "create",
    "delete",
    "get",
    "list",
    "rename",
]

# Alias to allow return-type annotations after the `list` name is shadowed by the function below.
_List = builtins.list


async def list(http: FabricHttpClient, workspace_id: UUID) -> _List[Warehouse]:
    """Return all warehouses and SQL analytics endpoints in a workspace.

    Combines results from ``GET /workspaces/{ws}/warehouses`` and
    ``GET /workspaces/{ws}/sqlEndpoints``, both followed through pagination.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace to query.

    Returns:
        A list of :class:`~fabric_dw.models.Warehouse` instances with their
        respective :class:`~fabric_dw.models.WarehouseKind`.
    """
    result: _List[Warehouse] = []

    wh_path = f"/workspaces/{workspace_id}/warehouses"
    async for item in http.iter_paginated(HttpBase.FABRIC, wh_path):
        result.append(Warehouse.from_api(item, kind=WarehouseKind.WAREHOUSE))

    async for item in http.iter_paginated(
        HttpBase.FABRIC, f"/workspaces/{workspace_id}/sqlEndpoints"
    ):
        result.append(Warehouse.from_api(item, kind=WarehouseKind.SQL_ENDPOINT))

    return result


async def get(http: FabricHttpClient, workspace_id: UUID, warehouse_id: UUID) -> Warehouse:
    """Fetch a single warehouse by ID.

    Uses the type-specific ``GET /workspaces/{ws}/warehouses/{wh}`` endpoint.
    SQL analytics endpoints should be discovered via :func:`list`.

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

    body: dict[str, object] = {"type": "Warehouse", "displayName": name}
    if description is not None:
        body["description"] = description
    if collation is not None:
        body["creationPayload"] = {"defaultCollation": collation}

    resp = await http.request(
        "POST", HttpBase.FABRIC, f"/workspaces/{workspace_id}/items", json=body
    )

    # 202 = LRO initiated; poll the operation then fetch the new warehouse
    location = resp.headers["Location"]
    lro_result = await http.poll_operation(location)

    # Extract the new warehouse ID from resourceLocation or Location URL
    resource_location: str = str(lro_result.get("resourceLocation", ""))
    if resource_location:
        new_id = UUID(resource_location.rstrip("/").split("/")[-1])
    else:
        new_id = UUID(location.rstrip("/").split("/")[-1])

    return await get(http, workspace_id, new_id)


async def rename(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    new_name: str,
    *,
    description: str | None = None,
) -> Warehouse:
    """Rename a Warehouse (and optionally update its description).

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the warehouse.
        warehouse_id: The UUID of the warehouse to rename.
        new_name: The new display name. Must be a non-empty string.
        description: Optional new description. Omitted from the body if ``None``.

    Returns:
        The updated :class:`~fabric_dw.models.Warehouse` as returned by the API.

    Raises:
        ValueError: If *new_name* is empty/whitespace.
    """
    if not new_name or not new_name.strip():
        msg = "Warehouse name must be a non-empty string"
        raise ValueError(msg)

    body: dict[str, object] = {"displayName": new_name}
    if description is not None:
        body["description"] = description

    resp = await http.request(
        "PATCH",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/warehouses/{warehouse_id}",
        json=body,
    )
    return Warehouse.from_api(resp.json(), kind=WarehouseKind.WAREHOUSE)


async def delete(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
) -> None:
    """Delete a Warehouse.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the warehouse.
        warehouse_id: The UUID of the warehouse to delete.

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
