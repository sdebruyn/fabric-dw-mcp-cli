"""Bounded-concurrency helpers for Fabric service calls.

This is THE single place where the concurrency-vs-rate-limit tradeoff is
decided.  All bulk fan-out in the services layer goes through
:func:`bounded_gather`; the underlying HTTP client's ``aiolimiter`` RPS
limiter still applies beneath this layer and guards against 429s.

Choosing ``concurrency=8`` here means at most 8 workspace-level requests are
in-flight simultaneously.  Raise it if the tenant has a high rate limit;
lower it if you see 429 responses slipping through.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal, TypeVar, overload

__all__ = ["bounded_gather"]

T = TypeVar("T")


@overload
async def bounded_gather(
    factories: Sequence[Callable[[], Awaitable[T]]],
    *,
    concurrency: int = ...,
    return_exceptions: Literal[False] = ...,
) -> list[T]: ...


@overload
async def bounded_gather(
    factories: Sequence[Callable[[], Awaitable[T]]],
    *,
    concurrency: int = ...,
    return_exceptions: Literal[True],
) -> list[T | BaseException]: ...


async def bounded_gather(
    factories: Sequence[Callable[[], Awaitable[T]]],
    *,
    concurrency: int = 8,
    return_exceptions: bool = False,
) -> list[T] | list[T | BaseException]:
    """Run coroutine factories with bounded concurrency, preserving input order.

    Takes *zero-argument callables* that each return a coroutine (factories),
    rather than pre-created coroutines.  This avoids the "coroutine was never
    awaited" warning that arises when ``asyncio.Semaphore`` queues work that
    was already scheduled but not yet started.

    Args:
        factories: A sequence of zero-argument callables, each returning an
            awaitable.  They are started in order but may complete out of
            order; the result list is reordered to match input order.
        concurrency: Maximum number of coroutines running simultaneously.
            Defaults to 8.
        return_exceptions: When ``False`` (default) the first exception
            propagates immediately and cancels remaining tasks.  When
            ``True`` exceptions are caught and placed in the result list
            instead of being raised.

    Returns:
        A list of results in the same order as *factories*.  When
        *return_exceptions* is ``True``, failed entries contain the
        exception instance instead of a result value.

    Raises:
        BaseException: The first exception raised by any factory when
            *return_exceptions* is ``False``.
    """
    if not factories:
        return []

    semaphore = asyncio.Semaphore(concurrency)

    async def _run(factory: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await factory()

    tasks = [asyncio.create_task(_run(f)) for f in factories]

    results: list[T | BaseException] = []
    if return_exceptions:
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        results = list(raw)
    else:
        try:
            raw = await asyncio.gather(*tasks, return_exceptions=False)
            results = list(raw)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    return results
