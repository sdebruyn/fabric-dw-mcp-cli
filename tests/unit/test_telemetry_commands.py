"""Tests for fabric_dw.telemetry_commands — per-command usage instrumentation.

Written TDD-first.  All tests run without a network and without real Azure
Monitor SDK calls (every telemetry emission is mocked via monkeypatch).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from fabric_dw.telemetry_commands import (
    _KNOWN_DOMAINS,
    DOMAIN_MAP,
    duration_bucket,
    emit_command_invoked,
    map_status,
    now_ms,
    resolve_domain,
)

# Telemetry patch targets (lazily imported inside emit_command_invoked).
_TELEMETRY_ENABLED = "fabric_dw.telemetry.telemetry_enabled"
_EMIT_EVENT = "fabric_dw.telemetry.emit_event"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def disable_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable real telemetry by default (safe tests, no network)."""
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")


# ---------------------------------------------------------------------------
# resolve_domain
# ---------------------------------------------------------------------------


class TestResolveDomain:
    def test_known_cli_group(self) -> None:
        assert resolve_domain("warehouses") == "warehouses"

    def test_known_cli_group_sql_endpoints(self) -> None:
        assert resolve_domain("sql-endpoints") == "sql_endpoints"

    def test_known_mcp_tool_exact(self) -> None:
        assert resolve_domain("list_warehouses") == "warehouses"

    def test_known_mcp_tool_create_warehouse(self) -> None:
        assert resolve_domain("create_warehouse") == "warehouses"

    def test_known_mcp_tool_execute_sql(self) -> None:
        assert resolve_domain("execute_sql") == "sql"

    def test_known_mcp_tool_generate_dbt_profile(self) -> None:
        assert resolve_domain("generate_dbt_profile") == "dbt"

    def test_unknown_returns_unknown(self) -> None:
        assert resolve_domain("does_not_exist_abc") == "unknown"

    def test_all_domain_map_values_are_known_domains(self) -> None:
        """Every value in DOMAIN_MAP must be a known domain string."""
        bad = {k: v for k, v in DOMAIN_MAP.items() if v not in _KNOWN_DOMAINS}
        assert bad == {}, f"Unknown domain values in DOMAIN_MAP: {bad}"


# ---------------------------------------------------------------------------
# Domain coverage — exhaustive check
# ---------------------------------------------------------------------------


class TestDomainCoverage:
    """Every registered MCP tool and every CLI command must resolve to a known domain."""

    # Canonical MCP tool names taken from test_server.EXPECTED_TOOL_NAMES.
    MCP_TOOL_NAMES: frozenset[str] = frozenset(
        {
            "list_workspaces",
            "get_workspace",
            "set_workspace_collation",
            "list_warehouses",
            "get_warehouse",
            "create_warehouse",
            "rename_warehouse",
            "delete_warehouse",
            "takeover_warehouse",
            "get_warehouse_permissions",
            "list_sql_endpoints",
            "get_sql_endpoint",
            "refresh_sql_endpoint_metadata",
            "get_sql_endpoint_permissions",
            "get_audit_settings",
            "enable_audit",
            "disable_audit",
            "set_audit_action_groups",
            "add_audit_group",
            "remove_audit_group",
            "set_audit_retention",
            "list_running_queries",
            "kill_session",
            "list_connections",
            "list_request_history",
            "list_session_history",
            "list_frequent_queries",
            "list_long_running_queries",
            "execute_sql",
            "list_snapshots",
            "create_snapshot",
            "rename_snapshot",
            "delete_snapshot",
            "roll_snapshot_timestamp",
            "list_restore_points",
            "get_restore_point",
            "create_restore_point",
            "update_restore_point",
            "delete_restore_point",
            "restore_warehouse_in_place",
            "list_schemas",
            "create_schema",
            "delete_schema",
            "list_views",
            "read_view",
            "get_view",
            "create_view",
            "update_view",
            "drop_view",
            "rename_view",
            "list_procedures",
            "get_procedure",
            "create_procedure",
            "update_procedure",
            "drop_procedure",
            "list_functions",
            "get_function",
            "create_function",
            "update_function",
            "drop_function",
            "rename_function",
            "list_tables",
            "read_table",
            "create_table",
            "create_empty_table",
            "clone_table",
            "rename_table",
            "delete_table",
            "clear_table",
            "load_table_from_url",
            "get_sql_pools_configuration",
            "list_sql_pools",
            "get_sql_pool",
            "create_sql_pool",
            "update_sql_pool",
            "delete_sql_pool",
            "enable_sql_pools",
            "disable_sql_pools",
            "list_sql_pool_insights",
            "list_statistics",
            "show_statistics",
            "create_statistics",
            "update_statistics",
            "delete_statistics",
            "generate_dbt_profile",
            "clear_cache",
        }
    )

    # Canonical CLI group names (top-level groups registered on the root cli).
    CLI_GROUP_NAMES: frozenset[str] = frozenset(
        {
            "workspaces",
            "warehouses",
            "sql-endpoints",
            "sql",
            "tables",
            "views",
            "procedures",
            "schemas",
            "statistics",
            "functions",
            "snapshots",
            "restore-points",
            "audit",
            "queries",
            "sql-pools",
            "dbt",
            "cache",
            "config",
            "completion",
        }
    )

    def test_all_mcp_tools_resolve_to_known_domain(self) -> None:
        """Every MCP tool name must resolve to a domain that is NOT 'unknown'."""
        unknown = {name for name in self.MCP_TOOL_NAMES if resolve_domain(name) == "unknown"}
        assert unknown == set(), f"MCP tools with unknown domain: {sorted(unknown)}"

    def test_all_cli_groups_resolve_to_known_domain(self) -> None:
        """Every top-level CLI group name must resolve to a domain that is NOT 'unknown'."""
        unknown = {name for name in self.CLI_GROUP_NAMES if resolve_domain(name) == "unknown"}
        assert unknown == set(), f"CLI groups with unknown domain: {sorted(unknown)}"


# ---------------------------------------------------------------------------
# map_status
# ---------------------------------------------------------------------------


class TestMapStatus:
    def test_none_is_success(self) -> None:
        assert map_status(None) == "success"

    def test_usage_error_is_user_error(self) -> None:
        exc = click.exceptions.UsageError("bad input")
        assert map_status(exc) == "user_error"

    def test_abort_is_user_error(self) -> None:
        exc = click.exceptions.Abort()
        assert map_status(exc) == "user_error"

    def test_system_exit_0_is_success(self) -> None:
        exc = SystemExit(0)
        assert map_status(exc) == "success"

    def test_system_exit_1_is_user_error(self) -> None:
        exc = SystemExit(1)
        assert map_status(exc) == "user_error"

    def test_click_exit_0_is_success(self) -> None:
        exc = click.exceptions.Exit(0)
        assert map_status(exc) == "success"

    def test_click_exit_1_is_user_error(self) -> None:
        exc = click.exceptions.Exit(1)
        assert map_status(exc) == "user_error"

    def test_value_error_is_user_error(self) -> None:

        exc = ValueError("invalid argument")
        assert map_status(exc) == "user_error"

    def test_config_error_is_user_error(self) -> None:
        from fabric_dw.exceptions import ConfigError  # noqa: PLC0415

        exc = ConfigError("missing env var")
        assert map_status(exc) == "user_error"

    def test_not_found_error_is_user_error(self) -> None:
        from fabric_dw.exceptions import NotFoundError  # noqa: PLC0415

        exc = NotFoundError("resource not found")
        assert map_status(exc) == "user_error"

    def test_fabric_error_is_api_error(self) -> None:
        from fabric_dw.exceptions import FabricError  # noqa: PLC0415

        exc = FabricError("api error", status=500)
        assert map_status(exc) == "api_error"

    def test_tool_error_is_user_error(self) -> None:
        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        exc = ToolError("bad tool call")
        assert map_status(exc) == "user_error"

    def test_unexpected_exception_is_api_error(self) -> None:
        exc = RuntimeError("unexpected")
        assert map_status(exc) == "api_error"

    def test_keyboard_interrupt_is_api_error(self) -> None:
        exc = KeyboardInterrupt()
        assert map_status(exc) == "api_error"


# ---------------------------------------------------------------------------
# duration_bucket
# ---------------------------------------------------------------------------


class TestDurationBucket:
    def test_under_100ms(self) -> None:
        assert duration_bucket(0.0) == "<100ms"
        assert duration_bucket(50.0) == "<100ms"
        assert duration_bucket(99.9) == "<100ms"

    def test_exactly_100ms(self) -> None:
        assert duration_bucket(100.0) == "<1s"

    def test_500ms(self) -> None:
        assert duration_bucket(500.0) == "<1s"

    def test_999ms(self) -> None:
        assert duration_bucket(999.9) == "<1s"

    def test_exactly_1s(self) -> None:
        assert duration_bucket(1_000.0) == "<10s"

    def test_5s(self) -> None:
        assert duration_bucket(5_000.0) == "<10s"

    def test_exactly_10s(self) -> None:
        assert duration_bucket(10_000.0) == ">10s"

    def test_60s(self) -> None:
        assert duration_bucket(60_000.0) == ">10s"


# ---------------------------------------------------------------------------
# now_ms
# ---------------------------------------------------------------------------


class TestNowMs:
    def test_returns_float(self) -> None:
        t = now_ms()
        assert isinstance(t, float)

    def test_is_monotonic(self) -> None:
        t1 = now_ms()
        t2 = now_ms()
        assert t2 >= t1


# ---------------------------------------------------------------------------
# emit_command_invoked — disabled → nothing emitted
# ---------------------------------------------------------------------------


class TestEmitCommandInvokedDisabled:
    def test_disabled_emits_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When telemetry is disabled, emit_event must never be called."""
        monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")

        with patch(_EMIT_EVENT) as mock_emit, patch(_TELEMETRY_ENABLED, return_value=False):
            emit_command_invoked(
                name="warehouses.list",
                surface="cli",
                status="success",
                duration_ms=50.0,
            )
        mock_emit.assert_not_called()


# ---------------------------------------------------------------------------
# emit_command_invoked — enabled path (spy on emit_event)
# ---------------------------------------------------------------------------


class TestEmitCommandInvokedEnabled:
    @pytest.fixture(autouse=True)
    def enable_telemetry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
        monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
        monkeypatch.delenv("DO_NOT_TRACK", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("JENKINS_URL", raising=False)
        monkeypatch.delenv("TRAVIS", raising=False)
        monkeypatch.delenv("CIRCLECI", raising=False)
        monkeypatch.delenv("GITLAB_CI", raising=False)
        monkeypatch.delenv("TF_BUILD", raising=False)

    def _run(self, **kwargs):  # type: ignore[no-untyped-def]
        """Call emit_command_invoked with telemetry_enabled patched to True."""
        with patch(_TELEMETRY_ENABLED, return_value=True), patch(_EMIT_EVENT) as mock_emit:
            emit_command_invoked(**kwargs)
        return mock_emit

    def test_emits_one_event(self) -> None:
        mock = self._run(
            name="warehouses.list",
            surface="cli",
            status="success",
            duration_ms=50.0,
        )
        mock.assert_called_once()

    def test_event_name_is_command_invoked(self) -> None:
        mock = self._run(
            name="warehouses.list",
            surface="cli",
            status="success",
            duration_ms=50.0,
        )
        args, _ = mock.call_args
        assert args[0] == "command_invoked"

    def test_attributes_contain_name(self) -> None:
        mock = self._run(
            name="warehouses.list",
            surface="cli",
            status="success",
            duration_ms=50.0,
        )
        attrs: dict = mock.call_args[0][1]
        assert attrs["name"] == "warehouses.list"

    def test_attributes_contain_domain(self) -> None:
        mock = self._run(
            name="warehouses.list",
            surface="cli",
            status="success",
            duration_ms=50.0,
        )
        attrs: dict = mock.call_args[0][1]
        assert attrs["domain"] == "warehouses"

    def test_attributes_contain_surface_cli(self) -> None:
        mock = self._run(
            name="warehouses.list",
            surface="cli",
            status="success",
            duration_ms=50.0,
        )
        attrs: dict = mock.call_args[0][1]
        assert attrs["surface"] == "cli"

    def test_attributes_contain_surface_mcp(self) -> None:
        mock = self._run(
            name="list_warehouses",
            surface="mcp",
            status="success",
            duration_ms=50.0,
        )
        attrs: dict = mock.call_args[0][1]
        assert attrs["surface"] == "mcp"

    def test_attributes_contain_status(self) -> None:
        mock = self._run(
            name="warehouses.list",
            surface="cli",
            status="success",
            duration_ms=50.0,
        )
        attrs: dict = mock.call_args[0][1]
        assert attrs["status"] == "success"

    def test_attributes_contain_duration_bucket(self) -> None:
        mock = self._run(
            name="warehouses.list",
            surface="cli",
            status="success",
            duration_ms=50.0,
        )
        attrs: dict = mock.call_args[0][1]
        assert attrs["duration_ms_bucket"] == "<100ms"

    def test_destructive_op_attribute_when_true(self) -> None:
        mock = self._run(
            name="delete_warehouse",
            surface="mcp",
            status="success",
            duration_ms=200.0,
            destructive=True,
        )
        attrs: dict = mock.call_args[0][1]
        assert attrs.get("destructive_op") is True

    def test_destructive_op_absent_when_false(self) -> None:
        mock = self._run(
            name="list_warehouses",
            surface="mcp",
            status="success",
            duration_ms=50.0,
            destructive=False,
        )
        attrs: dict = mock.call_args[0][1]
        assert "destructive_op" not in attrs

    def test_no_identifiers_in_attributes(self) -> None:
        """The emitted attributes must not contain any identifier-like strings."""
        mock = self._run(
            name="warehouses.list",
            surface="cli",
            status="success",
            duration_ms=50.0,
        )
        attrs: dict = mock.call_args[0][1]
        # None of the values should look like workspace names, GUIDs, or SQL text.
        for key, value in attrs.items():
            if isinstance(value, str):
                # Should only be from a fixed categorical set, never free-form user input.
                assert len(value) < 200, f"Attribute {key!r} value too long: {value!r}"
                # Must not contain SQL keywords that would indicate SQL leakage.
                lower = value.lower()
                assert "select " not in lower, f"SQL-like text in {key!r}: {value!r}"
                assert "from " not in lower, f"SQL-like text in {key!r}: {value!r}"

    def test_emit_failure_does_not_raise(self) -> None:
        """If emit_event raises, emit_command_invoked must not propagate."""
        with (
            patch(_TELEMETRY_ENABLED, return_value=True),
            patch(_EMIT_EVENT, side_effect=RuntimeError("network error")),
        ):
            # Must not raise.
            emit_command_invoked(
                name="warehouses.list",
                surface="cli",
                status="success",
                duration_ms=50.0,
            )


# ---------------------------------------------------------------------------
# CLI integration — command_invoked emitted per command
# ---------------------------------------------------------------------------


class TestCliCommandInvokedInstrumentation:
    """Verify that the CLI emits exactly one command_invoked per leaf command."""

    def _run_cli(self, args: list[str], monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """Run the CLI with *args* and return the emit_event mock."""
        from fabric_dw.cli._main import cli  # noqa: PLC0415

        # Enable telemetry for these tests.
        monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("JENKINS_URL", raising=False)
        monkeypatch.delenv("TRAVIS", raising=False)
        monkeypatch.delenv("CIRCLECI", raising=False)
        monkeypatch.delenv("GITLAB_CI", raising=False)
        monkeypatch.delenv("TF_BUILD", raising=False)

        runner = CliRunner()
        # Patch telemetry_enabled at the source (fabric_dw.telemetry) so both
        # the per-command and lifecycle telemetry are controlled.
        with patch(_TELEMETRY_ENABLED, return_value=True), patch(_EMIT_EVENT) as mock_emit:
            runner.invoke(cli, args, catch_exceptions=True)
        return mock_emit

    def test_cache_clear_emits_command_invoked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = self._run_cli(["cache", "clear"], monkeypatch)
        command_invoked_calls = [c for c in mock.call_args_list if c[0][0] == "command_invoked"]
        assert len(command_invoked_calls) == 1

    def test_cache_clear_name_is_cache_clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = self._run_cli(["cache", "clear"], monkeypatch)
        command_invoked_calls = [c for c in mock.call_args_list if c[0][0] == "command_invoked"]
        assert command_invoked_calls, "No command_invoked event emitted"
        attrs: dict = command_invoked_calls[0][0][1]
        assert attrs["name"] == "cache.clear"

    def test_cache_clear_surface_is_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = self._run_cli(["cache", "clear"], monkeypatch)
        command_invoked_calls = [c for c in mock.call_args_list if c[0][0] == "command_invoked"]
        assert command_invoked_calls
        attrs: dict = command_invoked_calls[0][0][1]
        assert attrs["surface"] == "cli"

    def test_cache_clear_domain_is_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = self._run_cli(["cache", "clear"], monkeypatch)
        command_invoked_calls = [c for c in mock.call_args_list if c[0][0] == "command_invoked"]
        assert command_invoked_calls
        attrs: dict = command_invoked_calls[0][0][1]
        assert attrs["domain"] == "cache"

    def test_disabled_emits_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When telemetry is disabled, no command_invoked event is emitted."""
        from fabric_dw.cli._main import cli  # noqa: PLC0415

        monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")
        runner = CliRunner()
        with patch(_EMIT_EVENT) as mock_emit, patch(_TELEMETRY_ENABLED, return_value=False):
            runner.invoke(cli, ["cache", "clear"], catch_exceptions=True)
        command_invoked_calls = [
            c for c in mock_emit.call_args_list if c[0][0] == "command_invoked"
        ]
        assert len(command_invoked_calls) == 0


# ---------------------------------------------------------------------------
# MCP instrumentation — command_invoked emitted per tool call
# ---------------------------------------------------------------------------


class TestMcpCommandInvokedInstrumentation:
    """Verify that MCP tool calls emit exactly one command_invoked event."""

    @pytest.fixture(autouse=True)
    def enable_telemetry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("JENKINS_URL", raising=False)
        monkeypatch.delenv("TRAVIS", raising=False)
        monkeypatch.delenv("CIRCLECI", raising=False)
        monkeypatch.delenv("GITLAB_CI", raising=False)
        monkeypatch.delenv("TF_BUILD", raising=False)

    def _make_spy(self) -> MagicMock:
        return MagicMock()

    def test_wrap_emits_on_success(self) -> None:
        """_wrap_mcp_tool_with_telemetry emits command_invoked on success."""
        import asyncio  # noqa: PLC0415

        from fabric_dw.mcp._helpers import _wrap_mcp_tool_with_telemetry  # noqa: PLC0415

        async def _tool() -> str:
            return "ok"

        wrapped = _wrap_mcp_tool_with_telemetry(_tool, "list_warehouses")

        with patch(_TELEMETRY_ENABLED, return_value=True), patch(_EMIT_EVENT) as mock_emit:
            asyncio.run(wrapped())

        command_invoked_calls = [
            c for c in mock_emit.call_args_list if c[0][0] == "command_invoked"
        ]
        assert len(command_invoked_calls) == 1

    def test_wrap_emits_on_exception(self) -> None:
        """_wrap_mcp_tool_with_telemetry emits even when the tool raises."""
        import asyncio  # noqa: PLC0415

        from fabric_dw.mcp._helpers import _wrap_mcp_tool_with_telemetry  # noqa: PLC0415

        async def _tool() -> str:
            raise RuntimeError("oops")

        wrapped = _wrap_mcp_tool_with_telemetry(_tool, "list_warehouses")

        with (
            patch(_TELEMETRY_ENABLED, return_value=True),
            patch(_EMIT_EVENT) as mock_emit,
            pytest.raises(RuntimeError),
        ):
            asyncio.run(wrapped())

        command_invoked_calls = [
            c for c in mock_emit.call_args_list if c[0][0] == "command_invoked"
        ]
        assert len(command_invoked_calls) == 1

    def test_wrap_status_success(self) -> None:
        import asyncio  # noqa: PLC0415

        from fabric_dw.mcp._helpers import _wrap_mcp_tool_with_telemetry  # noqa: PLC0415

        async def _tool() -> str:
            return "ok"

        wrapped = _wrap_mcp_tool_with_telemetry(_tool, "list_warehouses")

        with patch(_TELEMETRY_ENABLED, return_value=True), patch(_EMIT_EVENT) as mock_emit:
            asyncio.run(wrapped())

        attrs: dict = mock_emit.call_args_list[-1][0][1]
        assert attrs["status"] == "success"

    def test_wrap_status_api_error_on_unexpected_exception(self) -> None:
        import asyncio  # noqa: PLC0415

        from fabric_dw.mcp._helpers import _wrap_mcp_tool_with_telemetry  # noqa: PLC0415

        async def _tool() -> str:
            raise RuntimeError("unexpected")

        wrapped = _wrap_mcp_tool_with_telemetry(_tool, "list_warehouses")

        with (
            patch(_TELEMETRY_ENABLED, return_value=True),
            patch(_EMIT_EVENT) as mock_emit,
            pytest.raises(RuntimeError),
        ):
            asyncio.run(wrapped())

        attrs: dict = mock_emit.call_args_list[-1][0][1]
        assert attrs["status"] == "api_error"

    def test_wrap_status_user_error_on_tool_error(self) -> None:
        import asyncio  # noqa: PLC0415

        from mcp.server.fastmcp.exceptions import ToolError  # noqa: PLC0415

        from fabric_dw.mcp._helpers import _wrap_mcp_tool_with_telemetry  # noqa: PLC0415

        async def _tool() -> str:
            raise ToolError("bad input")

        wrapped = _wrap_mcp_tool_with_telemetry(_tool, "list_warehouses")

        with (
            patch(_TELEMETRY_ENABLED, return_value=True),
            patch(_EMIT_EVENT) as mock_emit,
            pytest.raises(ToolError),
        ):
            asyncio.run(wrapped())

        attrs: dict = mock_emit.call_args_list[-1][0][1]
        assert attrs["status"] == "user_error"

    def test_wrap_name_attribute(self) -> None:
        import asyncio  # noqa: PLC0415

        from fabric_dw.mcp._helpers import _wrap_mcp_tool_with_telemetry  # noqa: PLC0415

        async def _tool() -> str:
            return "ok"

        wrapped = _wrap_mcp_tool_with_telemetry(_tool, "list_warehouses")

        with patch(_TELEMETRY_ENABLED, return_value=True), patch(_EMIT_EVENT) as mock_emit:
            asyncio.run(wrapped())

        attrs: dict = mock_emit.call_args_list[-1][0][1]
        assert attrs["name"] == "list_warehouses"

    def test_wrap_surface_is_mcp(self) -> None:
        import asyncio  # noqa: PLC0415

        from fabric_dw.mcp._helpers import _wrap_mcp_tool_with_telemetry  # noqa: PLC0415

        async def _tool() -> str:
            return "ok"

        wrapped = _wrap_mcp_tool_with_telemetry(_tool, "list_warehouses")

        with patch(_TELEMETRY_ENABLED, return_value=True), patch(_EMIT_EVENT) as mock_emit:
            asyncio.run(wrapped())

        attrs: dict = mock_emit.call_args_list[-1][0][1]
        assert attrs["surface"] == "mcp"

    def test_wrap_destructive_flag(self) -> None:
        import asyncio  # noqa: PLC0415

        from fabric_dw.mcp._helpers import _wrap_mcp_tool_with_telemetry  # noqa: PLC0415

        async def _tool() -> str:
            return "ok"

        wrapped = _wrap_mcp_tool_with_telemetry(_tool, "delete_warehouse", destructive=True)

        with patch(_TELEMETRY_ENABLED, return_value=True), patch(_EMIT_EVENT) as mock_emit:
            asyncio.run(wrapped())

        attrs: dict = mock_emit.call_args_list[-1][0][1]
        assert attrs.get("destructive_op") is True

    def test_emit_failure_does_not_break_tool(self) -> None:
        """If telemetry emission fails, the tool result must still propagate."""
        import asyncio  # noqa: PLC0415

        from fabric_dw.mcp._helpers import _wrap_mcp_tool_with_telemetry  # noqa: PLC0415

        async def _tool() -> str:
            return "result"

        wrapped = _wrap_mcp_tool_with_telemetry(_tool, "list_warehouses")

        with (
            patch(_TELEMETRY_ENABLED, return_value=True),
            patch(_EMIT_EVENT, side_effect=RuntimeError("emit failed")),
        ):
            result = asyncio.run(wrapped())

        assert result == "result"

    def test_mutating_tool_via_instrumented_mcp_emits_exactly_one_event(self) -> None:
        """A mutating tool registered via mutating_tool() emits EXACTLY ONE command_invoked.

        This is the regression test for the double-emission bug: mutating_tool()
        calls _wrap_mcp_tool_with_telemetry (layer 1) and then mcp.tool() which
        triggers InstrumentedFastMCP.tool() (potential layer 2).  The fix marks
        already-wrapped callables with __fabric_telemetry_wrapped__ so that
        InstrumentedFastMCP.tool() skips the second wrapping.
        """
        import asyncio  # noqa: PLC0415

        from fabric_dw.mcp._helpers import InstrumentedFastMCP, mutating_tool  # noqa: PLC0415

        instrumented_mcp: InstrumentedFastMCP = InstrumentedFastMCP("test-server")

        @mutating_tool(instrumented_mcp, "create_warehouse")
        async def create_warehouse(name: str) -> dict:  # type: ignore[return]
            return {"name": name}

        with patch(_TELEMETRY_ENABLED, return_value=True), patch(_EMIT_EVENT) as mock_emit:
            asyncio.run(
                instrumented_mcp._tool_manager.call_tool("create_warehouse", {"name": "wh1"})
            )

        command_invoked_calls = [
            c for c in mock_emit.call_args_list if c[0][0] == "command_invoked"
        ]
        assert len(command_invoked_calls) == 1, (
            f"Expected exactly 1 command_invoked event, got {len(command_invoked_calls)}"
        )

    def test_read_only_tool_via_instrumented_mcp_emits_exactly_one_event(self) -> None:
        """A read-only tool registered via @mcp.tool() emits EXACTLY ONE command_invoked."""
        import asyncio  # noqa: PLC0415

        from fabric_dw.mcp._helpers import InstrumentedFastMCP  # noqa: PLC0415

        instrumented_mcp: InstrumentedFastMCP = InstrumentedFastMCP("test-server-ro")

        @instrumented_mcp.tool(name="list_warehouses")
        async def list_warehouses(workspace: str) -> list:  # type: ignore[return]
            _ = workspace
            return []

        with patch(_TELEMETRY_ENABLED, return_value=True), patch(_EMIT_EVENT) as mock_emit:
            asyncio.run(
                instrumented_mcp._tool_manager.call_tool("list_warehouses", {"workspace": "ws1"})
            )

        command_invoked_calls = [
            c for c in mock_emit.call_args_list if c[0][0] == "command_invoked"
        ]
        assert len(command_invoked_calls) == 1, (
            f"Expected exactly 1 command_invoked event, got {len(command_invoked_calls)}"
        )


# ---------------------------------------------------------------------------
# CLI flush ordering — command_invoked must be enqueued before flush
# ---------------------------------------------------------------------------


class TestCliFlushOrdering:
    """Verify that command_invoked is emitted before flush_telemetry runs.

    Regression tests for the ordering bug where flush_telemetry() (called via
    call_on_close) ran BEFORE emit_command_invoked (in the finally block of
    _InstrumentedGroup.invoke), causing the command_invoked event to be lost.
    """

    def _run_cli_capture_order(self, args: list[str], monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """Run CLI and return the sequence of telemetry calls in invocation order."""
        from fabric_dw.cli._main import cli  # noqa: PLC0415

        monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("JENKINS_URL", raising=False)
        monkeypatch.delenv("TRAVIS", raising=False)
        monkeypatch.delenv("CIRCLECI", raising=False)
        monkeypatch.delenv("GITLAB_CI", raising=False)
        monkeypatch.delenv("TF_BUILD", raising=False)

        call_order: list[str] = []

        def _track_emit(event_name: str, _attrs: dict) -> None:
            call_order.append(f"emit:{event_name}")

        def _track_flush(_timeout_ms: int = 2000) -> None:
            call_order.append("flush")

        runner = CliRunner()  # type: ignore[no-untyped-call]
        with (
            patch(_TELEMETRY_ENABLED, return_value=True),
            patch(_EMIT_EVENT, side_effect=_track_emit),
            patch("fabric_dw.cli._main.flush_telemetry", side_effect=_track_flush),
        ):
            runner.invoke(cli, args, catch_exceptions=True)

        return call_order

    def test_command_invoked_enqueued_before_flush(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """command_invoked must appear in the call order BEFORE the final flush."""
        order = self._run_cli_capture_order(["cache", "clear"], monkeypatch)

        assert "emit:command_invoked" in order, "command_invoked event was never emitted"
        assert "flush" in order, "flush_telemetry was never called"

        last_command_invoked_idx = max(
            i for i, e in enumerate(order) if e == "emit:command_invoked"
        )
        last_flush_idx = max(i for i, e in enumerate(order) if e == "flush")

        assert last_command_invoked_idx < last_flush_idx, (
            f"command_invoked (index {last_command_invoked_idx}) must come before "
            f"flush (index {last_flush_idx}); actual order: {order}"
        )
