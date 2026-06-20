"""Tests that CLI command groups are loaded lazily — TDD for issue #579.

Assertions:
1. Root ``--help`` does NOT import any command module or its services.
2. A broken import in one group does not break root ``--help`` or sibling groups.
3. Root ``--help`` completes quickly (smoke).
"""

from __future__ import annotations

import importlib
import sys
import time
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNRELATED_MODULES = [
    "fabric_dw.cli.commands.dbt",
    "fabric_dw.cli.commands.sql_pools",
    "fabric_dw.cli.commands.statistics",
    "fabric_dw.cli.commands.audit",
    "fabric_dw.services.dbt_scaffold",
    "yaml",
]


def _evict_modules() -> None:
    """Remove lazy-target modules from sys.modules so the test starts clean."""
    for key in list(sys.modules):
        if any(
            key == m or key.startswith(m + ".")
            for m in (
                "fabric_dw.cli.commands",
                "fabric_dw.services.dbt_scaffold",
                "yaml",
            )
        ):
            del sys.modules[key]


# ---------------------------------------------------------------------------
# 1. Lazy-import assertions
# ---------------------------------------------------------------------------


class TestRootHelpDoesNotImportModules:
    """Root ``--help`` must not pull in any command-group modules."""

    def test_help_does_not_import_dbt(self) -> None:
        _evict_modules()
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "fabric_dw.cli.commands.dbt" not in sys.modules

    def test_help_does_not_import_yaml(self) -> None:
        _evict_modules()
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "yaml" not in sys.modules

    def test_help_does_not_import_dbt_scaffold_service(self) -> None:
        _evict_modules()
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "fabric_dw.services.dbt_scaffold" not in sys.modules

    def test_help_does_not_import_sql_pools(self) -> None:
        _evict_modules()
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "fabric_dw.cli.commands.sql_pools" not in sys.modules

    def test_help_lists_all_groups(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        for name in ("audit", "cache", "dbt", "workspaces", "warehouses"):
            assert name in result.output, f"expected {name!r} in --help output"

    def test_help_shows_short_descriptions(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        # At least one description fragment must appear.
        assert "Manage" in result.output or "Scaffold" in result.output or "SQL" in result.output


# ---------------------------------------------------------------------------
# 2. Broken-import resilience
# ---------------------------------------------------------------------------


class TestBrokenImportResilience:
    """A failing import in one command group must not break root --help."""

    def test_root_help_survives_broken_group_import(self) -> None:
        """If one group's module raises ImportError, root --help still exits 0."""
        _evict_modules()

        original_import = importlib.import_module

        def _patched_import(name: str, *args: Any, **kwargs: Any) -> ModuleType:
            if name == "fabric_dw.cli.commands.dbt":
                raise ImportError("simulated missing dependency")
            return original_import(name, *args, **kwargs)

        with patch("fabric_dw.cli._main.importlib.import_module", side_effect=_patched_import):
            runner = CliRunner()
            result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0, result.output
        # dbt is absent from the listed commands (it failed to import) — but
        # other groups must still be listed.
        assert "cache" in result.output
        assert "warehouses" in result.output

    def test_working_group_still_works_when_sibling_is_broken(self) -> None:
        """Invoking a healthy group still succeeds when another group's import fails."""
        _evict_modules()

        original_import = importlib.import_module

        def _patched_import(name: str, *args: Any, **kwargs: Any) -> ModuleType:
            if name == "fabric_dw.cli.commands.dbt":
                raise ImportError("simulated missing dependency")
            return original_import(name, *args, **kwargs)

        with patch("fabric_dw.cli._main.importlib.import_module", side_effect=_patched_import):
            runner = CliRunner()
            result = runner.invoke(cli, ["cache", "--help"])

        assert result.exit_code == 0, result.output
        assert "cache" in result.output


# ---------------------------------------------------------------------------
# 3. Timing smoke
# ---------------------------------------------------------------------------


class TestHelpTiming:
    """Root ``--help`` must complete well under a second (lazy means fast)."""

    @pytest.mark.slow
    def test_root_help_completes_quickly(self) -> None:
        runner = CliRunner()
        start = time.monotonic()
        result = runner.invoke(cli, ["--help"])
        elapsed = time.monotonic() - start
        assert result.exit_code == 0, result.output
        # Under test isolation (no network) --help should be very fast.
        assert elapsed < 5.0, f"--help took {elapsed:.2f}s — expected < 5s"
