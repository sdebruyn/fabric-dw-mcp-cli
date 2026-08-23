"""Shared, non-fixture test helpers for tests/unit/cli/ and its subpackages.

``_StopWatchError`` is not a pytest fixture -- it is a plain exception class
imported directly by ``--watch`` loop tests to terminate an otherwise-infinite
watch loop after a fixed number of iterations (see ``fabric_dw.cli._watch``).
Centralised here so ``test_watch.py``, ``commands/test_queries.py``, and
``commands/test_sql.py`` share one definition instead of three near-identical
copies.
"""

from __future__ import annotations


class _StopWatchError(Exception):
    """Terminate a watch-loop test after the requested number of sleeps."""
