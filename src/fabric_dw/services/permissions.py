"""Item access details service for Microsoft Fabric (admin API).

Wraps the admin endpoint ``GET /v1/admin/workspaces/{workspaceId}/items/{itemId}/users``
to return the list of principals (users, groups, service principals) that have
access to a given item, along with their effective permissions.

Caller must be a Fabric Administrator (Tenant.Read.All or Tenant.ReadWrite.All scope).

Reference:
    https://learn.microsoft.com/en-us/rest/api/fabric/admin/items/list-item-access-details
"""

from __future__ import annotations

from uuid import UUID

from fabric_dw.exceptions import PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import ItemAccess

__all__ = ["list_item_access"]

_ADMIN_HINT = (
    "This endpoint requires Fabric Administrator role. "
    "See https://learn.microsoft.com/en-us/fabric/admin/microsoft-fabric-admin"
    "?WT.mc_id=MVP_310840 for how to request it."
)

# The admin items/users endpoint paginates under "accessDetails" instead of the
# default "value" key used by most Fabric list endpoints.  Named here so that a
# future schema change is caught at review time rather than silently yielding
# zero results.
_ACCESS_DETAILS_KEY = "accessDetails"


async def list_item_access(
    http: FabricHttpClient,
    workspace_id: UUID,
    item_id: UUID,
) -> list[ItemAccess]:
    """Return the list of principals with access to *item_id* in *workspace_id*.

    Follows ``continuationUri`` pagination until all pages are consumed.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        item_id: GUID of the item (Warehouse or SQL Endpoint).

    Returns:
        A list of :class:`~fabric_dw.models.ItemAccess` objects, one per principal.

    Raises:
        PermissionDeniedError: If the caller is not a Fabric Administrator (HTTP 403).
        NotFoundError: If the workspace or item does not exist (HTTP 404).
    """
    # The optional ?type= query param is intentionally omitted: Warehouse and
    # SQLEndpoint items do not filter by type, and omitting it returns all principals.
    path = f"/admin/workspaces/{workspace_id}/items/{item_id}/users"

    try:
        return [
            ItemAccess.from_api(raw)
            async for raw in http.iter_paginated(HttpBase.FABRIC, path, key=_ACCESS_DETAILS_KEY)
        ]
    except PermissionDeniedError as exc:
        raise PermissionDeniedError(_ADMIN_HINT) from exc
