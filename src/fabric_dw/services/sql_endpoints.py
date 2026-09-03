"""Service functions for Microsoft Fabric SQL Analytics Endpoint operations."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from fabric_dw._fabric_api import resolve_backing_lakehouse, resolve_lakehouse_connection_string
from fabric_dw.exceptions import (
    CapacityInactiveError,
    FabricServerError,
    NotFoundError,
    PermissionDeniedError,
)
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import TableSyncStatus, Warehouse, WarehouseKind
from fabric_dw.services._helpers import scan_all_workspaces
from fabric_dw.services.capacities import get_capacity_states
from fabric_dw.services.workspaces import list_all as _list_all_workspaces

_logger = logging.getLogger("fabric_dw.sql_endpoints")

# Bounded polling for eventual-consistency fields (e.g. connection_string).
_CONN_STRING_POLL_INTERVAL: float = 5.0
# For lakehouse-derived endpoints the connection string lives on the *Lakehouse*
# body (sqlEndpointProperties.connectionString) and is available within ~20s of
# provisioning.  The GET /sqlEndpoints/{id} resource always returns an empty
# connectionString for these endpoints — it never populates.  The fallback reads
# from the parent Lakehouse instead, so the window needed is just the
# provisioning time (≈20s), not the original 10-minute guess.
_CONN_STRING_POLL_TIMEOUT: float = 120.0

__all__ = [
    "LakehouseDiscoveryGap",
    "LakehouseDiscoveryStatus",
    "find_undiscovered_lakehouse_tables",
    "get_endpoint",
    "get_endpoint_connection_string",
    "list_all_workspaces",
    "list_endpoints",
    "list_lakehouse_table_names",
    "refresh_metadata",
]


async def list_endpoints(http: FabricHttpClient, workspace_id: UUID) -> list[Warehouse]:
    """Return all SQL analytics endpoints in a workspace.

    Pages through ``GET /workspaces/{ws}/sqlEndpoints`` and returns each item
    parsed as a :class:`~fabric_dw.models.Warehouse` with
    ``kind=SQL_ENDPOINT``.

    Note (incomplete metadata vs. Warehouses):
        Unlike Warehouses, SQL-endpoint list rows carry **no**
        ``connection_string`` and **no** ``created_date``.  This is an API
        limitation, not a bug here.  The Fabric ``SQLEndpoint`` resource schema
        (used by both ``GET /sqlEndpoints`` and ``GET /sqlEndpoints/{id}``)
        exposes only ``id``, ``displayName``, ``description``, ``type``,
        ``workspaceId``, ``folderId``, ``sensitivityLabel``, ``tags`` and
        ``defaultIdentity`` — neither ``createdDate`` nor ``connectionString``
        is present (contrast Get Warehouse, which returns connection string +
        created date + collation).  See
        https://learn.microsoft.com/rest/api/fabric/sqlendpoint/items/list-sql-endpoints
        and the type-specific-properties table at
        https://learn.microsoft.com/rest/api/fabric/articles/onelakecatalog/overview#get-type-specific-item-properties
        (SQLEndpoint is absent from it).

        * ``connection_string`` — only resolvable per-endpoint, either via the
          dedicated ``Items - Get Connection String`` API or via the parent
          Lakehouse's ``properties.sqlEndpointProperties.connectionString``
          (see :func:`fabric_dw._fabric_api.resolve_lakehouse_connection_string`
          / #347).  Both are
          N+1; the list endpoint deliberately does NOT enrich it.
        * ``created_date`` — not returned by the endpoint resource at all (list
          or item), so it cannot be surfaced from a single list request.

        Per the "one request → fix it, per-item request → leave it" rule, both
        are left out of the list.  A future opt-in ``--enrich`` flag could fill
        them per endpoint if the extra calls are acceptable.

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

    Workspaces that raise :class:`~fabric_dw.exceptions.PermissionDeniedError`,
    :class:`~fabric_dw.exceptions.NotFoundError`, or
    :class:`~fabric_dw.exceptions.CapacityInactiveError` (capacity paused
    between the proactive filter and the fan-out call) are skipped with a
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
        lambda ws: list_endpoints(http, ws.id),
        logger=_logger,
        # CapacityInactiveError: proactive capacity filtering is best-effort
        # (see _get_capacity_states_safe above) and the capacity can also flip
        # inactive between the filter check and the fan-out call; skip that one
        # workspace like an inaccessible one instead of aborting the whole scan.
        skip_errors=(PermissionDeniedError, NotFoundError, CapacityInactiveError),
        capacity_states=capacity_states,
    )


async def get_endpoint(http: FabricHttpClient, workspace_id: UUID, endpoint_id: UUID) -> Warehouse:
    """Fetch a single SQL analytics endpoint by ID.

    Uses ``GET /workspaces/{ws}/sqlEndpoints/{id}``.  When the endpoint's own
    ``connectionString`` is empty (which is permanent for lakehouse-derived
    endpoints), falls back to scanning ``GET /workspaces/{ws}/lakehouses`` for
    the parent Lakehouse whose ``properties.sqlEndpointProperties.id`` matches
    *endpoint_id* and reads the connection string from there.  No extra
    lakehouse call is made when the endpoint resource already carries a
    connection string.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint to retrieve.

    Returns:
        A populated :class:`~fabric_dw.models.Warehouse` instance with
        ``kind == WarehouseKind.SQL_ENDPOINT``.  The ``connection_string``
        field is populated whenever the parent Lakehouse exposes it (i.e. after
        ``provisioningStatus`` reaches ``"Success"``).

    Raises:
        NotFoundError: If the endpoint does not exist (404).
    """
    resp = await http.request(
        "GET",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/sqlEndpoints/{endpoint_id}",
    )
    wh = Warehouse.from_api(resp.json(), kind=WarehouseKind.SQL_ENDPOINT)

    if wh.connection_string:
        # Fast path: endpoint resource already carries the connection string.
        return wh

    # Slow path: lakehouse-derived endpoints never populate connectionString on
    # the /sqlEndpoints/{id} resource.  Look it up via the parent Lakehouse.
    _logger.debug(
        "endpoint %s has empty connectionString on /sqlEndpoints resource; "
        "falling back to lakehouse scan for workspace %s",
        endpoint_id,
        workspace_id,
    )
    lh_conn = await resolve_lakehouse_connection_string(http, workspace_id, endpoint_id)
    if lh_conn:
        # Return a copy with the connection string resolved from the lakehouse,
        # preserving every other field (description, collation, created_date, …).
        return wh.model_copy(update={"connection_string": lh_conn})

    return wh


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
    the endpoint is created.  This function calls :func:`get_endpoint`
    (which includes the lakehouse-fallback for lakehouse-derived endpoints)
    until the connection string is non-empty, up to *timeout* seconds.
    For lakehouse-derived endpoints the value is available within ~20s of
    ``provisioningStatus`` reaching ``"Success"``; the default timeout is
    120 s, well above that window.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint.
        poll_interval: Seconds between polls (default 5.0).
        timeout: Maximum wall-clock seconds to wait (default 120.0).

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
        raw_value = body.get("value", []) if isinstance(body, dict) else []

    raw_items = raw_value if isinstance(raw_value, list) else []
    return [TableSyncStatus.model_validate(item) for item in raw_items]


# ---------------------------------------------------------------------------
# Lakehouse discovery-gap cross-check (#1064)
# ---------------------------------------------------------------------------
#
# tables.list_table_sync_status (TDS-only) lists tables from sys.tables, which
# on a SQL Analytics Endpoint is itself populated by the metadata sync -- a
# Lakehouse Delta table whose discovery has not completed, or has failed, has
# no sys.tables row and so is invisible to that function no matter what filter
# is passed. The functions below close that gap for the one case Fabric's REST
# API actually lets us check: a non-schema-enabled Lakehouse-backed endpoint.
#
# What was investigated and what it ruled out:
#
# - Resolving the endpoint back to its backing item: there is no reverse link
#   on GET /sqlEndpoints/{id} itself. resolve_backing_lakehouse (_fabric_api.py)
#   pages GET /workspaces/{ws}/lakehouses and matches on
#   properties.sqlEndpointProperties.id -- the same scan get_endpoint already
#   performs for the connection-string fallback. Returns None for anything that
#   isn't a Lakehouse (a mirrored database, a mirrored warehouse, etc.), which
#   this module cannot enumerate tables for at all -- there is no
#   "list source tables" REST API for those item kinds, so a SQL-endpoint whose
#   backing item is one of them keeps today's catalog-only coverage, degraded
#   but stated rather than silently claimed complete.
#
# - Enumerating a Lakehouse's tables: GET /workspaces/{ws}/lakehouses/{id}/tables
#   ("Lakehouse - List Tables") returns {name, type, format, location} per
#   table -- no schema field. Microsoft's own Lakehouse-schemas documentation
#   says explicitly that for a SCHEMA-ENABLED lakehouse you must use a
#   different, Unity-Catalog-compatible API family instead
#   (onelake.table.fabric.microsoft.com/delta/...), which needs its own base
#   URL, its own name-based addressing scheme, and (unconfirmed from the docs
#   alone) a different OAuth scope than FABRIC_SCOPE -- a materially bigger,
#   riskier change to make untested. So List Tables is used here instead, and
#   only when it is safe to: a lakehouse GET/list response's
#   properties.defaultSchema field is documented as present ONLY for a
#   schema-enabled lakehouse -- its presence is the signal used to refuse the
#   cross-check rather than guess at which schema a bare table name belongs to.


class LakehouseDiscoveryStatus(StrEnum):
    """Outcome of :func:`find_undiscovered_lakehouse_tables`."""

    #: The comparison ran; ``LakehouseDiscoveryGap.missing_table_names`` holds
    #: the (possibly empty) result.
    OK = "ok"
    #: *endpoint_id* has no matching Lakehouse in the workspace's ``/lakehouses``
    #: listing -- it backs something else (a mirrored database, a mirrored
    #: warehouse, etc.), or its parent Lakehouse has since been deleted. There is
    #: no REST API this codebase can use to enumerate that item kind's tables.
    NOT_LAKEHOUSE_BACKED = "not_lakehouse_backed"
    #: The backing Lakehouse has schema support enabled (``properties.defaultSchema``
    #: is present). The classic "List Tables" REST API returns bare table names
    #: with no schema attribution, so a comparison against ``sys.tables`` cannot
    #: be trusted to attribute a table to the right schema -- refused rather
    #: than risking a false "missing" report for a table that actually exists
    #: under a different schema.
    SCHEMA_ENABLED_UNSUPPORTED = "schema_enabled_unsupported"


@dataclass(frozen=True)
class LakehouseDiscoveryGap:
    """Result of comparing a Lakehouse's table inventory against a known set.

    Attributes:
        status: Which of the three outcomes in :class:`LakehouseDiscoveryStatus`
            applies.
        missing_table_names: Bare (unqualified) table names present in the
            Lakehouse's default (``dbo``) schema but absent from the
            ``known_dbo_names`` set passed to
            :func:`find_undiscovered_lakehouse_tables`. Only ever non-empty
            when ``status is LakehouseDiscoveryStatus.OK``.
    """

    status: LakehouseDiscoveryStatus
    missing_table_names: tuple[str, ...] = field(default_factory=tuple)


async def list_lakehouse_table_names(
    http: FabricHttpClient,
    workspace_id: UUID,
    lakehouse_id: UUID,
) -> list[str]:
    """Return every table name in a Lakehouse via the "List Tables" REST API.

    Pages ``GET /workspaces/{ws}/lakehouses/{id}/tables``. The response array
    lives under the ``"data"`` key (not the usual ``"value"``) and each entry
    carries ``name``, ``type`` (``Managed``/``External``), ``format``, and
    ``location`` -- no schema attribution, so callers must only use this for a
    non-schema-enabled Lakehouse (see :func:`find_undiscovered_lakehouse_tables`,
    which enforces that).

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the Lakehouse.
        lakehouse_id: The UUID of the Lakehouse to list tables for.

    Returns:
        A (possibly empty) list of bare table names, in API response order.
    """
    names: list[str] = []
    async for tbl in http.iter_paginated(
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables",
        key="data",
    ):
        name = tbl.get("name")
        if name:
            names.append(str(name))
    return names


async def find_undiscovered_lakehouse_tables(
    http: FabricHttpClient,
    workspace_id: UUID,
    endpoint_id: UUID,
    known_dbo_names: frozenset[str],
) -> LakehouseDiscoveryGap:
    """Find Lakehouse tables missing from a SQL endpoint's ``sys.tables`` catalog.

    Resolves *endpoint_id* to its backing Lakehouse via
    :func:`~fabric_dw._fabric_api.resolve_backing_lakehouse`, refuses the
    comparison for anything that isn't a non-schema-enabled Lakehouse (see
    :class:`LakehouseDiscoveryStatus`), and otherwise lists the Lakehouse's
    tables (:func:`list_lakehouse_table_names`) and returns the ones absent
    from *known_dbo_names* -- case-insensitively, matching T-SQL identifier
    semantics.

    This costs at least one extra REST call beyond ``list_table_sync_status``'s
    single TDS query (a lakehouse scan, plus a paginated table listing when a
    non-schema-enabled Lakehouse is found), so callers on a command that may
    run repeatedly should make this opt-in rather than call it
    unconditionally -- see ``tables sync-status --check-lakehouse`` /
    ``list_table_sync_status(check_lakehouse=True)``.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint to check.
        known_dbo_names: Bare table names already known to be present in the
            endpoint's ``dbo`` schema (typically every ``dbo``-schema row
            already returned by ``list_table_sync_status`` for this endpoint).

    Returns:
        A :class:`LakehouseDiscoveryGap` describing the outcome.
    """
    lakehouse = await resolve_backing_lakehouse(http, workspace_id, endpoint_id)
    if lakehouse is None:
        return LakehouseDiscoveryGap(status=LakehouseDiscoveryStatus.NOT_LAKEHOUSE_BACKED)
    if lakehouse.default_schema is not None:
        return LakehouseDiscoveryGap(status=LakehouseDiscoveryStatus.SCHEMA_ENABLED_UNSUPPORTED)

    table_names = await list_lakehouse_table_names(http, workspace_id, lakehouse.id)
    known_lower = {n.casefold() for n in known_dbo_names}
    missing = tuple(n for n in table_names if n.casefold() not in known_lower)
    return LakehouseDiscoveryGap(status=LakehouseDiscoveryStatus.OK, missing_table_names=missing)
