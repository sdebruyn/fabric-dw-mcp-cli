"""Tests for FabricHttpClient - written BEFORE the implementation (TDD)."""

from __future__ import annotations

import asyncio
import logging
import time
import unittest.mock
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from freezegun import freeze_time

from fabric_dw.auth import FABRIC_SCOPE, SQL_SCOPE
from fabric_dw.exceptions import (
    AuthError,
    BadRequestError,
    FabricError,
    FabricServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
)
from fabric_dw.http_client import _DEFAULT_TIMEOUT, FabricHttpClient, HttpBase, _parse_retry_after

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106


class _FakeClock:
    """A controllable monotonic clock and asyncio.sleep replacement.

    Use as a context manager to patch ``fabric_dw.http_client._time.monotonic``
    and ``asyncio.sleep`` for the duration of the ``with`` block::

        clock = _FakeClock()
        with clock:
            client._pause_until = clock.now + 1.0
            await client.request(...)
        assert clock.sleeps == [1.0]

    Attributes:
        now: The current fake monotonic time (float).
        sleeps: Durations passed to ``asyncio.sleep`` (positive values only).
    """

    def __init__(self, now: float = 1_000.0) -> None:
        # Start well above zero so that ``_pause_until - now`` arithmetic is clean.
        self.now: float = now
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        """Record the requested duration and advance the fake clock; no real wait."""
        if seconds > 0:
            self.sleeps.append(seconds)
            self.now += seconds

    def __enter__(self) -> _FakeClock:
        self._p_monotonic = patch(
            "fabric_dw.http_client._time.monotonic", side_effect=self.monotonic
        )
        self._p_sleep = patch("asyncio.sleep", side_effect=self.sleep)
        self._p_monotonic.start()
        self._p_sleep.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        # Stop in reverse start order (LIFO) so nested patchers unwind correctly.
        self._p_sleep.stop()
        self._p_monotonic.stop()


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


@pytest.mark.slow
async def test_rps_limiter_timing() -> None:
    """6 requests at 2 RPS complete in ~2.0s (wall-clock test; marked slow).

    AsyncLimiter(2, 1) drains at 2 tokens/s: first 2 fire immediately,
    then 1 more every 0.5 s, so the 6th fires at ~2.0 s.

    This test measures real elapsed time to verify the AsyncLimiter is wired up
    correctly.  It is excluded from the default ``just check`` run via
    ``@pytest.mark.slow``; run it explicitly with ``-m slow`` when needed.
    """
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


async def test_429_retry_after_honored() -> None:
    """A 429 with Retry-After: 1 should retry once and succeed.

    Uses a fake clock so the test is instantaneous: asserts that asyncio.sleep
    was called with a duration >= 1.0 s (the Retry-After value), proving the
    pause deadline is honoured without real wall-clock waiting.
    """
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return httpx.Response(200, json={"value": []})

    clock = _FakeClock()
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = await _get_client(rps=10)
        async with client:
            with clock:
                resp = await client.request("GET", HttpBase.FABRIC, "/items")

    assert resp.status_code == 200
    assert call_count == 2
    # The client must have slept for at least the Retry-After duration (1 s).
    assert len(clock.sleeps) >= 1, f"Expected >=1 deadline sleep; got {clock.sleeps}"
    assert sum(clock.sleeps) >= 1.0, (
        f"Total sleep {sum(clock.sleeps):.3f}s < 1.0 — Retry-After not honoured"
    )


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


async def test_403_raises_permission_denied() -> None:
    """HTTP 403 should raise PermissionDeniedError."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(403, json={"error": "forbidden"})
        )
        client = await _get_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await client.request("GET", HttpBase.FABRIC, "/items")


async def test_404_raises_not_found() -> None:
    """HTTP 404 should raise NotFoundError."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        client = await _get_client()
        async with client:
            with pytest.raises(NotFoundError):
                await client.request("GET", HttpBase.FABRIC, "/items")


# ---------------------------------------------------------------------------
# 5xx retried then FabricServerError
# ---------------------------------------------------------------------------


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


async def test_iter_paginated_custom_key() -> None:
    """iter_paginated with key='someOtherKey' must yield items from that key, not from 'value'.

    The response body contains both 'someOtherKey' (with id=1) and 'value' (with id=99).
    Only the item from 'someOtherKey' should be yielded.
    """
    page: dict[str, Any] = {
        "someOtherKey": [{"id": 1}],
        "value": [{"id": 99}],
    }

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(200, json=page)
        )

        client = await _get_client()
        async with client:
            items = [
                item
                async for item in client.iter_paginated(
                    HttpBase.FABRIC, "/items", key="someOtherKey"
                )
            ]

    assert items == [{"id": 1}], f"Expected only id=1 from 'someOtherKey'; got {items}"


async def test_iter_paginated_params_only_on_first_request() -> None:
    """params must be sent on the first request only, not on continuation requests.

    Mock two responses:
    - First: contains continuationUri.
    - Second: no continuationUri.
    Verify via captured request URLs/params that the first call includes params={"x": "y"}
    and the second call (continuation URL) does NOT include those params.
    """
    continuation_url = "https://api.fabric.microsoft.com/v1/items?continuation=tok"
    page1: dict[str, Any] = {
        "value": [{"id": "first"}],
        "continuationUri": continuation_url,
    }
    page2: dict[str, Any] = {"value": [{"id": "second"}]}

    captured_requests: list[httpx.Request] = []

    def side_effect(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if "continuation" in str(request.url):
            return httpx.Response(200, json=page2)
        return httpx.Response(200, json=page1)

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(url__regex=r"https://api\.fabric\.microsoft\.com/v1/items.*").mock(
            side_effect=side_effect
        )

        client = await _get_client()
        async with client:
            items = [
                item
                async for item in client.iter_paginated(
                    HttpBase.FABRIC, "/items", params={"x": "y"}
                )
            ]

    assert items == [{"id": "first"}, {"id": "second"}]
    assert len(captured_requests) == 2, f"Expected 2 requests; got {len(captured_requests)}"

    first_url = str(captured_requests[0].url)
    second_url = str(captured_requests[1].url)

    # First request must contain the custom param
    assert "x=y" in first_url, f"Expected 'x=y' in first request URL; got {first_url}"
    # Second request (continuation) must NOT contain the custom param
    assert "x=y" not in second_url, (
        f"Params must not be forwarded to continuation URL; second URL: {second_url}"
    )


# ---------------------------------------------------------------------------
# LRO polling
# ---------------------------------------------------------------------------


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


async def test_get_operation_result_not_found_propagates() -> None:
    """get_operation_result should propagate NotFoundError when the result endpoint returns 404."""
    op_id = "no-such-op"
    result_url = f"https://api.fabric.microsoft.com/v1/operations/{op_id}/result"

    with respx.mock:
        respx.get(result_url).mock(return_value=httpx.Response(404, json={"error": "not found"}))

        client = await _get_client()
        async with client:
            with pytest.raises(NotFoundError):
                await client.get_operation_result(op_id)


# ---------------------------------------------------------------------------
# params type: Mapping[str, Any]
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Exception context attributes
# ---------------------------------------------------------------------------


async def test_status_mapping_fills_exception_attributes() -> None:
    """Error exceptions must carry status, request_id, and body attributes."""
    req_id = "test-req-id-001"

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(
                404,
                headers={"x-ms-request-id": req_id},
                json={"error": {"code": "ItemNotFound", "message": "not found"}},
            )
        )
        client = await _get_client()
        async with client:
            with pytest.raises(NotFoundError) as exc_info:
                await client.request("GET", HttpBase.FABRIC, "/items")

    err = exc_info.value
    assert err.status == 404
    assert err.request_id == req_id
    assert isinstance(err.body, dict)
    assert err.body.get("error") is not None


async def test_auth_error_carries_status() -> None:
    """AuthError must have status=401."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        client = await _get_client()
        async with client:
            with pytest.raises(AuthError) as exc_info:
                await client.request("GET", HttpBase.FABRIC, "/items")

    assert exc_info.value.status == 401


async def test_server_error_carries_status() -> None:
    """FabricServerError must have status=500."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        client = await _get_client()
        async with client:
            with pytest.raises(FabricServerError) as exc_info:
                await client.request("GET", HttpBase.FABRIC, "/items")

    assert exc_info.value.status == 500


async def test_rate_limited_error_carries_status() -> None:
    """RateLimitedError raised after consecutive 429s must carry status=429."""

    def side_effect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"}, json={})

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = await _get_client(rps=10)
        async with client:
            with pytest.raises(RateLimitedError) as exc_info:
                await client.request("GET", HttpBase.FABRIC, "/items")

    assert exc_info.value.status == 429


# ---------------------------------------------------------------------------
# FabricError __str__ with hint and request_id
# ---------------------------------------------------------------------------


def test_fabric_error_str_no_extras() -> None:
    """FabricError without hint/request_id returns the plain message."""
    err = FabricError("plain message")
    assert str(err) == "plain message"


def test_fabric_error_str_with_hint() -> None:
    """FabricError with hint appends it on a new line."""
    err = FabricError("base msg", hint="try again later")
    assert str(err) == "base msg\nHint: try again later"


def test_fabric_error_str_with_request_id() -> None:
    """FabricError with request_id appends it."""
    err = FabricError("base msg", request_id="abc-123")
    assert str(err) == "base msg (request-id: abc-123)"


def test_fabric_error_str_with_hint_and_request_id() -> None:
    """FabricError with both hint and request_id includes both."""
    err = FabricError("base msg", hint="do X", request_id="rid-42")
    text = str(err)
    assert "Hint: do X" in text
    assert "request-id: rid-42" in text


# ---------------------------------------------------------------------------
# 429 deadline aggregation (concurrent)
# ---------------------------------------------------------------------------


async def test_429_deadline_aggregated_for_concurrent_requests() -> None:
    """Two concurrent 429s with Retry-After values aggregate to the MAX deadline.

    Both requests see a 429 first. After the deadline expires, both succeed.

    Uses a fake clock so the test is instantaneous: asserts that asyncio.sleep
    was called at least once with a duration >= 1.0 s (the Retry-After value),
    proving the aggregated pause deadline is honoured without real waiting.
    """
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            # First two calls return 429 with 1 second Retry-After
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return httpx.Response(200, json={"value": []})

    clock = _FakeClock()
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = FabricHttpClient(credential=_make_credential(), rps=10)
        async with client:
            with clock:
                results = await asyncio.gather(
                    client.request("GET", HttpBase.FABRIC, "/items"),
                    client.request("GET", HttpBase.FABRIC, "/items"),
                )

    assert all(r.status_code == 200 for r in results)
    # Both gather coroutines must have respected the shared deadline:
    # one sleep per coroutine, each >= 1.0 s (the Retry-After value).
    assert len(clock.sleeps) >= 2, (
        f"Expected >=2 deadline sleeps (one per coroutine); got {clock.sleeps}"
    )
    assert max(clock.sleeps) >= 1.0, (
        f"Largest sleep {max(clock.sleeps):.3f}s < 1.0 — Retry-After not honoured"
    )


# ---------------------------------------------------------------------------
# 429 honours Retry-After via deadline (single request)
# ---------------------------------------------------------------------------


async def test_429_deadline_single_request_honors_retry_after() -> None:
    """429 with Retry-After: 1 sets _pause_until and waits before retry.

    Uses a fake clock so the test is instantaneous: asserts that asyncio.sleep
    was called with a duration >= 1.0 s, proving the pause deadline from the
    Retry-After header is honoured deterministically.
    """
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return httpx.Response(200, json={"ok": True})

    clock = _FakeClock()
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = FabricHttpClient(credential=_make_credential(), rps=10)
        async with client:
            with clock:
                resp = await client.request("GET", HttpBase.FABRIC, "/items")

    assert resp.status_code == 200
    assert call_count == 2
    # Must have slept for at least the Retry-After duration (1 s).
    assert len(clock.sleeps) >= 1, f"Expected >=1 deadline sleep; got {clock.sleeps}"
    assert sum(clock.sleeps) >= 1.0, (
        f"Total sleep {sum(clock.sleeps):.3f}s < 1.0 — pause deadline not honoured"
    )


# ---------------------------------------------------------------------------
# Correlation id in debug log
# ---------------------------------------------------------------------------


async def test_debug_log_includes_request_id_when_present(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When x-ms-request-id is in the response, it must appear in the log extra."""
    req_id = "fabric-req-id-xyz"

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(
                200,
                headers={"x-ms-request-id": req_id},
                json={},
            )
        )

        client = await _get_client(rps=10)
        async with client:
            with caplog.at_level(logging.DEBUG, logger="fabric_dw.http"):
                await client.request("GET", HttpBase.FABRIC, "/items")

    fabric_records = [r for r in caplog.records if r.name == "fabric_dw.http"]
    assert len(fabric_records) >= 1
    record = fabric_records[0]
    # request_id should be available as an extra attribute on the log record
    assert hasattr(record, "request_id"), f"request_id not in log record: {record.__dict__}"
    assert record.request_id == req_id


# ---------------------------------------------------------------------------
# Configurability: constructor parameters
# ---------------------------------------------------------------------------


def test_constructor_parameters_are_stored() -> None:
    """FabricHttpClient stores custom constructor parameters."""
    cred = _make_credential()
    client = FabricHttpClient(
        credential=cred,
        rps=5,
        timeout=60.0,
        max_429_retries=3,
        poll_interval=5.0,
        token_refresh_buffer=600.0,
    )
    assert client._timeout == 60.0
    assert client._max_429_retries == 3
    assert client._poll_interval == 5.0
    assert client._token_refresh_buffer == 600.0


async def test_timeout_wired_into_http_client() -> None:
    """The timeout parameter must be forwarded to the underlying httpx.AsyncClient."""
    cred = _make_credential()
    client = FabricHttpClient(credential=cred, timeout=42.0)
    async with client:
        assert client._http is not None
        assert client._http.timeout.read == 42.0


# ---------------------------------------------------------------------------
# poll_operation jitter
# ---------------------------------------------------------------------------


async def test_poll_operation_jitter_within_bounds() -> None:
    """poll_operation sleep must add the jitter produced by random.uniform.

    Patches random.uniform to a fixed non-zero value (0.15) so the test is
    deterministic: each sleep must equal exactly base_wait + 0.15 where
    base_wait=1.0 (from Retry-After: 1).  This proves the code applies jitter
    rather than only checking that it falls within a probabilistic range.
    """
    poll_count = 0
    sleep_durations: list[float] = []

    original_sleep = asyncio.sleep

    async def mock_sleep(seconds: float) -> None:
        sleep_durations.append(seconds)
        # Use a tiny actual sleep so the test doesn't hang
        await original_sleep(0)

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        poll_count += 1
        if poll_count < 4:
            return httpx.Response(202, headers={"Retry-After": "1"}, json={"status": "Running"})
        return httpx.Response(200, json={"status": "Succeeded"})

    # Patch random.uniform to a fixed non-zero value so jitter is deterministic.
    # base_wait=1.0 (Retry-After: 1), jitter_max=1.0*0.25=0.25, fixed jitter=0.15
    _fixed_jitter = 0.15

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/operations/op-jitter").mock(
            side_effect=side_effect
        )

        client = FabricHttpClient(credential=_make_credential(), rps=10, poll_interval=2.0)
        async with client:
            with (
                unittest.mock.patch("asyncio.sleep", side_effect=mock_sleep),
                unittest.mock.patch(
                    "fabric_dw.http_client.random.uniform", return_value=_fixed_jitter
                ),
            ):
                await client.poll_operation(
                    "https://api.fabric.microsoft.com/v1/operations/op-jitter"
                )

    # Each sleep must be exactly base_wait (1.0) + fixed jitter (0.15) = 1.15
    assert len(sleep_durations) >= 3, f"Expected at least 3 sleeps; got {len(sleep_durations)}"
    for dur in sleep_durations:
        assert dur == pytest.approx(1.0 + _fixed_jitter), (
            f"Sleep {dur:.3f} != expected {1.0 + _fixed_jitter:.3f}; jitter may not be applied"
        )


# ---------------------------------------------------------------------------
# Garbage Retry-After header (regression: must not crash)
# ---------------------------------------------------------------------------


async def test_429_with_garbage_retry_after_falls_back_and_succeeds() -> None:
    """A malformed Retry-After value must NOT raise ValueError.

    The 429 loop must catch the parse error, fall back to 1.0s,
    log a warning, and then retry — succeeding on the next response.
    """
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "garbage"}, json={})
        return httpx.Response(200, json={"ok": True})

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = FabricHttpClient(credential=_make_credential(), rps=10)
        async with client:
            resp = await client.request("GET", HttpBase.FABRIC, "/items")

    # Must succeed (no ValueError propagated)
    assert resp.status_code == 200
    assert call_count == 2


async def test_429_garbage_retry_after_emits_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A malformed Retry-After must emit a WARNING log with the raw header value."""
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "soon"}, json={})
        return httpx.Response(200, json={})

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = FabricHttpClient(credential=_make_credential(), rps=10)
        async with client:
            with caplog.at_level(logging.WARNING, logger="fabric_dw.http"):
                await client.request("GET", HttpBase.FABRIC, "/items")

    warning_records = [
        r for r in caplog.records if r.levelno == logging.WARNING and r.name == "fabric_dw.http"
    ]
    assert len(warning_records) >= 1, f"No WARNING emitted; records={caplog.records}"
    assert "soon" in warning_records[0].getMessage(), (
        f"Raw header value not in warning: {warning_records[0].getMessage()}"
    )


# ---------------------------------------------------------------------------
# Token fetch ordering: deadline wait BEFORE token fetch
# ---------------------------------------------------------------------------


async def test_send_once_deadline_sleep_happens_before_get_token() -> None:
    """_send_once must sleep for the pause deadline BEFORE calling _get_token.

    We monkeypatch _send_once internals by tracking call order via a shared
    event log: asyncio.sleep records when it's called; _get_token records
    when it's called.  The sleep entry must precede the get_token entry.
    """
    event_log: list[str] = []

    original_get_token = FabricHttpClient._get_token  # type: ignore[attr-defined]
    original_sleep = asyncio.sleep

    async def tracking_get_token(self: FabricHttpClient) -> str:
        event_log.append("get_token")
        return await original_get_token(self)

    async def tracking_sleep(seconds: float) -> None:
        if seconds > 0:
            event_log.append(f"sleep:{seconds:.2f}")
        await original_sleep(0)  # Don't actually wait in tests

    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0.05"}, json={})
        return httpx.Response(200, json={"ok": True})

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = FabricHttpClient(credential=_make_credential(), rps=10)
        async with client:
            with (
                unittest.mock.patch.object(FabricHttpClient, "_get_token", tracking_get_token),
                unittest.mock.patch("asyncio.sleep", side_effect=tracking_sleep),
            ):
                resp = await client.request("GET", HttpBase.FABRIC, "/items")

    assert resp.status_code == 200
    assert call_count == 2

    # Verify ordering: the deadline sleep must appear BEFORE the second get_token call
    sleep_events = [e for e in event_log if e.startswith("sleep:")]
    token_events = [e for e in event_log if e == "get_token"]

    assert len(sleep_events) >= 1, f"Expected at least one deadline sleep; log={event_log}"
    assert len(token_events) >= 2, f"Expected >=2 get_token calls in log={event_log}"

    first_sleep_idx = next(i for i, e in enumerate(event_log) if e.startswith("sleep:"))
    second_token_idx = [i for i, e in enumerate(event_log) if e == "get_token"][1]
    assert first_sleep_idx < second_token_idx, (
        f"Deadline sleep ({first_sleep_idx}) must precede second get_token ({second_token_idx}); "
        f"log={event_log}"
    )


# ---------------------------------------------------------------------------
# _pause_until extended mid-sleep triggers a second sleep (while-loop guard)
# ---------------------------------------------------------------------------


async def test_send_once_re_sleeps_when_pause_until_extended_mid_sleep() -> None:
    """_send_once must re-check _pause_until after waking and sleep again if extended.

    Regression for the original single-if implementation: if a concurrent coroutine
    extends _pause_until while this coroutine is sleeping, the while loop must
    detect the remaining time and sleep again rather than proceeding immediately.

    The original test was FLAKY because it used ``time.monotonic() + 0.05`` to set
    the deadline, so a slow scheduler could let real time advance past the deadline
    before _send_once checked it, recording zero sleeps.

    Fix: use ``_FakeClock`` to control the monotonic clock.  The fake clock starts
    at ``now=1000.0``; we set ``_pause_until = 1001.0`` (1 s in the fake future).
    The fake sleep advances the clock, so after the first sleep the clock is at
    1001.0 + extension.  The while loop detects the extension and sleeps again.
    No real time passes; the test is deterministic.
    """
    clock = _FakeClock(now=1_000.0)
    # We need a custom sleep that extends _pause_until on the first call,
    # simulating a concurrent coroutine updating the shared deadline.
    # We wrap clock.sleep to inject the side-effect on the first call.
    first_sleep_done = False

    async def extending_sleep(seconds: float) -> None:
        nonlocal first_sleep_done
        if not first_sleep_done and seconds > 0:
            # Simulate a concurrent coroutine extending the deadline mid-sleep.
            client._pause_until += 0.5
            first_sleep_done = True
        await clock.sleep(seconds)

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        client = FabricHttpClient(credential=_make_credential(), rps=10)
        async with client:
            # Set deadline 1 s into the fake future (clock.now = 1000, deadline = 1001).
            # This is guaranteed to be in the future because the fake clock is frozen
            # until fake sleep is called — no real-time race condition.
            client._pause_until = clock.now + 1.0
            with (
                patch("fabric_dw.http_client._time.monotonic", side_effect=clock.monotonic),
                patch("asyncio.sleep", side_effect=extending_sleep),
            ):
                resp = await client.request("GET", HttpBase.FABRIC, "/items")

    assert resp.status_code == 200
    # The while loop must have produced at least 2 deadline sleeps:
    # once for the original deadline (1 s), once after _pause_until was extended (0.5 s).
    assert len(clock.sleeps) >= 2, (
        f"Expected >=2 sleeps (original + extended deadline); got {clock.sleeps}"
    )


# ---------------------------------------------------------------------------
# 4xx body surfacing (BadRequestError for unmapped 4xx)
# ---------------------------------------------------------------------------


async def test_400_raises_bad_request_error_with_json_body() -> None:
    """HTTP 400 must raise BadRequestError and include the parsed JSON error body.

    Before this fix, 400 passed through _map_status silently, causing the Fabric
    errorCode/message to be discarded.  Now the body is surfaced on the exception.
    """
    error_payload = {
        "errorCode": "InvalidItemType",
        "message": "The item type 'Warehouse' is not valid for this endpoint.",
        "requestId": "req-abc-123",
    }
    with respx.mock:
        respx.post("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(400, json=error_payload)
        )
        client = await _get_client()
        async with client:
            with pytest.raises(BadRequestError) as exc_info:
                await client.request("POST", HttpBase.FABRIC, "/items", json={"x": 1})

    err = exc_info.value
    assert err.status == 400
    assert err.body is not None
    assert err.body.get("errorCode") == "InvalidItemType"
    assert "InvalidItemType" in str(err)


async def test_400_raises_bad_request_error_with_plain_text_body() -> None:
    """HTTP 400 with a non-JSON body must still raise BadRequestError (body=None)."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(400, text="Bad Request")
        )
        client = await _get_client()
        async with client:
            with pytest.raises(BadRequestError) as exc_info:
                await client.request("GET", HttpBase.FABRIC, "/items")

    assert exc_info.value.status == 400
    assert exc_info.value.body is None  # non-JSON body → body not parsed


async def test_422_raises_bad_request_error() -> None:
    """Any unmapped 4xx (e.g. 422) must raise BadRequestError, not pass through silently."""
    with respx.mock:
        respx.post("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(422, json={"errorCode": "ValidationFailed"})
        )
        client = await _get_client()
        async with client:
            with pytest.raises(BadRequestError) as exc_info:
                await client.request("POST", HttpBase.FABRIC, "/items", json={})

    assert exc_info.value.status == 422


async def test_bad_request_error_includes_request_id_header() -> None:
    """BadRequestError must capture x-ms-request-id when present in the response headers."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(
                400,
                json={"errorCode": "SomeError"},
                headers={"x-ms-request-id": "req-id-xyz"},
            )
        )
        client = await _get_client()
        async with client:
            with pytest.raises(BadRequestError) as exc_info:
                await client.request("GET", HttpBase.FABRIC, "/items")

    assert exc_info.value.request_id == "req-id-xyz"
    assert "req-id-xyz" in str(exc_info.value)


async def test_401_still_raises_auth_error_not_bad_request() -> None:
    """HTTP 401 must still raise AuthError (mapped in _STATUS_TO_EXC), not BadRequestError."""
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        client = await _get_client()
        async with client:
            with pytest.raises(AuthError):
                await client.request("GET", HttpBase.FABRIC, "/items")


# ---------------------------------------------------------------------------
# Timeout retry: idempotent methods retried; non-idempotent not retried
# ---------------------------------------------------------------------------


async def test_get_timeout_once_then_success_is_retried() -> None:
    """A GET that times out once then succeeds must be retried (idempotent method).

    The first call raises httpx.ReadTimeout; the second returns 200.
    The client must return the successful response without propagating the timeout.
    """
    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ReadTimeout("read timeout", request=request)
        return httpx.Response(200, json={"value": []})

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = await _get_client(rps=10)
        async with client:
            resp = await client.request("GET", HttpBase.FABRIC, "/items")

    assert resp.status_code == 200
    assert call_count == 2, f"Expected 2 calls (1 timeout + 1 success); got {call_count}"


async def test_post_timeout_is_not_retried() -> None:
    """A POST that times out must NOT be retried (non-idempotent method).

    Re-sending a timed-out POST risks duplicating server-side state or causing
    a 409 Conflict.  The timeout must be raised immediately.
    """
    call_count = 0

    def side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ReadTimeout("read timeout", request=request)

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.post("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = await _get_client(rps=10)
        async with client:
            with pytest.raises(httpx.ReadTimeout):
                await client.request("POST", HttpBase.FABRIC, "/items", json={"x": 1})

    assert call_count == 1, (
        f"POST timeout must not be retried; expected 1 call but got {call_count}"
    )


def test_default_timeout_is_60_seconds() -> None:
    """The default request timeout must be 60.0 seconds (bumped from 30.0).

    Slow Fabric responses during LRO polling or large query results were causing
    spurious ReadTimeout errors; a 60s default reduces this without disabling
    timeouts entirely.
    """
    assert _DEFAULT_TIMEOUT == 60.0, f"Expected _DEFAULT_TIMEOUT == 60.0; got {_DEFAULT_TIMEOUT}"


async def test_post_5xx_is_retried() -> None:
    """POST on 5xx must still be retried (FabricServerError path in _should_retry).

    The switch from retry_if_exception_type(FabricServerError) to
    retry_if_exception(_should_retry) keeps 5xx retry regardless of HTTP method.
    This test guards against accidentally removing the isinstance(exc,
    FabricServerError) branch from _should_retry.
    """
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, json={"error": "server error"})

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.post("https://api.fabric.microsoft.com/v1/items").mock(side_effect=side_effect)

        client = await _get_client(rps=10)
        async with client:
            with pytest.raises(FabricServerError):
                await client.request("POST", HttpBase.FABRIC, "/items", json={"x": 1})

    assert call_count == 3, f"POST 5xx must be retried 3 times (tenacity budget); got {call_count}"


# ---------------------------------------------------------------------------
# C04: poll_operation malformed Retry-After must not crash
# ---------------------------------------------------------------------------


async def test_poll_operation_malformed_retry_after_uses_fallback() -> None:
    """poll_operation must survive a malformed Retry-After header (C04).

    A garbage value must not propagate as an exception — _parse_retry_after
    always returns a float (0.0 on failure), and poll_operation falls back to
    poll_interval when it gets 0.0 from a non-null header.
    """
    poll_count = 0
    sleep_durations: list[float] = []
    original_sleep = asyncio.sleep

    async def mock_sleep(seconds: float) -> None:
        sleep_durations.append(seconds)
        await original_sleep(0)

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            return httpx.Response(
                202,
                headers={"Retry-After": "not-a-date-or-number"},
                json={"status": "Running"},
            )
        return httpx.Response(200, json={"status": "Succeeded"})

    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/operations/op-c04").mock(
            side_effect=side_effect
        )

        client = FabricHttpClient(credential=_make_credential(), rps=10, poll_interval=0.5)
        async with client:
            with unittest.mock.patch("asyncio.sleep", side_effect=mock_sleep):
                result = await client.poll_operation(
                    "https://api.fabric.microsoft.com/v1/operations/op-c04"
                )

    assert result["status"] == "Succeeded"
    # Must have slept once (falling back to poll_interval on malformed header)
    assert len(sleep_durations) >= 1, f"Expected at least 1 sleep; got {sleep_durations}"
    # The sleep duration should be around poll_interval (0.5 s), not 0.
    assert all(d > 0 for d in sleep_durations), (
        f"All sleeps must be positive (fallback to poll_interval); got {sleep_durations}"
    )


# ---------------------------------------------------------------------------
# C06: per-scope token cache
# ---------------------------------------------------------------------------


async def test_get_token_caches_per_scope() -> None:
    """_get_token must maintain separate cache entries per scope (C06).

    Fetching a token for FABRIC_SCOPE and then for SQL_SCOPE must produce
    two separate get_token calls (one per scope), not reuse the FABRIC_SCOPE
    token for the SQL request.
    """
    scopes_requested: list[str] = []

    async def tracking_get_token(scope: str, *_: object, **__: object) -> AccessToken:
        scopes_requested.append(scope)
        return AccessToken(token=f"token-for-{scope}", expires_on=int(time.time()) + 3600)

    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(side_effect=tracking_get_token)

    client = FabricHttpClient(credential=cred, rps=10)
    async with client:
        tok_fabric = await client._get_token(FABRIC_SCOPE)  # type: ignore[attr-defined]
        tok_sql = await client._get_token(SQL_SCOPE)  # type: ignore[attr-defined]
        # Second call for each scope should be from cache (token not expired)
        tok_fabric2 = await client._get_token(FABRIC_SCOPE)  # type: ignore[attr-defined]

    assert tok_fabric == f"token-for-{FABRIC_SCOPE}"
    assert tok_sql == f"token-for-{SQL_SCOPE}"
    # Cache hit: third call must NOT trigger another credential fetch
    assert tok_fabric2 == tok_fabric
    assert scopes_requested.count(FABRIC_SCOPE) == 1, (
        f"Expected 1 fetch for FABRIC_SCOPE; got {scopes_requested.count(FABRIC_SCOPE)}"
    )
    assert scopes_requested.count(SQL_SCOPE) == 1, (
        f"Expected 1 fetch for SQL_SCOPE; got {scopes_requested.count(SQL_SCOPE)}"
    )


# ---------------------------------------------------------------------------
# C16: iter_paginated non-dict page body is handled gracefully
# ---------------------------------------------------------------------------


async def test_iter_paginated_non_dict_page_does_not_crash() -> None:
    """iter_paginated must not crash when a page body is not a dict (C16).

    A list or null JSON body should produce zero items and stop pagination
    cleanly, not crash with AttributeError on .get().
    """
    with respx.mock:
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(200, json=[{"id": "x"}, {"id": "y"}])
        )

        client = await _get_client()
        async with client:
            items = [item async for item in client.iter_paginated(HttpBase.FABRIC, "/items")]

    # Non-dict body → no items yielded, no exception raised
    assert items == [], f"Expected empty list for non-dict page; got {items}"


# ---------------------------------------------------------------------------
# C27: combined retry deadline bounds overall wait
# ---------------------------------------------------------------------------


async def test_combined_deadline_aborts_429_loop() -> None:
    """A combined_deadline_s that has already elapsed must abort the 429-loop (C27).

    Sets combined_deadline_s=0 so the deadline is immediately in the past.
    The first iteration of _do_request must detect this and raise RateLimitedError
    rather than entering the 429 retry loop at all.
    """
    with respx.mock:
        # The route is registered but should never be hit because the deadline
        # is already past before any request is made.
        respx.get("https://api.fabric.microsoft.com/v1/items").mock(
            return_value=httpx.Response(200, json={})
        )

        client = FabricHttpClient(
            credential=_make_credential(),
            rps=10,
            combined_deadline_s=0,  # deadline already expired
        )
        async with client:
            with pytest.raises(RateLimitedError, match="deadline"):
                await client.request("GET", HttpBase.FABRIC, "/items")
