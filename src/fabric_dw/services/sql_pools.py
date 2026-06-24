"""Service functions for managing workspace SQL Pools configuration (beta API).

This module targets the beta endpoint::

    GET/PATCH /v1/workspaces/{ws}/warehouses/sqlPoolsConfiguration?beta=true

The endpoint is **workspace-level** — there is no warehouse ID in the path
despite the ``/warehouses/`` segment.  Callers must hold the workspace admin
role.

.. warning::
   The SQL Pools API is currently in beta / preview.  It may change before GA.
   The ``?beta=true`` query parameter is centralised in :data:`_BETA_PARAMS` so
   that removing it later is a one-liner.

.. warning::
   ``update_configuration`` (PATCH) is **destructive**: any pool *not* included
   in the request body will be permanently deleted.  Always validate before
   calling or use the client-side helpers that pre-fetch the current state.
"""

from __future__ import annotations

import types
from collections.abc import Mapping
from uuid import UUID

from fabric_dw.exceptions import AlreadyExistsError, NotFoundError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import SqlPool, SqlPoolsConfiguration

__all__ = [
    "create_pool",
    "delete_pool",
    "disable",
    "enable",
    "get_configuration",
    "update_configuration",
    "update_pool",
]

# The beta query parameter — centralised so removing it later is a one-liner.
# Microsoft Learn docs sample uses capital-T "True" for this query parameter.
# Wrapped in MappingProxyType to prevent accidental mutation by callers.
_BETA_PARAMS: Mapping[str, str] = types.MappingProxyType({"beta": "True"})


def _config_path(workspace_id: UUID) -> str:
    return f"/workspaces/{workspace_id}/warehouses/sqlPoolsConfiguration"


def _rebuild_config(*, enabled: bool, pools: list[SqlPool]) -> SqlPoolsConfiguration:
    """Build a :class:`SqlPoolsConfiguration` from *enabled* flag and *pools* list.

    Centralises the repeated ``model_validate`` pattern used across all
    read-modify-write operations to ensure ``by_alias`` / ``mode="json"``
    serialisation details are applied consistently.

    Args:
        enabled: Value for ``customSQLPoolsEnabled``.
        pools: The full list of :class:`SqlPool` instances to include.

    Returns:
        A validated :class:`SqlPoolsConfiguration` ready for :func:`update_configuration`.
    """
    return SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": enabled,
            "customSQLPools": [p.model_dump(by_alias=True, mode="json") for p in pools],
        }
    )


async def get_configuration(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> SqlPoolsConfiguration:
    """Fetch the SQL Pools configuration for a workspace.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.

    Returns:
        The current :class:`~fabric_dw.models.SqlPoolsConfiguration`.

    Raises:
        PermissionDeniedError: If the caller does not have the workspace admin role
            (HTTP 403).
    """
    resp = await http.request(
        "GET",
        HttpBase.FABRIC,
        _config_path(workspace_id),
        params=_BETA_PARAMS,
    )
    return SqlPoolsConfiguration.model_validate(resp.json())


async def update_configuration(
    http: FabricHttpClient,
    workspace_id: UUID,
    config: SqlPoolsConfiguration,
) -> SqlPoolsConfiguration:
    """Replace the SQL Pools configuration for a workspace.

    .. warning::
       Any pool **not** included in ``config.custom_sql_pools`` will be
       **permanently deleted** by the Fabric service.  Pass the full desired
       pool list every time.

    Client-side validation runs before the PATCH request so constraint
    violations (sum > 100, multiple defaults) are caught locally without
    burning an API call.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        config: The full desired :class:`~fabric_dw.models.SqlPoolsConfiguration`.
            Must pass all model-level validations.

    Returns:
        The fresh :class:`~fabric_dw.models.SqlPoolsConfiguration` retrieved
        via a follow-up GET after the PATCH.

    Raises:
        PermissionDeniedError: If the caller does not have the workspace admin role
            (HTTP 403).
        ValueError: If ``config`` violates client-side constraints (sum > 100,
            multiple defaults, etc.).
    """
    config.validate_for_patch()
    body = config.model_dump(by_alias=True, mode="json", exclude_none=True)

    await http.request(
        "PATCH",
        HttpBase.FABRIC,
        _config_path(workspace_id),
        json=body,
        params=_BETA_PARAMS,
    )
    # PATCH returns empty/partial body on this endpoint; re-fetch required.
    return await get_configuration(http, workspace_id)


async def enable(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> SqlPoolsConfiguration:
    """Enable custom SQL Pools for a workspace without modifying the pool list.

    Fetches the current configuration and PATCHes the full config document with
    ``customSQLPoolsEnabled=true``, preserving all existing pools.

    The Fabric beta API requires a full configuration document on PATCH — a
    minimal ``{"customSQLPoolsEnabled": true}`` payload does not preserve the
    existing pool list.  The full roundtrip is therefore necessary.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.

    Returns:
        The updated :class:`~fabric_dw.models.SqlPoolsConfiguration`.

    Raises:
        PermissionDeniedError: If the caller does not have the workspace admin role
            (HTTP 403).
        ValueError: If no custom SQL pools are defined.  The Fabric API rejects
            ``customSQLPoolsEnabled=True`` with an empty pool list; this guard
            surfaces the constraint as a clear, actionable message before the
            PATCH is attempted.
    """
    current = await get_configuration(http, workspace_id)
    if current.custom_sql_pools_enabled:
        return current

    if not current.custom_sql_pools:
        msg = (
            "Cannot enable custom SQL pools: no pools are defined. "
            "Create at least one pool first (`sql-pools create`)."
        )
        raise ValueError(msg)

    enabled_config = _rebuild_config(enabled=True, pools=current.custom_sql_pools)
    return await update_configuration(http, workspace_id, enabled_config)


async def disable(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> SqlPoolsConfiguration:
    """Disable custom SQL Pools for a workspace, preserving the pool configuration.

    Per API documentation: "When set to false, the configuration is disabled
    but preserved.  Re-enabling it restores the previously saved configuration."

    Fetches the current configuration and PATCHes the full config document with
    ``customSQLPoolsEnabled=false``, keeping all pool definitions intact so they
    can be restored by :func:`enable`.

    The Fabric beta API requires a full configuration document on PATCH — a
    minimal ``{"customSQLPoolsEnabled": false}`` payload does not preserve the
    existing pool list.  The full roundtrip is therefore necessary.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.

    Returns:
        The updated :class:`~fabric_dw.models.SqlPoolsConfiguration`.

    Raises:
        PermissionDeniedError: If the caller does not have the workspace admin role
            (HTTP 403).
    """
    current = await get_configuration(http, workspace_id)
    if not current.custom_sql_pools_enabled:
        return current

    disabled_config = _rebuild_config(enabled=False, pools=current.custom_sql_pools)
    return await update_configuration(http, workspace_id, disabled_config)


async def create_pool(
    http: FabricHttpClient,
    workspace_id: UUID,
    pool: SqlPool,
) -> SqlPoolsConfiguration:
    """Add a new pool to the workspace SQL Pools configuration.

    Fetches the current pool list, appends the new pool, and PATCHes the full
    list back.  Raises :class:`~fabric_dw.exceptions.AlreadyExistsError` if a pool
    with the same name already exists.

    Note:
        This function performs a read-modify-write (GET then PATCH) without
        optimistic concurrency control.  The Fabric REST API does not expose
        ETags or ``If-Match`` on the SQL Pools endpoint.  Under concurrent
        modification the last writer wins silently.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        pool: The new :class:`~fabric_dw.models.SqlPool` to create.

    Returns:
        The updated :class:`~fabric_dw.models.SqlPoolsConfiguration`.

    Raises:
        AlreadyExistsError: If a pool with ``pool.name`` already exists.
        PermissionDeniedError: If the caller does not have the workspace admin role.
    """
    current = await get_configuration(http, workspace_id)
    if any(p.name == pool.name for p in current.custom_sql_pools):
        msg = f"pool {pool.name!r} already exists; use update to modify it"
        raise AlreadyExistsError(msg)
    new_pools = [*current.custom_sql_pools, pool]
    new_config = _rebuild_config(enabled=current.custom_sql_pools_enabled, pools=new_pools)
    return await update_configuration(http, workspace_id, new_config)


async def update_pool(
    http: FabricHttpClient,
    workspace_id: UUID,
    name: str,
    *,
    max_resource_percentage: int | None = None,
    is_default: bool | None = None,
    optimize_for_reads: bool | None = None,
    classifier_type: str | None = None,
    classifier_values: list[str] | None = None,
) -> SqlPoolsConfiguration:
    """Update an existing pool in the workspace SQL Pools configuration.

    Fetches the current pool list, finds the named pool, applies only the
    provided field overrides, then PATCHes the full list back.  Fields not
    supplied are left unchanged.

    **Classifier semantics**: if neither *classifier_type* nor
    *classifier_values* is provided, the existing classifier is left
    completely untouched.  If **either** is provided, **both** must be
    provided and the classifier object is replaced wholesale — no partial
    merge is performed.

    Note:
        Setting ``is_default=True`` does **not** automatically clear
        ``is_default`` on other pools.  The caller must explicitly unset it
        on the previous default if the API rejects multiple defaults.

    Note:
        This function performs a read-modify-write (GET then PATCH) without
        optimistic concurrency control.  The Fabric REST API does not expose
        ETags or ``If-Match`` on the SQL Pools endpoint.  Under concurrent
        modification the last writer wins silently.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        name: The name of the pool to update.
        max_resource_percentage: New max-resource-percentage (1-100), or ``None`` to keep.
        is_default: New default flag, or ``None`` to keep.
        optimize_for_reads: New optimize-for-reads flag, or ``None`` to keep.
        classifier_type: New classifier type string.  Must be supplied together
            with *classifier_values* or not at all.
        classifier_values: New classifier value list.  Must be supplied together
            with *classifier_type* or not at all.

    Returns:
        The updated :class:`~fabric_dw.models.SqlPoolsConfiguration`.

    Raises:
        ValueError: If exactly one of *classifier_type* / *classifier_values*
            is supplied without the other.
        NotFoundError: If no pool named *name* exists.
        PermissionDeniedError: If the caller does not have the workspace admin role.
    """
    if (classifier_type is None) != (classifier_values is None):
        msg = "classifier_type and classifier_values must be provided together (or neither)"
        raise ValueError(msg)
    current = await get_configuration(http, workspace_id)
    existing = next((p for p in current.custom_sql_pools if p.name == name), None)
    if existing is None:
        msg = f"pool {name!r} not found; use create to add it"
        raise NotFoundError(msg)

    raw = existing.model_dump(by_alias=True, mode="json")
    if max_resource_percentage is not None:
        raw["maxResourcePercentage"] = max_resource_percentage
    if is_default is not None:
        raw["isDefault"] = is_default
    if optimize_for_reads is not None:
        raw["optimizeForReads"] = optimize_for_reads
    if classifier_type is not None and classifier_values is not None:
        # Replace the classifier object wholesale — no partial merge to avoid
        # sending an empty string type or stale values to the server.
        raw["classifier"] = {
            "type": classifier_type,
            "value": classifier_values,
        }

    new_pool = SqlPool.model_validate(raw)
    new_pools = [new_pool if p.name == name else p for p in current.custom_sql_pools]
    new_config = _rebuild_config(enabled=current.custom_sql_pools_enabled, pools=new_pools)
    return await update_configuration(http, workspace_id, new_config)


async def delete_pool(
    http: FabricHttpClient,
    workspace_id: UUID,
    name: str,
) -> SqlPoolsConfiguration:
    """Remove a pool from the workspace SQL Pools configuration.

    Fetches the current pool list, drops the named pool, and PATCHes the
    remaining list back.  Raises :class:`~fabric_dw.exceptions.NotFoundError` if no
    pool with that name exists.

    Note:
        This function performs a read-modify-write (GET then PATCH) without
        optimistic concurrency control.  The Fabric REST API does not expose
        ETags or ``If-Match`` on the SQL Pools endpoint.  Under concurrent
        modification the last writer wins silently.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.
        name: The name of the pool to delete.

    Returns:
        The updated :class:`~fabric_dw.models.SqlPoolsConfiguration`.

    Raises:
        NotFoundError: If no pool named ``name`` exists.
        PermissionDeniedError: If the caller does not have the workspace admin role.
    """
    current = await get_configuration(http, workspace_id)
    if not any(p.name == name for p in current.custom_sql_pools):
        msg = f"pool {name!r} not found"
        raise NotFoundError(msg)
    new_pools = [p for p in current.custom_sql_pools if p.name != name]
    new_config = _rebuild_config(enabled=current.custom_sql_pools_enabled, pools=new_pools)
    return await update_configuration(http, workspace_id, new_config)
