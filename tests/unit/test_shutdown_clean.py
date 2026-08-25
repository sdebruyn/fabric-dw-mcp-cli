"""End-to-end subprocess test: verify clean stderr at CLI shutdown.

Runs the real ``fabric-dw`` entry point as a child process and asserts that the
process exits without printing any of the shutdown-noise signatures that
indicate a leaked connection pool, an unclosed session, or a finaliser that
raised on the way out:

- ``Unclosed client session``           <- aiohttp ResourceWarning (#385/#387)
- ``Exception ignored in``              <- GC finaliser crash
- ``Traceback (most recent call last)`` <- any unexpected traceback
- ``_close_pool_connections``           <- urllib3 pool finaliser (#389)

None of this is observable in-process: the warnings are emitted by finalisers
during interpreter teardown, after pytest has stopped looking.  A subprocess is
the only place they show up.

The two variants cover different teardown paths.  ``config show`` runs the root
callback and a real leaf command without touching the network, so it is cheap
enough for the default suite; it is expected to succeed, because a command that
died before opening anything would make the assertions below vacuous.
``workspaces list`` builds a credential and the aiohttp session it owns, which is
what actually exercises the resource teardown; it is marked ``slow`` because the
auth attempt takes seconds to fail, and it is *allowed* to fail, since a failed
command still has to close what it opened.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._stderr_helpers import sanitize_stderr as _sanitize_stderr

# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_STDERR_SUBSTRINGS = [
    "Unclosed client session",
    "Exception ignored in",
    "Traceback (most recent call last)",
    "_close_pool_connections",
]

# Invoke fabric-dw via ``python -c "from fabric_dw.cli import main; main()"``
# so the test works with the in-tree development install without relying on
# PATH or the console-script shim being on the PATH under the test runner.
_CLI_RUNNER = [
    sys.executable,
    "-c",
    "from fabric_dw.cli import main; main()",
]


def _env(config_home: Path) -> dict[str, str]:
    """Return the child environment with the two inputs that matter neutralised.

    The config file and ``FABRIC_AUTH`` are the ones that can stop the child
    before it opens anything: an unrecognised ``FABRIC_AUTH`` raises a usage
    error inside the root callback, which would fail the local variant on a
    developer machine while its stderr was perfectly clean, and a developer's
    real config could name defaults that change which teardown path runs.

    Other ``FABRIC_*`` variables are deliberately left inherited.  The retry and
    default-workspace ones are read further down, by code neither variant
    reaches, so clearing them would suggest a sensitivity that is not there.
    """
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env.pop("FABRIC_AUTH", None)
    return env


def _assert_quiet_stderr(result: subprocess.CompletedProcess[str]) -> None:
    stderr = _sanitize_stderr(result.stderr)
    for forbidden in _FORBIDDEN_STDERR_SUBSTRINGS:
        assert forbidden not in stderr, (
            f"Forbidden string {forbidden!r} found in stderr.\nFull stderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cli_exits_without_shutdown_noise_on_a_local_command(tmp_path: Path) -> None:
    """A command that runs the full CLI path but no network must exit quietly.

    ``config show`` goes through the root callback (logging setup, config load,
    auth-mode resolution) and a leaf command body, then exits.  Anything that
    outlives that and raises in a finaliser lands on stderr here.
    """
    result = subprocess.run(  # noqa: S603
        [*_CLI_RUNNER, "config", "show", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        env=_env(tmp_path),
        check=False,
    )

    assert result.returncode == 0, f"config show failed:\n{result.stderr}"
    _assert_quiet_stderr(result)


@pytest.mark.slow
def test_cli_exits_without_shutdown_noise_on_auth_command(tmp_path: Path) -> None:
    """The credential path must tear down its aiohttp session and urllib3 pools.

    ``workspaces list`` builds a ``DefaultAzureCredential`` and the HTTP client.
    Auth is expected to fail (no valid credentials in the test environment), but
    a failed command still has to close what it opened.

    Marked ``slow`` because the auth attempt may take up to ~5 s before failing.
    """
    env = _env(tmp_path)
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

    _assert_quiet_stderr(result)
