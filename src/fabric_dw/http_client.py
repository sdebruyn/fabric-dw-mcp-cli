"""Async HTTP client for Microsoft Fabric and Power BI REST APIs.

Provides:
- Global rate-limiting via aiolimiter (default 2 RPS).
- 429 Retry-After handling with a monotonic deadline (``_pause_until``).
- 5xx retry with tenacity exponential back-off (max 3 attempts).
- Timeout retry for idempotent methods (GET/HEAD/OPTIONS) only (max 3 attempts).
- Standard error mapping (401 -> AuthError, 403 -> PermissionDeniedError, 404 -> NotFoundError).
- continuationUri pagination.
- LRO (202 + Location) polling with jitter.

Retry arithmetic
~~~~~~~~~~~~~~~~
Tenacity wraps ``_request_with_retry`` and retries on ``FabricServerError``
(5xx) up to 3 attempts with exponential back-off.  Inside each attempt,
``_do_request`` executes a 429-loop of up to ``max_429_retries`` iterations.
Both mechanisms share a common wall-clock deadline (``_combined_deadline_s``
seconds from the first attempt, default 300 s) so the combined worst-case
latency is bounded even if the server returns large Retry-After values.

Timeout retry safety
~~~~~~~~~~~~~~~~~~~~
``httpx.TimeoutException`` (including ``ReadTimeout``, ``ConnectTimeout``, etc.)
is retried ONLY for idempotent HTTP methods (GET, HEAD, OPTIONS).  POST, PATCH,
and DELETE are NOT retried on timeout because the server may have received and
committed the request — re-sending a POST would duplicate the resource or cause
a 409 Conflict.  LRO status polling (GET) is covered by the safe retry path.

Credential ownership
~~~~~~~~~~~~~~~~~~~~
``FabricHttpClient`` calls ``credential.close()`` on ``__aexit__`` whenever the
credential exposes a callable ``close`` attribute.  If ``close()`` returns a
coroutine (as ``azure.identity.aio`` credentials do — they hold an internal
``aiohttp.ClientSession``), the result is awaited; bare sync ``close()`` methods
are called without awaiting.  The close is guarded against teardown failures
(errors are suppressed so a broken credential teardown never aborts the CLI
command).  Credentials that do not expose a ``close`` attribute at all (e.g. plain
``AsyncTokenCredential`` protocol implementations) are left unclosed — callers are
responsible for their lifecycle in that case.
"""

from __future__ import annotations

import asyncio
import datetime
import http
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Callable, Mapping
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from azure.core.exceptions import ClientAuthenticationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from fabric_dw import auth, telemetry
from fabric_dw.exceptions import (
    AuthError,
    BadRequestError,
    FabricError,
    FabricServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    auth_error_from_credential_exc,
)
from fabric_dw.logging import redact_auth_header

_logger = logging.getLogger("fabric_dw.http")

__all__ = [
    "FabricHttpClient",
    "HttpBase",
]

# Module-level defaults (used as constructor defaults)
_DEFAULT_RPS: int = 2
_DEFAULT_TIMEOUT: float = 60.0  # bumped from 30.0 to reduce spurious timeouts on slow responses
_MAX_429_RETRIES: int = 10
_DEFAULT_POLL_INTERVAL: float = 2.0
_TOKEN_REFRESH_BUFFER: float = 300.0  # seconds before expiry to refresh

# Maximum wall-clock seconds for the combined 5xx-tenacity + 429-loop budget.
# When this deadline is reached, whichever retry loop is active at that point
# aborts and re-raises; this prevents unbounded waits when a server advertises
# very large Retry-After values across multiple tenacity attempts.
_DEFAULT_COMBINED_DEADLINE_S: float = 300.0

# HTTP methods that are safe to retry on timeout: repeating them cannot duplicate
# server-side state.  POST/PATCH/DELETE are excluded — a timed-out POST may have
# committed server-side; re-sending it would create a duplicate resource or 409.
_IDEMPOTENT_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

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

    Always returns a non-negative float; never raises — malformed values
    fall back to 0.0.

    Args:
        value: The raw Retry-After header value.

    Returns:
        Number of seconds to wait as a float (>= 0).
    """
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass

    # Try HTTP-date form, e.g. "Wed, 21 Oct 2026 07:28:00 GMT"
    try:
        retry_dt = parsedate_to_datetime(value)
        now = datetime.datetime.now(tz=datetime.UTC)
        delta = (retry_dt - now).total_seconds()
        return max(0.0, delta)
    except (TypeError, ValueError):
        return 0.0


def _is_malformed_retry_after(value: str) -> bool:
    """Return True when *value* is neither a valid numeric seconds string nor an HTTP date.

    Used by ``_do_request`` to distinguish a legitimately-zero Retry-After (e.g.
    ``"0.00"``, ``"0.000"``) from a completely unrecognisable value that fell
    through ``_parse_retry_after`` as ``0.0``.

    Args:
        value: The raw Retry-After header value (not yet stripped).

    Returns:
        ``True`` only when the value cannot be parsed as a number or an HTTP date.
    """
    stripped = value.strip()
    try:
        float(stripped)
    except ValueError:
        pass
    else:
        return False  # valid number (including "0.00", "0.000", negative, etc.)
    try:
        parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return True  # truly malformed
    else:
        return False  # valid HTTP date (even a past one mapping to 0.0)


def _parse_json_body(resp: httpx.Response) -> dict[str, object] | None:
    """Parse the response JSON as a dict, returning ``None`` on failure.

    Narrows the broad exception to ``(ValueError, json.JSONDecodeError)``
    (httpx uses ``json.JSONDecodeError`` for invalid JSON) and silently
    returns ``None`` for any other content type or malformed body.
    """
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            return parsed  # type: ignore[return-value]
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def _make_should_retry(method: str) -> Callable[[BaseException], bool]:
    """Return a tenacity retry predicate bound to *method*.

    Using a closure instead of a module-global ContextVar avoids shared
    mutable state across concurrent coroutines and makes the method binding
    explicit and testable.

    Returns:
        A predicate ``(exc: BaseException) -> bool`` that tenacity passes
        to ``retry_if_exception``.
    """
    upper = method.upper()

    def _should_retry(exc: BaseException) -> bool:
        """Retry on 5xx *or* on timeout for idempotent methods.

        - Retry :class:`~fabric_dw.exceptions.FabricServerError` (5xx) ONLY
          when ``is_retriable`` is ``True`` (the default).  A Fabric error
          envelope with ``"isRetriable": false`` (e.g. the ~22s
          InternalServerError from a paused-capacity workspace) must NOT be
          retried — fail fast, no 60-70s back-off waste.
        - Retry :class:`httpx.TimeoutException` ONLY for idempotent HTTP
          methods (GET, HEAD, OPTIONS).  POST/PATCH/DELETE are not retried
          on timeout — the server may have already committed the request.
        """
        if isinstance(exc, FabricServerError):
            return exc.is_retriable
        if isinstance(exc, httpx.TimeoutException):
            return upper in _IDEMPOTENT_METHODS
        return False

    return _should_retry


class FabricHttpClient:
    """Async HTTP client for Fabric and Power BI REST APIs.

    Usage::

        async with FabricHttpClient(credential) as client:
            resp = await client.request("GET", HttpBase.FABRIC, "/workspaces")

    Credential ownership
    ~~~~~~~~~~~~~~~~~~~~
    ``FabricHttpClient`` calls ``credential.close()`` in ``__aexit__`` whenever the
    credential exposes a callable ``close`` attribute.  If the return value is a
    coroutine (as ``azure.identity.aio`` credentials return — they hold an internal
    ``aiohttp.ClientSession``), it is awaited; bare sync ``close()`` methods are
    called without awaiting.  A missing ``close`` attribute is silently ignored, and
    any exception raised by ``close()`` is suppressed so teardown never aborts the
    command.

    Retry arithmetic
    ~~~~~~~~~~~~~~~~
    Tenacity wraps ``_request_with_retry`` and retries on ``FabricServerError``
    (5xx) up to 3 attempts.  Inside each tenacity attempt, ``_do_request`` runs
    a 429-loop of up to ``max_429_retries`` iterations.  Both mechanisms share a
    common wall-clock deadline (``combined_deadline_s`` seconds from the first
    send, default 300 s) so the total latency is bounded even when the server
    advertises large ``Retry-After`` values.

    Args:
        credential:             Azure credential used to fetch bearer tokens.
        rps:                    Maximum requests per second (default 2).
        timeout:                HTTP request timeout in seconds (default 60.0).
        max_429_retries:        Maximum consecutive 429 responses before raising
                                ``RateLimitedError`` (default 10).
        poll_interval:          Default LRO polling interval in seconds (default 2.0).
        token_refresh_buffer:   Seconds before token expiry at which a refresh is
                                triggered (default 300.0).
        combined_deadline_s:    Maximum wall-clock seconds for the combined
                                5xx-retry + 429-loop budget (default 300.0).
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
        combined_deadline_s: float = _DEFAULT_COMBINED_DEADLINE_S,
    ) -> None:
        self._credential = credential
        self._limiter = AsyncLimiter(max_rate=rps, time_period=1)
        self._http: httpx.AsyncClient | None = None
        # Per-scope token cache: dict[scope, AccessToken]
        self._tokens: dict[str, AccessToken] = {}
        self._token_lock = asyncio.Lock()
        self._timeout = timeout
        self._max_429_retries = max_429_retries
        self._poll_interval = poll_interval
        self._token_refresh_buffer = token_refresh_buffer
        self._combined_deadline_s = combined_deadline_s
        # Monotonic deadline: sleep until this time before each send.
        # 0.0 means "no pause needed".
        # Safety: this field is read and written by concurrent coroutines on
        # the single-threaded asyncio event loop.  The read-modify-write
        # ``self._pause_until = max(self._pause_until, deadline)`` in
        # ``_do_request`` is not interrupted by an ``await``, so it is
        # effectively atomic on CPython's event loop; no lock is needed.
        self._pause_until: float = 0.0

    async def __aenter__(self) -> FabricHttpClient:
        self._http = httpx.AsyncClient(http2=True, timeout=self._timeout)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        # Close the credential when it exposes an async close() method.
        # azure.identity.aio credentials (DefaultAzureCredential, ClientSecretCredential,
        # etc.) hold an aiohttp.ClientSession internally; calling close() releases it and
        # prevents the "Unclosed client session" ResourceWarning on process exit.
        # The guard handles credentials that do not expose close() at all (e.g. plain
        # AsyncTokenCredential protocol stubs).  Errors are suppressed so that a
        # credential teardown failure never aborts the CLI command.
        _close = getattr(self._credential, "close", None)
        if callable(_close):
            try:
                result = _close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                _logger.debug("Suppressed error closing credential", exc_info=True)

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _get_token(self, scope: str = auth.FABRIC_SCOPE) -> str:
        """Return a valid bearer token for *scope*, refreshing if close to expiry.

        A lock ensures that under concurrent calls only one refresh is
        performed even if multiple coroutines see the token as expired at
        the same time.  Tokens are cached per-scope so that different scopes
        (e.g. ``FABRIC_SCOPE``, ``SQL_SCOPE``) do not share a single cache entry.

        Args:
            scope: The OAuth2 scope to request a token for.
                   Defaults to :data:`~fabric_dw.auth.FABRIC_SCOPE`.
        """
        async with self._token_lock:
            token = self._tokens.get(scope)
            if token is None or token.expires_on - time.time() < self._token_refresh_buffer:
                try:
                    token = await self._credential.get_token(scope)
                except ClientAuthenticationError as exc:
                    raise auth_error_from_credential_exc(exc) from exc
                self._tokens[scope] = token
                # Decode the tid claim from the new token and cache it in the
                # telemetry layer (no-op when telemetry is disabled or tid is
                # already known).  Never raises — fail-safe wrapper.
                telemetry.cache_tenant_id_from_token(token.token)
                # Derive and record the resolved auth mode from the credential
                # that produced this token.  For DefaultAzureCredential, this
                # inspects _successful_credential (set after the first get_token
                # succeeds) and maps its class name to a telemetry mode string.
                # No-op for non-DAC credentials (SyncCredentialAdapter wrapping
                # InteractiveBrowserCredential / ClientSecretCredential) because
                # get_credential() already called set_auth_mode for those modes.
                # Always fail-safe: never raises.
                auth.record_auth_mode_from_default_credential(self._credential)
        return token.token

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
            RateLimitedError: After exactly ``max_429_retries`` consecutive 429 responses
                              or when the combined retry deadline is exceeded.
            FabricServerError: On persistent 5xx errors.
        """
        url = f"{base}{path}"
        combined_deadline = time.monotonic() + self._combined_deadline_s
        return await self._request_with_retry(
            method, url, json=json, params=params, combined_deadline=combined_deadline
        )

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json: object = None,
        params: Mapping[str, Any] | None = None,
        combined_deadline: float | None = None,
    ) -> httpx.Response:
        """Inner request with tenacity 5xx and idempotent-timeout retry.

        The tenacity decorator is built per-call so the method is bound
        directly in the retry predicate closure, avoiding a fragile module-
        global ContextVar.
        """
        should_retry = _make_should_retry(method)

        @retry(
            retry=retry_if_exception(should_retry),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.1, min=0.1, max=2),
            reraise=True,
        )
        async def _attempt() -> httpx.Response:
            return await self._do_request(
                method, url, json=json, params=params, combined_deadline=combined_deadline
            )

        return await _attempt()

    async def _do_request(
        self,
        method: str,
        url: str,
        *,
        json: object = None,
        params: Mapping[str, Any] | None = None,
        combined_deadline: float | None = None,
    ) -> httpx.Response:
        """Execute a request with rate limiting and 429 handling.

        Delegates to:
        - ``_send_once``: waits for the pause deadline, acquires the rate
          limiter, and performs the actual HTTP send.
        - ``_map_status``: maps error status codes to typed exceptions.

        On 429: updates the shared monotonic deadline and retries up to
        ``_max_429_retries`` consecutive times before raising
        ``RateLimitedError``.  Respects *combined_deadline* to bound the
        total wall-clock wait shared with the tenacity 5xx-retry layer.
        """
        if self._http is None:
            msg = "Client must be used as an async context manager"
            raise RuntimeError(msg)

        consecutive_429 = 0

        while True:
            # Shared deadline check: abort if the combined retry budget is spent.
            if combined_deadline is not None and time.monotonic() >= combined_deadline:
                raise RateLimitedError(
                    f"Combined retry deadline exceeded for {url}",
                    status=429,
                )

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
                wait_s = _parse_retry_after(retry_after_raw)
                # Warn and override only when the header was truly unrecognizable.
                # _parse_retry_after returns 0.0 both for a legitimate "0 seconds"
                # value AND for a completely unparseable string.  Distinguish them
                # by re-checking: if the raw value is a parseable number (any form
                # of zero like "0.00") or a valid HTTP date (even a past one that
                # maps to 0.0), treat it as intentional.  Only warn otherwise.
                if wait_s == 0.0 and _is_malformed_retry_after(retry_after_raw):
                    _logger.warning(
                        "Malformed Retry-After header %r; falling back to 1.0s",
                        retry_after_raw,
                    )
                    wait_s = 1.0

                # Aggregate concurrent 429s: keep the latest (furthest) deadline.
                # This read-modify-write is safe on the single-threaded asyncio
                # event loop because there is no ``await`` between the read and
                # the write — the assignment is effectively atomic.
                deadline = time.monotonic() + wait_s
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
            now = time.monotonic()
            remaining = self._pause_until - now
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)

        # Fetch token outside the limiter to avoid wasting RPS budget on refresh.
        token = await self._get_token()

        headers = {"Authorization": f"Bearer {token}"}

        async with self._limiter:
            t0 = time.monotonic()
            resp = await self._http.request(
                method,
                url,
                headers=headers,
                json=json,
                params=params,
            )
            elapsed_ms = (time.monotonic() - t0) * 1000

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
        for any 5xx response.  JSON body is parsed best-effort (only
        ``ValueError``/``json.JSONDecodeError`` are suppressed).  The
        ``x-ms-request-id`` header is captured for all raised exceptions.
        """
        status = resp.status_code
        request_id = resp.headers.get("x-ms-request-id")

        # Best-effort JSON body parse — narrowed to JSON-specific errors only
        body = _parse_json_body(resp)

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
            # Parse the isRetriable flag from the Fabric error envelope.
            # Fabric sets isRetriable=false for errors like the ~22s
            # InternalServerError from paused-capacity workspaces.  When
            # false, the retry predicate in _make_should_retry will skip
            # back-off entirely, failing fast rather than wasting 60-70s.
            is_retriable = True
            if body is not None:
                raw_retriable = body.get("isRetriable")
                if raw_retriable is False:
                    is_retriable = False
            raise FabricServerError(
                f"Server error {status} for {url}: {resp.text}",
                status=status,
                request_id=request_id,
                body=body,
                is_retriable=is_retriable,
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
        # Apply the same combined wall-clock deadline used by request() so that
        # 429 loops inside paginated fetches are time-bounded (C27).
        combined_deadline = time.monotonic() + self._combined_deadline_s

        while url is not None:
            # continuationUri is always a full URL; use _request_with_retry directly
            resp = await self._request_with_retry(
                "GET", url, params=params if first else None, combined_deadline=combined_deadline
            )
            first = False
            raw = resp.json()
            if not isinstance(raw, dict):
                # Non-dict page body (e.g. an array or null): no items, no continuation.
                break
            data: dict[str, object] = raw
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
            The result body as a ``dict``, e.g.
            ``{"id": "...", "type": "WarehouseSnapshot", ...}``.
            Raises ``FabricServerError`` if the response body is not a JSON object.

        Raises:
            NotFoundError: If the result is not available (404).
            FabricServerError: On 5xx errors or a non-dict response body.
        """
        result_url = f"{HttpBase.FABRIC}/operations/{operation_id}/result"
        combined_deadline = time.monotonic() + self._combined_deadline_s
        resp = await self._request_with_retry(
            "GET", result_url, combined_deadline=combined_deadline
        )
        body = _parse_json_body(resp)
        if body is None:
            raise FabricServerError(
                f"LRO result for {operation_id} returned a non-dict body: {resp.text!r}"
            )
        return body

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
        deadline = time.monotonic() + timeout_s
        # Apply the combined wall-clock deadline so that 429 loops inside
        # each poll request are time-bounded (C27).  For poll_operation the
        # natural combined deadline is per-request: reset it each iteration so
        # that a fresh ``_combined_deadline_s`` budget is granted per poll GET,
        # while the LRO ``deadline`` bounds the overall polling duration.
        #
        # Note: combined_deadline is intentionally re-computed each iteration
        # inside the loop (see below) so that each poll attempt gets its own
        # fresh 429-retry budget rather than sharing a single budget across
        # potentially hundreds of polls.

        while True:
            if time.monotonic() >= deadline:
                raise FabricServerError(f"LRO timed out after {timeout_s}s for {location}")

            # Fresh combined_deadline per poll GET so the 429-retry window is
            # scoped to each individual HTTP request, not the entire LRO wait.
            poll_combined_deadline = time.monotonic() + self._combined_deadline_s
            resp = await self._request_with_retry(
                "GET", location, combined_deadline=poll_combined_deadline
            )
            body = _parse_json_body(resp) or {}
            status = body.get("status", "")

            if status == "Succeeded":
                return body
            if status == "Failed":
                raise FabricServerError(f"LRO failed for {location}: {body.get('error', body)}")

            # Not finished yet - honour Retry-After or fall back to default, with jitter.
            # _parse_retry_after always returns a float (never raises).
            retry_after_raw = resp.headers.get("Retry-After")
            base_wait = (
                _parse_retry_after(retry_after_raw) if retry_after_raw else self._poll_interval
            )
            # If parse returned 0 but a header was present, fall back to poll interval.
            if base_wait == 0.0 and retry_after_raw is not None:
                base_wait = self._poll_interval
            wait_s = base_wait + random.uniform(0, base_wait * 0.25)  # noqa: S311
            await asyncio.sleep(wait_s)
