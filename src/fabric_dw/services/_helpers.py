"""Shared utilities for Fabric DW services."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import Protocol, TypeVar
from uuid import UUID

__all__ = ["compact", "scan_all_workspaces"]

_T = TypeVar("_T")


class _HasNameAndId(Protocol):
    """Structural protocol for objects with ``name`` and ``id`` attributes."""

    @property
    def name(self) -> str: ...

    @property
    def id(self) -> UUID: ...


def compact(mapping: Mapping[str, object]) -> dict[str, object]:
    """Return a copy of *mapping* with all ``None``-valued entries removed.

    Use this to build request bodies that should omit optional fields::

        body = compact({"displayName": name, "description": description})

    Args:
        mapping: A mapping whose values may be ``None``.

    Returns:
        A new ``dict[str, object]`` with every key whose value is ``None``
        filtered out.
    """
    return {k: v for k, v in mapping.items() if v is not None}


async def scan_all_workspaces(
    workspaces: Sequence[_HasNameAndId],
    fetch: Callable[[_HasNameAndId], Coroutine[object, object, list[_T]]],
    *,
    logger: logging.Logger,
    skip_errors: tuple[type[BaseException], ...],
) -> list[_T]:
    """Fan-out *fetch* over every workspace with bounded concurrency.

    Workspaces that raise any exception in *skip_errors* are skipped with a
    per-workspace ``warning`` log entry.  Any other exception (including other
    :class:`BaseException` subclasses) propagates immediately.

    Args:
        workspaces: Sequence of workspace objects.  Each element must have a
            ``name`` attribute used in log messages.
        fetch: Async callable that receives a workspace object and returns a
            ``list[T]`` of items for that workspace.
        logger: Logger for per-workspace and summary warnings.
        skip_errors: Exception types to skip (log + continue).

    Returns:
        A flat list of all items collected from accessible workspaces.
    """
    # Import here to avoid circular imports at module level.
    from fabric_dw.services._concurrency import bounded_gather  # noqa: PLC0415

    total = len(workspaces)
    raw = await bounded_gather(
        [lambda ws=ws: fetch(ws) for ws in workspaces],  # type: ignore[misc]
        return_exceptions=True,
    )

    out: list[_T] = []
    skipped = 0
    for ws, result in zip(workspaces, raw, strict=True):
        if isinstance(result, skip_errors):
            logger.warning("skipping workspace %s: %s", ws.name, result)
            skipped += 1
        elif isinstance(result, BaseException):
            raise result
        else:
            out.extend(result)  # type: ignore[arg-type]

    if skipped:
        logger.warning("skipped %d of %d workspaces due to access errors", skipped, total)

    return out
