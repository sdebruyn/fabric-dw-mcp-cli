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

from uuid import UUID

from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import SqlPoolsConfiguration

__all__ = [
    "disable",
    "enable",
    "get_configuration",
    "update_configuration",
]

# The beta query parameter — centralised so removing it later is a one-liner.
# Microsoft Learn docs sample uses capital-T "True" for this query parameter.
_BETA_PARAMS: dict[str, str] = {"beta": "True"}


def _config_path(workspace_id: UUID) -> str:
    return f"/workspaces/{workspace_id}/warehouses/sqlPoolsConfiguration"


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
        PermissionDenied: If the caller does not have the workspace admin role
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
        PermissionDenied: If the caller does not have the workspace admin role
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
    return await get_configuration(http, workspace_id)


async def enable(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> SqlPoolsConfiguration:
    """Enable custom SQL Pools for a workspace without modifying the pool list.

    Fetches the current configuration and PATCHes only ``customSQLPoolsEnabled=true``,
    preserving all existing pools.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.

    Returns:
        The updated :class:`~fabric_dw.models.SqlPoolsConfiguration`.

    Raises:
        PermissionDenied: If the caller does not have the workspace admin role
            (HTTP 403).
    """
    current = await get_configuration(http, workspace_id)
    if current.custom_sql_pools_enabled:
        return current

    enabled_config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": current.model_dump(by_alias=True, mode="json")["customSQLPools"],
        }
    )
    return await update_configuration(http, workspace_id, enabled_config)


async def disable(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> SqlPoolsConfiguration:
    """Disable custom SQL Pools for a workspace, preserving the pool configuration.

    Per API documentation: "When set to false, the configuration is disabled
    but preserved.  Re-enabling it restores the previously saved configuration."

    Fetches the current configuration and PATCHes only ``customSQLPoolsEnabled=false``,
    keeping all pool definitions intact so they can be restored by :func:`enable`.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The Fabric workspace UUID.

    Returns:
        The updated :class:`~fabric_dw.models.SqlPoolsConfiguration`.

    Raises:
        PermissionDenied: If the caller does not have the workspace admin role
            (HTTP 403).
    """
    current = await get_configuration(http, workspace_id)
    if not current.custom_sql_pools_enabled:
        return current

    disabled_config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": False,
            "customSQLPools": current.model_dump(by_alias=True, mode="json")["customSQLPools"],
        }
    )
    return await update_configuration(http, workspace_id, disabled_config)
