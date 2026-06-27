"""SQL audit settings service for Microsoft Fabric Data Warehouses and SQL Analytics Endpoints.

Wraps the item-scoped ``/settings/sqlAudit`` endpoint for both item types:

- ``GET /v1/workspaces/{ws}/warehouses/{id}/settings/sqlAudit``
- ``GET /v1/workspaces/{ws}/sqlEndpoints/{id}/settings/sqlAudit``

Per Microsoft Learn, SQL auditing applies to both Data Warehouses and SQL Analytics
Endpoints (``Applies to: ✅ SQL analytics endpoint and Warehouse``).

Public functions:

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
from fabric_dw.models import AuditSettings, WarehouseKind

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


def _validate_action_group(name: str) -> None:
    """Raise :exc:`ValueError` if *name* does not match ``^[A-Z0-9_]+$``.

    Args:
        name: The action-group name to validate.

    Raises:
        ValueError: If *name* contains characters outside the allowed set.
    """
    if not _ACTION_GROUP_RE.match(name):
        msg = (
            f"Invalid action_group name {name!r}: "
            "names must match ^[A-Z0-9_]+$ (upper-case letters, digits, and underscores only)"
        )
        raise ValueError(msg)


async def _require_enabled(
    http: FabricHttpClient,
    workspace_id: UUID,
    item_id: UUID,
    kind: WarehouseKind,
    *,
    msg: str = "audit is disabled; enable first",
) -> AuditSettings:
    """Fetch current audit settings and raise if auditing is disabled.

    Performs a ``GET /settings/sqlAudit`` and raises :exc:`ValueError` when the
    returned state is ``"Disabled"``.  Used as a shared pre-flight guard by
    :func:`add_action_group` and :func:`remove_action_group`.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        item_id: GUID of the Data Warehouse or SQL Analytics Endpoint.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
        msg: Error message to use when auditing is disabled.

    Returns:
        The current :class:`~fabric_dw.models.AuditSettings`.

    Raises:
        ValueError: If auditing is currently disabled.
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
        NotFoundError: If the item does not exist (HTTP 404).
    """
    current = await get_settings(http, workspace_id, item_id, kind)
    if current.state == "Disabled":
        raise ValueError(msg)
    return current


def _audit_path(workspace_id: UUID, item_id: UUID, kind: WarehouseKind) -> str:
    """Return the relative path for the sqlAudit settings endpoint.

    The collection segment depends on the item kind:

    - :attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE` → ``/warehouses/{id}/settings/sqlAudit``
    - :attr:`~fabric_dw.models.WarehouseKind.SQL_ENDPOINT` →
      ``/sqlEndpoints/{id}/settings/sqlAudit``

    Source: Microsoft REST API reference for
    ``GET/PATCH /v1/workspaces/{ws}/sqlEndpoints/{id}/settings/sqlAudit``
    (verified at https://learn.microsoft.com/en-us/rest/api/fabric/sqlendpoint/sql-audit-settings).

    Args:
        workspace_id: GUID of the Fabric workspace.
        item_id: GUID of the Data Warehouse or SQL Analytics Endpoint.
        kind: The :class:`~fabric_dw.models.WarehouseKind` discriminating the URL segment.

    Returns:
        A relative URL path string (no leading ``https://api.fabric.microsoft.com/v1``).

    Raises:
        ValueError: If *kind* is :attr:`~fabric_dw.models.WarehouseKind.SNAPSHOT`.
            SQL audit is not supported on warehouse snapshots (per Microsoft Learn,
            "SQL audit logs aren't supported for warehouse snapshots").  Without this
            guard a snapshot would fall through to the ``/warehouses/`` route and
            return a cryptic 404.
    """
    if kind == WarehouseKind.SNAPSHOT:
        msg = "SQL audit is not supported on warehouse snapshots."
        raise ValueError(msg)
    collection = "sqlEndpoints" if kind == WarehouseKind.SQL_ENDPOINT else "warehouses"
    return f"/workspaces/{workspace_id}/{collection}/{item_id}/settings/sqlAudit"


async def get_settings(
    http: FabricHttpClient,
    workspace_id: UUID,
    item_id: UUID,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
) -> AuditSettings:
    """Fetch the current SQL audit settings for a Data Warehouse or SQL Analytics Endpoint.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        item_id: GUID of the Data Warehouse or SQL Analytics Endpoint.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            Defaults to :attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE` for
            backwards compatibility.

    Returns:
        The current :class:`~fabric_dw.models.AuditSettings`.

    Raises:
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
        NotFoundError: If the item does not exist (HTTP 404).
    """
    path = _audit_path(workspace_id, item_id, kind)
    resp = await http.request("GET", HttpBase.FABRIC, path)
    return AuditSettings.model_validate(resp.json())


async def enable(
    http: FabricHttpClient,
    workspace_id: UUID,
    item_id: UUID,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    *,
    retention_days: int = 0,
) -> AuditSettings:
    """Enable SQL auditing on a Data Warehouse or SQL Analytics Endpoint.

    Performs a pre-flight ``GET /settings/sqlAudit`` to read the current
    ``auditActionsAndGroups`` before sending the PATCH.  The Fabric API resets
    any field omitted from a partial PATCH to its default value, so the current
    action-group list is always round-tripped alongside ``state`` and
    ``retentionDays``.  On a first-time enable the
    :attr:`~fabric_dw.models.AuditSettings.action_groups` model field
    defaults to an empty list (``default_factory=list``), which is the
    safe no-op value.

    Note:
        This function performs a read-modify-write (GET then PATCH) without
        optimistic concurrency control.  The Fabric REST API does not expose
        ETags or ``If-Match`` on the ``/settings/sqlAudit`` endpoint.  Under
        concurrent modification the last writer wins silently.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        item_id: GUID of the Data Warehouse or SQL Analytics Endpoint.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            Defaults to :attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE` for
            backwards compatibility.
        retention_days: How many days to retain audit logs.  ``0`` means
            unlimited (Microsoft's interpretation per the Learn documentation).

    Returns:
        The fresh :class:`~fabric_dw.models.AuditSettings` after the update.

    Raises:
        ValueError: If *retention_days* is negative.
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
        NotFoundError: If the item does not exist (HTTP 404) — raised by the
            pre-flight GET or the final re-fetch.
    """
    if retention_days < 0:
        msg = f"retention_days must be >= 0; got {retention_days}"
        raise ValueError(msg)

    path = _audit_path(workspace_id, item_id, kind)

    # Fetch current settings to round-trip the existing action groups into
    # the PATCH body.  Omitting auditActionsAndGroups causes the Fabric API
    # to silently reset it to defaults.  current.action_groups is always a
    # valid list (default_factory=list), so this is safe even when audit was
    # previously Disabled and no custom groups have been configured.
    current = await get_settings(http, workspace_id, item_id, kind)
    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        path,
        json={
            "state": "Enabled",
            "retentionDays": retention_days,
            "auditActionsAndGroups": current.action_groups,
        },
    )
    # PATCH returns empty/partial body on this endpoint; re-fetch required.
    return await get_settings(http, workspace_id, item_id, kind)


async def disable(
    http: FabricHttpClient,
    workspace_id: UUID,
    item_id: UUID,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
) -> AuditSettings:
    """Disable SQL auditing on a Data Warehouse or SQL Analytics Endpoint.

    Performs a pre-flight ``GET /settings/sqlAudit`` to read the current
    ``retentionDays`` and ``auditActionsAndGroups`` before sending the PATCH.
    The Fabric API resets any field omitted from a partial PATCH to its default
    value, so both fields are always round-tripped alongside ``state=Disabled``.
    This preserves the configured retention period and action-group list so a
    subsequent re-enable restores them rather than wiping the prior configuration.

    Note:
        This function performs a read-modify-write (GET then PATCH) without
        optimistic concurrency control.  The Fabric REST API does not expose
        ETags or ``If-Match`` on the ``/settings/sqlAudit`` endpoint.  Under
        concurrent modification the last writer wins silently.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        item_id: GUID of the Data Warehouse or SQL Analytics Endpoint.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            Defaults to :attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE` for
            backwards compatibility.

    Returns:
        The fresh :class:`~fabric_dw.models.AuditSettings` after the update.

    Raises:
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
        NotFoundError: If the item does not exist (HTTP 404) -- raised by the
            pre-flight GET or the final re-fetch.
    """
    path = _audit_path(workspace_id, item_id, kind)
    # Pre-flight GET to preserve retentionDays and auditActionsAndGroups in the
    # PATCH body.  Omitting either field causes the Fabric API to silently reset
    # it to its default value, wiping custom retention and group configuration.
    current = await get_settings(http, workspace_id, item_id, kind)
    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        path,
        json={
            "state": "Disabled",
            "retentionDays": current.retention_days,
            "auditActionsAndGroups": current.action_groups,
        },
    )
    # PATCH returns empty/partial body on this endpoint; re-fetch required.
    return await get_settings(http, workspace_id, item_id, kind)


async def set_retention(
    http: FabricHttpClient,
    workspace_id: UUID,
    item_id: UUID,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    *,
    days: int,
) -> AuditSettings:
    """Update the audit log retention period for an already-enabled audit.

    Performs a pre-flight GET (via :func:`_require_enabled`) to verify that
    auditing is currently enabled.  Setting retention while audit is disabled
    is not meaningful — the Fabric service accepts the PATCH silently but the
    setting has no effect.  Raising ``ValueError`` eagerly gives callers a
    clear signal to enable auditing first.

    The PATCH body re-asserts ``state=Enabled`` and the current
    ``auditActionsAndGroups`` alongside ``retentionDays``.  Omitting either
    field from a partial PATCH causes the Fabric API to silently reset it to
    its default value, which would wipe custom action groups.  Since
    ``_require_enabled`` guarantees the current state is already ``"Enabled"``,
    re-asserting it does not change the effective audit state.

    The Fabric REST API does not document an upper bound for ``retentionDays``.
    Only the lower bound (>= 1) is enforced here; the API will reject any value
    it considers out of range and surface an appropriate error.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        item_id: GUID of the Data Warehouse or SQL Analytics Endpoint.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            Defaults to :attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE` for
            backwards compatibility.
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

    # _require_enabled performs a GET and returns the current settings.
    # Reuse the fetched settings to include state and auditActionsAndGroups
    # in the PATCH body — omitting them causes the Fabric API to silently
    # reset auditActionsAndGroups to defaults (the data-loss bug this fixes).
    current = await _require_enabled(
        http,
        workspace_id,
        item_id,
        kind,
        msg="audit is disabled; enable first before setting retention",
    )

    path = _audit_path(workspace_id, item_id, kind)
    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        path,
        json={
            "state": current.state,
            "retentionDays": days,
            "auditActionsAndGroups": current.action_groups,
        },
    )
    # PATCH returns empty/partial body on this endpoint; re-fetch required.
    return await get_settings(http, workspace_id, item_id, kind)


async def set_action_groups(
    http: FabricHttpClient,
    workspace_id: UUID,
    item_id: UUID,
    action_groups: list[str],
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    *,
    ensure_enabled: bool = True,
) -> AuditSettings:
    """Replace the audited action groups for a Data Warehouse or SQL Analytics Endpoint.

    Action-group names must consist exclusively of upper-case ASCII letters,
    digits, and underscores (``^[A-Z0-9_]+$``).  Examples of valid names:
    ``BATCH_COMPLETED_GROUP``, ``SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP``.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: GUID of the Fabric workspace.
        item_id: GUID of the Data Warehouse or SQL Analytics Endpoint.
        action_groups: List of action-group name strings to set.  Pass an empty
            list to clear all action groups.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            Defaults to :attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE` for
            backwards compatibility.
        ensure_enabled: Controls whether auditing is simultaneously activated.
            When ``True`` (default), the PATCH also sets ``state=Enabled`` so
            that auditing is active after the call even if it was previously
            disabled — this is the typical "set groups and start auditing" path.
            When ``False``, only the action-group list is changed and the current
            audit state is preserved; a :exc:`ValueError` is raised if the audit
            is currently disabled (consistent with :func:`add_action_group` and
            :func:`remove_action_group`).

            Note:
                This is an intentional design choice: ``set_action_groups``
                doubles as "initialise auditing" (default) and "update groups
                on a live audit" (``ensure_enabled=False``).  When only
                updating groups is desired, pass ``ensure_enabled=False`` to
                preserve the current audit state.

    Returns:
        The authoritative :class:`~fabric_dw.models.AuditSettings` after the
        PATCH, constructed locally from the pre-PATCH settings and the supplied
        action-group list.  The ``GET /settings/sqlAudit`` endpoint is
        eventually consistent with multi-minute lag; polling it after PATCH is
        self-defeating.  A failed PATCH still raises via
        :meth:`~fabric_dw.http_client.FabricHttpClient.request`.

    Raises:
        ValueError: If any name in *action_groups* does not match ``^[A-Z0-9_]+$``.
        ValueError: If *ensure_enabled* is ``False`` and auditing is currently disabled
            (``state == "Disabled"``).  Enable auditing first with :func:`enable`.
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
    """
    for name in action_groups:
        _validate_action_group(name)

    # Pre-flight GET to obtain current settings so we can construct the
    # authoritative post-PATCH state without a stale re-fetch.
    if not ensure_enabled:
        # ensure_enabled=False: guard against writing to a disabled audit config.
        current = await _require_enabled(http, workspace_id, item_id, kind)
    else:
        current = await get_settings(http, workspace_id, item_id, kind)

    path = _audit_path(workspace_id, item_id, kind)

    # Fabric's PATCH /settings/sqlAudit accepts an ``auditActionsAndGroups`` field
    # alongside ``state`` and ``retentionDays``.  Using PATCH to set the action groups
    # avoids the EntityNotFound (404) that the POST method returns on freshly-created
    # warehouses, since PATCH with state=Enabled is idempotent and always works.
    # retentionDays is always round-tripped: omitting it from a partial PATCH causes
    # the Fabric API to silently reset it to its default value (data-loss bug).
    patch_body: dict[str, object] = {
        "auditActionsAndGroups": action_groups,
        "retentionDays": current.retention_days,
    }
    if ensure_enabled:
        patch_body["state"] = "Enabled"
    else:
        # Round-trip the current state so the Fabric API does not reset it to
        # its default.  _require_enabled already confirmed state is "Enabled".
        patch_body["state"] = current.state

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
    item_id: UUID,
    group: str,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
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
        item_id: GUID of the Data Warehouse or SQL Analytics Endpoint.
        group: Name of the action group to add.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            Defaults to :attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE` for
            backwards compatibility.

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
        NotFoundError: If the item does not exist (HTTP 404).
    """
    _validate_action_group(group)

    current = await _require_enabled(http, workspace_id, item_id, kind)

    if group in current.action_groups:
        return current

    new_groups = [*current.action_groups, group]
    path = _audit_path(workspace_id, item_id, kind)
    # Round-trip state and retentionDays alongside the updated group list.
    # Omitting either field from a partial PATCH causes the Fabric API to
    # silently reset it to its default value (data-loss bug).
    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        path,
        json={
            "state": current.state,
            "retentionDays": current.retention_days,
            "auditActionsAndGroups": new_groups,
        },
    )
    # Return the authoritative post-PATCH state constructed locally.
    # Do NOT poll GET: the GET endpoint lags the PATCH by minutes; the PATCH
    # itself is the authoritative source of truth.
    return current.model_copy(update={"action_groups": new_groups})


async def remove_action_group(
    http: FabricHttpClient,
    workspace_id: UUID,
    item_id: UUID,
    group: str,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
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
        item_id: GUID of the Data Warehouse or SQL Analytics Endpoint.
        group: Name of the action group to remove.
        kind: The :class:`~fabric_dw.models.WarehouseKind` of the item.
            Defaults to :attr:`~fabric_dw.models.WarehouseKind.WAREHOUSE` for
            backwards compatibility.

    Returns:
        The authoritative :class:`~fabric_dw.models.AuditSettings` after the
        PATCH (or the current settings when the group was not present).

    Raises:
        ValueError: If *group* does not match ``^[A-Z0-9_]+$``.
        ValueError: If auditing is currently disabled (``state == "Disabled"``).
            Enable auditing first with :func:`enable`.
        PermissionDeniedError: If the caller lacks the required permission (HTTP 403).
        NotFoundError: If the item does not exist (HTTP 404).
    """
    _validate_action_group(group)

    current = await _require_enabled(http, workspace_id, item_id, kind)

    if group not in current.action_groups:
        # Group already absent — idempotent success, no PATCH needed.
        return current

    new_groups = [g for g in current.action_groups if g != group]
    path = _audit_path(workspace_id, item_id, kind)
    # Round-trip state and retentionDays alongside the updated group list.
    # Omitting either field from a partial PATCH causes the Fabric API to
    # silently reset it to its default value (data-loss bug).
    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        path,
        json={
            "state": current.state,
            "retentionDays": current.retention_days,
            "auditActionsAndGroups": new_groups,
        },
    )
    # Return the authoritative post-PATCH state constructed locally.
    # Do NOT poll GET: the GET endpoint lags the PATCH by minutes; the PATCH
    # itself is the authoritative source of truth.
    return current.model_copy(update={"action_groups": new_groups})
