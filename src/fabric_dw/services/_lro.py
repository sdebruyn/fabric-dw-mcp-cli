"""Shared LRO (Long-Running Operation) ID-extraction helper for Fabric DW services.

After a Fabric LRO completes, the created resource ID can appear in up to three
different places depending on the API version and response shape:

- **Path A** — the LRO status body contains ``resourceId``, ``createdItemId``,
  or ``itemId`` directly.
- **Path B** — the LRO status body has no ID; the ``GET /operations/{id}/result``
  sub-endpoint returns the created item (under the key ``"id"``).
- **Path C** — last resort: list all items of the relevant type and return the one
  that matches a supplied predicate (typically the newest user-defined item).

Use :func:`resolve_lro_item_id` to encode this three-path fallback **once** with
named constants for retry behaviour.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TypeVar
from urllib.parse import urlparse
from uuid import UUID

from fabric_dw.exceptions import FabricServerError, NotFoundError
from fabric_dw.http_client import FabricHttpClient

__all__ = [
    "LRO_DETAIL_WAIT_S",
    "LRO_MAX_DETAIL_RETRIES",
    "extract_operation_id",
    "resolve_lro_item_id",
]

_logger = logging.getLogger("fabric_dw.lro")

# ---------------------------------------------------------------------------
# Named constants — previously scattered as bare literals across services
# ---------------------------------------------------------------------------

#: Maximum number of times to retry a detail-GET when the provisioning lag
#: means ``parentWarehouseId`` is not yet populated after the LRO completes.
LRO_MAX_DETAIL_RETRIES: int = 5

#: Seconds to wait between each detail-GET retry.
LRO_DETAIL_WAIT_S: float = 3.0

#: Maximum number of times to retry ``GET /operations/{id}/result`` on 404.
#: Fabric's ``/result`` endpoint can transiently return 404 for a short window
#: immediately after the operation status transitions to ``Succeeded``.
_LRO_RESULT_404_MAX_RETRIES: int = 2

#: Seconds to wait between 404 retries on the ``/result`` endpoint.
_LRO_RESULT_404_WAIT_S: float = 1.5

T = TypeVar("T")


def extract_operation_id(location: str) -> str:
    """Parse the LRO operation UUID from a Fabric ``Location`` header URL.

    Fabric LRO ``Location`` headers have the form
    ``https://api.fabric.microsoft.com/v1/operations/{operationId}`` (possibly with a
    trailing slash or query string).  This helper strips any query/fragment, takes the
    last non-empty path segment, and validates that it is a UUID.

    Args:
        location: The ``Location`` header value returned on a 202 response.

    Returns:
        The operation UUID as a string (lower-case, hyphen-separated).

    Raises:
        FabricServerError: If the last path segment cannot be parsed as a UUID.
    """
    parsed = urlparse(location)
    # Remove query/fragment and split on '/', dropping empty segments from trailing slash.
    segments = [seg for seg in parsed.path.split("/") if seg]
    if not segments:
        msg = f"LRO: cannot parse operation id from Location URL (no path segments): {location!r}"
        raise FabricServerError(msg)
    candidate = segments[-1]
    try:
        return str(UUID(candidate))
    except ValueError:
        msg = f"LRO: last path segment {candidate!r} of Location URL is not a UUID: {location!r}"
        raise FabricServerError(msg) from None


async def resolve_lro_item_id(
    http: FabricHttpClient,
    *,
    operation_result: dict[str, object],
    location: str,
    result_id_keys: tuple[str, ...] = ("resourceId", "createdItemId", "itemId"),
) -> str | None:
    """Attempt to resolve the created item's ID from an LRO result, in priority order.

    Tries **Path A** then **Path B**.  Returns ``None`` if neither path yields an ID
    (caller is responsible for implementing **Path C** if needed).

    **Path A** — look for *resource-specific* ID keys in the LRO status body
    (``resourceId``, ``createdItemId``, ``itemId``, or caller-supplied keys).
    The generic ``"id"`` key is intentionally excluded from the default set
    because a status body ``"id"`` field is often the *operation* ID, not the
    *resource* ID, and would incorrectly short-circuit Path B.

    **Path B** — call ``GET /operations/{op_id}/result`` and look for ``"id"``.
    This is the correct place to resolve the generic ``"id"`` field because the
    ``/result`` sub-endpoint returns the *created resource*, not the operation.
    A bounded retry (up to :data:`_LRO_RESULT_404_MAX_RETRIES` attempts with
    :data:`_LRO_RESULT_404_WAIT_S` delay) handles the transient 404 window that
    Fabric can return immediately after a ``Succeeded`` status.  If 404 persists,
    :class:`~fabric_dw.exceptions.FabricServerError` is raised with context.

    Args:
        http: Authenticated Fabric HTTP client.
        operation_result: The dict returned by
            :meth:`~fabric_dw.http_client.FabricHttpClient.poll_operation`.
        location: The ``Location`` header from the original LRO response (used
            to derive the operation ID for the ``/result`` sub-call).
        result_id_keys: Keys to probe in the status body for Path A.

    Returns:
        The created resource ID as a string, or ``None`` if both paths failed.

    Raises:
        FabricServerError: If the ``/result`` endpoint returns 404 after all
            retries are exhausted (operation result not yet available).
    """
    # Path A: check the status body directly.
    for key in result_id_keys:
        raw = operation_result.get(key)
        if raw is not None:
            return str(raw)

    # Path B: fall back to the /result sub-endpoint with bounded 404 retry.
    op_id = extract_operation_id(location)
    last_exc: NotFoundError | None = None
    for attempt in range(_LRO_RESULT_404_MAX_RETRIES + 1):
        if attempt > 0:
            _logger.warning(
                "LRO %s: GET /result returned 404 (attempt %d/%d) — "
                "Fabric result endpoint not yet available; retrying in %ss",
                op_id,
                attempt,
                _LRO_RESULT_404_MAX_RETRIES,
                _LRO_RESULT_404_WAIT_S,
            )
            await asyncio.sleep(_LRO_RESULT_404_WAIT_S)
        try:
            lro_result = await http.get_operation_result(op_id)
        except NotFoundError as exc:
            last_exc = exc
            continue
        result_id_raw = lro_result.get("id")
        if result_id_raw is not None:
            return str(result_id_raw)
        return None

    # All retries exhausted — surface a clear error instead of a confusing NotFoundError.
    msg = (
        f"LRO {op_id}: GET /operations/{op_id}/result returned 404 after "
        f"{_LRO_RESULT_404_MAX_RETRIES + 1} attempt(s) — operation result not yet available"
    )
    raise FabricServerError(msg) from last_exc
