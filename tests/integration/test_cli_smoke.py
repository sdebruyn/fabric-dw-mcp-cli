"""Live CLI smoke tests that invoke the real ``fabric-dw`` console-script entry point.

Each test runs the binary as a subprocess — exactly as an end user would — so that
interpreter-exit finalizers, atexit handlers, and weakref callbacks all fire in a real
Python process shutdown.  This class of bug is invisible to Click's ``CliRunner`` (which
stays in-process) and to ``python -c "from fabric_dw.cli import main; main()"`` (which
bypasses the installed console-script shim and its argv handling).

Binary resolution
-----------------
1. ``shutil.which("fabric-dw")`` — respects the PATH of the test process; usually the
   right answer when the project is installed in a virtualenv that is active.
2. The sibling ``bin/fabric-dw`` next to ``sys.executable`` — reliable fallback inside
   ``uv run`` where the venv bin directory may not be on PATH.

ResourceWarning-as-error
------------------------
Each subprocess is launched with ``PYTHONWARNINGS=error::ResourceWarning`` and
``PYTHONDEVMODE=1`` so that unclosed file handles, sockets, or aiohttp sessions are
promoted to hard exceptions that abort the process (non-zero exit) rather than being
silently swallowed.

Telemetry
---------
CI environments (``GITHUB_ACTIONS=true``) disable telemetry in the subprocess.  The
child env deliberately unsets the CI marker and injects ``FABRIC_TELEMETRY=1`` so the
telemetry init → flush → shutdown path is exercised for every command.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from .conftest import SharedWarehouseTarget

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Error patterns that must never appear in stderr after a clean run.
# ---------------------------------------------------------------------------

_STDERR_FORBIDDEN = (
    "Unclosed client session",
    "Exception ignored in",
    "Traceback (most recent call last)",
    "ResourceWarning",
    "_close_pool_connections",
)

# Known CI environment variable names that disable telemetry; unset in child env
# so that the telemetry init / flush / shutdown code path is exercised.
_CI_ENV_VARS = (
    "CI",
    "GITHUB_ACTIONS",
    "JENKINS_URL",
    "TRAVIS",
    "CIRCLECI",
    "GITLAB_CI",
    "TF_BUILD",
)

# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def _resolve_binary() -> str:
    """Locate the ``fabric-dw`` console-script entry point.

    Resolution order:
    1. ``shutil.which("fabric-dw")`` — uses the active PATH; reliable when the
       project venv is activated or uv installs into a PATH-visible bin directory.
    2. ``<dir(sys.executable)>/fabric-dw[.exe]`` — sibling in the venv ``bin/``
       directory; reliable under ``uv run`` even when the venv bin is not on PATH.

    Raises:
        FileNotFoundError: When neither strategy finds an executable.
    """
    # Strategy 1: PATH lookup.
    via_which = shutil.which("fabric-dw")
    if via_which:
        return via_which

    # Strategy 2: sibling of sys.executable in the venv bin directory.
    exe_dir = Path(sys.executable).parent
    for candidate_name in ("fabric-dw", "fabric-dw.exe"):
        candidate = exe_dir / candidate_name
        if candidate.is_file():
            return str(candidate)

    raise FileNotFoundError(
        "Could not locate the 'fabric-dw' binary. "
        "Ensure the package is installed in the active virtualenv "
        f"(checked PATH and {exe_dir})."
    )


_FABRIC_DW_BIN = _resolve_binary()

# ---------------------------------------------------------------------------
# Child environment builder
# ---------------------------------------------------------------------------


def _child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build the child process environment.

    - Inherits the current process environment so credentials pass through.
    - Promotes ResourceWarnings to errors via PYTHONWARNINGS + PYTHONDEVMODE.
    - Unsets CI detection vars and forces ``FABRIC_TELEMETRY=1`` so the
      telemetry init / flush / shutdown path is exercised even in CI.
    """
    env = os.environ.copy()

    # Make resource leaks hard failures in the child process.
    env["PYTHONWARNINGS"] = "error::ResourceWarning"
    env["PYTHONDEVMODE"] = "1"

    # Unset CI markers so the child process activates telemetry, then force it on.
    for ci_var in _CI_ENV_VARS:
        env.pop(ci_var, None)
    env["FABRIC_TELEMETRY"] = "1"

    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Session-scoped fixture: expose the shared warehouse as subprocess env vars.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def _cli_smoke_sql_env(shared_warehouse: SharedWarehouseTarget) -> dict[str, str]:
    """Return env-var overrides pointing the CLI at the shared test warehouse.

    The ``sql exec`` smoke command needs a workspace + warehouse to connect to.
    Rather than hard-coding names, we reuse the ``shared_warehouse`` session fixture
    and surface its coordinates as the ``FABRIC_DW_DEFAULT_*`` env vars the CLI
    already supports.
    """
    return {
        "FABRIC_DW_DEFAULT_WORKSPACE": str(shared_warehouse.workspace_id),
        "FABRIC_DW_DEFAULT_WAREHOUSE": shared_warehouse.warehouse.name,
    }


# ---------------------------------------------------------------------------
# Workspace ID fixture (skips when not set).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _cli_smoke_workspace_id() -> str:
    """Return FABRIC_TEST_WORKSPACE_ID, skipping when absent."""
    raw = os.environ.get("FABRIC_TEST_WORKSPACE_ID")
    if not raw:
        pytest.skip("set FABRIC_TEST_WORKSPACE_ID to run CLI smoke tests")
    return raw


# ---------------------------------------------------------------------------
# Helper: run a fabric-dw subprocess and return CompletedProcess.
# ---------------------------------------------------------------------------


def _run(
    *args: str,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run ``fabric-dw <args>`` as a subprocess and capture output.

    Args:
        *args: Arguments to pass after the binary name.
        env: Child environment (built by :func:`_child_env`).
        timeout: Maximum seconds to wait for the process to finish.

    Returns:
        :class:`subprocess.CompletedProcess` with ``stdout`` and ``stderr`` decoded.
    """
    return subprocess.run(  # noqa: S603 — binary path is resolved from the installed package
        [_FABRIC_DW_BIN, *args],
        capture_output=True,
        text=True,
        env=env if env is not None else _child_env(),
        timeout=timeout,
        check=False,
    )


# ---------------------------------------------------------------------------
# Smoke tests.
# ---------------------------------------------------------------------------


def test_help_exits_zero_and_clean_stderr() -> None:
    """``fabric-dw --help`` must exit 0 with no resource warnings.

    This exercises pure plumbing / teardown with no network calls — the
    cheapest possible end-to-end signal that the entry point and interpreter
    shutdown are clean.
    """
    result = _run("--help")
    assert result.returncode == 0, f"--help exited {result.returncode}; stderr:\n{result.stderr}"
    assert result.stdout.strip(), "--help produced no output"
    for forbidden in _STDERR_FORBIDDEN:
        assert forbidden not in result.stderr, (
            f"'--help' stderr contains forbidden pattern {forbidden!r}:\n{result.stderr}"
        )


@pytest.mark.usefixtures("_cli_smoke_workspace_id")
def test_workspaces_list_clean_stderr_and_nonempty_stdout() -> None:
    """``workspaces list`` must exit 0 and return a non-empty Rich table.

    Exercises the REST path (aiohttp HTTP client + async credential lifecycle +
    paginated response + Rich table rendering) through interpreter teardown.
    """
    result = _run("workspaces", "list", env=_child_env())
    assert result.returncode == 0, (
        f"workspaces list exited {result.returncode}; stderr:\n{result.stderr}"
    )
    assert result.stdout.strip(), "workspaces list produced no output"
    for forbidden in _STDERR_FORBIDDEN:
        assert forbidden not in result.stderr, (
            f"'workspaces list' stderr contains forbidden pattern {forbidden!r}:\n{result.stderr}"
        )


def test_workspaces_get_json_valid(
    _cli_smoke_workspace_id: str,  # noqa: PT019 — value IS used in the body
) -> None:
    """``workspaces get <id> --json`` must exit 0 and return parseable JSON.

    Exercises the same REST path as ``workspaces list`` but via the JSON output
    branch; also confirms the workspace object is well-formed.
    """
    result = _run("--json", "workspaces", "get", _cli_smoke_workspace_id, env=_child_env())
    assert result.returncode == 0, (
        f"workspaces get --json exited {result.returncode}; stderr:\n{result.stderr}"
    )
    stdout = result.stdout.strip()
    assert stdout, "workspaces get --json produced no output"
    parsed = json.loads(stdout)
    assert isinstance(parsed, dict), f"expected a JSON object, got: {type(parsed)}"
    assert "id" in parsed, f"expected 'id' key in workspace JSON; got keys: {list(parsed)}"
    for forbidden in _STDERR_FORBIDDEN:
        assert forbidden not in result.stderr, (
            f"'workspaces get --json' stderr contains forbidden pattern "
            f"{forbidden!r}:\n{result.stderr}"
        )


def test_sql_exec_select1_clean_stderr(
    _cli_smoke_sql_env: dict[str, str],  # noqa: PT019 — value IS used in the body
) -> None:
    """``sql exec -q 'SELECT 1'`` must exit 0 with a clean stderr.

    Exercises the sync SQL / TDS credential path (mssql-python lifecycle, separate
    from the async aiohttp credential) through interpreter teardown.  The workspace
    and warehouse are supplied via FABRIC_DW_DEFAULT_* env vars injected by the
    ``_cli_smoke_sql_env`` fixture.
    """
    child_env = _child_env(_cli_smoke_sql_env)
    result = _run("sql", "exec", "-q", "SELECT 1 AS n", env=child_env)
    assert result.returncode == 0, (
        f"sql exec exited {result.returncode}; stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    )
    assert result.stdout.strip(), "sql exec produced no output"
    for forbidden in _STDERR_FORBIDDEN:
        assert forbidden not in result.stderr, (
            f"'sql exec' stderr contains forbidden pattern {forbidden!r}:\n{result.stderr}"
        )
