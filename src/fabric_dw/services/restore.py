"""Service functions for Microsoft Fabric Warehouse Restore Point operations.

API reference:
    https://learn.microsoft.com/en-us/rest/api/fabric/warehouse/restore-points
"""

from __future__ import annotations

import http as _http
from uuid import UUID

from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import CreationModeType, RestorePoint
from fabric_dw.services._helpers import compact
from fabric_dw.services._lro import resolve_lro_item_id

__all__ = [
    "create_point",
    "delete_point",
    "get_point",
    "list_points",
    "restore_in_place",
    "update_point",
]

_HTTP_201_CREATED = _http.HTTPStatus.CREATED
_HTTP_202_ACCEPTED = _http.HTTPStatus.ACCEPTED


def _rp_base(workspace_id: UUID, warehouse_id: UUID) -> str:
    """Return the base path for restore-point operations."""
    return f"/workspaces/{workspace_id}/warehouses/{warehouse_id}/restorePoints"


async def list_points(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
) -> list[RestorePoint]:
    """Return all restore points for *warehouse_id* in *workspace_id*.

    Uses continuation-URI pagination until all pages are consumed.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        warehouse_id: The Fabric warehouse UUID.

    Returns:
        A list of :class:`~fabric_dw.models.RestorePoint` instances.
    """
    return [
        RestorePoint.from_api(item)
        async for item in http.iter_paginated(
            HttpBase.FABRIC,
            _rp_base(workspace_id, warehouse_id),
        )
    ]


async def get_point(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    point_id: str,
) -> RestorePoint:
    """Return a single restore point by ID.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        warehouse_id: The Fabric warehouse UUID.
        point_id: The restore point ID (string, e.g. ``"1726617378000"``).

    Returns:
        The :class:`~fabric_dw.models.RestorePoint`.

    Raises:
        NotFoundError: If the restore point does not exist.
        PermissionDeniedError: If the caller has insufficient permissions.
    """
    resp = await http.request(
        "GET",
        HttpBase.FABRIC,
        f"{_rp_base(workspace_id, warehouse_id)}/{point_id}",
    )
    return RestorePoint.from_api(resp.json())


async def create_point(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    *,
    name: str | None = None,
    description: str | None = None,
) -> RestorePoint:
    """Create a restore point for *warehouse_id* at the current timestamp.

    The API may respond with 201 (synchronous) or 202 + LRO Location header.
    Both paths are handled: when a 202 is returned the LRO is polled to
    completion and the resulting restore point is fetched via GET.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        warehouse_id: The Fabric warehouse UUID.
        name: Optional display name for the restore point (max 128 chars).
        description: Optional description (max 512 chars).

    Returns:
        The newly-created :class:`~fabric_dw.models.RestorePoint`.
    """
    body = compact({"displayName": name, "description": description})

    resp = await http.request(
        "POST",
        HttpBase.FABRIC,
        _rp_base(workspace_id, warehouse_id),
        json=body or None,
    )

    if resp.status_code == _HTTP_201_CREATED:
        # Synchronous success — body contains the RestorePoint directly.
        return RestorePoint.from_api(resp.json())

    # 202 Accepted — poll the LRO then fetch the created restore point.
    location: str = resp.headers.get("Location", "")
    if not location:
        msg = "Fabric returned 202 for create_point but the Location header is missing"
        raise ValueError(msg)
    operation_result = await http.poll_operation(location)

    # Use the shared LRO helper to probe Path A (status body) then Path B
    # (/result sub-endpoint).  For restore-points, the status body sometimes
    # carries "resourceId" or "id"; the /result endpoint is the primary path.
    resource_id_str = await resolve_lro_item_id(
        http,
        operation_result=operation_result,
        location=location,
        result_id_keys=("resourceId", "id"),
    )
    if resource_id_str:
        return await get_point(http, workspace_id, warehouse_id, resource_id_str)

    # Path C — last resort: list all restore points and return the newest
    # UserDefined one.  Creation is serialised (the API enforces a single
    # in-flight create), so the highest numeric ID (timestamp-based integer)
    # among UserDefined points is the one just created.
    # NOTE: IDs are decimal digit strings (epoch-millisecond timestamps).
    # Comparing numerically (int(p.id)) avoids the lexicographic ordering bug
    # that would arise when IDs have different string lengths.
    points = await list_points(http, workspace_id, warehouse_id)
    user_points = [p for p in points if p.creation_mode == CreationModeType.USER_DEFINED]
    if user_points:
        # Non-digit IDs are treated as 0; if all points have non-digit IDs the
        # selection is arbitrary (but the API guarantees epoch-ms decimal strings).
        return max(user_points, key=lambda p: int(p.id) if p.id.isdigit() else 0)

    msg = (
        "Restore point create succeeded but the created point could not be located: "
        f"no UserDefined restore points found after LRO completed. "
        f"LRO result: {operation_result}"
    )
    raise RuntimeError(msg)


async def update_point(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    point_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> RestorePoint:
    """Rename and/or re-describe an existing restore point.

    At least one of *name* or *description* must be provided.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        warehouse_id: The Fabric warehouse UUID.
        point_id: The restore point ID string.
        name: New display name (max 128 chars).
        description: New description (max 512 chars).

    Returns:
        The updated :class:`~fabric_dw.models.RestorePoint`.

    Raises:
        ValueError: If neither *name* nor *description* is supplied.
        NotFoundError: If the restore point does not exist.
        PermissionDeniedError: If the caller has insufficient permissions.
    """
    if name is None and description is None:
        msg = "At least one of name or description must be provided"
        raise ValueError(msg)

    body = compact({"displayName": name, "description": description})

    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        f"{_rp_base(workspace_id, warehouse_id)}/{point_id}",
        json=body,
    )
    # PATCH returns a minimal body (often just the ID); GET the full resource to
    # ensure we return a correctly-populated RestorePoint.
    return await get_point(http, workspace_id, warehouse_id, point_id)


async def delete_point(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    point_id: str,
) -> None:
    """Delete a user-defined restore point.

    System-created restore points cannot be deleted (the API will return an
    error). Only user-defined restore points support deletion.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        warehouse_id: The Fabric warehouse UUID.
        point_id: The restore point ID string.

    Raises:
        NotFoundError: If the restore point does not exist.
        PermissionDeniedError: If the caller has insufficient permissions.
    """
    await http.request(
        "DELETE",
        HttpBase.FABRIC,
        f"{_rp_base(workspace_id, warehouse_id)}/{point_id}",
    )


async def restore_in_place(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    point_id: str,
) -> None:
    """Restore *warehouse_id* in-place to the specified restore point.

    This is a destructive, long-running operation (LRO). The warehouse will
    be unavailable for approximately 10 minutes while the restore completes.
    The API may respond synchronously (200) or with 202 + Location for polling.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        warehouse_id: The Fabric warehouse UUID.
        point_id: The restore point ID string.

    Raises:
        NotFoundError: If the restore point does not exist.
        PermissionDeniedError: If the caller has insufficient permissions.
        FabricServerError: If the LRO fails or times out.
    """
    resp = await http.request(
        "POST",
        HttpBase.FABRIC,
        f"{_rp_base(workspace_id, warehouse_id)}/{point_id}/restore",
    )

    if resp.status_code == _HTTP_202_ACCEPTED:
        location: str = resp.headers.get("Location", "")
        if not location:
            msg = "Fabric returned 202 for restore_in_place but the Location header is missing"
            raise ValueError(msg)
        await http.poll_operation(location)
    # 200 OK means synchronous success — nothing further to do.
