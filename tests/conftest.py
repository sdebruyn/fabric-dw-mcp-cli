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
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# Capture the telemetry module at session start, before any _reload_telemetry()
# calls can replace it in sys.modules.  Tests that import telemetry symbols at
# file-load time (e.g. ``from fabric_dw.telemetry import telemetry_enabled`` in
# test_config.py) hold a reference to *this* module object even after sys.modules
# is replaced, so the per-test reset must also clear caches on this original object.
# Import is deferred to avoid loading fabric_dw.telemetry before the test
# collection phase — the reference is populated on first fixture call below.
_orig_telemetry_module: ModuleType | None = None

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
        "test_app_exited_emission.py",
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
    """Reset telemetry module globals before and after each test.

    Two tiers of reset are applied:

    1. **All tests** — the per-process caches added in #844 (``_install_method_cache``
       and ``_config_disabled_cache``) are always reset.  Any test that calls
       ``telemetry_enabled()`` on the shared module instance can set these caches;
       without a universal teardown they bleed into subsequent tests regardless of
       which module file those tests live in.

    2. **Self-managed telemetry modules only** (``test_telemetry.py``,
       ``test_telemetry_commands.py``, ``test_app_exited_emission.py``) — the
       heavier globals are also reset:

       - ``_tenant_id_override`` → ``None``
         (prevents ``set_tenant_id("runtime-tenant-xyz")`` from bleeding into
         the next test's ``emit_event`` envelope and reaching production App Insights)
       - ``_tenant_id_cache`` → ``_UNSET`` sentinel
         (forces re-read from disk; the on-disk file is isolated per-test via
         ``XDG_CONFIG_HOME``/``tmp_path``)
       - ``_sdk_initialised``, ``_tracer``, ``_otel_logger`` → initial values
         (prevents a test that calls ``_get_tracer()`` from leaving a live logger
         object that causes subsequent tests to skip the SDK init path entirely)

    All resets are applied *before* and *after* each test (yield fixture) so state
    never leaks from a previous test even if it raised.

    Tests that manipulate these globals via ``mod._foo = ...`` on a reloaded module
    are unaffected because ``_reload_telemetry()`` produces a fresh module object
    with its own namespace.  This fixture guards the *shared* module instance that
    lives in ``sys.modules["fabric_dw.telemetry"]``.
    """
    global _orig_telemetry_module  # noqa: PLW0603
    # Capture the module reference on the very first fixture invocation.  At this
    # point no _reload_telemetry() call has happened, so sys.modules holds the
    # original module object (the same one that file-level imports in other test
    # modules have bound to).
    if _orig_telemetry_module is None:
        _orig_telemetry_module = sys.modules.get("fabric_dw.telemetry")  # type: ignore[assignment]

    is_self_managed = (
        request.path is not None and request.path.name in _TELEMETRY_SELF_MANAGED_MODULES
    )

    def _reset() -> None:
        # Collect all distinct module objects that need their caches cleared.
        # sys.modules may point to a reloaded module after _reload_telemetry();
        # _orig_telemetry_module is the original object that file-level imports
        # (e.g. test_config.py) continue to reference even after reloading.
        seen: set[int] = set()
        mods_to_clean: list[ModuleType] = []
        for candidate in (sys.modules.get("fabric_dw.telemetry"), _orig_telemetry_module):
            if candidate is not None and id(candidate) not in seen:
                seen.add(id(candidate))
                mods_to_clean.append(candidate)

        for mod in mods_to_clean:
            # Mutate the module namespace dict directly to avoid both B010 (setattr
            # with a constant name) and unresolved-attribute errors from ty on the
            # opaque ModuleType.  vars() returns the live __dict__ so changes are
            # immediately visible via the module object.
            ns = vars(mod)
            # Per-process caches (#844): reset for every test because any test that
            # calls telemetry_enabled() or _detect_install_method() on the live
            # module — even via an import-time reference — can populate these.
            # The asserts below verify the names still exist in the module so a
            # rename in telemetry.py causes a loud failure here rather than a
            # silent no-op that lets caches bleed between tests.
            assert "_install_method_cache" in ns, (
                "_install_method_cache not found in fabric_dw.telemetry — "
                "update both telemetry.py and tests/conftest.py"
            )
            assert "_config_disabled_cache" in ns, (
                "_config_disabled_cache not found in fabric_dw.telemetry — "
                "update both telemetry.py and tests/conftest.py"
            )
            ns["_install_method_cache"] = None
            ns["_config_disabled_cache"] = None
            if not is_self_managed:
                continue
            # Heavier globals: only needed for tests that exercise the telemetry
            # enabled-path directly (self-managed modules).
            ns["_tenant_id_override"] = None
            # _UNSET is a sentinel object; look it up by name to reset _tenant_id_cache.
            # KeyError on ns["_UNSET"] (a dict lookup, not assignment) would mean
            # _UNSET was renamed in telemetry.py — update both files if that happens.
            ns["_tenant_id_cache"] = ns["_UNSET"]
            ns["_sdk_initialised"] = False
            ns["_tracer"] = None
            ns["_otel_logger"] = None

    _reset()
    yield
    _reset()
