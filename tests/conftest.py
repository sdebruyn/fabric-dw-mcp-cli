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

For the self-managed telemetry modules (``test_telemetry.py`` and
``test_telemetry_commands.py``) two additional defence-in-depth fixtures are
applied:

- ``_mock_configure_azure_monitor`` — patches ``configure_azure_monitor``
  (the function imported inside ``telemetry.py``'s SDK-init path) to a no-op
  so no real connection to the production App Insights resource is ever
  attempted, even when telemetry is intentionally enabled in a test.
- ``_reset_telemetry_module_globals`` — resets ``_tenant_id_override`` and the
  ``_tenant_id_cache`` sentinel between tests so values cannot bleed across tests
  in the same process.
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

# All environment variables that can alter the MCP security/filter behaviour.
_FABRIC_MCP_VARS = (
    "FABRIC_MCP_READONLY",
    "FABRIC_MCP_ALLOW_DESTRUCTIVE",
    "FABRIC_MCP_WORKSPACES",
    "FABRIC_MCP_ALLOW_REMOTE",
)

# Test modules that exercise telemetry behaviour directly and therefore manage
# their own FABRIC_DW_TELEMETRY_OPT_OUT env state.  They are exempt from the
# global telemetry-disable fixture below.
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

    On a typical developer machine ``telemetry_enabled()`` returns ``True`` (no
    opt-out env var, no config opt-out), so without this fixture every in-process
    ``CliRunner`` test — and every integration smoke test that spawns the real
    ``fdw`` binary as a subprocess — would emit **real** telemetry to the
    **production** Application Insights resource, drowning genuine usage in test
    noise (see issue tracking this).

    Setting ``FABRIC_DW_TELEMETRY_OPT_OUT=1`` via ``monkeypatch.setenv`` makes
    :func:`fabric_dw.telemetry.telemetry_enabled` return ``False``, so
    ``emit_event`` / provider init / flush all become no-ops.
    ``monkeypatch`` mutates ``os.environ`` in place, so subprocess tests that do
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
    monkeypatch.setenv("FABRIC_DW_TELEMETRY_OPT_OUT", "1")


@pytest.fixture(autouse=True)
def _mock_configure_azure_monitor(
    request: pytest.FixtureRequest,
) -> Generator[MagicMock, None, None]:
    """Patch configure_azure_monitor to a no-op in self-managed telemetry modules.

    Applied only to ``test_telemetry.py`` and ``test_telemetry_commands.py``
    (the ``_TELEMETRY_SELF_MANAGED_MODULES``).  Those modules deliberately exercise
    the enabled-telemetry code path and are exempt from ``_disable_telemetry_globally``,
    so without this fixture a real call to ``configure_azure_monitor`` inside
    ``_get_tracer`` would attempt to connect to the production App Insights resource.

    The patch targets the import site inside ``telemetry.py``'s local import
    (``from azure.monitor.opentelemetry import configure_azure_monitor``), so only
    the telemetry module's SDK-init path is affected; the real SDK is never reached.
    Individual tests that need to inspect the mock can access it via the fixture
    value; tests that patch ``_get_tracer`` or ``emit_event`` directly are
    unaffected because those patches take effect before the SDK init path is reached.
    """
    if request.path is None or request.path.name not in _TELEMETRY_SELF_MANAGED_MODULES:
        yield MagicMock()
        return
    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as mock_configure:
        yield mock_configure


@pytest.fixture(autouse=True)
def _reset_telemetry_module_globals(
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """Reset telemetry module globals before and after each self-managed test.

    Applied only to ``test_telemetry.py`` and ``test_telemetry_commands.py``.
    Without this fixture, a test that calls ``set_tenant_id("runtime-tenant-xyz")``
    leaves ``_tenant_id_override`` set in the live module for subsequent tests in the
    same process.  If those tests then trigger ``emit_event`` with a real SDK
    initialisation, the dummy tenant bleeds into the exported envelope — which was
    confirmed to reach the production App Insights resource.

    This fixture resets both:
    - ``_tenant_id_override`` → ``None``
    - ``_tenant_id_cache`` → the ``_UNSET`` sentinel (forces re-read from disk,
      which is isolated by ``XDG_CONFIG_HOME``/``tmp_path`` in individual tests)

    Both resets are applied *before* and *after* each test (yield fixture), so state
    never leaks from a previous test even if it raised.

    Tests that manipulate these globals directly via ``mod._tenant_id_override = ...``
    on a reloaded module are unaffected because ``_reload_telemetry()`` produces a
    fresh module object with its own namespace.  This fixture guards the *shared*
    module instance that lives in ``sys.modules["fabric_dw.telemetry"]``.
    """
    if request.path is None or request.path.name not in _TELEMETRY_SELF_MANAGED_MODULES:
        yield
        return

    def _reset() -> None:
        mod = sys.modules.get("fabric_dw.telemetry")
        if mod is None:
            return
        # Mutate the module namespace dict directly to avoid both B010 (setattr
        # with a constant name) and unresolved-attribute errors from ty on the
        # opaque ModuleType.  vars() returns the live __dict__ so changes are
        # immediately visible via the module object.
        ns = vars(mod)
        # Reset the runtime tenant override so a previous test's set_tenant_id()
        # call cannot bleed into the next test's emit_event envelope.
        ns["_tenant_id_override"] = None
        # Reset the in-memory tenant cache to the _UNSET sentinel so that
        # _get_cached_tenant_id() re-reads from disk on next access.  The on-disk
        # file is isolated per-test via XDG_CONFIG_HOME / tmp_path.
        # KeyError here means _UNSET was renamed in telemetry.py — update both files.
        ns["_tenant_id_cache"] = ns["_UNSET"]

    _reset()
    yield
    _reset()
