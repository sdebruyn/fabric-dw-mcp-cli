"""Tests for shared CLI helpers in _utils.py."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import anyio
import click
import pytest
from click.testing import CliRunner

from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.cli._context import CliContext
from fabric_dw.cli._main import cli
from fabric_dw.cli.commands._utils import (
    build_http_client,
    build_sql_target,
    confirm_destructive,
    get_ctx,
    load_sql_body,
    make_resolver,
    parse_iso_datetime,
    parse_qualified_name,
    resolve_item,
    resolve_workspace,
    resolve_workspace_id,
    validate_workspace_option_or_all_workspaces,
    validate_workspace_or_all_workspaces,
)
from fabric_dw.config import Defaults, UserConfig
from fabric_dw.exceptions import ConfigError
from fabric_dw.models import WarehouseKind
from fabric_dw.resolver import Resolver
from fabric_dw.sql import SqlTarget

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
WS_UUID = UUID(WS_GUID)
WH_UUID = UUID(WH_GUID)

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item_entry(
    *,
    connection_string: str | None = "wh.datawarehouse.fabric.microsoft.com",
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
    display_name: str = "SalesWarehouse",
) -> ItemEntry:
    return ItemEntry(
        id=WH_UUID,
        kind=kind,
        connection_string=connection_string,
        fetched_at=_NOW,
        display_name=display_name,
    )


def _make_mock_http() -> AsyncMock:
    return AsyncMock()


# ---------------------------------------------------------------------------
# build_http_client
# ---------------------------------------------------------------------------


class TestBuildHttpClient:
    """build_http_client yields an authenticated FabricHttpClient."""

    def test_yields_http_client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        mock_ctx = MagicMock()
        mock_ctx.auth = "az-cli"
        mock_http = _make_mock_http()

        # Patch the credential getter and FabricHttpClient so no real auth happens.
        with (
            patch("fabric_dw.auth.get_credential", return_value=MagicMock()),
            patch(
                "fabric_dw.http_client.FabricHttpClient.__aenter__",
                return_value=mock_http,
            ),
            patch("fabric_dw.http_client.FabricHttpClient.__aexit__", return_value=False),
        ):

            async def _run() -> object:
                async with build_http_client(mock_ctx) as http:
                    return http

            result = anyio.run(_run)

        # The result should be our mock (the value returned by __aenter__).
        assert result is mock_http

    def test_config_error_is_translated_to_usage_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ConfigError from get_credential must become a click.UsageError (C22).

        This ensures that configuration problems (e.g. missing env vars, unknown
        credential mode) surface as a clean click message rather than a raw traceback,
        even though ConfigError is not a subtype of FabricError.
        """
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        mock_ctx = MagicMock()
        mock_ctx.auth = "sp"

        with patch(
            "fabric_dw.cli.commands._utils._auth.get_credential",
            side_effect=ConfigError("Missing required env vars: AZURE_TENANT_ID"),
        ):

            async def _run() -> None:
                async with build_http_client(mock_ctx):
                    pass  # pragma: no cover

            with pytest.raises(click.UsageError, match="AZURE_TENANT_ID"):
                anyio.run(_run)


class TestBuildHttpClientCliPresentation:
    """End-to-end: ConfigError from get_credential must not produce a traceback."""

    def test_cli_presents_config_error_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ConfigError raised during credential setup must be shown as a UsageError.

        No raw Python traceback must appear in the output; the CLI must exit non-zero
        with a human-readable message.
        """
        from click.testing import CliRunner  # noqa: PLC0415

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        runner = CliRunner()
        with patch(
            "fabric_dw.cli.commands._utils._auth.get_credential",
            side_effect=ConfigError("Missing required env vars: AZURE_TENANT_ID"),
        ):
            result = runner.invoke(cli, ["workspaces", "list"])
        assert result.exit_code != 0
        assert "Traceback" not in (result.output or "")
        assert "AZURE_TENANT_ID" in (result.output or "")


# ---------------------------------------------------------------------------
# Retry-budget precedence matrix
# ---------------------------------------------------------------------------


def _make_ctx_with_config(
    *,
    cli_retries: int | None = None,
    cli_deadline: int | None = None,
    cfg_retries: int | None = None,
    cfg_deadline: int | None = None,
) -> CliContext:
    """Build a CliContext with the given CLI-option and config-file values."""
    defaults = Defaults(max_429_retries=cfg_retries, retry_deadline_s=cfg_deadline)
    return CliContext(
        max_429_retries=cli_retries,
        retry_deadline_s=cli_deadline,
        _config=UserConfig(defaults=defaults),
    )


class TestResolveMax429Retries:
    """Precedence: CLI > env > config > None."""

    def test_cli_wins_over_env_and_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_DW_MAX_429_RETRIES", "5")
        ctx = _make_ctx_with_config(cli_retries=20, cfg_retries=3)
        from fabric_dw.cli.commands._utils import _resolve_max_429_retries  # noqa: PLC0415

        assert _resolve_max_429_retries(ctx) == 20

    def test_env_wins_over_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_DW_MAX_429_RETRIES", "7")
        ctx = _make_ctx_with_config(cfg_retries=3)
        from fabric_dw.cli.commands._utils import _resolve_max_429_retries  # noqa: PLC0415

        assert _resolve_max_429_retries(ctx) == 7

    def test_config_wins_when_no_cli_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FABRIC_DW_MAX_429_RETRIES", raising=False)
        ctx = _make_ctx_with_config(cfg_retries=4)
        from fabric_dw.cli.commands._utils import _resolve_max_429_retries  # noqa: PLC0415

        assert _resolve_max_429_retries(ctx) == 4

    def test_all_absent_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FABRIC_DW_MAX_429_RETRIES", raising=False)
        ctx = _make_ctx_with_config()
        from fabric_dw.cli.commands._utils import _resolve_max_429_retries  # noqa: PLC0415

        assert _resolve_max_429_retries(ctx) is None

    def test_malformed_env_falls_through_to_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_DW_MAX_429_RETRIES", "not-an-int")
        ctx = _make_ctx_with_config(cfg_retries=6)
        from fabric_dw.cli.commands._utils import _resolve_max_429_retries  # noqa: PLC0415

        assert _resolve_max_429_retries(ctx) == 6

    def test_below_min_env_falls_through_to_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_DW_MAX_429_RETRIES", "0")
        ctx = _make_ctx_with_config(cfg_retries=9)
        from fabric_dw.cli.commands._utils import _resolve_max_429_retries  # noqa: PLC0415

        assert _resolve_max_429_retries(ctx) == 9

    def test_float_formatted_int_env_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_DW_MAX_429_RETRIES='20.0' is accepted as 20 (Docker float-int)."""
        monkeypatch.setenv("FABRIC_DW_MAX_429_RETRIES", "20.0")
        ctx = _make_ctx_with_config()
        from fabric_dw.cli.commands._utils import _resolve_max_429_retries  # noqa: PLC0415

        assert _resolve_max_429_retries(ctx) == 20


class TestResolveRetryDeadlineS:
    """Precedence: CLI > env > config > None."""

    def test_cli_wins_over_env_and_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_DW_RETRY_DEADLINE_S", "60")
        ctx = _make_ctx_with_config(cli_deadline=500, cfg_deadline=120)
        from fabric_dw.cli.commands._utils import _resolve_retry_deadline_s  # noqa: PLC0415

        assert _resolve_retry_deadline_s(ctx) == 500

    def test_env_wins_over_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_DW_RETRY_DEADLINE_S", "250")
        ctx = _make_ctx_with_config(cfg_deadline=120)
        from fabric_dw.cli.commands._utils import _resolve_retry_deadline_s  # noqa: PLC0415

        assert _resolve_retry_deadline_s(ctx) == 250

    def test_config_wins_when_no_cli_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FABRIC_DW_RETRY_DEADLINE_S", raising=False)
        ctx = _make_ctx_with_config(cfg_deadline=180)
        from fabric_dw.cli.commands._utils import _resolve_retry_deadline_s  # noqa: PLC0415

        assert _resolve_retry_deadline_s(ctx) == 180

    def test_all_absent_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FABRIC_DW_RETRY_DEADLINE_S", raising=False)
        ctx = _make_ctx_with_config()
        from fabric_dw.cli.commands._utils import _resolve_retry_deadline_s  # noqa: PLC0415

        assert _resolve_retry_deadline_s(ctx) is None

    def test_malformed_env_falls_through_to_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_DW_RETRY_DEADLINE_S", "not-an-int")
        ctx = _make_ctx_with_config(cfg_deadline=240)
        from fabric_dw.cli.commands._utils import _resolve_retry_deadline_s  # noqa: PLC0415

        assert _resolve_retry_deadline_s(ctx) == 240

    def test_below_min_env_falls_through_to_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_DW_RETRY_DEADLINE_S", "0")
        ctx = _make_ctx_with_config(cfg_deadline=300)
        from fabric_dw.cli.commands._utils import _resolve_retry_deadline_s  # noqa: PLC0415

        assert _resolve_retry_deadline_s(ctx) == 300

    def test_float_formatted_int_env_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FABRIC_DW_RETRY_DEADLINE_S='300.0' is accepted as 300 (Docker float-int)."""
        monkeypatch.setenv("FABRIC_DW_RETRY_DEADLINE_S", "300.0")
        ctx = _make_ctx_with_config()
        from fabric_dw.cli.commands._utils import _resolve_retry_deadline_s  # noqa: PLC0415

        assert _resolve_retry_deadline_s(ctx) == 300

    def test_inf_env_falls_through_to_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """inf in env var is rejected (OverflowError); config value is used instead."""
        monkeypatch.setenv("FABRIC_DW_RETRY_DEADLINE_S", "inf")
        ctx = _make_ctx_with_config(cfg_deadline=120)
        from fabric_dw.cli.commands._utils import _resolve_retry_deadline_s  # noqa: PLC0415

        assert _resolve_retry_deadline_s(ctx) == 120


# ---------------------------------------------------------------------------
# make_resolver
# ---------------------------------------------------------------------------


class TestMakeResolver:
    """make_resolver returns a (Resolver, LookupCache) pair."""

    def test_returns_resolver_and_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        mock_http = _make_mock_http()
        resolver, cache = make_resolver(mock_http)

        assert isinstance(resolver, Resolver)
        assert isinstance(cache, LookupCache)

    def test_returns_fresh_pair_each_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        mock_http = _make_mock_http()
        resolver1, cache1 = make_resolver(mock_http)
        resolver2, cache2 = make_resolver(mock_http)

        # Each call should produce distinct objects.
        assert resolver1 is not resolver2
        assert cache1 is not cache2


# ---------------------------------------------------------------------------
# resolve_workspace_id
# ---------------------------------------------------------------------------


class TestResolveWorkspaceId:
    """resolve_workspace_id delegates to Resolver.workspace_id."""

    def test_returns_uuid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        mock_http = _make_mock_http()

        with patch(
            "fabric_dw.resolver.Resolver.workspace_id",
            new=AsyncMock(return_value=WS_UUID),
        ):
            result = anyio.run(resolve_workspace_id, mock_http, WS_GUID)

        assert result == WS_UUID


# ---------------------------------------------------------------------------
# resolve_item
# ---------------------------------------------------------------------------


class TestResolveItem:
    """resolve_item resolves workspace and item names to UUIDs + entry."""

    def test_returns_ws_id_and_entry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        mock_http = _make_mock_http()
        entry = _make_item_entry()

        with (
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.resolver.Resolver.item",
                new=AsyncMock(return_value=entry),
            ),
        ):
            ws_id, result_entry = anyio.run(resolve_item, mock_http, WS_GUID, WH_GUID)

        assert ws_id == WS_UUID
        assert result_entry is entry


# ---------------------------------------------------------------------------
# build_sql_target
# ---------------------------------------------------------------------------


class TestBuildSqlTarget:
    """build_sql_target builds a SqlTarget or raises on missing connection string."""

    def test_returns_target_and_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        mock_http = _make_mock_http()
        entry = _make_item_entry(connection_string="wh.datawarehouse.fabric.microsoft.com")

        with (
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.resolver.Resolver.item",
                new=AsyncMock(return_value=entry),
            ),
        ):
            target, result_entry = anyio.run(build_sql_target, mock_http, WS_GUID, WH_GUID)

        assert isinstance(target, SqlTarget)
        assert target.connection_string == "wh.datawarehouse.fabric.microsoft.com"
        assert target.database == "SalesWarehouse"
        assert target.workspace_id == WS_GUID
        assert result_entry is entry

    def test_raises_click_exception_when_no_connection_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        mock_http = _make_mock_http()
        entry = _make_item_entry(connection_string=None, display_name="MyWarehouse")

        with (
            patch(
                "fabric_dw.resolver.Resolver.workspace_id",
                new=AsyncMock(return_value=WS_UUID),
            ),
            patch(
                "fabric_dw.resolver.Resolver.item",
                new=AsyncMock(return_value=entry),
            ),
            pytest.raises(click.ClickException) as exc_info,
        ):
            anyio.run(build_sql_target, mock_http, WS_GUID, WH_GUID)

        assert "MyWarehouse" in str(exc_info.value.format_message())


# ---------------------------------------------------------------------------
# parse_qualified_name
# ---------------------------------------------------------------------------


class TestParseQualifiedName:
    """parse_qualified_name wraps the domain helper with UsageError."""

    def test_splits_schema_and_name(self) -> None:
        schema, name = parse_qualified_name("dbo.MyTable")
        assert schema == "dbo"
        assert name == "MyTable"

    def test_custom_kind_label_in_error(self) -> None:
        with pytest.raises(click.UsageError) as exc_info:
            parse_qualified_name("no_dot_here", kind="view")
        assert "view" in str(exc_info.value)
        assert "no_dot_here" in str(exc_info.value)

    def test_missing_dot_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError):
            parse_qualified_name("nodot")

    def test_default_kind_is_object(self) -> None:
        with pytest.raises(click.UsageError) as exc_info:
            parse_qualified_name("nodot")
        assert "object" in str(exc_info.value)

    def test_multiple_dots_parses_first(self) -> None:
        # identifiers.parse_qualified_name behaviour: split on first dot.
        schema, name = parse_qualified_name("schema.table.extra")
        assert schema == "schema"
        assert name == "table.extra"


# ---------------------------------------------------------------------------
# load_sql_body
# ---------------------------------------------------------------------------


class TestLoadSqlBody:
    """load_sql_body returns the body text or raises UsageError."""

    def test_returns_inline_select(self) -> None:
        body = load_sql_body("SELECT 1", None)
        assert body == "SELECT 1"

    def test_returns_file_contents(self, tmp_path: Path) -> None:
        f = tmp_path / "query.sql"
        f.write_text("SELECT 2", encoding="utf-8")
        body = load_sql_body(None, str(f))
        assert body == "SELECT 2"

    def test_file_bom_stripped(self, tmp_path: Path) -> None:
        f = tmp_path / "query.sql"
        f.write_bytes(b"\xef\xbb\xbfSELECT 3")  # UTF-8 BOM
        body = load_sql_body(None, str(f))
        assert body == "SELECT 3"

    def test_both_raises_usage_error(self, tmp_path: Path) -> None:
        f = tmp_path / "q.sql"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(click.UsageError, match="not both"):
            load_sql_body("SELECT 1", str(f))

    def test_neither_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError):
            load_sql_body(None, None)

    def test_missing_file_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="not found"):
            load_sql_body(None, "/nonexistent/path/query.sql")

    def test_custom_option_names_in_error_messages(self, tmp_path: Path) -> None:
        """Callers can pass custom option names into error messages."""
        f = tmp_path / "q.sql"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(click.UsageError, match="--body"):
            load_sql_body("SELECT 1", str(f), inline_opt="--body")
        with pytest.raises(click.UsageError, match="--body"):
            load_sql_body(None, None, inline_opt="--body")


# ---------------------------------------------------------------------------
# parse_iso_datetime
# ---------------------------------------------------------------------------


class TestParseIsoDatetime:
    """parse_iso_datetime normalises ISO-8601 strings to aware datetimes."""

    def test_utc_naive_assumed_utc(self) -> None:
        dt = parse_iso_datetime("2024-01-01T00:00:00", "--since")
        assert dt.tzinfo == UTC

    def test_utc_aware_returned_as_utc(self) -> None:
        dt = parse_iso_datetime("2024-01-01T00:00:00Z", "--since")
        assert dt.tzinfo == UTC

    def test_offset_converted_to_utc(self) -> None:
        dt = parse_iso_datetime("2024-01-01T02:00:00+02:00", "--since")
        assert dt.tzinfo == UTC
        assert dt.hour == 0  # 02:00 +02:00 == 00:00 UTC

    def test_assume_utc_false_leaves_naive(self) -> None:
        dt = parse_iso_datetime("2024-01-01T00:00:00", "--since", assume_utc=False)
        assert dt.tzinfo is None

    def test_assume_utc_false_preserves_offset(self) -> None:
        dt = parse_iso_datetime("2024-01-01T02:00:00+02:00", "--since", assume_utc=False)
        assert dt.utcoffset() is not None

    def test_invalid_string_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError) as exc_info:
            parse_iso_datetime("not-a-date", "--since")
        assert "--since" in str(exc_info.value)
        assert "not-a-date" in str(exc_info.value)

    def test_param_name_in_error_message(self) -> None:
        with pytest.raises(click.UsageError) as exc_info:
            parse_iso_datetime("bad", "--snapshot-dt")
        assert "--snapshot-dt" in str(exc_info.value)

    def test_date_only_string_accepted(self) -> None:
        dt = parse_iso_datetime("2024-06-01", "--since")
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.day == 1


# ---------------------------------------------------------------------------
# confirm_destructive
# ---------------------------------------------------------------------------


class TestConfirmDestructive:
    """confirm_destructive skips prompt when yes=True; returns bool (never raises).

    Policy: declining a destructive prompt is NOT an error (exit 0 / no-op).
    """

    def test_yes_flag_returns_true(self) -> None:
        result = confirm_destructive("Delete everything?", yes=True)
        assert result is True

    def test_declined_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("click.confirm", lambda *_a, **_kw: False)
        result = confirm_destructive("Delete everything?", yes=False)
        assert result is False

    def test_confirmed_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("click.confirm", lambda *_a, **_kw: True)
        result = confirm_destructive("Delete everything?", yes=False)
        assert result is True


# ---------------------------------------------------------------------------
# validate_workspace_or_all_workspaces
# ---------------------------------------------------------------------------


class TestValidateWorkspaceOrAllWorkspaces:
    """Shared guard for the WORKSPACE / --all-workspaces mutual-exclusion contract."""

    def test_explicit_workspace_passes(self) -> None:
        # Should not raise.
        validate_workspace_or_all_workspaces("my-ws", all_workspaces=False)

    def test_all_workspaces_passes(self) -> None:
        # Should not raise.
        validate_workspace_or_all_workspaces(None, all_workspaces=True)

    def test_both_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="mutually exclusive"):
            validate_workspace_or_all_workspaces("my-ws", all_workspaces=True)

    def test_neither_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="Provide WORKSPACE"):
            validate_workspace_or_all_workspaces(None, all_workspaces=False)


# ---------------------------------------------------------------------------
# parse_iso_datetime sanity range
# ---------------------------------------------------------------------------


class TestParseIsodatetimeSanityRange:
    """parse_iso_datetime rejects years outside 2000-2100."""

    def test_valid_year_passes(self) -> None:
        dt = parse_iso_datetime("2024-06-01T12:00:00Z", "--ts")
        assert dt.year == 2024

    def test_year_before_2000_raises(self) -> None:
        with pytest.raises(click.UsageError, match="out of the expected range"):
            parse_iso_datetime("1999-12-31T23:59:59", "--ts")

    def test_year_after_2100_raises(self) -> None:
        with pytest.raises(click.UsageError, match="out of the expected range"):
            parse_iso_datetime("2101-01-01T00:00:00", "--ts")

    def test_epoch_raises(self) -> None:
        with pytest.raises(click.UsageError, match="out of the expected range"):
            parse_iso_datetime("1970-01-01T00:00:00", "--ts")


# ---------------------------------------------------------------------------
# get_ctx
# ---------------------------------------------------------------------------


class TestGetCtx:
    """get_ctx casts click_ctx.obj to CliContext."""

    def test_returns_obj(self) -> None:
        mock_ctx = MagicMock(spec=click.Context)
        sentinel = MagicMock(spec=CliContext)
        mock_ctx.obj = sentinel

        result = get_ctx(mock_ctx)

        assert result is sentinel


# ---------------------------------------------------------------------------
# resolve_workspace_arg / resolve_warehouse_arg — missing-default error messages
# ---------------------------------------------------------------------------


class TestResolveWorkspaceArgErrorMessage:
    """resolve_workspace_arg raises UsageError with actionable guidance when no default is set."""

    def test_missing_workspace_error_suggests_config_default(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error message must mention the config-set command and the word 'default'."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        # warehouses list requires WORKSPACE (or --all-workspaces); invoke with neither.
        result = runner.invoke(cli, ["warehouses", "list"])
        assert result.exit_code != 0
        output = result.output
        assert "no workspace specified" in output
        assert "config set workspace" in output
        assert "default" in output


class TestResolveWarehouseArgErrorMessage:
    """resolve_warehouse_arg raises UsageError with actionable guidance when no default is set."""

    def test_missing_warehouse_error_suggests_config_default(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error message must mention the config-set command, 'default', and SQL Analytics Endpoint."""  # noqa: E501
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WAREHOUSE", raising=False)
        # warehouses get needs a workspace (-w) and a WAREHOUSE; supply the
        # workspace via -w but omit the warehouse positional.
        result = runner.invoke(cli, ["-w", WS_GUID, "warehouses", "get"])
        assert result.exit_code != 0
        output = result.output
        assert "no warehouse specified" in output
        assert "config set warehouse" in output
        assert "default" in output
        assert "SQL Analytics Endpoint" in output


# ---------------------------------------------------------------------------
# resolve_workspace — precedence for the global -w/--workspace option
# ---------------------------------------------------------------------------


def _ctx_with(*, workspace: str | None = None, config_workspace: str | None = None) -> CliContext:
    """Build a CliContext with an explicit -w value and/or a configured default."""
    ctx = CliContext(workspace=workspace)
    # Inject a pre-loaded config so .config does not touch disk.
    ctx._config = UserConfig(defaults=Defaults(workspace=config_workspace))
    return ctx


class TestResolveWorkspace:
    """resolve_workspace precedence: -w > env > config > UsageError."""

    def test_explicit_workspace_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_DW_DEFAULT_WORKSPACE", "env-ws")
        ctx = _ctx_with(workspace="flag-ws", config_workspace="cfg-ws")
        assert resolve_workspace(ctx) == "flag-ws"

    def test_env_var_used_when_no_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FABRIC_DW_DEFAULT_WORKSPACE", "env-ws")
        ctx = _ctx_with(workspace=None, config_workspace="cfg-ws")
        assert resolve_workspace(ctx) == "env-ws"

    def test_config_used_when_no_flag_or_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        ctx = _ctx_with(workspace=None, config_workspace="cfg-ws")
        assert resolve_workspace(ctx) == "cfg-ws"

    def test_missing_everywhere_raises_with_flag_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FABRIC_DW_DEFAULT_WORKSPACE", raising=False)
        ctx = _ctx_with(workspace=None, config_workspace=None)
        with pytest.raises(click.UsageError) as excinfo:
            resolve_workspace(ctx)
        msg = str(excinfo.value)
        assert "no workspace specified" in msg
        assert "-w/--workspace" in msg
        assert "config set workspace" in msg
        # -w must be mentioned before the config-set fallback.
        assert msg.index("-w/--workspace") < msg.index("config set workspace")


# ---------------------------------------------------------------------------
# validate_workspace_option_or_all_workspaces — -w vs -A mutual exclusion
# ---------------------------------------------------------------------------


class TestValidateWorkspaceOptionOrAllWorkspaces:
    """The -w/--workspace vs -A/--all-workspaces mutual-exclusion guard."""

    def test_explicit_workspace_only_passes(self) -> None:
        validate_workspace_option_or_all_workspaces("my-ws", all_workspaces=False)

    def test_all_workspaces_only_passes(self) -> None:
        validate_workspace_option_or_all_workspaces(None, all_workspaces=True)

    def test_neither_passes(self) -> None:
        # A configured default may still supply the workspace later, so neither
        # explicit -w nor -A is valid (unlike the positional WORKSPACE guard).
        validate_workspace_option_or_all_workspaces(None, all_workspaces=False)

    def test_both_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="mutually exclusive"):
            validate_workspace_option_or_all_workspaces("my-ws", all_workspaces=True)
