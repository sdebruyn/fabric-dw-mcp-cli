"""Shared utilities for Fabric DW services."""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["compact"]


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
