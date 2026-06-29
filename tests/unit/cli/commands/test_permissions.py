"""Tests for permissions CLI sub-commands — TDD style."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

from click.testing import CliRunner

from fabric_dw.cache import ItemEntry
from fabric_dw.cli._main import cli
from fabric_dw.models import DatabasePermission, DatabasePrincipal, ItemAccess, WarehouseKind
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


def _make_ep_entry() -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=WarehouseKind.SQL_ENDPOINT,
        connection_string="ep.datawarehouse.fabric.microsoft.com",
        fetched_at=datetime.now(tz=UTC),
        display_name="SalesLakehouse",
    )


def _make_item_access() -> ItemAccess:
    return ItemAccess.model_validate(
        {
            "principal": {
                "id": WH_GUID,
                "displayName": "Alice Smith",
                "type": "User",
                "userDetails": {"userPrincipalName": "alice@contoso.com"},
            },
            "itemAccessDetails": {
                "type": "Warehouse",
                "permissions": ["Read"],
                "additionalPermissions": [],
            },
        }
    )


def _make_db_permission() -> DatabasePermission:
    return DatabasePermission(
        principal_name="alice@contoso.com",
        principal_type="EXTERNAL_USER",
        state="GRANT",
        permission_name="SELECT",
        securable_class="DATABASE",
        schema_name=None,
        object_name=None,
    )


def _make_db_principal() -> DatabasePrincipal:
    return DatabasePrincipal(
        name="alice@contoso.com",
        type="EXTERNAL_USER",
        authentication_type="EXTERNAL",
    )


# ---------------------------------------------------------------------------
# permissions item list
# ---------------------------------------------------------------------------


class TestPermissionsItemList:
    """permissions item list -- Fabric REST admin API item-level permissions."""

    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.permissions.list_item_access",
                new=AsyncMock(return_value=[_make_item_access()]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "permissions", "item", "list", WH_GUID])
        assert result.exit_code == 0, result.output

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.permissions.list_item_access",
                new=AsyncMock(return_value=[_make_item_access()]),
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "-w", WS_GUID, "permissions", "item", "list", WH_GUID]
            )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    def test_list_for_sql_endpoint_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.resolve_item",
                new=AsyncMock(return_value=(WS_UUID, _make_ep_entry())),
            ),
            patch(
                "fabric_dw.services.permissions.list_item_access",
                new=AsyncMock(return_value=[]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "permissions", "item", "list", WH_GUID])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# permissions sql list
# ---------------------------------------------------------------------------


class TestPermissionsSqlList:
    """permissions sql list -- T-SQL database permissions."""

    def test_list_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_http = AsyncMock()
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(mock_http),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch(
                "fabric_dw.services.permissions.list_sql_permissions",
                new=AsyncMock(return_value=[_make_db_permission()]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "permissions", "sql", "list", WH_GUID])
        assert result.exit_code == 0, result.output

    def test_list_with_principal_filter(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=[_make_db_permission()])
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.list_sql_permissions", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "sql",
                    "list",
                    WH_GUID,
                    "--principal",
                    "alice@contoso.com",
                ],
            )
        assert result.exit_code == 0, result.output
        _args, kwargs = mock_svc.call_args
        assert kwargs.get("principal") == "alice@contoso.com"

    def test_list_json_output(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.permissions.list_sql_permissions",
                new=AsyncMock(return_value=[_make_db_permission()]),
            ),
        ):
            result = runner.invoke(
                cli, ["--json", "-w", WS_GUID, "permissions", "sql", "list", WH_GUID]
            )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]["principal_name"] == "alice@contoso.com"


# ---------------------------------------------------------------------------
# permissions sql principals
# ---------------------------------------------------------------------------


class TestPermissionsSqlPrincipals:
    """permissions sql principals -- list database principals."""

    def test_principals_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.permissions.list_database_principals",
                new=AsyncMock(return_value=[_make_db_principal()]),
            ),
        ):
            result = runner.invoke(
                cli, ["-w", WS_GUID, "permissions", "sql", "principals", WH_GUID]
            )
        assert result.exit_code == 0, result.output

    def test_principals_user_type_filter(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=[_make_db_principal()])
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.list_database_principals", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "permissions", "sql", "principals", WH_GUID, "--type", "user"],
            )
        assert result.exit_code == 0, result.output
        _args, kwargs = mock_svc.call_args
        assert kwargs.get("principal_type") == "user"


# ---------------------------------------------------------------------------
# permissions sql mine
# ---------------------------------------------------------------------------


class TestPermissionsSqlMine:
    """permissions sql mine -- sys.fn_my_permissions."""

    def test_mine_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
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
                "fabric_dw.services.permissions.my_permissions",
                new=AsyncMock(return_value=[{"permission_name": "SELECT"}]),
            ),
        ):
            result = runner.invoke(cli, ["-w", WS_GUID, "permissions", "sql", "mine", WH_GUID])
        assert result.exit_code == 0, result.output

    def test_mine_with_schema_scope(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_svc = AsyncMock(return_value=[])
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.my_permissions", new=mock_svc),
        ):
            result = runner.invoke(
                cli,
                ["-w", WS_GUID, "permissions", "sql", "mine", WH_GUID, "--scope", "schema:dbo"],
            )
        assert result.exit_code == 0, result.output
        _args, kwargs = mock_svc.call_args
        assert kwargs.get("scope") == "schema:dbo"


# ---------------------------------------------------------------------------
# permissions sql grant
# ---------------------------------------------------------------------------


class TestPermissionsSqlGrant:
    """permissions sql grant."""

    def test_grant_exits_zero_on_database_scope(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_grant = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.grant_permission", new=mock_grant),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "sql",
                    "grant",
                    WH_GUID,
                    "SELECT",
                    "--to",
                    "alice@contoso.com",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_grant.assert_awaited_once()
        assert "Granted" in result.output

    def test_grant_with_schema_scope(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_grant = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.grant_permission", new=mock_grant),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "sql",
                    "grant",
                    WH_GUID,
                    "EXECUTE",
                    "--to",
                    "analysts",
                    "--schema",
                    "dbo",
                ],
            )
        assert result.exit_code == 0, result.output
        _args, kwargs = mock_grant.call_args
        assert kwargs.get("schema") == "dbo"

    def test_grant_with_grant_option(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_grant = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.grant_permission", new=mock_grant),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "sql",
                    "grant",
                    WH_GUID,
                    "SELECT",
                    "--to",
                    "alice@contoso.com",
                    "--with-grant-option",
                ],
            )
        assert result.exit_code == 0, result.output
        _args, kwargs = mock_grant.call_args
        assert kwargs.get("with_grant_option") is True

    def test_grant_scope_mutual_exclusivity(self, runner: CliRunner, cache_env: Path) -> None:
        """--database and --schema together must produce a UsageError."""
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "permissions",
                "sql",
                "grant",
                WH_GUID,
                "SELECT",
                "--to",
                "alice@contoso.com",
                "--database",
                "--schema",
                "dbo",
            ],
        )
        # UsageError is exit code 2 in Click
        assert result.exit_code != 0

    def test_grant_invalid_permission_rejected(self, runner: CliRunner, cache_env: Path) -> None:
        """An invalid permission token produces a ClickException (non-zero exit)."""
        _ = cache_env
        mock_grant = AsyncMock(side_effect=ValueError("Invalid permission"))
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.grant_permission", new=mock_grant),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "sql",
                    "grant",
                    WH_GUID,
                    "SELECTX",
                    "--to",
                    "alice@contoso.com",
                ],
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# permissions sql deny
# ---------------------------------------------------------------------------


class TestPermissionsSqlDeny:
    """permissions sql deny."""

    def test_deny_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_deny = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.deny_permission", new=mock_deny),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "sql",
                    "deny",
                    WH_GUID,
                    "SELECT",
                    "--to",
                    "alice@contoso.com",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_deny.assert_awaited_once()
        assert "Denied" in result.output

    def test_deny_scope_mutual_exclusivity(self, runner: CliRunner, cache_env: Path) -> None:
        """--schema and --object together must produce a non-zero exit."""
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "permissions",
                "sql",
                "deny",
                WH_GUID,
                "SELECT",
                "--to",
                "alice@contoso.com",
                "--schema",
                "dbo",
                "--object",
                "dbo.sales",
            ],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# permissions sql revoke
# ---------------------------------------------------------------------------


class TestPermissionsSqlRevoke:
    """permissions sql revoke."""

    def test_revoke_exits_zero(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_revoke = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.revoke_permission", new=mock_revoke),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "sql",
                    "revoke",
                    WH_GUID,
                    "SELECT",
                    "--from",
                    "alice@contoso.com",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_revoke.assert_awaited_once()
        assert "Revoked" in result.output

    def test_revoke_with_cascade(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_revoke = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.revoke_permission", new=mock_revoke),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "sql",
                    "revoke",
                    WH_GUID,
                    "SELECT",
                    "--from",
                    "alice@contoso.com",
                    "--cascade",
                ],
            )
        assert result.exit_code == 0, result.output
        _args, kwargs = mock_revoke.call_args
        assert kwargs.get("cascade") is True

    def test_revoke_grant_option_only(self, runner: CliRunner, cache_env: Path) -> None:
        _ = cache_env
        mock_revoke = AsyncMock(return_value=None)
        with (
            patch(
                "fabric_dw.cli.commands.permissions.build_http_client",
                new=_make_http_cm(AsyncMock()),
            ),
            patch(
                "fabric_dw.cli.commands.permissions.build_sql_target",
                new=AsyncMock(return_value=(_make_sql_target(), _make_wh_entry())),
            ),
            patch("fabric_dw.services.permissions.revoke_permission", new=mock_revoke),
        ):
            result = runner.invoke(
                cli,
                [
                    "-w",
                    WS_GUID,
                    "permissions",
                    "sql",
                    "revoke",
                    WH_GUID,
                    "SELECT",
                    "--from",
                    "alice@contoso.com",
                    "--grant-option-only",
                ],
            )
        assert result.exit_code == 0, result.output
        _args, kwargs = mock_revoke.call_args
        assert kwargs.get("grant_option_only") is True

    def test_revoke_scope_mutual_exclusivity(self, runner: CliRunner, cache_env: Path) -> None:
        """--database and --object together must produce a non-zero exit."""
        _ = cache_env
        result = runner.invoke(
            cli,
            [
                "-w",
                WS_GUID,
                "permissions",
                "sql",
                "revoke",
                WH_GUID,
                "SELECT",
                "--from",
                "alice@contoso.com",
                "--database",
                "--object",
                "dbo.sales",
            ],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Removal contract: old command names must no longer exist
# ---------------------------------------------------------------------------


class TestRemovedCommandsAreGone:
    """Verify that the old per-item-kind permissions commands were removed."""

    def test_warehouses_permissions_command_removed(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """'warehouses permissions' must no longer exist (moved to 'permissions item list')."""
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "warehouses", "permissions", "--help"])
        # Should produce a "No such command" error, not exit 0.
        assert result.exit_code != 0

    def test_sql_endpoints_permissions_command_removed(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """'sql-endpoints permissions' must no longer exist."""
        _ = cache_env
        result = runner.invoke(cli, ["-w", WS_GUID, "sql-endpoints", "permissions", "--help"])
        assert result.exit_code != 0
