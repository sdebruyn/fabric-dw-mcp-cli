"""Ownership service: take over a Fabric Warehouse item."""

from __future__ import annotations

from uuid import UUID

from fabric_dw.exceptions import ItemKindError, PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import WarehouseKind

__all__ = ["takeover"]

_TAKEOVER_HINT = "takeover requires Admin/Member/Contributor role on the workspace"

# Fabric error code returned when the caller is already the owner of the item.
_ALREADY_OWNER_ERROR_CODE = "ArtifactTakeOverNotAllowedByOwner"


def _fabric_error_code(body: dict[str, object] | None) -> str | None:
    """Extract the error code from a Fabric or Power BI error envelope.

    The core Fabric REST API puts the code at the top level
    (``{"errorCode": "..."}``), while the legacy Power BI namespace used by
    ``/takeover`` nests it under ``error`` (and mirrors it under
    ``error["pbi.error"]``)::

        {"error": {"code": "...", "pbi.error": {"code": "..."}}}

    Checks the top-level key first, then falls back to the nested shapes.
    Returns ``None`` if *body* is ``None`` or none of the keys are present.
    """
    if body is None:
        return None
    top_level = body.get("errorCode")
    if isinstance(top_level, str):
        return top_level
    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, str):
            return code
        pbi_error = error.get("pbi.error")
        if isinstance(pbi_error, dict):
            pbi_code = pbi_error.get("code")
            if isinstance(pbi_code, str):
                return pbi_code
    return None


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
        PermissionDeniedError: If Fabric returns HTTP 403
            ``ArtifactTakeOverNotAllowedByOwner``, indicating the caller is already
            the owner; raised without the generic role hint. Note: on the live tenant
            a self-takeover may instead return HTTP 2xx (Fabric treats it as an
            idempotent no-op), in which case this function returns ``None`` without
            raising. Callers must handle **both** outcomes.
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
        # Detect the "already owner" case: Fabric returns 403 with errorCode
        # ArtifactTakeOverNotAllowedByOwner when the caller already owns the item.
        # Surface a clear, accurate message instead of the generic role hint.
        error_code = _fabric_error_code(exc.body)
        if error_code == _ALREADY_OWNER_ERROR_CODE:
            raise PermissionDeniedError(
                "You are already the owner of this warehouse; nothing to take over.",
                status=exc.status,
                request_id=exc.request_id,
                body=exc.body,
            ) from exc
        # Generic 403: preserve the original status/request_id/body from the HTTP
        # layer and surface the remediation hint via the hint= field.
        raise PermissionDeniedError(
            str(exc.args[0]),
            status=exc.status,
            request_id=exc.request_id,
            body=exc.body,
            hint=_TAKEOVER_HINT,
        ) from exc
