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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from fabric_dw.http_client import HttpBase

if TYPE_CHECKING:
    from fabric_dw.http_client import FabricHttpClient

__all__ = [
    "LakehouseMatch",
    "resolve_backing_lakehouse",
    "resolve_lakehouse_connection_string",
]


@dataclass(frozen=True)
class LakehouseMatch:
    """The Lakehouse item that backs a SQL analytics endpoint.

    Attributes:
        id: The Lakehouse item's UUID.
        connection_string: The lakehouse's ``properties.sqlEndpointProperties.connectionString``,
            or ``None`` if still empty (the endpoint is still provisioning).
        default_schema: The lakehouse's ``properties.defaultSchema``, or ``None``.
            Per the Fabric REST API reference, this property is returned **only**
            for a schema-enabled lakehouse — its presence is therefore the
            documented signal that the lakehouse has multiple schemas rather
            than a single implicit ``dbo`` schema.
    """

    id: UUID
    connection_string: str | None
    default_schema: str | None


async def resolve_backing_lakehouse(
    http: FabricHttpClient,
    workspace_id: UUID,
    endpoint_id: UUID,
) -> LakehouseMatch | None:
    """Find the Lakehouse item that backs a SQL analytics endpoint, if any.

    Pages ``GET /workspaces/{ws}/lakehouses`` and locates the lakehouse whose
    ``properties.sqlEndpointProperties.id`` matches *endpoint_id*. There is no
    reverse link on the ``/sqlEndpoints/{id}`` resource itself — a SQL analytics
    endpoint does not carry a pointer back to its parent item — so a scan of
    every lakehouse in the workspace is the only way to make this connection
    with the APIs Fabric exposes today. This is the same scan
    :func:`resolve_lakehouse_connection_string` already performs; the two share
    this one implementation so the workspace is only paged once per call site.

    Returns ``None`` when no lakehouse in the workspace pairs with *endpoint_id*
    — either the endpoint belongs to something other than a Lakehouse (a
    Warehouse, a mirrored database, etc., none of which appear in
    ``/lakehouses``), or it is a lakehouse-derived endpoint whose parent
    lakehouse has since been deleted.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace to search.
        endpoint_id: The UUID of the SQL analytics endpoint to resolve.

    Returns:
        A :class:`LakehouseMatch` for the paired lakehouse, or ``None`` if no
        lakehouse in the workspace pairs with *endpoint_id*.
    """
    # str(UUID) is always lowercase; lowercase the API value too so the match is
    # robust against Fabric returning an uppercase/mixed-case UUID string.
    endpoint_id_str = str(endpoint_id).lower()
    async for lh in http.iter_paginated(HttpBase.FABRIC, f"/workspaces/{workspace_id}/lakehouses"):
        props = lh.get("properties")
        props_dict = cast("dict[str, Any]", props) if isinstance(props, dict) else {}
        sql_ep = props_dict.get("sqlEndpointProperties")
        sql_ep_dict = cast("dict[str, Any]", sql_ep) if isinstance(sql_ep, dict) else {}
        if str(sql_ep_dict.get("id", "")).lower() != endpoint_id_str:
            continue
        conn = str(sql_ep_dict.get("connectionString", ""))
        default_schema = props_dict.get("defaultSchema")
        lh_id = lh.get("id")
        if lh_id is None:
            return None
        return LakehouseMatch(
            id=UUID(str(lh_id)),
            connection_string=conn or None,
            default_schema=str(default_schema) if default_schema else None,
        )
    return None


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

    Thin wrapper around :func:`resolve_backing_lakehouse` that extracts just the
    connection string, kept for the existing connection-string-only call sites.

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
    match = await resolve_backing_lakehouse(http, workspace_id, endpoint_id)
    return match.connection_string if match else None
