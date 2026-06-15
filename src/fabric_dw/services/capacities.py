"""Service helpers for Microsoft Fabric capacity state.

Provides a lightweight helper that fetches ``GET /v1/capacities`` and builds a
``capacityId -> state`` mapping used by ``-A`` workspace scans to skip
workspaces whose capacity is paused or suspended **before** issuing the
data-plane call that would otherwise hang for ~22s and return a generic 500.

Only the minimal fields (``id``, ``displayName``, ``sku``, ``state``) are
modelled; extra API fields are silently ignored.
"""

from __future__ import annotations

import logging

from fabric_dw.exceptions import PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient, HttpBase

__all__ = ["get_capacity_states"]

_logger = logging.getLogger("fabric_dw.capacities")

# The state value that indicates an active, healthy capacity.
ACTIVE_STATE: str = "Active"


async def get_capacity_states(http: FabricHttpClient) -> dict[str, str] | None:
    """Return a mapping of lower-cased capacity ID to state string.

    Fetches ``GET /v1/capacities`` and builds a ``{capacity_id_lower: state}``
    dict.  Capacity IDs are lower-cased so callers can normalise workspace
    ``capacityId`` values (which may be returned in any case) before lookup.

    Returns ``None`` when the caller lacks the ``Capacity.Read.All`` permission
    (HTTP 403) — in that case, the proactive filtering cannot run and the
    caller should fall back to the defensive per-workspace error handling.

    Args:
        http: An authenticated :class:`~fabric_dw.http_client.FabricHttpClient`.

    Returns:
        A ``dict[str, str]`` mapping lower-cased capacity UUIDs to their
        ``state`` strings (e.g. ``"Active"`` or ``"Inactive"``), or ``None``
        if the caller is not permitted to read capacities.
    """
    try:
        result: dict[str, str] = {}
        async for item in http.iter_paginated(HttpBase.FABRIC, "/capacities"):
            raw_id = item.get("id")
            state = item.get("state")
            if isinstance(raw_id, str) and isinstance(state, str):
                result[raw_id.lower()] = state
    except PermissionDeniedError:
        _logger.debug(
            "GET /v1/capacities returned 403 — proactive capacity filtering unavailable; "
            "falling back to defensive per-workspace error handling"
        )
        return None
    _logger.debug("fetched capacity states for %d capacities", len(result))
    return result
