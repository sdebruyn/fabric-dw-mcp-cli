"""Service functions for Microsoft Fabric workspace operations."""

from __future__ import annotations

from uuid import UUID

from fabric_dw.exceptions import FabricError, NotFound
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import Workspace

__all__ = [
    "SUPPORTED_COLLATIONS",
    "get",
    "get_collation",
    "list_all",
    "set_collation",
]

# Collation values supported by Microsoft Fabric Data Warehouse.
# TODO: https://learn.microsoft.com/en-us/fabric/data-warehouse/collation
SUPPORTED_COLLATIONS: frozenset[str] = frozenset(
    {
        "Latin1_General_100_BIN2_UTF8",
        "Latin1_General_100_CI_AS_KS_WS_SC_UTF8",
    }
)


async def list_all(http: FabricHttpClient) -> list[Workspace]:
    """Return all workspaces the caller has access to, following pagination.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.

    Returns:
        A list of :class:`~fabric_dw.models.Workspace` instances.
    """
    return [
        Workspace.model_validate(item)
        async for item in http.iter_paginated(HttpBase.FABRIC, "/workspaces")
    ]


async def get(http: FabricHttpClient, workspace_id: UUID) -> Workspace:
    """Fetch a single workspace by ID.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace to retrieve.

    Returns:
        A populated :class:`~fabric_dw.models.Workspace` instance.
    """
    resp = await http.request("GET", HttpBase.FABRIC, f"/workspaces/{workspace_id}")
    return Workspace.model_validate(resp.json())


async def get_collation(http: FabricHttpClient, workspace_id: UUID) -> str | None:
    """Return the default Data Warehouse collation for a workspace, or ``None`` if absent.

    Microsoft's v1 REST API does not yet document a dedicated collation property
    on the workspace resource. If the field is present in the payload it is
    returned; otherwise ``None`` is returned without error. Do not fall back to
    a SQL query — keep scope small.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace to inspect.

    Returns:
        The collation string, or ``None`` if the workspace payload does not
        carry a ``defaultDataWarehouseCollation`` field.
    """
    resp = await http.request("GET", HttpBase.FABRIC, f"/workspaces/{workspace_id}")
    data: dict[str, object] = resp.json()
    value = data.get("defaultDataWarehouseCollation")
    return str(value) if value is not None else None


async def set_collation(
    http: FabricHttpClient,
    workspace_id: UUID,
    collation: str,
) -> None:
    """Set the default Data Warehouse collation for a workspace.

    Performs a best-effort PATCH on the workspace resource. If the Fabric API
    returns a 4xx response (e.g. because the endpoint is not yet available in
    the tenant region), a :class:`~fabric_dw.exceptions.FabricError` is raised
    with guidance to use the Fabric portal instead.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace to update.
        collation: The desired collation. Must be one of
            :data:`SUPPORTED_COLLATIONS`.

    Raises:
        ValueError: If *collation* is not in :data:`SUPPORTED_COLLATIONS`.
        FabricError: If the API rejects the PATCH with a 4xx status code.
    """
    if collation not in SUPPORTED_COLLATIONS:
        msg = f"Unsupported collation {collation!r}. Allowed values: {sorted(SUPPORTED_COLLATIONS)}"
        raise ValueError(msg)

    # TODO: https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/update-workspace
    # The v1 API may not yet expose defaultDataWarehouseCollation on all tenants.
    # If the PATCH fails with 4xx, instruct the user to set it via the portal.
    portal_url = "https://app.fabric.microsoft.com"
    portal_msg = (
        "Failed to set collation via the Fabric REST API. "
        "Please set the default Data Warehouse collation manually via the Fabric portal: "
        f"{portal_url}"
    )

    try:
        resp = await http.request(
            "PATCH",
            HttpBase.FABRIC,
            f"/workspaces/{workspace_id}",
            json={"defaultDataWarehouseCollation": collation},
        )
    except NotFound as exc:
        raise FabricError(portal_msg) from exc
    except FabricError:
        raise

    # http_client returns non-error responses (including 400) without raising;
    # treat any remaining 4xx as a portal-redirect case.
    if 400 <= resp.status_code < 500:  # noqa: PLR2004
        raise FabricError(portal_msg)
