"""Low-level Fabric REST helpers shared across modules.

This module holds small, dependency-light helpers that talk directly to the
Fabric REST API and are needed by more than one higher-level module (e.g. both
:mod:`fabric_dw.resolver` and :mod:`fabric_dw.services.sql_endpoints`).

It sits at the same layer as :mod:`fabric_dw.http_client`: it depends only on
the HTTP client and standard library, never on the resolver, cache, or service
layer.  Keeping these shared helpers here avoids upward-layering imports (a
low-level module importing a service module) without resorting to lazy imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fabric_dw.http_client import HttpBase

if TYPE_CHECKING:
    from uuid import UUID

    from fabric_dw.http_client import FabricHttpClient

__all__ = [
    "resolve_lakehouse_connection_string",
]


async def resolve_lakehouse_connection_string(
    http: FabricHttpClient,
    workspace_id: UUID,
    endpoint_id: UUID,
) -> str | None:
    """Find the connection string for a lakehouse-derived SQL endpoint via the parent Lakehouse.

    For lakehouse-derived SQL analytics endpoints, ``GET /sqlEndpoints/{id}``
    permanently returns an empty ``connectionString`` — the value lives only on
    the parent Lakehouse at
    ``properties.sqlEndpointProperties.connectionString``.

    This helper pages ``GET /workspaces/{ws}/lakehouses``, locates the lakehouse
    whose ``properties.sqlEndpointProperties.id`` matches *endpoint_id*, and
    returns that lakehouse's ``connectionString``.  Returns ``None`` when no
    matching lakehouse is found (e.g. the endpoint belongs to a Warehouse, not a
    Lakehouse) **or** when the matching lakehouse's connection string is still
    empty (the endpoint is still provisioning).

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace to search.
        endpoint_id: The UUID of the SQL analytics endpoint whose connection
            string we need.

    Returns:
        The non-empty connection string from the matching lakehouse, or ``None``
        if no lakehouse in the workspace has a paired endpoint with this ID (or
        the paired lakehouse has not yet exposed a connection string).
    """
    # str(UUID) is always lowercase; lowercase the API value too so the match is
    # robust against Fabric returning an uppercase/mixed-case UUID string.
    endpoint_id_str = str(endpoint_id).lower()
    async for lh in http.iter_paginated(HttpBase.FABRIC, f"/workspaces/{workspace_id}/lakehouses"):
        props = lh.get("properties")
        props_dict = cast("dict[str, Any]", props) if isinstance(props, dict) else {}
        sql_ep = props_dict.get("sqlEndpointProperties")
        sql_ep_dict = cast("dict[str, Any]", sql_ep) if isinstance(sql_ep, dict) else {}
        if str(sql_ep_dict.get("id", "")).lower() == endpoint_id_str:
            conn = str(sql_ep_dict.get("connectionString", ""))
            return conn or None
    return None
