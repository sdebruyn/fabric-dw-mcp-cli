"""Unit tests for permissions rls CLI sub-commands."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

from click.testing import CliRunner

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.models import SecurityPolicy, SecurityPredicate, WarehouseKind
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)


def _make_http_cm(http: object) -> object:
    @asynccontextmanager
    async def _cm(_ctx: object) -> AsyncIterator[object]:
        yield http

    return _cm


def _make_sql_target() -> SqlTarget:
    return SqlTarget(
        workspace_id=WS_GUID,
        database="SalesWarehouse",
        connection_string="wh.datawarehouse.fabric.microsoft.com",
    )


def _make_wh_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.WAREHOUSE,
        connection_string="wh.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesWarehouse",
    )


def _make_security_policy() -> SecurityPolicy:
    pred = SecurityPredicate(
        predicate_type="FILTER",
        operation=None,
        schema_name="dbo",
        table_name="Sales",
        predicate_definition="[rls].[fn_filter]([SalesRep])",
    )
    return SecurityPolicy(
        policy_schema="rls",
        policy_name="SalesFilter",
        is_enabled=True,
        predicates=[pred],
    )


# ---------------------------------------------------------------------------
# permissions rls list
# ---------------------------------------------------------------------------


class TestRlsList:
    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.rls.list_security_policies",
                new=AsyncMock(return_value=[_make_security_policy()]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "permissions", "rls", "list", WH_GUID])
        assert result.exit_code == 0, result.output

    def test_list_empty_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.rls.list_security_policies",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "permissions", "rls", "list", WH_GUID])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# permissions rls create
# ---------------------------------------------------------------------------


class TestRlsCreate:
    def test_create_filter_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.rls.create_security_policy", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "rls",
                    "create",
                    WH_GUID,
                    "rls.SalesFilter",
                    "--filter",
                    "rls.fn_filter(SalesRep)",
                    "--on",
                    "dbo.Sales",
                ],
            )
        assert result.exit_code == 0, result.output

    def test_create_block_with_operation(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.rls.create_security_policy", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "rls",
                    "create",
                    WH_GUID,
                    "rls.SalesBlock",
                    "--block",
                    "rls.fn_block(SalesRep)",
                    "--on",
                    "dbo.Sales",
                    "--operation",
                    "after-insert",
                ],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_svc.call_args
        assert kwargs["state"] is True
        preds = mock_svc.call_args.args[2]
        assert preds[0]["operation"] == "AFTER_INSERT"

    def test_create_both_filter_and_block_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "permissions",
                "rls",
                "create",
                WH_GUID,
                "rls.Policy",
                "--filter",
                "rls.fn(col)",
                "--block",
                "rls.fn(col)",
                "--on",
                "dbo.T",
            ],
        )
        assert result.exit_code != 0

    def test_create_neither_filter_nor_block_fails(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "permissions",
                "rls",
                "create",
                WH_GUID,
                "rls.Policy",
                "--on",
                "dbo.T",
            ],
        )
        assert result.exit_code != 0

    def test_create_state_off(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.rls.create_security_policy", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "rls",
                    "create",
                    WH_GUID,
                    "rls.P",
                    "--filter",
                    "rls.fn(col)",
                    "--on",
                    "dbo.T",
                    "--state",
                    "off",
                ],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_svc.call_args
        assert kwargs["state"] is False


# ---------------------------------------------------------------------------
# permissions rls add-predicate
# ---------------------------------------------------------------------------


class TestRlsAddPredicate:
    def test_add_filter_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.rls.add_predicate", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "rls",
                    "add-predicate",
                    WH_GUID,
                    "rls.SalesFilter",
                    "--filter",
                    "rls.fn_filter(col)",
                    "--on",
                    "dbo.Sales",
                ],
            )
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# permissions rls drop-predicate
# ---------------------------------------------------------------------------


class TestRlsDropPredicate:
    def test_drop_filter_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.rls.drop_predicate", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "rls",
                    "drop-predicate",
                    WH_GUID,
                    "rls.SalesFilter",
                    "--filter",
                    "--on",
                    "dbo.Sales",
                ],
            )
        assert result.exit_code == 0, result.output

    def test_drop_both_flags_fails(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "permissions",
                "rls",
                "drop-predicate",
                WH_GUID,
                "rls.P",
                "--filter",
                "--block",
                "--on",
                "dbo.T",
            ],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# permissions rls set-state
# ---------------------------------------------------------------------------


class TestRlsSetState:
    def test_enable_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.rls.set_policy_state", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "rls",
                    "set-state",
                    WH_GUID,
                    "rls.SalesFilter",
                    "--enable",
                ],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_svc.call_args
        assert kwargs["enabled"] is True

    def test_disable_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.rls.set_policy_state", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "rls",
                    "set-state",
                    WH_GUID,
                    "rls.SalesFilter",
                    "--disable",
                ],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_svc.call_args
        assert kwargs["enabled"] is False


# ---------------------------------------------------------------------------
# permissions rls drop (destructive)
# ---------------------------------------------------------------------------


class TestRlsDrop:
    def test_drop_aborted_without_yes(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "permissions",
                "rls",
                "drop",
                WH_GUID,
                "rls.SalesFilter",
            ],
        )
        # Aborted without confirmation -- Click Abort -> exit_code 1
        assert result.exit_code != 0
        assert "Aborted" in result.output

    def test_drop_with_yes_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.rls.drop_security_policy", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "-y",
                    "permissions",
                    "rls",
                    "drop",
                    WH_GUID,
                    "rls.SalesFilter",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_svc.assert_called_once()

    def test_drop_is_in_destructive_commands(self) -> None:
        from fabric_dw.cli._main import _DESTRUCTIVE_CLI_COMMANDS  # noqa: PLC0415

        assert "permissions.rls.drop" in _DESTRUCTIVE_CLI_COMMANDS
