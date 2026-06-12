"""Tests for fabric_dw.services._concurrency.bounded_gather."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from fabric_dw.services._concurrency import bounded_gather

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _const(value: int) -> Callable[[], Awaitable[int]]:
    """Return a factory that immediately resolves to *value*."""

    async def _f() -> int:
        return value

    return _f


def _raising(exc: BaseException) -> Callable[[], Awaitable[int]]:
    """Return a factory that immediately raises *exc*."""

    async def _f() -> int:
        raise exc

    return _f


# ---------------------------------------------------------------------------
# Basic correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_input_returns_empty_list() -> None:
    """bounded_gather with no factories must return an empty list."""
    result = await bounded_gather([])
    assert result == []


@pytest.mark.asyncio
async def test_single_factory_returns_single_result() -> None:
    """A single factory must return a one-element list."""
    result = await bounded_gather([_const(42)])
    assert result == [42]


@pytest.mark.asyncio
async def test_output_preserves_input_order() -> None:
    """Results must appear in the same order as the input factories."""
    factories = [_const(i) for i in range(10)]
    result = await bounded_gather(factories, concurrency=4)
    assert result == list(range(10))


@pytest.mark.asyncio
async def test_all_factories_called() -> None:
    """Every factory must be invoked exactly once."""
    called: list[int] = []

    def _factory(i: int) -> Callable[[], Awaitable[None]]:
        async def _f() -> None:
            called.append(i)

        return _f

    await bounded_gather([_factory(i) for i in range(5)])
    assert sorted(called) == list(range(5))


# ---------------------------------------------------------------------------
# Concurrency cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_cap_not_exceeded() -> None:
    """At most *concurrency* factories may run simultaneously."""
    max_concurrent = 0
    active = 0

    async def _slow() -> None:
        nonlocal max_concurrent, active
        active += 1
        max_concurrent = max(max_concurrent, active)
        # Yield control so other tasks can start if the semaphore allows.
        await asyncio.sleep(0)
        active -= 1

    cap = 3
    factories = [_slow for _ in range(10)]
    await bounded_gather(factories, concurrency=cap)

    assert max_concurrent <= cap


@pytest.mark.asyncio
async def test_concurrency_cap_of_one_runs_serially() -> None:
    """concurrency=1 must result in strictly serial execution."""
    order: list[str] = []

    def _factory(label: str) -> Callable[[], Awaitable[None]]:
        async def _f() -> None:
            order.append(f"start:{label}")
            await asyncio.sleep(0)
            order.append(f"end:{label}")

        return _f

    await bounded_gather([_factory("a"), _factory("b"), _factory("c")], concurrency=1)
    assert order == ["start:a", "end:a", "start:b", "end:b", "start:c", "end:c"]


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_execution_all_started_before_first_completes() -> None:
    """All factories are started before the first one completes (when concurrency allows)."""
    n = 5
    start_events: list[asyncio.Event] = [asyncio.Event() for _ in range(n)]
    proceed_event = asyncio.Event()

    async def _gated(i: int) -> int:
        start_events[i].set()
        # Block until we release all tasks.
        await proceed_event.wait()
        return i

    # Start the gather but don't await yet — run as a task.
    task = asyncio.create_task(
        bounded_gather([lambda i=i: _gated(i) for i in range(n)], concurrency=n)
    )

    # Wait until all n factories have signalled that they started.
    await asyncio.gather(*[asyncio.wait_for(e.wait(), timeout=2.0) for e in start_events])

    # Now allow all tasks to finish.
    proceed_event.set()
    results = await task

    assert sorted(results) == list(range(n))


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exception_propagates_when_return_exceptions_false() -> None:
    """An exception must propagate and be raised when return_exceptions=False."""
    boom = ValueError("boom")
    with pytest.raises(ValueError, match="boom"):
        await bounded_gather([_const(1), _raising(boom), _const(3)], return_exceptions=False)


@pytest.mark.asyncio
async def test_exception_in_result_when_return_exceptions_true() -> None:
    """When return_exceptions=True exceptions appear in the result list."""
    boom = RuntimeError("oops")
    result = await bounded_gather([_const(1), _raising(boom), _const(3)], return_exceptions=True)
    assert result[0] == 1
    assert result[1] is boom
    assert result[2] == 3


@pytest.mark.asyncio
async def test_multiple_exceptions_all_captured_when_return_exceptions_true() -> None:
    """All exceptions are captured when return_exceptions=True."""
    err_a = ValueError("a")
    err_b = TypeError("b")
    result = await bounded_gather(
        [_raising(err_a), _const(99), _raising(err_b)], return_exceptions=True
    )
    assert result[0] is err_a
    assert result[1] == 99
    assert result[2] is err_b
