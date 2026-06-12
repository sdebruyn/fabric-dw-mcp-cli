"""Async HTTP client for Microsoft Fabric and Power BI REST APIs.

Provides:
- Global rate-limiting via aiolimiter (default 2 RPS).
- 429 Retry-After handling with a monotonic deadline (``_pause_until``).
- 5xx retry with tenacity exponential back-off (max 3 attempts).
- Standard error mapping (401 -> AuthError, 403 -> PermissionDeniedError, 404 -> NotFoundError).
- continuationUri pagination.
- LRO (202 + Location) polling with jitter.

Retry arithmetic
~~~~~~~~~~~~~~~~
Tenacity wraps ``_request_with_retry`` and retries on ``FabricServerError``
(5xx) up to 3 attempts with exponential back-off.  Inside each attempt,
``_do_request`` executes a 429-loop of up to ``max_429_retries`` iterations.
Worst-case total attempts = 3 tenacity attempts x 5 429-retries = 15 sends.
In practice the 429-loop resets its counter on any non-429 response, so the
two mechanisms are largely independent.
"""

from __future__ import annotations

import asyncio
import datetime
import http
import logging
import random
import time as _time
from collections.abc import AsyncIterator, Mapping
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from fabric_dw import auth
from fabric_dw.exceptions import (
    AuthError,
    BadRequestError,
    FabricError,
    FabricServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
)
from fabric_dw.logging import redact_auth_header

_logger = logging.getLogger("fabric_dw.http")

__all__ = [
    "FabricHttpClient",
    "HttpBase",
    "_parse_retry_after",
]

# Module-level defaults (used as constructor defaults)
_DEFAULT_RPS: int = 2
_DEFAULT_TIMEOUT: float = 30.0
_MAX_429_RETRIES: int = 5
_DEFAULT_POLL_INTERVAL: float = 2.0
_TOKEN_REFRESH_BUFFER: float = 300.0  # seconds before expiry to refresh

# Status code → exception class mapping (4xx errors only; 5xx handled separately)
_STATUS_TO_EXC: dict[int, type[FabricError]] = {
    http.HTTPStatus.UNAUTHORIZED: AuthError,
    http.HTTPStatus.FORBIDDEN: PermissionDeniedError,
    http.HTTPStatus.NOT_FOUND: NotFoundError,
}


class HttpBase(StrEnum):
    """Base URLs for Fabric and Power BI REST APIs."""

    FABRIC = "https://api.fabric.microsoft.com/v1"
    POWERBI = "https://api.powerbi.com/v1.0/myorg"


def _parse_retry_after(value: str) -> float:
    """Parse a Retry-After header value into a wait duration in seconds.

    Handles both the integer-seconds form and the HTTP-date form
    (RFC 7231 section 7.1.3).

    Args:
        value: The raw Retry-After header value.

    Returns:
        Number of seconds to wait as a float (>= 0).
    """
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass

    # Try HTTP-date form, e.g. "Wed, 21 Oct 2026 07:28:00 GMT"
    retry_dt = parsedate_to_datetime(value)
    now = datetime.datetime.now(tz=datetime.UTC)
    delta = (retry_dt - now).total_seconds()
    return max(0.0, delta)


class FabricHttpClient:
    """Async HTTP client for Fabric and Power BI REST APIs.

    Usage::

        async with FabricHttpClient(credential) as client:
            resp = await client.request("GET", HttpBase.FABRIC, "/workspaces")

    Retry arithmetic
    ~~~~~~~~~~~~~~~~
    Tenacity wraps ``_request_with_retry`` and retries on ``FabricServerError``
    (5xx) up to 3 attempts.  Inside each tenacity attempt, ``_do_request`` runs
    a 429-loop of up to ``max_429_retries`` iterations.  Worst-case total sends
    = 3 x ``max_429_retries`` (default 15).  The 429 counter resets on any
    non-429 response, so both mechanisms are largely independent in practice.

    Args:
        credential:          Azure credential used to fetch bearer tokens.
        rps:                 Maximum requests per second (default 2).
        timeout:             HTTP request timeout in seconds (default 30.0).
        max_429_retries:     Maximum consecutive 429 responses before raising
                             ``RateLimitedError`` (default 5).
        poll_interval:       Default LRO polling interval in seconds (default 2.0).
        token_refresh_buffer: Seconds before token expiry at which a refresh is
                             triggered (default 300.0).
    """

    def __init__(  # noqa: PLR0913
        self,
        credential: AsyncTokenCredential,
        rps: int = _DEFAULT_RPS,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        max_429_retries: int = _MAX_429_RETRIES,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        token_refresh_buffer: float = _TOKEN_REFRESH_BUFFER,
    ) -> None:
        self._credential = credential
        self._limiter = AsyncLimiter(max_rate=rps, time_period=1)
        self._http: httpx.AsyncClient | None = None
        self._token: AccessToken | None = None
        self._token_lock = asyncio.Lock()
        self._timeout = timeout
        self._max_429_retries = max_429_retries
        self._poll_interval = poll_interval
        self._token_refresh_buffer = token_refresh_buffer
        # Monotonic deadline: sleep until this time before each send.
        # 0.0 means "no pause needed".
        self._pause_until: float = 0.0

    async def __aenter__(self) -> FabricHttpClient:
        self._http = httpx.AsyncClient(http2=True, timeout=self._timeout)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _get_token(self) -> str:
        """Return a valid bearer token, refreshing if close to expiry.

        A lock ensures that under concurrent calls only one refresh is
        performed even if multiple coroutines see the token as expired at
        the same time.
        """
        async with self._token_lock:
            if (
                self._token is None
                or self._token.expires_on - _time.time() < self._token_refresh_buffer
            ):
                self._token = await self._credential.get_token(auth.FABRIC_SCOPE)
        return self._token.token

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        base: HttpBase,
        path: str,
        *,
        json: object = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """Send a single HTTP request, applying rate-limiting and error handling.

        Args:
            method: HTTP method (e.g. "GET", "POST").
            base: Base URL enum value.
            path: Path to append to the base URL.
            json: Optional JSON body.
            params: Optional query parameters.

        Returns:
            The successful httpx.Response.

        Raises:
            AuthError: On 401.
            PermissionDeniedError: On 403.
            NotFoundError: On 404.
            RateLimitedError: After exactly ``max_429_retries`` consecutive 429 responses.
            FabricServerError: On persistent 5xx errors.
        """
        url = f"{base}{path}"
        return await self._request_with_retry(method, url, json=json, params=params)

    @retry(
        retry=retry_if_exception_type(FabricServerError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.1, max=2),
        reraise=True,
    )
    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json: object = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """Inner request method wrapped with tenacity 5xx retry."""
        return await self._do_request(method, url, json=json, params=params)

    async def _do_request(
        self,
        method: str,
        url: str,
        *,
        json: object = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """Execute a request with rate limiting and 429 handling.

        Delegates to:
        - ``_send_once``: waits for the pause deadline, acquires the rate
          limiter, and performs the actual HTTP send.
        - ``_map_status``: maps error status codes to typed exceptions.

        On 429: updates the shared monotonic deadline and retries up to
        ``_max_429_retries`` consecutive times before raising
        ``RateLimitedError``.
        """
        if self._http is None:
            msg = "Client must be used as an async context manager"
            raise RuntimeError(msg)

        consecutive_429 = 0

        while True:
            resp = await self._send_once(method, url, json=json, params=params)

            if resp.status_code == http.HTTPStatus.TOO_MANY_REQUESTS:
                consecutive_429 += 1
                if consecutive_429 >= self._max_429_retries:
                    raise RateLimitedError(
                        f"Received 429 {consecutive_429} consecutive times for {url}",
                        status=429,
                        request_id=resp.headers.get("x-ms-request-id"),
                    )

                retry_after_raw = resp.headers.get("Retry-After", "1")
                try:
                    wait_s = _parse_retry_after(retry_after_raw)
                except ValueError:
                    _logger.warning(
                        "Malformed Retry-After header %r; falling back to 1.0s",
                        retry_after_raw,
                    )
                    wait_s = 1.0

                # Aggregate concurrent 429s: keep the latest (furthest) deadline.
                deadline = _time.monotonic() + wait_s
                self._pause_until = max(self._pause_until, deadline)
                continue

            # Reset counter on non-429
            consecutive_429 = 0
            self._map_status(resp, url)
            return resp

    async def _send_once(
        self,
        method: str,
        url: str,
        *,
        json: object = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """Wait for any pause deadline, fetch token, acquire the limiter, and send.

        The pause-deadline wait happens first so that the token is fetched (and
        potentially refreshed) immediately before use, regardless of how long the
        429-induced pause was.  Token fetch happens BEFORE acquiring the
        rate-limiter slot so that a cache-miss refresh does not consume RPS budget.
        """
        assert self._http is not None  # noqa: S101 — enforced by _do_request

        # Honour the 429 pause deadline (aggregated across all concurrent callers).
        # This must happen before token fetch so the token is always fresh at send time.
        # Use a while loop so that if another coroutine extends _pause_until while we
        # are sleeping, we re-check and sleep again rather than waking up early.
        while True:
            now = _time.monotonic()
            remaining = self._pause_until - now
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)

        # Fetch token outside the limiter to avoid wasting RPS budget on refresh.
        token = await self._get_token()

        headers = {"Authorization": f"Bearer {token}"}

        async with self._limiter:
            t0 = _time.monotonic()
            resp = await self._http.request(
                method,
                url,
                headers=headers,
                json=json,
                params=params,
            )
            elapsed_ms = (_time.monotonic() - t0) * 1000

        if _logger.isEnabledFor(logging.DEBUG):
            safe_headers = redact_auth_header(dict(headers))
            request_id = resp.headers.get("x-ms-request-id")
            _logger.debug(
                "%s %s -> %d elapsed_ms=%.1f headers=%r",
                method,
                url,
                resp.status_code,
                elapsed_ms,
                safe_headers,
                extra={"request_id": request_id} if request_id else {},
            )

        return resp

    def _map_status(self, resp: httpx.Response, url: str) -> None:
        """Raise a typed ``FabricError`` subclass for error status codes.

        Uses ``_STATUS_TO_EXC`` for known 4xx codes (401, 403, 404); raises
        ``BadRequestError`` for any other 4xx (including 400) so that Fabric
        error details are never silently discarded; raises ``FabricServerError``
        for any 5xx response.  JSON body is parsed best-effort (parse errors
        are silently swallowed).  The ``x-ms-request-id`` header is captured
        for all raised exceptions.
        """
        status = resp.status_code
        request_id = resp.headers.get("x-ms-request-id")

        # Best-effort JSON body parse
        body: dict[str, object] | None = None
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                body = parsed
        except Exception:  # noqa: S110
            pass

        exc_class = _STATUS_TO_EXC.get(status)
        if exc_class is not None:
            raise exc_class(
                f"HTTP {status} for {url}: {resp.text}",
                status=status,
                request_id=request_id,
                body=body,
            )

        if http.HTTPStatus.BAD_REQUEST <= status < http.HTTPStatus.INTERNAL_SERVER_ERROR:
            # Unmapped 4xx — surface the full Fabric error body so callers can see
            # the errorCode/message (e.g. InvalidItemType, WorkspaceItemsLimitExceeded).
            raise BadRequestError(
                f"HTTP {status} for {url}: {resp.text}",
                status=status,
                request_id=request_id,
                body=body,
            )

        if status >= http.HTTPStatus.INTERNAL_SERVER_ERROR:
            raise FabricServerError(
                f"Server error {status} for {url}: {resp.text}",
                status=status,
                request_id=request_id,
                body=body,
            )

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def iter_paginated(
        self,
        base: HttpBase,
        path: str,
        *,
        key: str = "value",
        params: dict[str, str] | None = None,
    ) -> AsyncIterator[dict[str, object]]:
        """Iterate over all items across paginated responses.

        Follows the ``continuationUri`` field in each page until it is absent.

        Args:
            base: Base URL enum value (used only for the first request).
            path: Initial path to request.
            key: JSON key whose list value contains the items (default ``"value"``).
            params: Optional query parameters for the first request only.

        Yields:
            Individual items from the *key* array in each page.
        """
        url: str | None = f"{base}{path}"
        first = True

        while url is not None:
            # continuationUri is always a full URL; use _request_with_retry directly
            resp = await self._request_with_retry("GET", url, params=params if first else None)
            first = False
            data: dict[str, object] = resp.json()
            raw_items = data.get(key, [])
            if isinstance(raw_items, list):
                for item in raw_items:
                    if isinstance(item, dict):
                        yield {str(k): v for k, v in item.items()}

            cont = data.get("continuationUri")
            url = cont if isinstance(cont, str) else None

    # ------------------------------------------------------------------
    # LRO polling
    # ------------------------------------------------------------------

    async def get_operation_result(self, operation_id: str) -> dict[str, object]:
        """Fetch the result of a completed LRO via ``GET /v1/operations/{id}/result``.

        Microsoft Fabric LRO status bodies (from :meth:`poll_operation`) only contain
        status metadata — the created item ID is *not* included.  Once the operation has
        reached ``Succeeded``, the created item is available at a separate result endpoint.

        Args:
            operation_id: The UUID string of the LRO (from the ``x-ms-operation-id`` header
                or parsed from the ``Location`` header path).

        Returns:
            The result body, e.g. ``{"id": "...", "type": "WarehouseSnapshot", ...}``.

        Raises:
            NotFoundError: If the result is not available (404).
            FabricServerError: On 5xx errors.
        """
        result_url = f"{HttpBase.FABRIC}/operations/{operation_id}/result"
        resp = await self._request_with_retry("GET", result_url)
        return resp.json()  # type: ignore[no-any-return]

    async def poll_operation(
        self,
        location: str,
        *,
        timeout_s: float = 600,
    ) -> dict[str, object]:
        """Poll a long-running operation URL until it succeeds or fails.

        Args:
            location: Full URL of the LRO status endpoint.
            timeout_s: Maximum wall-clock seconds to wait (default 600).

        Returns:
            The final response body when ``status == "Succeeded"``.

        Raises:
            FabricServerError: If the operation status is ``"Failed"`` or the
                timeout is exceeded.
        """
        deadline = _time.monotonic() + timeout_s

        while True:
            if _time.monotonic() >= deadline:
                raise FabricServerError(f"LRO timed out after {timeout_s}s for {location}")

            resp = await self._request_with_retry("GET", location)
            body: dict[str, object] = resp.json()
            status = body.get("status", "")

            if status == "Succeeded":
                return body
            if status == "Failed":
                raise FabricServerError(f"LRO failed for {location}: {body.get('error', body)}")

            # Not finished yet - honour Retry-After or fall back to default, with jitter
            retry_after_raw = resp.headers.get("Retry-After")
            base_wait = (
                _parse_retry_after(retry_after_raw) if retry_after_raw else self._poll_interval
            )
            wait_s = base_wait + random.uniform(0, base_wait * 0.25)  # noqa: S311
            await asyncio.sleep(wait_s)
