"""Ownership service: take over a Fabric Warehouse item."""

from __future__ import annotations

from uuid import UUID

from fabric_dw.exceptions import PermissionDenied
from fabric_dw.http_client import FabricHttpClient, HttpBase

__all__ = ["takeover"]


async def takeover(http: FabricHttpClient, workspace_id: UUID, warehouse_id: UUID) -> None:
    """Take ownership of a Warehouse item in the given workspace.

    Sends a POST request to the Power BI legacy namespace to transfer
    ownership of the Warehouse to the currently authenticated principal.

    Takeover applies only to Warehouse items, not SQL Analytics Endpoints
    (per Microsoft Learn). Higher layers should refuse SQLEndpoint kind.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: The GUID of the workspace containing the Warehouse.
        warehouse_id: The GUID of the Warehouse item to take over.

    Raises:
        PermissionDenied: If the caller does not have Admin/Member/Contributor
            role on the workspace (HTTP 403).
        NotFound: If the workspace or warehouse does not exist (HTTP 404).
    """
    # TODO: CLI/MCP layer should refuse SQLEndpoint kind before calling this.
    path = f"/groups/{workspace_id}/datawarehouses/{warehouse_id}/takeover"

    try:
        await http.request("POST", HttpBase.POWERBI, path, json=None)
    except PermissionDenied as exc:
        raise PermissionDenied(  # noqa: TRY003
            "takeover requires Admin/Member/Contributor role on the workspace"
        ) from exc
