"""Tests that CLI command groups are loaded lazily — TDD for issue #579.

Assertions:
1. Root ``--help`` does NOT import any command module or its services.
2. A broken import in one group does not break root ``--help`` or sibling groups.
3. Root ``--help`` completes quickly (smoke).
4. Typo'd commands still get "Did you mean?" suggestions.
5. ``_COMMAND_MAP`` and ``_SHORT_HELP_MAP`` cover the same set of names.
"""

from __future__ import annotations

import importlib
import logging
import sys
import time
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from fabric_dw.cli._main import _COMMAND_MAP, _SHORT_HELP_MAP, cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_EVICT_PREFIXES = (
    "fabric_dw.cli.commands",
    "fabric_dw.services.dbt_scaffold",
    "yaml",
)


@pytest.fixture
def clean_modules() -> Any:
    """Evict lazy-target modules before a test and restore them afterwards.

    Evicting and NOT restoring leaves orphaned module objects in other test
    modules' globals (their imported names still point to the old object) while
    sys.modules now contains a freshly re-imported copy.  Any ``patch()`` call
    in a later test that uses the module path (e.g.
    ``"fabric_dw.cli.commands.tables.load_local_file"``) will patch the new
    copy, but functions imported by the test file at collection time still
    execute with the old copy — making the patch invisible.  Always restore.
    """
    saved = {
        key: mod
        for key, mod in sys.modules.items()
        if any(key == m or key.startswith(m + ".") for m in _EVICT_PREFIXES)
    }
    for key in saved:
        del sys.modules[key]
    yield
    # Restore: remove anything that was imported during the test, then put
    # back the originals so other test modules' globals keep working.
    for key in list(sys.modules):
        if any(key == m or key.startswith(m + ".") for m in _EVICT_PREFIXES):
            del sys.modules[key]
    sys.modules.update(saved)


# ---------------------------------------------------------------------------
# 1. Lazy-import assertions
# ---------------------------------------------------------------------------


class TestRootHelpDoesNotImportModules:
    """Root ``--help`` must not pull in any command-group modules."""

    def test_help_does_not_import_dbt(self, clean_modules: Any) -> None:  # noqa: ARG002
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "fabric_dw.cli.commands.dbt" not in sys.modules

    def test_help_does_not_import_yaml(self, clean_modules: Any) -> None:  # noqa: ARG002
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "yaml" not in sys.modules

    def test_help_does_not_import_dbt_scaffold_service(self, clean_modules: Any) -> None:  # noqa: ARG002
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "fabric_dw.services.dbt_scaffold" not in sys.modules

    def test_help_does_not_import_sql_pools(self, clean_modules: Any) -> None:  # noqa: ARG002
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
    """A failing import in one command group must not break root ``--help``."""

    def test_root_help_survives_broken_group_import(self, clean_modules: Any) -> None:  # noqa: ARG002
        """If one group's module raises ImportError, root --help still exits 0."""
        original_import = importlib.import_module

        def _patched_import(name: str, *args: Any, **kwargs: Any) -> ModuleType:
            if name == "fabric_dw.cli.commands.dbt":
                raise ImportError("simulated missing dependency")
            return original_import(name, *args, **kwargs)

        with patch("fabric_dw.cli._main.importlib.import_module", side_effect=_patched_import):
            runner = CliRunner()
            result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0, result.output
        # format_commands reads _SHORT_HELP_MAP unconditionally, so dbt still
        # appears in --help output even when its import fails.  Other groups
        # must also be listed.
        assert "cache" in result.output
        assert "warehouses" in result.output
        assert "dbt" in result.output

    def test_working_group_still_works_when_sibling_is_broken(self, clean_modules: Any) -> None:  # noqa: ARG002
        """Invoking a healthy group still succeeds when another group's import fails."""
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

    def test_broken_import_logs_warning(
        self,
        clean_modules: Any,  # noqa: ARG002
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failed module import must emit a WARNING so the user can diagnose it."""
        original_import = importlib.import_module

        def _patched_import(name: str, *args: Any, **kwargs: Any) -> ModuleType:
            if name == "fabric_dw.cli.commands.dbt":
                raise ImportError("simulated missing dependency")
            return original_import(name, *args, **kwargs)

        with (
            caplog.at_level(logging.WARNING, logger="fabric_dw.cli._main"),
            patch("fabric_dw.cli._main.importlib.import_module", side_effect=_patched_import),
        ):
            runner = CliRunner()
            runner.invoke(cli, ["dbt", "--help"])

        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("dbt" in str(m) for m in warning_msgs), (
            f"Expected a WARNING mentioning 'dbt' but got: {warning_msgs}"
        )


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


# ---------------------------------------------------------------------------
# 4. "Did you mean?" suggestions for typo'd commands
# ---------------------------------------------------------------------------


class TestTypoSuggestions:
    """Mistyped top-level commands must still produce "Did you mean?" hints."""

    def test_typo_suggests_closest_command(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["cach"])  # typo for "cache"
        # Click returns exit code 2 for unknown commands.
        assert result.exit_code == 2
        # Click's "Did you mean?" message should appear.
        assert "cache" in result.output.lower() or "did you mean" in result.output.lower(), (
            f"Expected a suggestion for 'cach' in output: {result.output!r}"
        )


# ---------------------------------------------------------------------------
# 5. Map-parity guard
# ---------------------------------------------------------------------------


class TestCommandMapParity:
    """_COMMAND_MAP and _SHORT_HELP_MAP must cover the same set of names."""

    def test_command_map_and_short_help_map_have_same_keys(self) -> None:
        assert _COMMAND_MAP.keys() == _SHORT_HELP_MAP.keys(), (
            f"Maps disagree.  "
            f"Missing from _SHORT_HELP_MAP: {_COMMAND_MAP.keys() - _SHORT_HELP_MAP.keys()!r}.  "
            f"Missing from _COMMAND_MAP: {_SHORT_HELP_MAP.keys() - _COMMAND_MAP.keys()!r}."
        )

    def test_all_short_help_strings_are_non_empty(self) -> None:
        empty = [name for name, text in _SHORT_HELP_MAP.items() if not text.strip()]
        assert empty == [], f"Commands with empty short help: {empty}"
