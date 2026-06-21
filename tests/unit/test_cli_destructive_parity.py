"""TDD tests for CLI destructive_op telemetry parity with MCP (issue #666).

These tests prove that:
1. Every CLI command that mirrors a permanently-destructive MCP tool carries
   ``destructive_op=True`` on its ``command_invoked`` event.
2. Non-destructive-but-confirming commands (``queries kill``, ``workspaces
   set-collation``) and local clears (``cache clear``, ``config clear``) do NOT
   set ``destructive_op=True``.
3. Conditional cases (``sql-endpoints refresh --recreate-tables``,
   ``tables load --if-exists truncate|replace``) mirror their MCP counterpart's
   conditional logic.
4. The bidirectional drift-guard cross-checks the CLI destructive set against
   the MCP destructive set so the two surfaces cannot silently diverge.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

import fabric_dw.cli._main as _main_mod
from fabric_dw.cli._main import (
    _DESTRUCTIVE_CLI_COMMANDS,
    _MCP_TO_CLI_DESTRUCTIVE_MAP,
    cli,
)
from fabric_dw.mcp._helpers import _DESTRUCTIVE_MCP_TOOLS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke_and_capture_destructive(args: list[str]) -> bool | None:
    """Run the CLI with *args* and return the ``destructive`` kwarg from the
    last ``emit_command_invoked`` call, or ``None`` if it was never called.

    Patches out all I/O and infrastructure so no real network access or auth
    is needed.
    """
    captured: list[dict] = []

    def _fake_emit(**kwargs: object) -> None:
        captured.append(dict(kwargs))

    runner = CliRunner()
    with (
        patch.object(_main_mod, "emit_command_invoked", side_effect=_fake_emit),
        patch.object(_main_mod, "record_app_started"),
        patch.object(_main_mod, "record_app_exited"),
        patch.object(_main_mod, "shutdown_telemetry"),
        patch.object(_main_mod, "maybe_print_first_run_notice"),
    ):
        runner.invoke(cli, args, catch_exceptions=True)

    if not captured:
        return None
    return captured[-1].get("destructive", False)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Drift guard — bidirectional cross-check
# ---------------------------------------------------------------------------


class TestDestructiveParity:
    """Bidirectional drift-guard between CLI and MCP destructive sets."""

    def test_every_mapped_mcp_tool_has_a_cli_counterpart(self) -> None:
        """Every entry in _MCP_TO_CLI_DESTRUCTIVE_MAP must map to a known CLI command.

        The CLI counterpart must be in ``_DESTRUCTIVE_CLI_COMMANDS``.
        """
        missing: list[str] = []
        for mcp_tool, cli_cmd in _MCP_TO_CLI_DESTRUCTIVE_MAP.items():
            if cli_cmd not in _DESTRUCTIVE_CLI_COMMANDS:
                missing.append(f"{mcp_tool!r} → {cli_cmd!r} not in _DESTRUCTIVE_CLI_COMMANDS")
        assert not missing, "\n".join(missing)

    def test_every_destructive_mcp_tool_appears_in_map(self) -> None:
        """Every permanently-destructive MCP tool must appear in _MCP_TO_CLI_DESTRUCTIVE_MAP.

        Tools that are conditional (e.g. refresh_sql_endpoint_metadata) are
        excluded from _DESTRUCTIVE_MCP_TOOLS and handled separately — they do
        NOT need to appear in the map.
        """
        unconditional_mcp = _DESTRUCTIVE_MCP_TOOLS
        missing = [
            f"{tool!r} not in _MCP_TO_CLI_DESTRUCTIVE_MAP"
            for tool in sorted(unconditional_mcp)
            if tool not in _MCP_TO_CLI_DESTRUCTIVE_MAP
        ]
        assert not missing, "\n".join(missing)

    def test_mcp_destructive_set_not_empty(self) -> None:
        """_DESTRUCTIVE_MCP_TOOLS must be non-empty (sanity check)."""
        assert _DESTRUCTIVE_MCP_TOOLS, "_DESTRUCTIVE_MCP_TOOLS is empty — check _helpers.py"

    def test_cli_destructive_set_not_empty(self) -> None:
        """_DESTRUCTIVE_CLI_COMMANDS must be non-empty (sanity check)."""
        assert _DESTRUCTIVE_CLI_COMMANDS, "_DESTRUCTIVE_CLI_COMMANDS is empty"


# ---------------------------------------------------------------------------
# Identity-based: destructive commands emit destructive_op=True
# ---------------------------------------------------------------------------


class TestDestructiveCliCommandsEmitDestructiveOp:
    """Each permanently-destructive CLI command must set destructive_op=True."""

    @pytest.mark.parametrize(
        "dotted_name",
        sorted(_DESTRUCTIVE_CLI_COMMANDS),
    )
    def test_destructive_command_name_in_frozenset(self, dotted_name: str) -> None:
        """All entries in _DESTRUCTIVE_CLI_COMMANDS follow the <group>.<subcommand> convention."""
        assert "." in dotted_name, (
            f"{dotted_name!r} is not in <group>.<subcommand> format"
        )

    def test_functions_drop_is_destructive(self) -> None:
        assert "functions.drop" in _DESTRUCTIVE_CLI_COMMANDS

    def test_procedures_drop_is_destructive(self) -> None:
        assert "procedures.drop" in _DESTRUCTIVE_CLI_COMMANDS

    def test_restore_points_delete_is_destructive(self) -> None:
        assert "restore-points.delete" in _DESTRUCTIVE_CLI_COMMANDS

    def test_restore_points_restore_is_destructive(self) -> None:
        assert "restore-points.restore" in _DESTRUCTIVE_CLI_COMMANDS

    def test_schemas_delete_is_destructive(self) -> None:
        assert "schemas.delete" in _DESTRUCTIVE_CLI_COMMANDS

    def test_snapshots_delete_is_destructive(self) -> None:
        assert "snapshots.delete" in _DESTRUCTIVE_CLI_COMMANDS

    def test_sql_pools_delete_is_destructive(self) -> None:
        assert "sql-pools.delete" in _DESTRUCTIVE_CLI_COMMANDS

    def test_statistics_delete_is_destructive(self) -> None:
        assert "statistics.delete" in _DESTRUCTIVE_CLI_COMMANDS

    def test_tables_delete_is_destructive(self) -> None:
        assert "tables.delete" in _DESTRUCTIVE_CLI_COMMANDS

    def test_tables_clear_is_destructive(self) -> None:
        assert "tables.clear" in _DESTRUCTIVE_CLI_COMMANDS

    def test_tables_cluster_by_is_destructive(self) -> None:
        assert "tables.cluster-by" in _DESTRUCTIVE_CLI_COMMANDS

    def test_views_drop_is_destructive(self) -> None:
        assert "views.drop" in _DESTRUCTIVE_CLI_COMMANDS

    def test_warehouses_delete_is_destructive(self) -> None:
        assert "warehouses.delete" in _DESTRUCTIVE_CLI_COMMANDS


# ---------------------------------------------------------------------------
# Emit-level tests: emit_command_invoked is called with destructive=True
# ---------------------------------------------------------------------------


class TestEmitDestructiveOpOnAbort:
    """Aborted (--yes not passed) destructive commands still emit destructive_op=True."""

    def test_tables_delete_emits_destructive_on_abort(self) -> None:
        """tables delete aborted at prompt must still report destructive_op=True."""
        # Without --yes the prompt is shown and the user can abort; the command
        # body calls confirm_destructive which raises Abort when stdin is empty
        # (CliRunner default).  The emit must happen regardless.
        destructive = _invoke_and_capture_destructive(
            ["-w", "myws", "tables", "delete", "mydw", "dbo.mytable"]
        )
        assert destructive is True, (
            f"tables delete did not emit destructive_op=True on abort; got {destructive!r}"
        )

    def test_warehouses_delete_emits_destructive_on_abort(self) -> None:
        """warehouses delete aborted at prompt must still report destructive_op=True."""
        destructive = _invoke_and_capture_destructive(
            ["-w", "myws", "warehouses", "delete", "mywarehouse"]
        )
        assert destructive is True, (
            f"warehouses delete did not emit destructive_op=True on abort; got {destructive!r}"
        )

    def test_snapshots_delete_emits_destructive_on_abort(self) -> None:
        """snapshots delete aborted at prompt must still report destructive_op=True."""
        destructive = _invoke_and_capture_destructive(
            ["-w", "myws", "snapshots", "delete", "mywarehouse", "mysnapshot"]
        )
        assert destructive is True, (
            f"snapshots delete did not emit destructive_op=True on abort; got {destructive!r}"
        )

    def test_views_drop_emits_destructive_on_abort(self) -> None:
        """views drop aborted at prompt must still report destructive_op=True."""
        destructive = _invoke_and_capture_destructive(
            ["-w", "myws", "views", "drop", "mywarehouse", "dbo.myview"]
        )
        assert destructive is True, (
            f"views drop did not emit destructive_op=True on abort; got {destructive!r}"
        )

    def test_statistics_delete_emits_destructive_on_abort(self) -> None:
        """statistics delete aborted at prompt must still report destructive_op=True."""
        destructive = _invoke_and_capture_destructive(
            ["-w", "myws", "statistics", "delete", "mywarehouse", "dbo.mytable", "mystat"]
        )
        assert destructive is True, (
            f"statistics delete did not emit destructive_op=True on abort; got {destructive!r}"
        )

    def test_schemas_delete_emits_destructive_on_abort(self) -> None:
        """schemas delete aborted at prompt must still report destructive_op=True."""
        destructive = _invoke_and_capture_destructive(
            ["-w", "myws", "schemas", "delete", "mywarehouse", "myschema"]
        )
        assert destructive is True, (
            f"schemas delete did not emit destructive_op=True on abort; got {destructive!r}"
        )


# ---------------------------------------------------------------------------
# Non-destructive confirming commands must NOT set destructive_op
# ---------------------------------------------------------------------------


class TestNonDestructiveCommandsDoNotSetDestructiveOp:
    """Commands that confirm but are not permanently destructive must not emit destructive_op."""

    def test_queries_kill_is_not_destructive(self) -> None:
        assert "queries.kill" not in _DESTRUCTIVE_CLI_COMMANDS

    def test_schemas_drop_not_in_set(self) -> None:
        """schemas.drop does not exist — the CLI command is schemas.delete."""
        assert "schemas.drop" not in _DESTRUCTIVE_CLI_COMMANDS

    def test_workspaces_set_collation_is_not_destructive(self) -> None:
        assert "workspaces.set-collation" not in _DESTRUCTIVE_CLI_COMMANDS

    def test_warehouses_rename_is_not_destructive(self) -> None:
        assert "warehouses.rename" not in _DESTRUCTIVE_CLI_COMMANDS

    def test_warehouses_takeover_is_not_destructive(self) -> None:
        assert "warehouses.takeover" not in _DESTRUCTIVE_CLI_COMMANDS

    def test_snapshots_roll_is_not_destructive(self) -> None:
        assert "snapshots.roll" not in _DESTRUCTIVE_CLI_COMMANDS

    def test_cache_clear_is_not_destructive(self) -> None:
        assert "cache.clear" not in _DESTRUCTIVE_CLI_COMMANDS

    def test_config_clear_is_not_destructive(self) -> None:
        assert "config.clear" not in _DESTRUCTIVE_CLI_COMMANDS

    def test_queries_kill_does_not_emit_destructive(self) -> None:
        """queries kill must NOT emit destructive_op=True."""
        destructive = _invoke_and_capture_destructive(
            ["-w", "myws", "queries", "kill", "mywarehouse", "123"]
        )
        # None (not called) or False are both acceptable; True is not
        assert destructive is not True, (
            "queries kill incorrectly emitted destructive_op=True"
        )

    def test_cache_clear_does_not_emit_destructive(self) -> None:
        """cache clear must NOT emit destructive_op=True."""
        destructive = _invoke_and_capture_destructive(["cache", "clear"])
        assert destructive is not True, (
            "cache clear incorrectly emitted destructive_op=True"
        )


# ---------------------------------------------------------------------------
# Conditional cases
# ---------------------------------------------------------------------------


class TestConditionalDestructiveCommands:
    """Conditional destructive commands mirror their MCP counterpart's flag logic."""

    def test_sql_endpoints_refresh_without_recreate_is_not_destructive(self) -> None:
        """sql-endpoints refresh without --recreate-tables must NOT emit destructive_op."""
        # This command tries to connect to the API; it will fail at auth/network,
        # but the emit happens regardless of outcome so we capture what was emitted.
        destructive = _invoke_and_capture_destructive(
            ["-w", "myws", "sql-endpoints", "refresh", "myendpoint"]
        )
        assert destructive is not True, (
            "sql-endpoints refresh (no --recreate-tables) incorrectly emitted destructive_op=True"
        )

    def test_sql_endpoints_refresh_with_recreate_is_destructive(self) -> None:
        """sql-endpoints refresh --recreate-tables must emit destructive_op=True."""
        destructive = _invoke_and_capture_destructive(
            ["-w", "myws", "sql-endpoints", "refresh", "myendpoint", "--recreate-tables"]
        )
        assert destructive is True, (
            "sql-endpoints refresh --recreate-tables did not emit destructive_op=True; "
            f"got {destructive!r}"
        )

    def test_tables_load_without_destructive_if_exists_is_not_destructive(self) -> None:
        """tables load without truncate/replace --if-exists must NOT emit destructive_op."""
        # Providing --file and --create without --if-exists truncate/replace is not destructive
        destructive = _invoke_and_capture_destructive(
            ["-w", "myws", "tables", "load", "mywarehouse", "dbo.mytable", "--url", "http://x"]
        )
        assert destructive is not True, (
            "tables load (no destructive if-exists) incorrectly emitted destructive_op=True"
        )

    def test_tables_load_with_if_exists_truncate_is_destructive(self) -> None:
        """tables load --if-exists truncate must emit destructive_op=True."""
        destructive = _invoke_and_capture_destructive(
            [
                "-w",
                "myws",
                "tables",
                "load",
                "mywarehouse",
                "dbo.mytable",
                "--file",
                "/tmp/data.csv",  # noqa: S108
                "--create",
                "--if-exists",
                "truncate",
            ]
        )
        assert destructive is True, (
            "tables load --if-exists truncate did not emit destructive_op=True; "
            f"got {destructive!r}"
        )

    def test_tables_load_with_if_exists_replace_is_destructive(self) -> None:
        """tables load --if-exists replace must emit destructive_op=True."""
        destructive = _invoke_and_capture_destructive(
            [
                "-w",
                "myws",
                "tables",
                "load",
                "mywarehouse",
                "dbo.mytable",
                "--file",
                "/tmp/data.csv",  # noqa: S108
                "--create",
                "--if-exists",
                "replace",
            ]
        )
        assert destructive is True, (
            "tables load --if-exists replace did not emit destructive_op=True; "
            f"got {destructive!r}"
        )
