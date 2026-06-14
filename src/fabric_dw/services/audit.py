"""SQL audit settings service for Microsoft Fabric Data Warehouses.

Wraps the warehouse-scoped ``/settings/sqlAudit`` endpoint:

- :func:`get_settings` — fetch current audit configuration.
- :func:`enable`       — enable auditing (optionally with a retention period).
- :func:`disable`      — disable auditing.
- :func:`set_action_groups`    — replace the list of audited action groups.
- :func:`add_action_group`     — add a single action group (idempotent).
- :func:`remove_action_group`  — remove a single action group (idempotent).
"""

from __future__ import annotations

import re
from uuid import UUID

from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import AuditSettings

__all__ = [
    "add_action_group",
    "disable",
    "enable",
    "get_settings",
    "remove_action_group",
    "set_action_groups",
    "set_retention",
]

_ACTION_GROUP_RE = re.compile(r"^[A-Z0-9_]+$")


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
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
        NotFoundError: If the warehouse does not exist (HTTP 404).
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
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
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
    # PATCH returns empty/partial body on this endpoint; re-fetch required.
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
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
    """
    path = _audit_path(workspace_id, warehouse_id)
    await http.request("PATCH", HttpBase.FABRIC, path, json={"state": "Disabled"})
    # PATCH returns empty/partial body on this endpoint; re-fetch required.
    return await get_settings(http, workspace_id, warehouse_id)


async def set_retention(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    *,
    days: int,
) -> AuditSettings:
    """Update the audit log retention period without changing the audit state.

    Performs a pre-flight GET to verify that auditing is currently enabled.
    Setting retention while audit is disabled is not meaningful — the Fabric
    service accepts the PATCH silently but the setting has no effect.  Raising
    ``ValueError`` eagerly gives callers a clear signal to enable auditing first.

    The Fabric REST API does not document an upper bound for ``retentionDays``.
    Only the lower bound (>= 1) is enforced here; the API will reject any value
    it considers out of range and surface an appropriate error.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        warehouse_id: GUID of the Data Warehouse.
        days: Retention period in days.  Must be >= 1.

    Returns:
        The fresh :class:`~fabric_dw.models.AuditSettings` after the update.

    Raises:
        ValueError: If *days* is less than 1.
        ValueError: If auditing is currently disabled (``state == "Disabled"``).
            Enable auditing first with :func:`enable`.
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
    """
    if days < 1:
        msg = f"days must be >= 1; got {days}"
        raise ValueError(msg)

    current = await get_settings(http, workspace_id, warehouse_id)
    if current.state == "Disabled":
        msg = "audit is disabled; enable first before setting retention"
        raise ValueError(msg)

    path = _audit_path(workspace_id, warehouse_id)
    await http.request("PATCH", HttpBase.FABRIC, path, json={"retentionDays": days})
    # PATCH returns empty/partial body on this endpoint; re-fetch required.
    return await get_settings(http, workspace_id, warehouse_id)


async def set_action_groups(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    action_groups: list[str],
    *,
    ensure_enabled: bool = True,
) -> AuditSettings:
    """Replace the audited action groups for a warehouse.

    Action-group names must consist exclusively of upper-case ASCII letters,
    digits, and underscores (``^[A-Z0-9_]+$``).  Examples of valid names:
    ``BATCH_COMPLETED_GROUP``, ``SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP``.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        warehouse_id: GUID of the Data Warehouse.
        action_groups: List of action-group name strings to set.  Pass an empty
            list to clear all action groups.
        ensure_enabled: When ``True`` (default), the PATCH also sets
            ``state=Enabled`` so that auditing is active after the call even if
            it was previously disabled.  When ``False``, only the action-group
            list is changed and the current audit state is left untouched.

    Returns:
        The authoritative :class:`~fabric_dw.models.AuditSettings` after the
        PATCH, constructed locally from the pre-PATCH settings and the supplied
        action-group list.  The ``GET /settings/sqlAudit`` endpoint is
        eventually consistent with multi-minute lag; polling it after PATCH is
        self-defeating.  A failed PATCH still raises via
        :meth:`~fabric_dw.http_client.FabricHttpClient.request`.

    Raises:
        ValueError: If any name in *action_groups* does not match ``^[A-Z0-9_]+$``.
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
    """
    for name in action_groups:
        if not _ACTION_GROUP_RE.match(name):
            msg = (
                f"Invalid action_group name {name!r}: "
                "names must match ^[A-Z0-9_]+$ (upper-case letters, digits, and underscores only)"
            )
            raise ValueError(msg)

    # Pre-flight GET to obtain current settings so we can construct the
    # authoritative post-PATCH state without a stale re-fetch.
    current = await get_settings(http, workspace_id, warehouse_id)

    path = _audit_path(workspace_id, warehouse_id)

    # Fabric's PATCH /settings/sqlAudit accepts an ``auditActionsAndGroups`` field
    # alongside ``state`` and ``retentionDays``.  Using PATCH to set the action groups
    # avoids the EntityNotFound (404) that the POST method returns on freshly-created
    # warehouses, since PATCH with state=Enabled is idempotent and always works.
    patch_body: dict[str, object] = {"auditActionsAndGroups": action_groups}
    if ensure_enabled:
        patch_body["state"] = "Enabled"

    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        path,
        json=patch_body,
    )
    # Return the authoritative post-PATCH state constructed locally.
    # Do NOT poll GET: the GET endpoint lags the PATCH by minutes; the PATCH
    # itself is the authoritative source of truth.
    new_state = "Enabled" if ensure_enabled else current.state
    return current.model_copy(update={"action_groups": list(action_groups), "state": new_state})


async def add_action_group(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    group: str,
) -> AuditSettings:
    """Add a single audit action group without overwriting the others.

    This is idempotent — if *group* is already present the current settings
    are returned unchanged without making a PATCH request.

    The group name must consist exclusively of upper-case ASCII letters,
    digits, and underscores (``^[A-Z0-9_]+$``).  The Fabric API documents a
    fixed set of valid group names (e.g. ``BATCH_COMPLETED_GROUP``,
    ``SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP``); invalid names are accepted
    by this client-side validation but will be rejected by the API.  The
    broad ``^[A-Z0-9_]+$`` pattern is used rather than a closed enum because
    Microsoft may extend the set of valid names without notice.

    Note:
        This function performs a read-modify-write (GET then PATCH) without
        optimistic concurrency control.  The Fabric REST API does not expose
        ETags or ``If-Match`` on the ``/settings/sqlAudit`` endpoint.  Under
        concurrent modification the last writer wins silently.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        warehouse_id: GUID of the Data Warehouse.
        group: Name of the action group to add.

    Returns:
        The authoritative :class:`~fabric_dw.models.AuditSettings` after the
        PATCH (or the current settings when the group was already present),
        constructed locally from the pre-PATCH settings and the computed new
        group list.  The ``GET /settings/sqlAudit`` endpoint is eventually
        consistent with multi-minute lag; polling it after PATCH is
        self-defeating.  A failed PATCH still raises via
        :meth:`~fabric_dw.http_client.FabricHttpClient.request`.

    Raises:
        ValueError: If *group* does not match ``^[A-Z0-9_]+$``.
        ValueError: If auditing is currently disabled (``state == "Disabled"``).
            Enable auditing first with :func:`enable`.
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
        NotFoundError: If the warehouse does not exist (HTTP 404).
    """
    if not _ACTION_GROUP_RE.match(group):
        msg = (
            f"Invalid action_group name {group!r}: "
            "names must match ^[A-Z0-9_]+$ (upper-case letters, digits, and underscores only)"
        )
        raise ValueError(msg)

    current = await get_settings(http, workspace_id, warehouse_id)

    if current.state == "Disabled":
        msg = "audit is disabled; enable first"
        raise ValueError(msg)

    if group in current.action_groups:
        return current

    new_groups = [*current.action_groups, group]
    path = _audit_path(workspace_id, warehouse_id)
    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        path,
        json={"auditActionsAndGroups": new_groups},
    )
    # Return the authoritative post-PATCH state constructed locally.
    # Do NOT poll GET: the GET endpoint lags the PATCH by minutes; the PATCH
    # itself is the authoritative source of truth.
    return current.model_copy(update={"action_groups": new_groups})


async def remove_action_group(
    http: FabricHttpClient,
    workspace_id: UUID,
    warehouse_id: UUID,
    group: str,
) -> AuditSettings:
    """Remove a single audit action group without overwriting the others.

    This is idempotent — if *group* is not present the current settings are
    returned unchanged without making a PATCH request.

    The group name must consist exclusively of upper-case ASCII letters,
    digits, and underscores (``^[A-Z0-9_]+$``).  The Fabric API documents a
    fixed set of valid group names (e.g. ``BATCH_COMPLETED_GROUP``,
    ``SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP``); invalid names are accepted
    by this client-side validation but will be rejected by the API.  The
    broad ``^[A-Z0-9_]+$`` pattern is used rather than a closed enum because
    Microsoft may extend the set of valid names without notice.

    Note:
        This function performs a read-modify-write (GET then PATCH) without
        optimistic concurrency control.  The Fabric REST API does not expose
        ETags or ``If-Match`` on the ``/settings/sqlAudit`` endpoint.  Under
        concurrent modification the last writer wins silently.

    The authoritative post-PATCH state is returned immediately after the PATCH
    succeeds, constructed locally from the pre-PATCH settings and the computed
    new group list.  The ``GET /settings/sqlAudit`` endpoint is eventually
    consistent with multi-minute lag; polling it after PATCH to confirm removal
    is self-defeating and causes spurious timeouts.  A failed PATCH still
    raises via :meth:`~fabric_dw.http_client.FabricHttpClient.request`.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        warehouse_id: GUID of the Data Warehouse.
        group: Name of the action group to remove.

    Returns:
        The authoritative :class:`~fabric_dw.models.AuditSettings` after the
        PATCH (or the current settings when the group was not present).

    Raises:
        ValueError: If *group* does not match ``^[A-Z0-9_]+$``.
        ValueError: If auditing is currently disabled (``state == "Disabled"``).
            Enable auditing first with :func:`enable`.
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
        NotFoundError: If the warehouse does not exist (HTTP 404).
    """
    if not _ACTION_GROUP_RE.match(group):
        msg = (
            f"Invalid action_group name {group!r}: "
            "names must match ^[A-Z0-9_]+$ (upper-case letters, digits, and underscores only)"
        )
        raise ValueError(msg)

    current = await get_settings(http, workspace_id, warehouse_id)

    if current.state == "Disabled":
        msg = "audit is disabled; enable first"
        raise ValueError(msg)

    if group not in current.action_groups:
        # Group already absent — idempotent success, no PATCH needed.
        return current

    new_groups = [g for g in current.action_groups if g != group]
    path = _audit_path(workspace_id, warehouse_id)
    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        path,
        json={"auditActionsAndGroups": new_groups},
    )
    # Return the authoritative post-PATCH state constructed locally.
    # Do NOT poll GET: the GET endpoint lags the PATCH by minutes; the PATCH
    # itself is the authoritative source of truth.
    return current.model_copy(update={"action_groups": new_groups})
