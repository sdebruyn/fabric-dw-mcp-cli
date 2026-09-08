"""Service functions for Microsoft Fabric SQL Analytics Endpoint operations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from fabric_dw._fabric_api import resolve_backing_lakehouse, resolve_lakehouse_connection_string
from fabric_dw.auth import STORAGE_SCOPE
from fabric_dw.exceptions import (
    CapacityInactiveError,
    FabricServerError,
    NotFoundError,
    PermissionDeniedError,
)
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import TableMetadataSyncStatus, TableSyncStatus, Warehouse, WarehouseKind
from fabric_dw.services._helpers import scan_all_workspaces
from fabric_dw.services.capacities import get_capacity_states
from fabric_dw.services.workspaces import list_all as _list_all_workspaces

if TYPE_CHECKING:
    import httpx

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
    "apply_lakehouse_discovery_gap",
    "find_undiscovered_lakehouse_tables",
    "get_endpoint",
    "get_endpoint_connection_string",
    "list_all_workspaces",
    "list_endpoints",
    "list_lakehouse_schema_names",
    "list_lakehouse_schema_table_names",
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
# Lakehouse discovery-gap cross-check (#1064; schema-enabled support #1060)
# ---------------------------------------------------------------------------
#
# GUIDING RULE FOR THIS FEATURE: this code is a detector. Its only job is to
# say "here is what is missing". Anywhere it cannot tell whether something is
# missing, it must say so (refuse, or raise) -- never silently fold "I don't
# know" into "nothing to report". A false all-clear from the one command
# whose entire purpose is finding gaps is worse than no answer at all. This
# has already gone wrong four times in this feature's history: sys.tables
# itself cannot see an undiscovered table (the reason this cross-check exists
# at all), a missing DMV row was once read as "never synced", an inconclusive
# cross-check briefly looked identical to a clean one, and a malformed OneLake
# API response used to be normalised to an empty page instead of raising (see
# _iter_onelake_table_api). One necessary exception: a *present but genuinely
# empty* collection -- a schema with zero tables, an endpoint with zero rows
# in its catalog -- is a real, valid "no gap here" answer, not an unknown.
# Only an absent, wrong-typed, or malformed collection is a failure; don't
# let this rule be misread as "never return an empty result".
#
# tables.list_table_sync_status (TDS-only) lists tables from sys.tables, which
# on a SQL Analytics Endpoint is itself populated by the metadata sync -- a
# Lakehouse Delta table whose discovery has not completed, or has failed, has
# no sys.tables row and so is invisible to that function no matter what filter
# is passed. The functions below close that gap by reading the backing
# Lakehouse's OWN table inventory directly, bypassing the endpoint's metadata
# sync entirely.
#
# Resolving the endpoint back to its backing item: there is no reverse link on
# GET /sqlEndpoints/{id} itself. resolve_backing_lakehouse (_fabric_api.py)
# pages GET /workspaces/{ws}/lakehouses and matches on
# properties.sqlEndpointProperties.id -- the same scan get_endpoint already
# performs for the connection-string fallback. Returns None for anything that
# isn't a Lakehouse (a mirrored database, a mirrored warehouse, etc.), which
# this module cannot enumerate tables for at all -- there is no
# "list source tables" REST API for those item kinds, so a SQL-endpoint whose
# backing item is one of them keeps today's catalog-only coverage, degraded
# but stated rather than silently claimed complete
# (LakehouseDiscoveryStatus.NOT_LAKEHOUSE_BACKED).
#
# Two Lakehouse table-listing APIs, kept deliberately separate:
#
# - Classic (non-schema-enabled) Lakehouse: GET
#   /workspaces/{ws}/lakehouses/{id}/tables ("Lakehouse - List Tables")
#   returns {name, type, format, location} per table -- no schema field,
#   which is fine because a non-schema-enabled Lakehouse only ever has the
#   implicit "dbo" schema.
# - Schema-enabled Lakehouse: the classic API gives no schema attribution at
#   all, so a bare name it returns cannot be trusted to belong to any
#   particular schema. Microsoft's OneLake table APIs overview instead
#   documents a Unity-Catalog-compatible API family, hosted at
#   onelake.table.fabric.microsoft.com, that returns schema and table
#   listings WITH schema attribution, and accepts the same STORAGE_SCOPE
#   bearer token already proven end to end by onelake_upload_file
#   (services/load.py) -- see HttpBase.ONELAKE_TABLE_API.
#
# Both paths are kept rather than pointing every Lakehouse at the newer API
# (whose docs say it also serves a fixed "dbo" schema for a non-schema-enabled
# Lakehouse, which could unify the two into one call): the classic "List
# Tables" API is GA and already working today, while the OneLake table API is
# preview, with no documented GA date and no documented throttling policy.
# Replacing a working GA path with a preview one for a modest simplification
# would put existing behaviour at risk of an undocumented upstream change.
# Do not collapse this duplication without re-confirming the OneLake table
# API has graduated to GA.


class LakehouseDiscoveryStatus(StrEnum):
    """Outcome of :func:`find_undiscovered_lakehouse_tables`."""

    #: The comparison ran; ``LakehouseDiscoveryGap.missing_tables`` and
    #: ``case_mismatched_tables`` hold the (possibly empty) result.
    OK = "ok"
    #: *endpoint_id* has no matching Lakehouse in the workspace's ``/lakehouses``
    #: listing -- it backs something else (a mirrored database, a mirrored
    #: warehouse, etc.), or its parent Lakehouse has since been deleted. There is
    #: no REST API this codebase can use to enumerate that item kind's tables.
    NOT_LAKEHOUSE_BACKED = "not_lakehouse_backed"


@dataclass(frozen=True)
class LakehouseDiscoveryGap:
    """Result of comparing a Lakehouse's table inventory against a known set.

    Attributes:
        status: Which of the two outcomes in :class:`LakehouseDiscoveryStatus`
            applies.
        missing_tables: ``(schema_name, name)`` pairs present in the
            Lakehouse's own table inventory that have NO match, exact or
            otherwise, in that schema's entry in ``known_names_by_schema``
            passed to :func:`find_undiscovered_lakehouse_tables`. A Lakehouse
            schema absent from ``known_names_by_schema`` entirely is treated
            as having an empty known set -- every one of its tables is
            reported missing, never silently skipped. Only ever non-empty
            when ``status is LakehouseDiscoveryStatus.OK``.
        case_mismatched_tables: ``(schema_name, lakehouse_name, catalog_name)``
            triples for a Lakehouse table that matches a name in that same
            schema's known set case-insensitively but not exactly -- e.g.
            Lakehouse ``dbo.FactSales`` against catalog ``dbo.factsales``.
            Reported separately from ``missing_tables`` rather than folded
            into it: on Fabric's case-sensitive default collation these ARE
            two distinct possible identifiers, so calling this "missing"
            would overstate the finding, but an exact-only comparison that
            dropped it silently would hide real casing drift just as badly as
            the case-insensitive comparison this replaced did. Only ever
            non-empty when ``status is LakehouseDiscoveryStatus.OK``.
    """

    status: LakehouseDiscoveryStatus
    missing_tables: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    case_mismatched_tables: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)


async def list_lakehouse_table_names(
    http: FabricHttpClient,
    workspace_id: UUID,
    lakehouse_id: UUID,
) -> list[str]:
    """Return every table name in a Lakehouse via the classic "List Tables" REST API.

    Pages ``GET /workspaces/{ws}/lakehouses/{id}/tables``. The response array
    lives under the ``"data"`` key (not the usual ``"value"``) and each entry
    carries ``name``, ``type`` (``Managed``/``External``), ``format``, and
    ``location`` -- no schema attribution. Only meaningful for a
    non-schema-enabled Lakehouse, whose single implicit schema is ``dbo``: see
    :func:`find_undiscovered_lakehouse_tables`, the only caller, which
    branches on ``LakehouseMatch.default_schema`` before choosing this API
    over :func:`list_lakehouse_schema_names` / :func:`list_lakehouse_schema_table_names`.

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


def _parse_onelake_page_body(resp: httpx.Response, path: str) -> dict[str, object]:
    """Parse and validate one OneLake table API page body, failing closed.

    Extracted from :func:`_iter_onelake_table_api` so that "the body must be a
    JSON object" is checked once, in one place, and independently testable.
    Raises :class:`~fabric_dw.exceptions.FabricServerError` rather than
    normalising a bad body to ``{}`` -- see the guiding rule in the module
    comment above.
    """
    try:
        body = resp.json()
    except ValueError as exc:
        raise FabricServerError(
            f"OneLake table API response for {path} was not valid JSON: {exc}"
        ) from exc
    if not isinstance(body, dict):
        raise FabricServerError(
            f"OneLake table API response for {path} was not a JSON object "
            f"(got {type(body).__name__})"
        )
    return body


def _extract_onelake_items(
    body: dict[str, object],
    path: str,
    *,
    key: str,
    required_fields: tuple[str, ...],
) -> list[dict[str, object]]:
    """Validate and return the *key* collection from a parsed OneLake page body.

    Fails closed (see the module's guiding rule): the *key* collection must
    be present and a list, and every entry must be an object carrying a
    truthy value for each of *required_fields*. An empty list is a valid,
    non-error result -- only an absent, wrong-typed, or malformed collection
    raises :class:`~fabric_dw.exceptions.FabricServerError`.
    """
    if key not in body:
        raise FabricServerError(
            f"OneLake table API response for {path} is missing the {key!r} key "
            f"(keys present: {sorted(body.keys())!r})"
        )
    raw_items = body[key]
    if not isinstance(raw_items, list):
        raise FabricServerError(
            f"OneLake table API response for {path} has a non-list {key!r} value "
            f"(got {type(raw_items).__name__})"
        )

    items: list[dict[str, object]] = []
    for i, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise FabricServerError(
                f"OneLake table API response for {path} has a non-object entry at "
                f"{key}[{i}] (got {type(raw_item).__name__})"
            )
        missing_fields = [f for f in required_fields if not raw_item.get(f)]
        if missing_fields:
            raise FabricServerError(
                f"OneLake table API response for {path} has an entry at {key}[{i}] "
                f"missing required field(s) {missing_fields!r}: {raw_item!r}"
            )
        items.append(raw_item)
    return items


async def _iter_onelake_table_api(
    http: FabricHttpClient,
    path: str,
    params: dict[str, str],
    *,
    key: str,
    required_fields: tuple[str, ...] = ("name",),
) -> AsyncIterator[dict[str, object]]:
    """Page through a OneLake table API (Unity-Catalog-compatible) listing.

    A sibling to :meth:`FabricHttpClient.iter_paginated`, not a variant of
    it: that method is hardwired to Fabric's own ``continuationUri``
    pagination shape, which this host does not use.

    ``max_results`` / ``page_token`` request parameters and the response's
    ``next_page_token`` are inferred from the open-source Unity Catalog REST
    spec that Microsoft's OneLake table APIs overview says this endpoint is
    *compatible with* -- Microsoft's own documentation never shows the
    request-side parameters or a multi-page response for this preview API, so
    treat these names as best-effort, not confirmed.

    Fails closed on anything that is not a well-formed page, rather than
    normalising it to "no items" (see the guiding rule in this module's
    docstring comment above -- this is the fourth time a variant of that
    mistake has come up in this feature). A response that is
    not valid JSON, is not a JSON object, is missing the *key* collection
    entirely, has a non-list value for *key*, or has an entry missing one of
    *required_fields* all raise :class:`~fabric_dw.exceptions.FabricServerError`
    instead of silently degrading to ``{}`` / ``[]``. Against a preview API
    with no documented GA date, response-shape drift is a realistic scenario,
    not a hypothetical, and a degraded 200 must never look like "nothing to
    report" from the one function whose entire purpose is finding what is
    missing.

    A *present but empty* collection (``{"tables": []}``, a schema that
    genuinely has no tables yet) is NOT an error -- it is a real, valid
    answer, and must keep yielding zero items without raising. Only an
    *absent or wrong-typed* collection, or a malformed entry inside it, is a
    failure.

    Also never truncates silently across pages: a response carries a
    ``next_page_token``, but if the follow-up request does not actually make
    progress -- the same token comes back, or the exact same items come back
    again -- returning what has been collected so far would silently produce
    a partial inventory. So this also raises ``FabricServerError`` rather
    than returning early in that case.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        path: The path (including the ``/delta/...`` prefix) to request.
        params: Query parameters sent on every page (e.g. ``catalog_name``,
            ``schema_name``); a copy is taken per page so ``page_token`` can
            be added without mutating the caller's dict.
        key: The JSON key whose list value contains the items for this
            listing (``"schemas"`` or ``"tables"``).
        required_fields: Field names every entry in *key* must carry a
            truthy value for (e.g. ``("name", "schema_name")`` for a tables
            listing, since schema attribution is the entire reason this API
            is used over the classic one). Defaults to ``("name",)``.

    Yields:
        Individual items from the *key* array in each page.

    Raises:
        FabricServerError: If the response is malformed in any of the ways
            described above, or if pagination does not make progress across
            a page boundary.
    """
    page_token: str | None = None
    seen_tokens: set[str] = set()
    previous_identities: frozenset[tuple[object, object]] | None = None

    while True:
        page_params = dict(params)
        if page_token is not None:
            page_params["page_token"] = page_token
        resp = await http.request(
            "GET",
            HttpBase.ONELAKE_TABLE_API,
            path,
            params=page_params,
            scope=STORAGE_SCOPE,
        )
        body = _parse_onelake_page_body(resp, path)
        items = _extract_onelake_items(body, path, key=key, required_fields=required_fields)

        raw_next_token = body.get("next_page_token")
        next_token = str(raw_next_token) if raw_next_token else None

        if next_token:
            identities = frozenset((i.get("schema_name"), i.get("name")) for i in items)
            no_progress = (
                next_token == page_token
                or next_token in seen_tokens
                or (bool(identities) and identities == previous_identities)
            )
            if no_progress:
                raise FabricServerError(
                    f"OneLake table API pagination for {path} did not make progress past "
                    f"page_token {page_token!r}; refusing to return a partial inventory"
                )
            seen_tokens.add(next_token)
            previous_identities = identities

        for item in items:
            yield {str(k): v for k, v in item.items()}

        if not next_token:
            return
        page_token = next_token


async def list_lakehouse_schema_names(
    http: FabricHttpClient,
    workspace_id: UUID,
    lakehouse_id: UUID,
) -> list[str]:
    """Return every schema name in a schema-enabled Lakehouse.

    Calls the Unity-Catalog-compatible ``GET .../unity-catalog/schemas``
    endpoint documented in Microsoft's OneLake table APIs overview, treating
    the Lakehouse itself as the catalog: ``catalog_name`` is the Lakehouse's
    own GUID (the same GUID addressing
    :func:`~fabric_dw._fabric_api.resolve_backing_lakehouse` already gives us).

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the Lakehouse.
        lakehouse_id: The UUID of the schema-enabled Lakehouse.

    Returns:
        A (possibly empty) list of schema names, in API response order.
    """
    names: list[str] = []
    async for item in _iter_onelake_table_api(
        http,
        f"/delta/{workspace_id}/{lakehouse_id}/api/2.1/unity-catalog/schemas",
        {"catalog_name": str(lakehouse_id)},
        key="schemas",
    ):
        name = item.get("name")
        if name:
            names.append(str(name))
    return names


async def list_lakehouse_schema_table_names(
    http: FabricHttpClient,
    workspace_id: UUID,
    lakehouse_id: UUID,
    schema_name: str,
) -> list[str]:
    """Return every table name in one schema of a schema-enabled Lakehouse.

    Calls the Unity-Catalog-compatible ``GET .../unity-catalog/tables``
    endpoint, filtered to *schema_name*. See :func:`list_lakehouse_schema_names`
    for the shared ``catalog_name`` addressing and API-maturity caveats.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the Lakehouse.
        lakehouse_id: The UUID of the schema-enabled Lakehouse.
        schema_name: The schema to list tables for (as returned by
            :func:`list_lakehouse_schema_names`).

    Returns:
        A (possibly empty) list of bare table names, in API response order.
    """
    names: list[str] = []
    async for item in _iter_onelake_table_api(
        http,
        f"/delta/{workspace_id}/{lakehouse_id}/api/2.1/unity-catalog/tables",
        {"catalog_name": str(lakehouse_id), "schema_name": schema_name},
        key="tables",
        required_fields=("name", "schema_name"),
    ):
        name = item.get("name")
        if name:
            names.append(str(name))
    return names


async def _list_schema_enabled_lakehouse_tables(
    http: FabricHttpClient,
    workspace_id: UUID,
    lakehouse_id: UUID,
) -> dict[str, list[str]]:
    """Return ``{schema_name: [table_name, ...]}`` for a schema-enabled Lakehouse.

    One call to list schemas, then one call per schema to list its tables --
    the OneLake table API has no single "everything" listing. Sequential
    rather than fanned out: this cross-check is opt-in and already the most
    expensive read path in this module, and concurrency here would only
    change how the existing rate limiter's budget is spent, not the total
    request count.
    """
    result: dict[str, list[str]] = {}
    for schema_name in await list_lakehouse_schema_names(http, workspace_id, lakehouse_id):
        result[schema_name] = await list_lakehouse_schema_table_names(
            http, workspace_id, lakehouse_id, schema_name
        )
    return result


async def find_undiscovered_lakehouse_tables(
    http: FabricHttpClient,
    workspace_id: UUID,
    endpoint_id: UUID,
    known_names_by_schema: Mapping[str, frozenset[str]],
) -> LakehouseDiscoveryGap:
    """Find Lakehouse tables missing from a SQL endpoint's ``sys.tables`` catalog.

    This function, and everything it calls, is a detector: see the guiding
    rule at the top of this module's "Lakehouse discovery-gap cross-check"
    comment block before changing any of its error handling. In short,
    absence of data must never be silently converted into absence of a gap
    -- except that a present, genuinely empty collection IS a valid answer.

    Resolves *endpoint_id* to its backing Lakehouse via
    :func:`~fabric_dw._fabric_api.resolve_backing_lakehouse`, refuses the
    comparison when nothing but a Lakehouse could pair with the endpoint (see
    :class:`LakehouseDiscoveryStatus`), and otherwise lists the Lakehouse's
    tables and compares each one against *known_names_by_schema*, per schema.

    Two listing paths, chosen by whether the backing Lakehouse has schema
    support enabled (see the module comment above for why both are kept):

    * Schema-enabled (``lakehouse.default_schema is not None``): every schema
      via :func:`list_lakehouse_schema_names`, and every schema's tables via
      :func:`list_lakehouse_schema_table_names`.
    * Classic (single implicit ``dbo`` schema): :func:`list_lakehouse_table_names`,
      compared against ``known_names_by_schema.get("dbo", frozenset())``.

    The comparison is **exact (case-sensitive)** within each schema, matching
    Fabric's default collation (``FABRIC_DEFAULT_COLLATION`` in ``models.py``,
    ``Latin1_General_100_BIN2_UTF8``, which Microsoft documents as
    case-sensitive). A case-insensitive comparison would silently hide the
    exact kind of drift this check exists to catch: on the default collation,
    ``FactSales`` and ``factsales`` are two distinct, independently valid
    table names, so folding them together would let a genuinely undiscovered
    ``FactSales`` disappear behind an unrelated ``factsales`` already in the
    catalog. Exact comparison is the safer default even for a workspace
    configured with a case-insensitive collation: it can produce a false
    positive (a table reported missing that the engine would actually treat
    as identical to an existing one), which is visible and judgeable by the
    caller, never a false negative, which is invisible. This function has no
    cheap way to read the endpoint's actual collation (it is TDS-only
    information; the SQLEndpoint REST resource does not expose it -- see
    ``list_endpoints``'s docstring above), so it does not attempt to branch on
    it. A schema present in the Lakehouse but absent from
    *known_names_by_schema* entirely is treated the same as a schema whose
    known set happens to be empty: every one of its tables is reported
    missing, never silently skipped.

    A Lakehouse table that fails the exact match but matches a
    *known_names_by_schema* entry (within the same schema) case-insensitively
    is reported separately, in ``case_mismatched_tables``, rather than folded
    into ``missing_tables``: it is a more specific and more useful finding
    (a likely casing drift) than a flat "missing" would be.

    This costs at least one extra REST call beyond ``list_table_sync_status``'s
    single TDS query (a lakehouse scan, plus one or more paginated listing
    calls), so callers on a command that may run repeatedly should make this
    opt-in rather than call it unconditionally -- see ``tables sync-status
    --check-lakehouse`` / ``list_table_sync_status(check_lakehouse=True)``.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint to check.
        known_names_by_schema: Table names already known to be present in the
            endpoint's catalog, keyed by schema name (typically built from
            every row already returned by ``list_table_sync_status`` for this
            endpoint, grouped by ``schema_name``).

    Returns:
        A :class:`LakehouseDiscoveryGap` describing the outcome.
    """
    lakehouse = await resolve_backing_lakehouse(http, workspace_id, endpoint_id)
    if lakehouse is None:
        return LakehouseDiscoveryGap(status=LakehouseDiscoveryStatus.NOT_LAKEHOUSE_BACKED)

    if lakehouse.default_schema is not None:
        tables_by_schema = await _list_schema_enabled_lakehouse_tables(
            http, workspace_id, lakehouse.id
        )
    else:
        # Classic Lakehouse: a single implicit "dbo" schema -- see the module
        # comment above for why this stays on the GA "List Tables" API rather
        # than the OneLake table API used for the schema-enabled case.
        classic_names = await list_lakehouse_table_names(http, workspace_id, lakehouse.id)
        tables_by_schema = {"dbo": classic_names}

    missing: list[tuple[str, str]] = []
    case_mismatched: list[tuple[str, str, str]] = []
    for schema_name, table_names in tables_by_schema.items():
        known = known_names_by_schema.get(schema_name, frozenset())
        # Case-insensitive lookup is used ONLY to distinguish "no match at
        # all" from "a case-only mismatch" below -- never to decide
        # "found"/"not found" on its own, which is exactly the bug this
        # replaces (see the docstring above).
        known_by_casefold: dict[str, str] = {}
        for known_name in known:
            known_by_casefold.setdefault(known_name.casefold(), known_name)

        for name in table_names:
            if name in known:
                continue
            catalog_match = known_by_casefold.get(name.casefold())
            if catalog_match is not None:
                case_mismatched.append((schema_name, name, catalog_match))
            else:
                missing.append((schema_name, name))

    return LakehouseDiscoveryGap(
        status=LakehouseDiscoveryStatus.OK,
        missing_tables=tuple(missing),
        case_mismatched_tables=tuple(case_mismatched),
    )


#: Shared wording for the CLI (ClickException) and MCP (ToolError) "the
#: cross-check was explicitly requested but could not run" error -- both
#: surfaces funnel a bare ValueError into their own presentation error type
#: (see :func:`apply_lakehouse_discovery_gap`), so one message serves both.
_DISCOVERY_GAP_NOT_LAKEHOUSE_BACKED_MSG = (
    "the Lakehouse discovery-gap cross-check could not run: this endpoint's "
    "backing item could not be resolved to a Lakehouse (it may be backed by "
    "a mirrored database or similar)."
)


async def apply_lakehouse_discovery_gap(
    http: FabricHttpClient,
    workspace_id: UUID,
    endpoint_id: UUID,
    items: list[TableMetadataSyncStatus],
) -> list[TableMetadataSyncStatus]:
    """Cross-reference the backing Lakehouse and append discovery-gap rows to *items*.

    Shared by the CLI (``tables sync-status --check-lakehouse``) and MCP
    (``list_table_sync_status(check_lakehouse=True)``) surfaces, which used to
    each carry a near-identical copy of this logic (#1060). Both call sites
    already funnel a bare :class:`ValueError` into their own presentation
    error type (``click.ClickException`` / ``ToolError``), so this raises the
    plain, surface-agnostic exception rather than either one directly.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.
        workspace_id: The UUID of the workspace containing the endpoint.
        endpoint_id: The UUID of the SQL analytics endpoint being reported on.
        items: The catalog-only rows already returned by
            ``tables.list_table_sync_status`` for this endpoint.

    Returns:
        *items* plus one synthetic ``in_endpoint_catalog=False`` row per
        Lakehouse table the cross-check found missing (or case-mismatched),
        sorted by ``(schema_name, name)``.

    Raises:
        ValueError: If the cross-check cannot run at all (no matching
            Lakehouse), rather than silently returning *items* unchanged --
            the caller explicitly asked for the cross-check, so "no extra
            rows" must never be readable as "fully discovered".
    """
    known_by_schema: dict[str, set[str]] = {}
    for t in items:
        known_by_schema.setdefault(t.schema_name, set()).add(t.name)
    known_names_by_schema = {schema: frozenset(names) for schema, names in known_by_schema.items()}

    gap = await find_undiscovered_lakehouse_tables(
        http, workspace_id, endpoint_id, known_names_by_schema
    )
    if gap.status == LakehouseDiscoveryStatus.NOT_LAKEHOUSE_BACKED:
        raise ValueError(_DISCOVERY_GAP_NOT_LAKEHOUSE_BACKED_MSG)
    if not gap.missing_tables and not gap.case_mismatched_tables:
        return items
    extra = [
        TableMetadataSyncStatus(
            schema_name=schema_name,
            name=name,
            qualified_name=f"{schema_name}.{name}",
            last_update_time_utc=None,
            latest_log_version=None,
            latest_checkpoint_version=None,
            is_blocked=None,
            in_endpoint_catalog=False,
        )
        for schema_name, name in gap.missing_tables
    ] + [
        TableMetadataSyncStatus(
            schema_name=schema_name,
            name=lakehouse_name,
            qualified_name=f"{schema_name}.{lakehouse_name}",
            last_update_time_utc=None,
            latest_log_version=None,
            latest_checkpoint_version=None,
            is_blocked=None,
            in_endpoint_catalog=False,
            case_mismatched_catalog_name=catalog_name,
        )
        for schema_name, lakehouse_name, catalog_name in gap.case_mismatched_tables
    ]
    return sorted([*items, *extra], key=lambda t: (t.schema_name, t.name))
