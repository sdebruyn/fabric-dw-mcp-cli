"""Ownership service: take over a Fabric Warehouse item."""

from __future__ import annotations

from uuid import UUID

from fabric_dw.exceptions import ItemKindError, PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import WarehouseKind

__all__ = ["takeover"]

_TAKEOVER_HINT = "takeover requires Admin/Member/Contributor role on the workspace"


async def takeover(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    *,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
) -> None:
    """Take ownership of a Warehouse item in the given workspace.

    Sends a POST request to the Power BI legacy namespace to transfer
    ownership of the Warehouse to the currently authenticated principal.

    Takeover applies only to Warehouse items, not SQL Analytics Endpoints
    (per Microsoft Learn). Pass ``kind`` so this function can enforce the
    constraint defensively; callers that resolve the item kind should pass it.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: The GUID of the workspace containing the Warehouse.
        warehouse_id: The GUID of the Warehouse item to take over.
        kind: The resolved item kind. Must be
            :attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE`; passing
            :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT` raises
            :class:`~fabric_dw.exceptions.ItemKindError`.

    Raises:
        ItemKindError: If *kind* is not ``WAREHOUSE`` (e.g. ``SQL_ENDPOINT``).
        PermissionDeniedError: If the caller does not have Admin/Member/Contributor
            role on the workspace (HTTP 403).
        NotFoundError: If the workspace or warehouse does not exist (HTTP 404).
    """
    if kind != WarehouseKind.WAREHOUSE:
        msg = (
            f"takeover is only supported for Warehouse items, not {kind.value!r}. "
            "SQL Analytics Endpoints do not support ownership takeover."
        )
        raise ItemKindError(msg)

    path = f"/groups/{workspace_id}/datawarehouses/{warehouse_id}/takeover"

    try:
        await http.request("POST", HttpBase.POWERBI, path, json=None)
    except PermissionDeniedError as exc:
        # Preserve the original status/request_id/body from the HTTP layer and
        # surface the remediation hint via the hint= field (not as the message).
        raise PermissionDeniedError(
            str(exc.args[0]) if exc.args else _TAKEOVER_HINT,
            status=exc.status,
            request_id=exc.request_id,
            body=exc.body,
            hint=_TAKEOVER_HINT,
        ) from exc
