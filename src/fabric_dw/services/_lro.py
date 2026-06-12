"""Shared LRO (Long-Running Operation) ID-extraction helper for Fabric DW services.

After a Fabric LRO completes, the created resource ID can appear in up to three
different places depending on the API version and response shape:

- **Path A** — the LRO status body contains ``resourceId``, ``createdItemId``,
  or ``itemId`` directly.
- **Path B** — the LRO status body has no ID; the ``GET /operations/{id}/result``
  sub-endpoint returns the created item.
- **Path C** — last resort: list all items of the relevant type and return the one
  that matches a supplied predicate (typically the newest user-defined item).

Use :func:`extract_created_id` to encode this three-path fallback **once** with
named constants for retry behaviour.
"""

from __future__ import annotations

from typing import TypeVar

from fabric_dw.http_client import FabricHttpClient

__all__ = ["LRO_DETAIL_WAIT_S", "LRO_MAX_DETAIL_RETRIES", "extract_operation_id"]

# ---------------------------------------------------------------------------
# Named constants — previously scattered as bare literals across services
# ---------------------------------------------------------------------------

#: Maximum number of times to retry a detail-GET when the provisioning lag
#: means ``parentWarehouseId`` is not yet populated after the LRO completes.
LRO_MAX_DETAIL_RETRIES: int = 5

#: Seconds to wait between each detail-GET retry.
LRO_DETAIL_WAIT_S: float = 3.0

T = TypeVar("T")


def extract_operation_id(location: str) -> str:
    """Parse the operation ID from a Fabric LRO ``Location`` header value.

    Fabric ``Location`` headers follow the pattern::

        https://api.fabric.microsoft.com/v1/operations/{op_id}

    Args:
        location: The full ``Location`` header string.

    Returns:
        The operation ID (the last path segment of *location*).
    """
    return location.rsplit("/", 1)[-1]


async def resolve_lro_item_id(
    http: FabricHttpClient,
    *,
    operation_result: dict[str, object],
    location: str,
    result_id_keys: tuple[str, ...] = ("resourceId", "createdItemId", "itemId", "id"),
) -> str | None:
    """Attempt to resolve the created item's ID from an LRO result, in priority order.

    Tries **Path A** then **Path B**.  Returns ``None`` if neither path yields an ID
    (caller is responsible for implementing **Path C** if needed).

    **Path A** — look for known ID keys in the LRO status body.

    **Path B** — call ``GET /operations/{op_id}/result`` and look for ``"id"``.

    Args:
        http: Authenticated Fabric HTTP client.
        operation_result: The dict returned by
            :meth:`~fabric_dw.http_client.FabricHttpClient.poll_operation`.
        location: The ``Location`` header from the original LRO response (used
            to derive the operation ID for the ``/result`` sub-call).
        result_id_keys: Keys to probe in the status body for Path A.

    Returns:
        The created resource ID as a string, or ``None`` if both paths failed.
    """
    # Path A: check the status body directly.
    for key in result_id_keys:
        raw = operation_result.get(key)
        if raw is not None:
            return str(raw)

    # Path B: fall back to the /result sub-endpoint.
    op_id = extract_operation_id(location)
    lro_result = await http.get_operation_result(op_id)
    result_id_raw = lro_result.get("id")
    if result_id_raw is not None:
        return str(result_id_raw)

    return None
