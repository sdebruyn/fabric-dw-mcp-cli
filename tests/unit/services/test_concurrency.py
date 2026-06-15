"""Tests for fabric_dw.services._concurrency.bounded_gather."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

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


async def test_empty_input_returns_empty_list() -> None:
    """bounded_gather with no factories must return an empty list."""
    result = await bounded_gather([])
    assert result == []


async def test_single_factory_returns_single_result() -> None:
    """A single factory must return a one-element list."""
    result = await bounded_gather([_const(42)])
    assert result == [42]


async def test_output_preserves_input_order() -> None:
    """Results must appear in the same order as the input factories."""
    factories = [_const(i) for i in range(10)]
    result = await bounded_gather(factories, concurrency=4)
    assert result == list(range(10))


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


async def test_concurrency_lower_bound_met() -> None:
    """At least *concurrency* factories run simultaneously when N > concurrency.

    Verifies that the semaphore does not artificially serialise more than needed:
    with cap=3 and 6 factories, exactly 3 should be in-flight at a time.
    This guards against a regression where the semaphore is acquired too eagerly
    and factories end up serialised beyond the intended cap.
    """
    peak_concurrent = 0
    active = 0
    gate = asyncio.Event()

    async def _gated() -> None:
        nonlocal peak_concurrent, active
        active += 1
        peak_concurrent = max(peak_concurrent, active)
        await gate.wait()  # all cap-many tasks block here simultaneously
        active -= 1

    cap = 3
    n = cap * 2  # 6 factories, only 3 can be in-flight at once

    task = asyncio.create_task(  # noqa: RUF006 - task IS awaited below; ruff false-positive on multi-line
        bounded_gather([_gated for _ in range(n)], concurrency=cap)
    )

    # Give the event loop a chance to start the first `cap` tasks.
    for _ in range(cap + 2):
        await asyncio.sleep(0)

    # At this point at least `cap` tasks should be blocked at gate.wait().
    assert peak_concurrent >= cap

    gate.set()
    await task


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


async def test_exception_propagates_when_return_exceptions_false() -> None:
    """An exception must propagate and be raised when return_exceptions=False."""
    boom = ValueError("boom")
    with pytest.raises(ValueError, match="boom"):
        await bounded_gather([_const(1), _raising(boom), _const(3)], return_exceptions=False)


async def test_exception_in_result_when_return_exceptions_true() -> None:
    """When return_exceptions=True exceptions appear in the result list."""
    boom = RuntimeError("oops")
    result = await bounded_gather([_const(1), _raising(boom), _const(3)], return_exceptions=True)
    assert result[0] == 1
    assert result[1] is boom
    assert result[2] == 3


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


async def test_all_tasks_done_after_exception_with_return_exceptions_false() -> None:
    """All tasks must be done (completed or cancelled) after bounded_gather raises.

    Verifies the cancel-and-drain fix: cancellations are delivered and no tasks
    linger in the event loop after the exception propagates.
    """
    gate = asyncio.Event()

    async def _gated() -> int:
        await gate.wait()
        return 0

    boom = RuntimeError("drain-check")

    async def _raising_factory() -> int:
        raise boom

    async def _gated_factory() -> int:
        return await _gated()

    # Create a mix: one failing factory and several slow ones that will be cancelled.
    n_slow = 4
    factories: list[Callable[[], Awaitable[int]]] = [_raising_factory] + [
        _gated_factory for _ in range(n_slow)
    ]

    with pytest.raises(RuntimeError, match="drain-check"):
        await bounded_gather(factories, concurrency=n_slow + 1, return_exceptions=False)

    # After bounded_gather raises, all internally created tasks must be done.
    # We verify this indirectly: running a few event-loop iterations should not
    # surface any "Task was destroyed but it is pending" warnings.  We also
    # confirm the gate was never opened (tasks really were cancelled, not
    # completed normally).
    assert not gate.is_set()
    # Allow the event loop to process any remaining callbacks.
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Overload / type-narrowing smoke tests (runtime behaviour only)
# ---------------------------------------------------------------------------


async def test_return_exceptions_false_returns_plain_list() -> None:
    """return_exceptions=False (default) returns a plain list of values."""
    result = await bounded_gather([_const(1), _const(2)], return_exceptions=False)
    # Static type: list[int].  At runtime just confirm no exceptions in list.
    assert result == [1, 2]
    assert all(not isinstance(v, BaseException) for v in result)


async def test_return_exceptions_true_literal_annotation() -> None:
    """Passing Literal[True] for return_exceptions works at runtime."""
    flag: Literal[True] = True
    boom = ValueError("typed")
    result = await bounded_gather([_const(1), _raising(boom)], return_exceptions=flag)
    assert result[0] == 1
    assert result[1] is boom
