"""Root conftest: env-var isolation for all tests.

Guarantees that every test starts without any ``FABRIC_MCP_*`` guard variables
set in the process environment, regardless of the shell or CI environment that
launched the process.  Tests that need a specific value set them explicitly via
``monkeypatch.setenv`` or ``pytest.MonkeyPatch``; those values are cleaned up
automatically after the test by pytest's monkeypatch machinery.

Without this autouse fixture, a test that asserts "the guard is absent so the
operation is allowed" would silently become a false positive whenever a developer
runs the suite from a shell that exports ``FABRIC_MCP_READONLY=1`` or similar.
"""

from __future__ import annotations

import pytest

# All environment variables that can alter the MCP security/filter behaviour.
_FABRIC_MCP_VARS = (
    "FABRIC_MCP_READONLY",
    "FABRIC_MCP_ALLOW_DESTRUCTIVE",
    "FABRIC_MCP_WORKSPACES",
    "FABRIC_MCP_ALLOW_REMOTE",
)


@pytest.fixture(autouse=True)
def _isolate_fabric_mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all FABRIC_MCP_* env vars for the duration of every test.

    Tests that need a specific value should set it explicitly::

        def test_readonly(monkeypatch):
            monkeypatch.setenv("FABRIC_MCP_READONLY", "1")
            ...
    """
    for var in _FABRIC_MCP_VARS:
        monkeypatch.delenv(var, raising=False)
