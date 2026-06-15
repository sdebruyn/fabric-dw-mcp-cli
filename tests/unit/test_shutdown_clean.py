"""End-to-end subprocess test: verify clean stderr at CLI shutdown.

Runs the real ``fabric-dw`` entry point as a child process with telemetry
**enabled** (but pointing at a bogus, non-routable exporter endpoint) and
asserts that the process exits without printing any of the shutdown-noise
signatures that indicate leaked connection pools or unclosed sessions:

- ``Unclosed client session``          ← aiohttp ResourceWarning (#385/#387)
- ``Exception ignored in``             ← GC finalizer crash
- ``Traceback (most recent call last)`` ← any unexpected traceback
- ``_close_pool_connections``          ← urllib3 pool finalizer (#389)

The command is allowed to fail (e.g. auth error, unreachable workspace) —
only the *absence* of the above stderr substrings is asserted.

Design notes
------------
- ``FABRIC_TELEMETRY_CONNECTION_STRING`` is set to a syntactically valid
  App Insights connection string pointing at a non-routable / refused endpoint
  so the exporter is fully initialised (SDK + urllib3 pool created) but the
  HTTP flush is a no-op (connection refused, quickly discarded).
- All CI detection env vars are removed so ``telemetry_enabled()`` returns
  True and the real SDK code path is exercised.
- ``PYTHONWARNINGS=error`` is **not** set here because the subprocess has
  its own warning filters; the test relies on observing stderr text rather
  than exit code.
- The ``--help`` variant runs in the default ``not slow`` suite because
  ``--help`` exits immediately without any auth or Fabric network call and
  the bogus exporter connection is refused instantly.  Total wall-clock time
  is typically < 5 s.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

# A syntactically-valid but non-routable App Insights connection string.
# Port 1 on 127.0.0.1 is reliably refused so the exporter fails fast.
_BOGUS_CONNECTION_STRING = (
    "InstrumentationKey=00000000-0000-0000-0000-000000000001;"  # gitleaks:allow
    "IngestionEndpoint=http://127.0.0.1:1/;"
    "LiveEndpoint=http://127.0.0.1:1/"
)

# Stderr substrings that indicate a leaked pool / broken teardown, or a
# PerformanceCounters crash (ZeroDivisionError from _get_processor_time on
# short-lived processes — #399).
_FORBIDDEN_STDERR_SUBSTRINGS = [
    "Unclosed client session",
    "Exception ignored in",
    "Traceback (most recent call last)",
    "_close_pool_connections",
    # #399: azure-monitor-opentelemetry PerformanceCounters ZeroDivisionError.
    # These appear even with disable_metrics=True unless
    # enable_performance_counters=False is also passed.
    "Error getting processor time",
    "_get_processor_time",
    "_performance_counters",
    # #411: Azure Monitor exporter / azure-core retry-policy noise when the
    # endpoint is unreachable (offline / firewalled users).
    "Retrying due to server request error",
    "missing a valid region",
]

# Invoke fabric-dw via ``python -c "from fabric_dw.cli import main; main()"``
# so the test works with the in-tree development install without relying on
# PATH or the console-script shim being on the PATH under the test runner.
_CLI_RUNNER = [
    sys.executable,
    "-c",
    "from fabric_dw.cli import main; main()",
]


def _build_subprocess_env() -> dict[str, str]:
    """Build an environment dict that forces telemetry on with a bogus endpoint."""
    env = dict(os.environ)

    # Point telemetry at the non-routable bogus endpoint.
    env["FABRIC_TELEMETRY_CONNECTION_STRING"] = _BOGUS_CONNECTION_STRING

    # Remove all CI detection vars so telemetry_enabled() returns True.
    for ci_var in (
        "CI",
        "GITHUB_ACTIONS",
        "JENKINS_URL",
        "TRAVIS",
        "CIRCLECI",
        "GITLAB_CI",
        "TF_BUILD",
    ):
        env.pop(ci_var, None)

    # Remove opt-out vars.
    env.pop("FABRIC_TELEMETRY", None)
    env.pop("FABRIC_DISABLE_TELEMETRY", None)
    env.pop("DO_NOT_TRACK", None)

    # Strip any caller-side statsbeat override so our setdefault in _get_tracer
    # is always exercised — a runner env with this set to "false" would otherwise
    # make setdefault a no-op and defeat the statsbeat-disable fix (#418).
    env.pop("APPLICATIONINSIGHTS_STATSBEAT_DISABLED_ALL", None)

    # Use a temp config dir so the first-run notice state is isolated.
    env["XDG_CONFIG_HOME"] = "/tmp/fabric_dw_test_shutdown_clean"  # noqa: S108

    return env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cli_exits_without_shutdown_noise_on_help() -> None:
    """CLI subprocess must not emit urllib3 pool or aiohttp session warnings on exit.

    Runs ``fabric-dw --help`` which exercises full CLI init (telemetry SDK
    initialisation, tracer creation, provider setup) and then exits cleanly
    via the teardown path (shutdown_telemetry, which flushes internally).

    ``--help`` exits without any auth/network call to Fabric, so the test is
    fully hermetic; only the telemetry exporter endpoint is attempted (and
    immediately refused by the bogus endpoint, completing fast).

    Note on the ``Unclosed client session`` assertion: ``--help`` does not
    create a credential and therefore never opens an aiohttp session, so this
    particular assertion is vacuous for this test variant.  It is kept in the
    shared ``_FORBIDDEN_STDERR_SUBSTRINGS`` list so that the full teardown
    regression suite is exercised in one place and any future command added to
    the test suite automatically inherits the check.  Real aiohttp coverage is
    provided by the slow ``test_cli_exits_without_shutdown_noise_on_auth_command``
    variant which creates a ``DefaultAzureCredential`` (and the aiohttp session
    it owns).
    """
    result = subprocess.run(  # noqa: S603
        [*_CLI_RUNNER, "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        env=_build_subprocess_env(),
        check=False,
    )

    stderr = result.stderr
    for forbidden in _FORBIDDEN_STDERR_SUBSTRINGS:
        assert forbidden not in stderr, (
            f"Forbidden string {forbidden!r} found in stderr.\nFull stderr:\n{stderr}"
        )


@pytest.mark.slow
def test_cli_exits_without_shutdown_noise_on_auth_command() -> None:
    """CLI subprocess must not emit shutdown noise on a real auth-touching command.

    This variant runs ``fabric-dw workspaces list`` which triggers credential
    creation (aiohttp session) + telemetry SDK.  Auth is expected to fail fast
    (no valid credentials in the test environment), but teardown must be clean.

    Marked ``slow`` because the auth attempt may take up to ~5 s before failing.
    """
    env = _build_subprocess_env()
    # Remove any real Azure credentials so auth fails fast without side-effects.
    for cred_var in (
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_CERTIFICATE_PATH",
    ):
        env.pop(cred_var, None)

    result = subprocess.run(  # noqa: S603
        [*_CLI_RUNNER, "workspaces", "list"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )

    stderr = result.stderr
    for forbidden in _FORBIDDEN_STDERR_SUBSTRINGS:
        assert forbidden not in stderr, (
            f"Forbidden string {forbidden!r} found in stderr.\nFull stderr:\n{stderr}"
        )
