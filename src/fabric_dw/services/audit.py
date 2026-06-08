"""SQL audit settings service for Microsoft Fabric Data Warehouses.

Wraps the warehouse-scoped ``/settings/sqlAudit`` endpoint:

- :func:`get_settings` — fetch current audit configuration.
- :func:`enable`       — enable auditing (optionally with a retention period).
- :func:`disable`      — disable auditing.
- :func:`set_action_groups` — replace the list of audited action groups.
"""

from __future__ import annotations

import asyncio
import re
from uuid import UUID

from fabric_dw.exceptions import NotFound
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import AuditSettings

__all__ = [
    "disable",
    "enable",
    "get_settings",
    "set_action_groups",
]

_ACTION_GROUP_RE = re.compile(r"^[A-Z_]+$")


def _audit_path(workspace_id: UUID, warehouse_id: UUID) -> str:
    """Return the relative path for the sqlAudit settings endpoint."""
    return f"/workspaces/{workspace_id}/warehouses/{warehouse_id}/settings/sqlAudit"


async def get_settings(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
) -> AuditSettings:
    """Fetch the current SQL audit settings for a warehouse.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        warehouse_id: GUID of the Data Warehouse.

    Returns:
        The current :class:`~fabric_dw.models.AuditSettings`.

    Raises:
        PermissionDenied: If the caller lacks the required permission (HTTP 403).
        NotFound: If the warehouse does not exist (HTTP 404).
    """
    path = _audit_path(workspace_id, warehouse_id)
    resp = await http.request("GET", HttpBase.FABRIC, path)
    return AuditSettings.model_validate(resp.json())


async def enable(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    *,
    retention_days: int = 0,
) -> AuditSettings:
    """Enable SQL auditing on a warehouse.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        warehouse_id: GUID of the Data Warehouse.
        retention_days: How many days to retain audit logs.  ``0`` means
            unlimited (Microsoft's interpretation per the Learn documentation).

    Returns:
        The fresh :class:`~fabric_dw.models.AuditSettings` after the update.

    Raises:
        ValueError: If *retention_days* is negative.
        PermissionDenied: If the caller lacks the required permission (HTTP 403).
    """
    if retention_days < 0:
        msg = f"retention_days must be >= 0; got {retention_days}"
        raise ValueError(msg)

    path = _audit_path(workspace_id, warehouse_id)
    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        path,
        json={"state": "Enabled", "retentionDays": retention_days},
    )
    return await get_settings(http, workspace_id, warehouse_id)


async def disable(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
) -> AuditSettings:
    """Disable SQL auditing on a warehouse.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        warehouse_id: GUID of the Data Warehouse.

    Returns:
        The fresh :class:`~fabric_dw.models.AuditSettings` after the update.

    Raises:
        PermissionDenied: If the caller lacks the required permission (HTTP 403).
    """
    path = _audit_path(workspace_id, warehouse_id)
    await http.request("PATCH", HttpBase.FABRIC, path, json={"state": "Disabled"})
    return await get_settings(http, workspace_id, warehouse_id)


async def set_action_groups(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    action_groups: list[str],
) -> AuditSettings:
    """Replace the audited action groups for a warehouse.

    Action-group names must consist exclusively of upper-case ASCII letters and
    underscores (``^[A-Z_]+$``).  Examples of valid names:
    ``BATCH_COMPLETED_GROUP``, ``SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP``.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        warehouse_id: GUID of the Data Warehouse.
        action_groups: List of action-group name strings to set.  Pass an empty
            list to clear all action groups.

    Returns:
        The fresh :class:`~fabric_dw.models.AuditSettings` after the update.

    Raises:
        ValueError: If any name in *action_groups* does not match ``^[A-Z_]+$``.
        PermissionDenied: If the caller lacks the required permission (HTTP 403).
    """
    for name in action_groups:
        if not _ACTION_GROUP_RE.match(name):
            msg = (
                f"Invalid action_group name {name!r}: "
                "names must match ^[A-Z_]+$ (upper-case letters and underscores only)"
            )
            raise ValueError(msg)

    path = _audit_path(workspace_id, warehouse_id)

    # Fabric briefly returns 404 (EntityNotFound) after warehouse creation or after
    # enabling auditing, before the sqlAudit resource is fully provisioned.
    # Retry the POST up to 3 times with a short wait before giving up.
    max_provision_retries = 3
    provision_wait_s = 2.0
    last_exc: NotFound | None = None
    for attempt in range(max_provision_retries):
        try:
            await http.request("POST", HttpBase.FABRIC, path, json=action_groups)
            break
        except NotFound as exc:
            last_exc = exc
            if attempt < max_provision_retries - 1:
                await asyncio.sleep(provision_wait_s)
    else:
        raise last_exc or RuntimeError("unreachable")

    return await get_settings(http, workspace_id, warehouse_id)
