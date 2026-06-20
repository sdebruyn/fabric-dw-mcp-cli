"""Unit-test shared fixtures."""

from __future__ import annotations

import logging
from collections.abc import Generator
from unittest.mock import patch

import pytest
from pytest_socket import disable_socket, enable_socket


@pytest.fixture(autouse=True)
def _block_real_sockets() -> Generator[None, None, None]:
    """Block all real network I/O for every unit test.

    Unit tests must not make live HTTP calls (or any TCP/UDP connections) to
    external hosts.  This fixture calls ``pytest-socket``'s ``disable_socket()``
    which replaces the ``socket.socket`` constructor with a stub that raises
    ``SocketBlockedError`` immediately, making accidental network access fail
    loudly instead of hanging or silently returning stale data.

    If a unit test legitimately needs to connect to ``localhost`` (e.g. an
    in-process server), pass ``allow_hosts=["localhost", "127.0.0.1", "::1"]``
    to ``disable_socket()``, or use ``@pytest.mark.allow_hosts(["localhost"])``
    on that specific test.

    Integration tests (``tests/integration/``) are NOT subject to this fixture
    because this conftest lives under ``tests/unit/`` and pytest scopes conftest
    fixtures to their directory tree.

    Unix-domain sockets (AF_UNIX) are allowed because asyncio's internal
    event-loop uses ``socket.socketpair()`` (AF_UNIX) as a wakeup pipe.
    Blocking those would crash every async test with ``SocketBlockedError``
    before the test body even runs.  AF_UNIX sockets cannot reach the internet,
    so permitting them is safe.
    """
    disable_socket(allow_unix_socket=True)
    try:
        yield
    finally:
        enable_socket()


@pytest.fixture(autouse=True)
def _suppress_telemetry_notice(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    """Suppress the first-run telemetry notice in all unit tests.

    Tests in test_telemetry.py test the notice behaviour directly and opt out
    by being in that module; all other unit tests are shielded so the notice
    does not appear in captured output and break JSON-parsing assertions.

    We patch the real binding on the telemetry module (A4) and also the
    imported references in each call site so all invocation paths are covered.
    """
    if "test_telemetry" in str(request.path):
        yield
        return

    with (
        patch("fabric_dw.telemetry.maybe_print_first_run_notice"),
        patch("fabric_dw.cli._main.maybe_print_first_run_notice"),
        patch("fabric_dw.mcp.server.maybe_print_first_run_notice"),
    ):
        yield


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
