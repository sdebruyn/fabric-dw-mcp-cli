"""Tests for FabricHttpClient – written BEFORE the implementation (TDD)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken, TokenCredential
from freezegun import freeze_time

from fabric_dw.exceptions import (
    AuthError,
    FabricServerError,
    NotFound,
    PermissionDenied,
    RateLimitedError,
)
from fabric_dw.http_client import FabricHttpClient, HttpBase, _parse_retry_after

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)


def _make_credential(token: AccessToken = _FAKE_TOKEN) -> TokenCredential:
    """Build a mock credential that returns *token*."""
    cred = MagicMock(spec=TokenCredential)
    cred.get_token = MagicMock(return_value=token)
    return cred


async def _get_client(rps: int = 10) -> FabricHttpClient:
    """Instantiate a FabricHttpClient with a mock credential."""
    return FabricHttpClient(credential=_make_credential(), rps=rps)


# ---------------------------------------------------------------------------
# _parse_retry_after
# ---------------------------------------------------------------------------


def test_parse_retry_after_integer() -> None:
    """Integer-second string should return that value as float."""
    assert _parse_retry_after("3") == 3.0


@freeze_time("2026-10-21 07:27:00 UTC")
def test_parse_retry_after_http_date() -> None:
    """HTTP-date string should return seconds-until-that-time as positive float."""
    result = _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT")
    # frozen at 07:27:00 → 60 seconds until 07:28:00
    assert result == pytest.approx(60.0, abs=1.0)


# ---------------------------------------------------------------------------
# Timed RPS test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rps_limiter_timing() -> None:
    """6 concurrent requests at 2 RPS should complete in ~3 s (±0.5 s tolerance)."""
    with respx.mock:
        route = respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(200, json={"value": []})
        )

        client = await _get_client(rps=2)
        async with client:
            start = time.monotonic()
            await asyncio.gather(
                *[client.request("GET", HttpBase.FABRIC, "/items") for _ in range(6)]
            )
            elapsed = time.monotonic() - start

    assert route.call_count == 6
    # With AsyncLimiter(2, 1): 6 requests / 2 rps = 3 s minimum (first 2 are free)
    # Allow ±0.6 s tolerance for scheduling jitter
    assert elapsed >= 2.4, f"Too fast: {elapsed:.2f}s — rate limiter not enforced"
    assert elapsed <= 4.5, f"Too slow: {elapsed:.2f}s — unexpected delay"


# ---------------------------------------------------------------------------
# 429 handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_retry_after_honored() -> None:
    """A 429 with Retry-After: 1 should retry once and succeed; elapsed >= ~0.9 s."""
    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return httpx.Response(200, json={"value": []})

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = await _get_client(rps=10)
        async with client:
            start = time.monotonic()
            resp = await client.request("GET", HttpBase.FABRIC, "/items")
            elapsed = time.monotonic() - start

    assert resp.status_code == 200
    assert call_count == 2
    assert elapsed >= 0.9, f"Retry-After not honored: {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_429_five_consecutive_raises() -> None:
    """Five consecutive 429 responses should raise RateLimitedError."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "1"}, json={})
        )

        client = await _get_client(rps=10)
        async with client:
            with pytest.raises(RateLimitedError):
                await client.request("GET", HttpBase.FABRIC, "/items")


# ---------------------------------------------------------------------------
# Status-code error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_401_raises_auth_error() -> None:
    """HTTP 401 should raise AuthError."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        client = await _get_client()
        async with client:
            with pytest.raises(AuthError):
                await client.request("GET", HttpBase.FABRIC, "/items")


@pytest.mark.asyncio
async def test_403_raises_permission_denied() -> None:
    """HTTP 403 should raise PermissionDenied."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(403, json={"error": "forbidden"})
        )
        client = await _get_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await client.request("GET", HttpBase.FABRIC, "/items")


@pytest.mark.asyncio
async def test_404_raises_not_found() -> None:
    """HTTP 404 should raise NotFound."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        client = await _get_client()
        async with client:
            with pytest.raises(NotFound):
                await client.request("GET", HttpBase.FABRIC, "/items")


# ---------------------------------------------------------------------------
# 5xx retried then FabricServerError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_500_retried_then_raises() -> None:
    """HTTP 500 should be retried (tenacity) and finally raise FabricServerError."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(500, json={"error": "server error"})
        )
        client = await _get_client()
        async with client:
            with pytest.raises(FabricServerError):
                await client.request("GET", HttpBase.FABRIC, "/items")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iter_paginated_follows_continuation_uri() -> None:
    """iter_paginated should follow continuationUri across pages and yield all items."""
    page1 = {
        "value": [{"id": "a"}, {"id": "b"}],
        "continuationUri": "https://api.fabric.microsoft.com/v1/items?continuation=xyz",
    }
    page2: dict[str, Any] = {
        "value": [{"id": "c"}],
    }

    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=page1)
        return httpx.Response(200, json=page2)

    with respx.mock(assert_all_called=False):
        respx.get(url__regex=r"https://api\.fabric\.microsoft\.com/v1/items.*").mock(
            side_effect=side_effect
        )

        client = await _get_client()
        async with client:
            items = [item async for item in client.iter_paginated(HttpBase.FABRIC, "/items")]

    assert items == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert call_count == 2


# ---------------------------------------------------------------------------
# LRO polling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_operation_succeeded() -> None:
    """poll_operation should return the body when status == 'Succeeded'."""
    poll_count = 0
    final_body = {"status": "Succeeded", "result": {"id": "op-123"}}

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            return httpx.Response(
                202,
                headers={"Retry-After": "1"},
                json={"status": "Running"},
            )
        return httpx.Response(200, json=final_body)

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/operations/op-123").mock(
            side_effect=side_effect
        )

        client = await _get_client()
        async with client:
            result = await client.poll_operation(
                "https://api.fabric.microsoft.com/v1/operations/op-123"
            )

    assert result == final_body
    assert poll_count == 2


@pytest.mark.asyncio
async def test_poll_operation_failed_raises() -> None:
    """poll_operation should raise FabricServerError when status == 'Failed'."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/operations/op-456").mock(
            return_value=httpx.Response(200, json={"status": "Failed", "error": "boom"})
        )

        client = await _get_client()
        async with client:
            with pytest.raises(FabricServerError):
                await client.poll_operation(
                    "https://api.fabric.microsoft.com/v1/operations/op-456"
                )
