"""Ordinary CLI and MCP execution must not pull in a telemetry exporter.

The failure this guards against is not a crash, it is a dependency creeping back
in through a transitive import and quietly re-acquiring the process-global
OpenTelemetry providers.  ``sys.modules`` is the cheap and exact signal: an
exporter cannot start a worker thread, write an offline-storage directory, or
contact an ingestion endpoint without first being imported.

Each check runs in a fresh interpreter.  In-process it would be worthless: the
rest of the suite has already imported a great deal by the time this file runs,
and a module another test pulled in would either mask a regression or invent one
depending on the run order.

``opentelemetry.sdk`` is named alongside the Azure packages because it is the
half that owns providers, processors and exporters.  ``opentelemetry.api``
resolves to a no-op provider and is a dependency of the MCP SDK, so its presence
says nothing either way.
"""

from __future__ import annotations

import subprocess
import sys

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
def test_no_exporter_module_is_imported(label: str, snippet: str) -> None:
    """No exporter module is present after *snippet* has run."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", snippet + _REPORT],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, f"{label} failed:\n{result.stdout}\n{result.stderr}"
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("FORBIDDEN:"))
    imported = [m for m in line.removeprefix("FORBIDDEN:").split(",") if m]
    assert not imported, f"{label} imported exporter modules: {imported}"
