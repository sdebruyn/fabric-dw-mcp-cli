"""Unit-test shared fixtures."""

from __future__ import annotations

import logging
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _reset_fabric_dw_logger() -> Generator[None, None, None]:
    """Restore ``fabric_dw`` logger state after every unit test.

    ``setup_logging()`` (C11) scopes to the ``fabric_dw`` named logger and sets
    ``propagate=False``.  Without cleanup that leaks across test modules —
    caplog fixtures in other modules stop capturing because records no longer
    propagate to the root logger where pytest installs its handler.

    This fixture restores the original propagation flag, level, and handler
    list so each test starts with a clean logging slate.
    """
    pkg = logging.getLogger("fabric_dw")
    orig_propagate = pkg.propagate
    orig_level = pkg.level
    orig_handlers = list(pkg.handlers)
    yield
    pkg.propagate = orig_propagate
    pkg.setLevel(orig_level)
    for h in list(pkg.handlers):
        pkg.removeHandler(h)
    for h in orig_handlers:
        pkg.addHandler(h)
