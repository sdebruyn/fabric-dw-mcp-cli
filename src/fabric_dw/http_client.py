"""Async HTTP client for Microsoft Fabric and Power BI REST APIs.

Provides:
- Global rate-limiting via aiolimiter (default 2 RPS).
- 429 Retry-After handling with a shared asyncio pause gate.
- 5xx retry with tenacity exponential back-off (max 3 attempts).
- Standard error mapping (401 -> AuthError, 403 -> PermissionDenied, 404 -> NotFound).
- continuationUri pagination.
- LRO (202 + Location) polling.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
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
    FabricServerError,
    NotFound,
    PermissionDenied,
    RateLimitedError,
)
from fabric_dw.logging import redact_auth_header

_logger = logging.getLogger("fabric_dw.http")

__all__ = [
    "FabricHttpClient",
    "HttpBase",
    "_parse_retry_after",
]

_MAX_429_RETRIES = 5
_DEFAULT_POLL_INTERVAL = 2.0
_TOKEN_REFRESH_BUFFER = 300  # seconds before expiry to refresh


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
    """

    def __init__(self, credential: AsyncTokenCredential, rps: int = 2) -> None:
        self._credential = credential
        self._limiter = AsyncLimiter(max_rate=rps, time_period=1)
        self._http: httpx.AsyncClient | None = None
        self._token: AccessToken | None = None
        self._token_lock = asyncio.Lock()
        # Pause gate: set when idle, clear when sleeping for a 429
        self._pause_event = asyncio.Event()
        self._pause_event.set()

    async def __aenter__(self) -> FabricHttpClient:
        self._http = httpx.AsyncClient(http2=True, timeout=30)
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
            if self._token is None or self._token.expires_on - _time.time() < _TOKEN_REFRESH_BUFFER:
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
            PermissionDenied: On 403.
            NotFound: On 404.
            RateLimitedError: When the server returns 429 more than _MAX_429_RETRIES times.
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
        """Execute a request with rate limiting and 429 handling."""
        if self._http is None:
            msg = "Client must be used as an async context manager"
            raise RuntimeError(msg)

        consecutive_429 = 0

        while True:
            # Wait if another coroutine is paused for 429
            await self._pause_event.wait()

            # Acquire rate-limit token
            async with self._limiter:
                token = await self._get_token()
                headers = {"Authorization": f"Bearer {token}"}

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
                _logger.debug(
                    "%s %s -> %d elapsed_ms=%.1f headers=%r",
                    method,
                    url,
                    resp.status_code,
                    elapsed_ms,
                    safe_headers,
                )

            if resp.status_code == 429:  # noqa: PLR2004
                consecutive_429 += 1
                if consecutive_429 >= _MAX_429_RETRIES:
                    raise RateLimitedError(  # noqa: TRY003
                        f"Received 429 {consecutive_429} consecutive times for {url}"
                    )

                retry_after_raw = resp.headers.get("Retry-After", "1")
                wait_s = _parse_retry_after(retry_after_raw)

                # Pause all other concurrent callers while we sleep
                self._pause_event.clear()
                try:
                    await asyncio.sleep(wait_s)
                finally:
                    self._pause_event.set()

                continue

            # Reset counter on non-429
            consecutive_429 = 0

            if resp.status_code == 401:  # noqa: PLR2004
                raise AuthError(f"Authentication failed for {url}: {resp.text}")  # noqa: TRY003
            if resp.status_code == 403:  # noqa: PLR2004
                raise PermissionDenied(f"Permission denied for {url}: {resp.text}")  # noqa: TRY003
            if resp.status_code == 404:  # noqa: PLR2004
                raise NotFound(f"Resource not found: {url}: {resp.text}")  # noqa: TRY003
            if resp.status_code >= 500:  # noqa: PLR2004
                raise FabricServerError(  # noqa: TRY003
                    f"Server error {resp.status_code} for {url}: {resp.text}"
                )

            return resp

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def iter_paginated(self, base: HttpBase, path: str) -> AsyncIterator[dict[str, object]]:
        """Iterate over all items across paginated responses.

        Follows the ``continuationUri`` field in each page until it is absent.

        Args:
            base: Base URL enum value (used only for the first request).
            path: Initial path to request.

        Yields:
            Individual items from the ``value`` array in each page.
        """
        url: str | None = f"{base}{path}"

        while url is not None:
            # continuationUri is always a full URL; use _request_with_retry directly
            resp = await self._request_with_retry("GET", url)
            data: dict[str, object] = resp.json()
            value = data.get("value", [])
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield {str(k): v for k, v in item.items()}

            cont = data.get("continuationUri")
            url = cont if isinstance(cont, str) else None

    # ------------------------------------------------------------------
    # LRO polling
    # ------------------------------------------------------------------

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
                raise FabricServerError(  # noqa: TRY003
                    f"LRO timed out after {timeout_s}s for {location}"
                )

            resp = await self._request_with_retry("GET", location)
            body: dict[str, object] = resp.json()
            status = body.get("status", "")

            if status == "Succeeded":
                return body
            if status == "Failed":
                raise FabricServerError(  # noqa: TRY003
                    f"LRO failed for {location}: {body.get('error', body)}"
                )

            # Not finished yet - honour Retry-After or fall back to default
            retry_after_raw = resp.headers.get("Retry-After")
            wait_s = (
                _parse_retry_after(retry_after_raw) if retry_after_raw else _DEFAULT_POLL_INTERVAL
            )
            await asyncio.sleep(wait_s)
