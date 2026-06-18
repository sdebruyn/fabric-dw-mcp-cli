"""Root conftest: env-var isolation for all tests.

Guarantees that every test starts without any ``FABRIC_MCP_*`` guard variables
set in the process environment, regardless of the shell or CI environment that
launched the process.  Tests that need a specific value set them explicitly via
``monkeypatch.setenv`` or ``pytest.MonkeyPatch``; those values are cleaned up
automatically after the test by pytest's monkeypatch machinery.

Without this autouse fixture, a test that asserts "the guard is absent so the
operation is allowed" would silently become a false positive whenever a developer
runs the suite from a shell that exports ``FABRIC_MCP_READONLY=1`` or similar.

This conftest also disables anonymous telemetry for the whole test run
(``_disable_telemetry_globally``) so that no test — in-process or subprocess —
ever performs a real telemetry send to the production Application Insights
resource.
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

# Test modules that exercise telemetry behaviour directly and therefore manage
# their own FABRIC_DISABLE_TELEMETRY / FABRIC_TELEMETRY env state.  They are
# exempt from the global telemetry-disable fixture below.
_TELEMETRY_SELF_MANAGED_MODULES = frozenset(
    {
        "test_telemetry.py",
        "test_telemetry_commands.py",
    }
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


@pytest.fixture(autouse=True)
def _disable_telemetry_globally(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disable anonymous telemetry for the entire test run — no real network sends.

    On a typical developer machine ``telemetry_enabled()`` returns ``True`` (not
    CI, no opt-out env var, no config opt-out), so without this fixture every
    in-process ``CliRunner`` test — and every integration smoke test that spawns
    the real ``fdw`` binary as a subprocess — would emit **real** telemetry to the
    **production** Application Insights resource, drowning genuine usage in test
    noise (see issue tracking this).

    Setting ``FABRIC_DISABLE_TELEMETRY=1`` via ``monkeypatch.setenv`` makes
    :func:`fabric_dw.telemetry.telemetry_enabled` return ``False``, so
    ``emit_event`` / provider init / flush all become no-ops:

    - The truthy ``FABRIC_DISABLE_TELEMETRY`` check wins even over the forced
      ``FABRIC_TELEMETRY=1`` that ``tests/integration/test_cli_smoke.py`` injects
      into its child env, because it is evaluated first in ``telemetry_enabled``.
    - ``monkeypatch`` mutates ``os.environ`` in place, so subprocess tests that do
      ``os.environ.copy()`` inherit the disable; ``monkeypatch`` auto-restores it
      after each test.

    ``tests/unit/test_telemetry.py`` and ``tests/unit/test_telemetry_commands.py``
    are exempt: they exercise telemetry behaviour directly and manage their own env
    state (mirrors the ``_suppress_telemetry_notice`` exemption in
    ``tests/unit/conftest.py``).  The check is by exact filename so unrelated
    modules that merely contain ``telemetry`` in their name (e.g. the self-test for
    this very fixture) are still covered.
    """
    if request.path is not None and request.path.name in _TELEMETRY_SELF_MANAGED_MODULES:
        return
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")
