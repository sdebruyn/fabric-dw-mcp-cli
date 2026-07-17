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

If neither strategy finds the binary, all tests in this module are **skipped** (not
collected with an error).  This keeps ``pytest`` (bare, from the repo root) working for
developers who have not installed the package yet.

ResourceWarning-as-error
------------------------
Each subprocess is launched with ``PYTHONWARNINGS=error::ResourceWarning`` and
``PYTHONDEVMODE=1`` so that unclosed file handles, sockets, or aiohttp sessions are
promoted to hard exceptions that abort the process (non-zero exit) rather than being
silently swallowed.

Telemetry
---------
The child env deliberately removes ``FABRIC_DW_TELEMETRY_OPT_OUT`` so the
telemetry init → flush → shutdown path is exercised for every command.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from fabric_dw.sql import _resolve_sql_retry_deadline_s
from tests._stderr_helpers import sanitize_stderr as _sanitize_stderr

from .conftest import SharedWarehouseTarget

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Subprocess timeout budget for SQL-touching smoke tests.
#
# INVARIANT: this value MUST be strictly greater than the *resolved* SQL retry
# deadline (the CLI's internal connect-retry budget, which is now configurable
# via FABRIC_SQL_RETRY_TIMEOUT_S) plus a generous margin for process startup,
# authentication, and query execution overhead.  If the two values are equal
# the subprocess is killed exactly when the CLI's retry loop gives up, leaving
# zero headroom for the startup/auth/query overhead that is paid on top of the
# retry budget.
#
# The resolved value is read at module load so that when FABRIC_SQL_RETRY_TIMEOUT_S
# is set (e.g. to 300 in the integration CI job) the subprocess timeout scales up
# accordingly and the invariant cannot be violated by env configuration.
#
# A dedicated unit test in tests/unit/ guards this invariant at import time so
# that it cannot silently drift back to an unsafe value.
# ---------------------------------------------------------------------------
_SQL_STARTUP_MARGIN_S: int = 120
_CONNECT_RETRY_TIMEOUT_S: float = _resolve_sql_retry_deadline_s()
_SQL_SMOKE_SUBPROCESS_TIMEOUT_S: int = math.ceil(_CONNECT_RETRY_TIMEOUT_S) + _SQL_STARTUP_MARGIN_S

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
    # PerformanceCounters ZeroDivisionError signatures (#399 / #391).
    # azure-monitor-opentelemetry PerformanceCounters callback crashes on
    # short-lived processes with a ZeroDivisionError from _get_processor_time
    # unless enable_performance_counters=False is passed at SDK init time.
    "Error getting processor time",
    "_get_processor_time",
)

# ---------------------------------------------------------------------------
# Binary resolution — deferred so a missing binary skips tests, not errors.
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


# Resolve at module load time but store None on failure so that collection
# succeeds even when the package is not installed.  The ``_require_binary``
# fixture below gates every test on the resolved value and skips when absent.
try:
    _FABRIC_DW_BIN: str | None = _resolve_binary()
except FileNotFoundError:
    _FABRIC_DW_BIN = None

# ---------------------------------------------------------------------------
# Binary guard fixture — skip the whole module when binary is missing.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _require_binary() -> None:
    """Skip all smoke tests when the ``fabric-dw`` binary is not installed."""
    if _FABRIC_DW_BIN is None:
        pytest.skip(
            "fabric-dw binary not found; install the package first "
            "(checked PATH and the venv bin directory)"
        )


# ---------------------------------------------------------------------------
# Child environment builder
# ---------------------------------------------------------------------------


def _child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build the child process environment.

    - Inherits the current process environment so credentials pass through.
    - Promotes ResourceWarnings to errors via PYTHONWARNINGS + PYTHONDEVMODE.
    - Removes ``FABRIC_DW_TELEMETRY_OPT_OUT`` so the telemetry init / flush /
      shutdown path is exercised even when the caller has opted out.
    """
    env = os.environ.copy()

    # Make resource leaks hard failures in the child process.
    env["PYTHONWARNINGS"] = "error::ResourceWarning"
    env["PYTHONDEVMODE"] = "1"

    # Ensure the child process exercises the telemetry path regardless of caller opt-out.
    env.pop("FABRIC_DW_TELEMETRY_OPT_OUT", None)
    env.pop("DO_NOT_TRACK", None)

    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Session-scoped fixture: expose the shared warehouse as subprocess env vars.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def _cli_smoke_sql_env(shared_warehouse: SharedWarehouseTarget) -> dict[str, str]:
    """Return env-var overrides pointing the CLI at the shared test warehouse.

    The ``sql`` smoke command needs a workspace + warehouse to connect to.
    Rather than hard-coding names, we reuse the ``shared_warehouse`` session fixture
    and surface its coordinates as the ``FABRIC_DW_DEFAULT_*`` env vars the CLI
    already supports.
    """
    return {
        "FABRIC_DW_DEFAULT_WORKSPACE": str(shared_warehouse.workspace_id),
        "FABRIC_DW_DEFAULT_WAREHOUSE": shared_warehouse.warehouse.name,
    }


# ---------------------------------------------------------------------------
# Session-scoped SQL cold-start warmup.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _cli_smoke_sql_warmup(
    _cli_smoke_sql_env: dict[str, str],
) -> None:
    """Absorb the SQL cold-start cost once per test session.

    On a cold Fabric capacity the first ``sql exec`` can consume most of the
    CLI's internal connect-retry budget (_CONNECT_RETRY_TIMEOUT_S = 120 s)
    before the warehouse becomes reachable.  Running one tolerant warmup call
    here — with the same generous timeout used by the SQL smoke tests
    (_SQL_SMOKE_SUBPROCESS_TIMEOUT_S) — ensures that by the time
    ``test_sql_exec_select1_clean_stderr`` runs the endpoint is already warm.

    This fixture is NOT autouse: only SQL-touching tests request it as an
    explicit parameter.  This prevents non-SQL smoke tests (workspaces, help)
    from inadvertently triggering the shared_warehouse provisioning and the
    240 s warmup call.

    Any exception (TimeoutExpired, OSError/FileNotFoundError for a stale binary
    path, or non-zero exit) is silently suppressed: the warmup is a best-effort
    probe.  If the endpoint is genuinely unreachable the subsequent per-test
    assertion will surface the real failure with full context.
    """
    if _FABRIC_DW_BIN is None:
        return  # binary guard fixture will skip the SQL tests; nothing to warm
    child_env = _child_env(_cli_smoke_sql_env)
    try:
        subprocess.run(  # noqa: S603
            [_FABRIC_DW_BIN, "sql", "exec", "-q", "SELECT 1 AS n"],
            capture_output=True,
            text=True,
            env=child_env,
            timeout=_SQL_SMOKE_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _logger.warning(
            "SQL cold-start warmup timed out after %ds; "
            "the per-test assertion may also fail if the endpoint is still unavailable.",
            _SQL_SMOKE_SUBPROCESS_TIMEOUT_S,
        )
    except OSError:
        # Covers FileNotFoundError (stale _FABRIC_DW_BIN) and other exec errors.
        _logger.warning(
            "SQL cold-start warmup could not launch the binary; "
            "the per-test assertion will surface the real failure.",
        )


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

    Raises:
        pytest.fail.Exception: When the subprocess exceeds *timeout* seconds,
            including any partial stdout/stderr captured before the hang so the
            failure is diagnosable.
    """
    assert _FABRIC_DW_BIN is not None, "_require_binary fixture should have skipped this test"
    try:
        return subprocess.run(  # noqa: S603 — binary path is resolved from the installed package
            [_FABRIC_DW_BIN, *args],
            capture_output=True,
            text=True,
            env=env if env is not None else _child_env(),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raw_out = exc.stdout or b""
        raw_err = exc.stderr or b""
        decoded_out = (
            raw_out.decode("utf-8", errors="replace") if isinstance(raw_out, bytes) else raw_out
        )
        decoded_err = (
            raw_err.decode("utf-8", errors="replace") if isinstance(raw_err, bytes) else raw_err
        )
        pytest.fail(
            f"fabric-dw {list(args)!r} timed out after {timeout}s.\n"
            f"stdout:\n{decoded_out}\nstderr:\n{decoded_err}"
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
    sanitized = _sanitize_stderr(result.stderr)
    for forbidden in _STDERR_FORBIDDEN:
        assert forbidden not in sanitized, (
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
    sanitized = _sanitize_stderr(result.stderr)
    for forbidden in _STDERR_FORBIDDEN:
        assert forbidden not in sanitized, (
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
    sanitized = _sanitize_stderr(result.stderr)
    for forbidden in _STDERR_FORBIDDEN:
        assert forbidden not in sanitized, (
            f"'workspaces get --json' stderr contains forbidden pattern "
            f"{forbidden!r}:\n{result.stderr}"
        )


def test_sql_exec_select1_clean_stderr(
    _cli_smoke_sql_env: dict[str, str],  # noqa: PT019 — value IS used in the body
    _cli_smoke_sql_warmup: None,  # noqa: PT019 — absorbs cold-start cost before this assertion
) -> None:
    """``sql exec -q 'SELECT 1'`` must exit 0 with a clean stderr.

    Exercises the sync SQL / TDS credential path (mssql-python lifecycle, separate
    from the async aiohttp credential) through interpreter teardown.  The workspace
    and warehouse are supplied via FABRIC_DW_DEFAULT_* env vars injected by the
    ``_cli_smoke_sql_env`` fixture.

    The ``_cli_smoke_sql_env`` async fixture is session-scoped and its resolved
    value (a plain ``dict[str, str]``) is injected here synchronously by
    pytest-asyncio under ``asyncio_mode = "auto"``.  No async machinery is needed
    inside this test function itself.

    The subprocess timeout is _SQL_SMOKE_SUBPROCESS_TIMEOUT_S, which is derived
    from _CONNECT_RETRY_TIMEOUT_S + a generous margin for startup/auth/query
    overhead.  See the module-level constant and the unit guard test for details.
    The session-scoped _cli_smoke_sql_warmup fixture absorbs most of the cold-
    start cost before this assertion runs.
    """
    child_env = _child_env(_cli_smoke_sql_env)
    result = _run(
        "sql", "exec", "-q", "SELECT 1 AS n", env=child_env, timeout=_SQL_SMOKE_SUBPROCESS_TIMEOUT_S
    )
    assert result.returncode == 0, (
        f"sql exec exited {result.returncode}; stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    )
    assert result.stdout.strip(), "sql exec produced no output"
    sanitized = _sanitize_stderr(result.stderr)
    for forbidden in _STDERR_FORBIDDEN:
        assert forbidden not in sanitized, (
            f"'sql exec' stderr contains forbidden pattern {forbidden!r}:\n{result.stderr}"
        )
