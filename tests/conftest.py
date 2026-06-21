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

- ``_isolate_telemetry_endpoint`` — points the SDK at a localhost dummy
  endpoint so nothing can reach the production Application Insights resource
  even when telemetry is intentionally enabled.
- ``_reset_telemetry_module_globals`` — resets ``_tenant_id_override`` and the
  ``_tenant_id_cache`` sentinel between tests so values cannot bleed across tests
  in the same process.
"""

from __future__ import annotations

from collections.abc import Generator

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

# Dummy connection string used by self-managed telemetry tests.  Instructs the
# SDK to target a localhost endpoint so that even if configure_azure_monitor is
# initialised, no traffic can reach the production App Insights resource.
# Individual tests may override this with monkeypatch.setenv; this fixture only
# installs it as a safe default.
_FAKE_CONNECTION_STRING = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000000;IngestionEndpoint=https://localhost/"
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


@pytest.fixture(autouse=True)
def _isolate_telemetry_endpoint(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the telemetry SDK at a localhost dummy endpoint in self-managed modules.

    Applied only to ``test_telemetry.py`` and ``test_telemetry_commands.py``
    (the ``_TELEMETRY_SELF_MANAGED_MODULES``).  Those modules deliberately exercise
    the enabled-telemetry code path and are exempt from ``_disable_telemetry_globally``,
    so without this fixture the real production App Insights connection string would
    be used whenever ``configure_azure_monitor`` is initialised.

    ``monkeypatch.setenv`` is used rather than a direct dict mutation so:
    - Individual tests that want a specific connection string can override it with
      their own ``monkeypatch.setenv(...)`` call — pytest runs fixture setup before
      the test body and test-body monkeypatches win.
    - The env var is automatically restored after each test.

    The instrumentation key ``00000000-0000-0000-0000-000000000000`` is a
    well-known null GUID and the endpoint is ``https://localhost/`` — traffic to
    this address is refused immediately by the OS, guaranteeing no egress to the
    real resource even if the SDK is initialised.
    """
    if request.path is None or request.path.name not in _TELEMETRY_SELF_MANAGED_MODULES:
        return
    monkeypatch.setenv("FABRIC_TELEMETRY_CONNECTION_STRING", _FAKE_CONNECTION_STRING)


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

    import sys  # noqa: PLC0415

    def _reset() -> None:
        mod = sys.modules.get("fabric_dw.telemetry")
        if mod is None:
            return
        # Reset the runtime tenant override so a previous test's set_tenant_id()
        # call cannot bleed into the next test's emit_event envelope.
        # setattr avoids unresolved-attribute errors on the opaque ModuleType.
        setattr(mod, "_tenant_id_override", None)
        # Reset the in-memory tenant cache to the _UNSET sentinel so that
        # _get_cached_tenant_id() re-reads from disk on next access.  The on-disk
        # file is isolated per-test via XDG_CONFIG_HOME / tmp_path.
        unset = getattr(mod, "_UNSET", None)
        if unset is not None:
            setattr(mod, "_tenant_id_cache", unset)

    _reset()
    yield
    _reset()
