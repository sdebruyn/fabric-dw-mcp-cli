"""Tests for FabricHttpClient - written BEFORE the implementation (TDD)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
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

_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106


def _make_credential(token: AccessToken = _FAKE_TOKEN) -> AsyncTokenCredential:
    """Build a mock credential that returns *token*."""
    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(return_value=token)
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
    """6 requests at 2 RPS complete in ~2.0s.

    AsyncLimiter(2, 1) drains at 2 tokens/s: first 2 fire immediately,
    then 1 more every 0.5 s, so the 6th fires at ~2.0 s.
    """
    elapsed: float = 0.0

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
    assert elapsed >= 1.9, f"Too fast: {elapsed:.2f}s — rate limiter not enforced"
    assert elapsed <= 3.0, f"Too slow: {elapsed:.2f}s — unexpected delay"


# ---------------------------------------------------------------------------
# 429 handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_retry_after_honored() -> None:
    """A 429 with Retry-After: 1 should retry once and succeed; elapsed >= ~0.9 s."""
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
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
async def test_429_raises_after_five_in_a_row() -> None:
    """Exactly 5 consecutive 429 responses must trigger RateLimitedError (_MAX_429_RETRIES = 5).

    The implementation raises when consecutive_429 >= 5, so the 5th 429 response
    is the one that causes the exception — meaning exactly 5 mocked 429s are consumed.
    """
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={})

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = await _get_client(rps=10)
        async with client:
            with pytest.raises(RateLimitedError):
                await client.request("GET", HttpBase.FABRIC, "/items")

    assert call_count == 5, f"Expected exactly 5 429 responses before raising; got {call_count}"


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

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=page1)
        return httpx.Response(200, json=page2)

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(url__regex=r"https://api\.fabric\.microsoft\.com/v1/items.*").mock(
            side_effect=side_effect
        )

        client = await _get_client()
        async with client:
            items = [item async for item in client.iter_paginated(HttpBase.FABRIC, "/items")]

        assert items == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert call_count == 2


@pytest.mark.asyncio
async def test_iter_paginated_single_page() -> None:
    """iter_paginated should yield all items from a single-page response."""
    page: dict[str, Any] = {"value": [{"id": "x"}, {"id": "y"}]}

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(200, json=page)
        )

        client = await _get_client()
        async with client:
            items = [item async for item in client.iter_paginated(HttpBase.FABRIC, "/items")]

    assert items == [{"id": "x"}, {"id": "y"}]


@pytest.mark.asyncio
async def test_iter_paginated_two_pages_follows_continuation_uri() -> None:
    """iter_paginated should follow continuationUri and yield items from both pages."""
    continuation_url = "https://api.fabric.microsoft.com/v1/items?continuation=abc"
    page1: dict[str, Any] = {
        "value": [{"id": "1"}],
        "continuationUri": continuation_url,
    }
    page2: dict[str, Any] = {"value": [{"id": "2"}, {"id": "3"}]}

    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if "continuation" in str(request.url):
            return httpx.Response(200, json=page2)
        return httpx.Response(200, json=page1)

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(url__regex=r"https://api\.fabric\.microsoft\.com/v1/items.*").mock(
            side_effect=side_effect
        )

        client = await _get_client()
        async with client:
            items = [item async for item in client.iter_paginated(HttpBase.FABRIC, "/items")]

    assert items == [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    assert call_count == 2


@pytest.mark.asyncio
async def test_iter_paginated_empty_value_list() -> None:
    """iter_paginated should yield nothing when the value list is empty."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(200, json={"value": []})
        )

        client = await _get_client()
        async with client:
            items = [item async for item in client.iter_paginated(HttpBase.FABRIC, "/items")]

    assert items == []


# ---------------------------------------------------------------------------
# LRO polling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_operation_succeeded() -> None:
    """poll_operation should return the body when status == 'Succeeded'."""
    poll_count = 0
    final_body = {"status": "Succeeded", "result": {"id": "op-123"}}

    def side_effect(_request: httpx.Request) -> httpx.Response:
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
                await client.poll_operation("https://api.fabric.microsoft.com/v1/operations/op-456")


@pytest.mark.asyncio
async def test_poll_operation_timeout_raises() -> None:
    """poll_operation should raise FabricServerError when timeout_s is exceeded.

    Uses timeout_s=0.1 so the deadline is always past by the first iteration.
    The mock always returns "Running" so the operation never completes.
    """
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/operations/op-timeout").mock(
            return_value=httpx.Response(200, json={"status": "Running"})
        )

        client = await _get_client()
        async with client:
            with pytest.raises(FabricServerError, match="timed out"):
                await client.poll_operation(
                    "https://api.fabric.microsoft.com/v1/operations/op-timeout",
                    timeout_s=0.1,
                )


# ---------------------------------------------------------------------------
# get_operation_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_operation_result_returns_body() -> None:
    """get_operation_result should GET /operations/{op_id}/result and return the body.

    Regression test: Fabric LRO status bodies do not include the created item ID.
    The result must be fetched separately via GET /v1/operations/{op_id}/result.
    """
    op_id = "b80e135a-adca-42e7-aaf0-59849af2ed78"
    result_url = f"https://api.fabric.microsoft.com/v1/operations/{op_id}/result"
    expected = {
        "id": "221a6eea-0f27-41eb-bcc5-e4d7b216ed43",
        "type": "WarehouseSnapshot",
        "displayName": "MySnapshot",
        "workspaceId": "a91e61ef-862e-4611-9d09-9c7cc07b2519",
    }

    with respx.mock:
        respx.get(result_url).mock(return_value=httpx.Response(200, json=expected))

        client = await _get_client()
        async with client:
            result = await client.get_operation_result(op_id)

    assert result == expected


@pytest.mark.asyncio
async def test_get_operation_result_not_found_propagates() -> None:
    """get_operation_result should propagate NotFound when the result endpoint returns 404."""
    op_id = "no-such-op"
    result_url = f"https://api.fabric.microsoft.com/v1/operations/{op_id}/result"

    with respx.mock:
        respx.get(result_url).mock(return_value=httpx.Response(404, json={"error": "not found"}))

        client = await _get_client()
        async with client:
            with pytest.raises(NotFound):
                await client.get_operation_result(op_id)


# ---------------------------------------------------------------------------
# params type: Mapping[str, Any]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_params_int_value_serialized() -> None:
    """request() must accept int param values and serialize them correctly (e.g. ?top=100)."""
    captured_url: str | None = None

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal captured_url
        captured_url = str(request.url)
        return httpx.Response(200, json={})

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(url__regex=r"https://api\.fabric\.microsoft\.com/v1/x.*").mock(
            side_effect=side_effect
        )

        client = await _get_client()
        async with client:
            await client.request("GET", HttpBase.FABRIC, "/x", params={"top": 100})

    assert captured_url is not None
    assert "top=100" in captured_url, f"Expected 'top=100' in URL; got {captured_url}"


# ---------------------------------------------------------------------------
# Token refresh concurrency safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_requests_fetch_token_once() -> None:
    """Five concurrent requests before any token is fetched must call get_token exactly once."""
    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(
        return_value=AccessToken(token="tok", expires_on=int(time.time()) + 3600)  # noqa: S106
    )

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(200, json={})
        )

        client = FabricHttpClient(credential=cred, rps=10)
        async with client:
            await asyncio.gather(
                *[client.request("GET", HttpBase.FABRIC, "/items") for _ in range(5)]
            )

    assert cred.get_token.call_count == 1, (
        f"Expected get_token called once; called {cred.get_token.call_count} times"
    )


# ---------------------------------------------------------------------------
# Debug-level logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_log_emitted_on_request(caplog: pytest.LogCaptureFixture) -> None:
    """A successful request at DEBUG must emit a log record from fabric_dw.http.

    The record must include method, url, status, elapsed_ms and must have the
    Authorization header value redacted to 'Bearer ***'.
    """
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(200, json={"value": []})
        )

        client = await _get_client(rps=10)
        async with client:
            with caplog.at_level(logging.DEBUG, logger="fabric_dw.http"):
                await client.request("GET", HttpBase.FABRIC, "/items")

    # Find the record from fabric_dw.http (there may also be httpx records)
    fabric_records = [r for r in caplog.records if r.name == "fabric_dw.http"]
    assert len(fabric_records) >= 1
    record = fabric_records[0]
    assert record.name == "fabric_dw.http"
    assert record.levelno == logging.DEBUG

    msg = record.getMessage()
    assert "GET" in msg
    assert "200" in msg

    # Authorization header must be redacted
    assert "Bearer ***" in msg or "Bearer ***" in str(record.__dict__)
    # Must not contain the raw token
    assert "fake-token" not in msg


@pytest.mark.asyncio
async def test_debug_log_contains_elapsed_ms(caplog: pytest.LogCaptureFixture) -> None:
    """Log record should contain elapsed_ms as a numeric attribute or in message."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(200, json={})
        )

        client = await _get_client(rps=10)
        async with client:
            with caplog.at_level(logging.DEBUG, logger="fabric_dw.http"):
                await client.request("GET", HttpBase.FABRIC, "/items")

    # Find the record from fabric_dw.http
    fabric_records = [r for r in caplog.records if r.name == "fabric_dw.http"]
    assert len(fabric_records) >= 1
    record = fabric_records[0]
    # elapsed_ms may be in the message string or as an extra attribute
    has_elapsed = (
        "elapsed" in record.getMessage().lower()
        or hasattr(record, "elapsed_ms")
        or "ms" in record.getMessage()
    )
    assert has_elapsed, f"No elapsed info in log record: {record.getMessage()}"
