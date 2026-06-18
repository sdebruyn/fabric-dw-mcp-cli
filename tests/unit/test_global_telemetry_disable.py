"""Self-test for the global ``_disable_telemetry_globally`` autouse fixture.

The fixture lives in ``tests/conftest.py`` (root) and disables anonymous
telemetry for the entire test run so that no test — in-process or subprocess —
ever performs a real send to the production Application Insights resource.

These tests confirm the fixture is actually active for ordinary modules.  Only
``test_telemetry.py`` and ``test_telemetry_commands.py`` are exempt (they manage
their own telemetry env state); this module is *not* in that set, so it is
subject to the global disable.
"""

from __future__ import annotations

import os

from fabric_dw.telemetry import telemetry_enabled


def test_disable_telemetry_env_is_set_by_global_fixture() -> None:
    """The global autouse fixture exports FABRIC_DISABLE_TELEMETRY=1."""
    assert os.environ.get("FABRIC_DISABLE_TELEMETRY") == "1"


def test_telemetry_is_disabled_inside_a_test() -> None:
    """With the fixture active, telemetry_enabled() must be False."""
    assert telemetry_enabled() is False
