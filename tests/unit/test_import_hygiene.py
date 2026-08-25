"""Ordinary use of fabric-dw must not drag in an observability backend.

``fabric_dw`` is imported into processes it does not own: an MCP host, a dbt
run, a notebook.  Two families of module are therefore off limits no matter how
they arrive, because both take effect process-wide rather than for this package:

- ``opentelemetry.sdk`` and ``opentelemetry.exporter`` own the concrete tracer
  and logger providers, and installing one replaces whatever the host process
  had.  (``opentelemetry.api`` is a different thing: it resolves to a no-op
  provider, it is a dependency of the MCP SDK, and it is fine.)
- ``azure.monitor`` starts a background exporter with its own worker thread,
  network egress and on-disk spool directory.

A transitive import is enough to cause all of that, which is why this asserts on
``sys.modules`` rather than on any particular call.  It is also why nothing here
has to run anything: if the module is absent, none of its behaviour is
reachable.

Each check runs in a fresh interpreter.  In-process it would be worthless: the
rest of the suite has already imported a great deal by the time this file runs,
and a module another test pulled in would either mask a regression or invent one
depending on the run order.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_FORBIDDEN_PREFIXES = ("azure.monitor", "opentelemetry.sdk", "opentelemetry.exporter")

_REPORT = f"""
import sys
forbidden = [m for m in sys.modules if m.startswith({_FORBIDDEN_PREFIXES!r})]
print("FORBIDDEN:" + ",".join(sorted(forbidden)))
"""

_IMPORT_PACKAGE = "import fabric_dw"

_RUN_CLI_HELP = """
from click.testing import CliRunner
from fabric_dw.cli._main import cli
result = CliRunner().invoke(cli, ["config", "--help"])
assert result.exit_code == 0, result.output
"""

_RUN_CLI_COMMAND = """
from click.testing import CliRunner
from fabric_dw.cli._main import cli
result = CliRunner().invoke(cli, ["config", "show", "--json"])
assert result.exit_code == 0, result.output
"""

_LIST_MCP_TOOLS = """
import asyncio
from fabric_dw.mcp.server import mcp
tools = asyncio.run(mcp.list_tools())
assert len(tools) > 100, len(tools)
"""


@pytest.mark.parametrize(
    ("label", "snippet"),
    [
        ("import fabric_dw", _IMPORT_PACKAGE),
        ("cli help", _RUN_CLI_HELP),
        ("cli command", _RUN_CLI_COMMAND),
        ("mcp tools/list", _LIST_MCP_TOOLS),
    ],
)
def test_no_backend_module_is_imported(label: str, snippet: str, tmp_path: Path) -> None:
    """None of the forbidden modules is present after *snippet* has run."""
    # The child runs a real CLI command, and load_config takes a FileLock next
    # to the config file it reads.  Without this redirect that lock lands in the
    # developer's own ~/.config/fabric-dw/.
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(tmp_path)
    env.pop("FABRIC_AUTH", None)

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", snippet + _REPORT],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )

    assert result.returncode == 0, f"{label} failed:\n{result.stdout}\n{result.stderr}"
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("FORBIDDEN:"))
    imported = [m for m in line.removeprefix("FORBIDDEN:").split(",") if m]
    assert not imported, f"{label} imported: {imported}"
