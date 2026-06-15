"""Service functions for Microsoft Fabric SQL Analytics Endpoint operations."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fabric_dw.exceptions import FabricServerError, NotFoundError, PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import TableSyncStatus, Warehouse, WarehouseKind
from fabric_dw.services._helpers import scan_all_workspaces
from fabric_dw.services.capacities import get_capacity_states
from fabric_dw.services.workspaces import list_all as _list_all_workspaces

_logger = logging.getLogger("fabric_dw.sql_endpoints")

# Bounded polling for eventual-consistency fields (e.g. connection_string).
_CONN_STRING_POLL_INTERVAL: float = 5.0
_CONN_STRING_POLL_TIMEOUT: float = 600.0  # 10 min — Fabric preview eventual-consistency window

__all__ = [
    "get_endpoint",
    "get_endpoint_connection_string",
    "list_all_workspaces",
    "list_endpoints",
    "refresh_metadata",
]


async def list_endpoints(http: FabricHttpClient, workspace_id: UUID) -> list[Warehouse]:
    """Return all SQL analytics endpoints in a workspace.

    Pages through ``GET /workspaces/{ws}/sqlEndpoints`` and returns each item
    parsed as a :class:`~fabric_dw.models.Warehouse` with
    ``kind=SQL_ENDPOINT``.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace to query.

    Returns:
        A list of :class:`~fabric_dw.models.Warehouse` instances with
        ``kind == WarehouseKind.SQL_ENDPOINT``.
    """
    return [
        Warehouse.from_api(item, kind=WarehouseKind.SQL_ENDPOINT)
        async for item in http.iter_paginated(
            HttpBase.FABRIC, f"/workspaces/{workspace_id}/sqlEndpoints"
        )
    ]


async def list_all_workspaces(http: FabricHttpClient) -> list[Warehouse]:
    """Scan every visible workspace and collect its SQL analytics endpoints.

    Iterates all workspaces returned by :func:`~fabric_dw.services.workspaces.list_all`
    and aggregates their SQL analytics endpoints using bounded concurrency (up to
    8 workspaces in parallel).

    Workspaces whose capacity is not ``"Active"`` are skipped **before** the
    data-plane call (proactive filter via ``GET /v1/capacities``), avoiding the
    ~22s hang that paused-capacity workspaces incur.  If the caller lacks the
    capacity-read permission, the proactive filter is unavailable and the
    defensive fallback applies: a non-retriable 5xx per workspace is silently
    skipped at ``DEBUG`` level.

    Workspaces that raise :class:`~fabric_dw.exceptions.PermissionDeniedError`
    or :class:`~fabric_dw.exceptions.NotFoundError` are skipped with a
    per-workspace ``WARNING`` log; a summary ``WARNING`` is logged after the scan.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.

    Returns:
        A flat list of :class:`~fabric_dw.models.Warehouse` instances (with
        ``kind == SQL_ENDPOINT``) from all accessible, active-capacity workspaces.
    """
    # Fetch workspaces and capacity states concurrently.  Capacity-state
    # fetching is best-effort: if GET /v1/capacities fails for any reason
    # other than 403 (which get_capacity_states already handles internally),
    # degrade to capacity_states=None and continue the scan via the defensive
    # per-workspace fallback.  The workspace listing must never abort just
    # because the capacity endpoint is unavailable.
    async def _get_capacity_states_safe() -> dict[str, str] | None:
        try:
            return await get_capacity_states(http)
        except Exception as exc:
            _logger.debug(
                "GET /v1/capacities failed (%s) — proactive capacity filtering unavailable; "
                "falling back to defensive per-workspace error handling",
                exc,
            )
            return None

    workspaces, capacity_states = await asyncio.gather(
        _list_all_workspaces(http),
        _get_capacity_states_safe(),
    )
    return await scan_all_workspaces(
        workspaces,
        lambda ws: list_endpoints(http, ws.id),  # type: ignore[union-attr]  # mypy false-positive: Sequence[_HasNameIdAndCapacity] exposes id: UUID but mypy loses the concrete type through the Protocol abstraction
        logger=_logger,
        skip_errors=(PermissionDeniedError, NotFoundError),
        capacity_states=capacity_states,
    )


async def get_endpoint(http: FabricHttpClient, workspace_id: UUID, endpoint_id: UUID) -> Warehouse:
    """Fetch a single SQL analytics endpoint by ID.

    Uses ``GET /workspaces/{ws}/sqlEndpoints/{id}``.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint to retrieve.

    Returns:
        A populated :class:`~fabric_dw.models.Warehouse` instance with
        ``kind == WarehouseKind.SQL_ENDPOINT``.

    Raises:
        NotFoundError: If the endpoint does not exist (404).
    """
    resp = await http.request(
        "GET",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/sqlEndpoints/{endpoint_id}",
    )
    return Warehouse.from_api(resp.json(), kind=WarehouseKind.SQL_ENDPOINT)


async def get_endpoint_connection_string(
    http: FabricHttpClient,
    workspace_id: UUID,
    endpoint_id: UUID,
    *,
    poll_interval: float = _CONN_STRING_POLL_INTERVAL,
    timeout: float = _CONN_STRING_POLL_TIMEOUT,
) -> str:
    """Return the connection string for a SQL analytics endpoint, polling until non-empty.

    SQL analytics endpoints are provisioned with eventual consistency: the
    ``connectionString`` field may be empty or absent immediately after
    the endpoint is created.  This function polls
    ``GET /workspaces/{ws}/sqlEndpoints/{id}`` until the connection string
    is non-empty, up to *timeout* seconds.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint.
        poll_interval: Seconds between polls (default 5.0).
        timeout: Maximum wall-clock seconds to wait (default 600.0, i.e. 10 min).

    Returns:
        The non-empty connection string.

    Raises:
        FabricServerError: If the connection string remains empty after *timeout* seconds.
        NotFoundError: If the endpoint does not exist (404).
    """
    import time as _time  # noqa: PLC0415 — local import avoids module-level shadowing

    deadline = _time.monotonic() + timeout
    while True:
        ep = await get_endpoint(http, workspace_id, endpoint_id)
        if ep.connection_string:
            return ep.connection_string

        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            raise FabricServerError(
                f"connection_string for SQL endpoint {endpoint_id} "
                f"remained empty after {timeout:.0f}s"
            )

        wait = min(poll_interval, remaining)
        _logger.debug(
            "connection_string not yet populated for endpoint %s; retrying in %.1fs",
            endpoint_id,
            wait,
        )
        await asyncio.sleep(wait)


async def refresh_metadata(
    http: FabricHttpClient,
    workspace_id: UUID,
    endpoint_id: UUID,
    *,
    recreate_tables: bool = False,
) -> list[TableSyncStatus]:
    """Trigger a metadata refresh for a SQL analytics endpoint.

    Issues ``POST /workspaces/{ws}/sqlEndpoints/{id}/refreshMetadata`` with
    an optional ``recreateTables`` body flag.

    The API supports two completion modes:

    * **Synchronous** (200/204, no ``Location`` or ``Operation-Location``
      response header): the per-table results are read directly from the
      response body.
    * **Asynchronous** (202 + ``Location`` / ``Operation-Location`` header):
      the function polls the LRO to completion via
      :meth:`~fabric_dw.http_client.FabricHttpClient.poll_operation` and then
      parses the per-table results from the operation result.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint to refresh.
        recreate_tables: When ``True``, pass ``recreateTables=true`` in the
            request body, instructing Fabric to drop and recreate all tables
            during the refresh.  **Destructive** — use with caution.

    Returns:
        A list of :class:`~fabric_dw.models.TableSyncStatus` objects, one per
        table, describing the outcome of the refresh.

    Raises:
        FabricServerError: If the async LRO fails or times out (async path
            only).
        NotFoundError: If the endpoint does not exist (404).
    """
    json_body: dict[str, object] | None = {"recreateTables": True} if recreate_tables else None

    resp = await http.request(
        "POST",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/sqlEndpoints/{endpoint_id}/refreshMetadata",
        json=json_body,
    )

    # The API may complete synchronously (200/204 with results inline) or
    # asynchronously (202 + Location / Operation-Location header).  Try the
    # async path first; fall back to treating the response body as the result.
    location: str | None = resp.headers.get("Location") or resp.headers.get("Operation-Location")

    if location:
        lro_body = await http.poll_operation(location)
        raw_value: object = lro_body.get("value", []) if isinstance(lro_body, dict) else []
    else:
        # Synchronous completion: parse the table sync statuses from the body directly.
        _logger.debug(
            "refresh_metadata for endpoint %s completed synchronously (no LRO header)",
            endpoint_id,
        )
        body: object = resp.json() if resp.content else {}
        raw_value = body.get("value", []) if isinstance(body, dict) else []  # type: ignore[union-attr]

    raw_items = raw_value if isinstance(raw_value, list) else []
    return [TableSyncStatus.model_validate(item) for item in raw_items]
