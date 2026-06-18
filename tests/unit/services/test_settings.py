"""Unit tests for services.settings — SQL construction and injection safety."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from fabric_dw.models import WarehouseSettings
from fabric_dw.services import settings
from tests.unit.services._helpers import _make_conn, _make_conn_for_ddl, _make_target

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

_SETTINGS_COLS = [
    "name",
    "is_result_set_caching_on",
    "time_travel_retention_period_days",
    "time_travel_retention_cutoff_date",
]

_SETTINGS_ROW: tuple[object, ...] = (
    "SalesWarehouse",
    True,
    7,
    _NOW,
)

_SETTINGS_ROW_CACHING_OFF: tuple[object, ...] = (
    "SalesWarehouse",
    False,
    7,
    _NOW,
)

_SETTINGS_ROW_NO_CUTOFF: tuple[object, ...] = (
    "SalesWarehouse",
    True,
    30,
    None,
)


# ===========================================================================
# get_settings
# ===========================================================================


class TestGetSettings:
    async def test_returns_warehouse_settings(self) -> None:
        target = _make_target()
        conn = _make_conn([_SETTINGS_ROW], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert isinstance(result, WarehouseSettings)
        assert result.database == "SalesWarehouse"
        assert result.result_set_caching is True
        assert result.time_travel_retention_days == 7
        assert result.time_travel_retention_cutoff_date == _NOW

    async def test_none_cutoff_is_none(self) -> None:
        target = _make_target()
        conn = _make_conn([_SETTINGS_ROW_NO_CUTOFF], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert result.time_travel_retention_cutoff_date is None

    async def test_caching_off_is_false(self) -> None:
        target = _make_target()
        conn = _make_conn([_SETTINGS_ROW_CACHING_OFF], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert result.result_set_caching is False

    async def test_selects_correct_columns(self) -> None:
        """Verify the SQL selects the four expected columns."""
        sql = settings._GET_SETTINGS_SQL
        assert "is_result_set_caching_on" in sql
        assert "time_travel_retention_period_days" in sql
        assert "time_travel_retention_cutoff_date" in sql
        assert "DB_ID()" in sql

    async def test_naive_cutoff_converted_to_utc(self) -> None:
        naive_ts = datetime(2024, 6, 1, 12, 0, 0)  # noqa: DTZ001
        row: tuple[object, ...] = ("SalesWarehouse", True, 7, naive_ts)
        target = _make_target()
        conn = _make_conn([row], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert result.time_travel_retention_cutoff_date is not None
        assert result.time_travel_retention_cutoff_date.tzinfo == UTC


# ===========================================================================
# set_result_set_caching
# ===========================================================================


class TestSetResultSetCaching:
    async def test_enable_uses_on_sql(self) -> None:
        """ON keyword is used when enabled=True."""
        assert "ON" in settings._SET_RSC_SQL_ON
        assert "OFF" not in settings._SET_RSC_SQL_ON

    async def test_disable_uses_off_sql(self) -> None:
        """OFF keyword is used when enabled=False."""
        assert "OFF" in settings._SET_RSC_SQL_OFF
        assert "ON" not in settings._SET_RSC_SQL_OFF.replace("RESULT_SET_CACHING", "")

    async def test_enable_returns_settings(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        read_conn = _make_conn([_SETTINGS_ROW], _SETTINGS_COLS)
        call_count = 0

        def _open(_t: object, **_kw: object) -> object:
            nonlocal call_count
            call_count += 1
            # First call is ALTER DATABASE (autocommit=True), second is SELECT
            if call_count == 1:
                return ddl_conn
            return read_conn

        with patch("fabric_dw.sql.open_connection", side_effect=_open):
            result = await settings.set_result_set_caching(target, enabled=True)
        assert isinstance(result, WarehouseSettings)
        assert result.result_set_caching is True

    async def test_disable_returns_settings(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        read_conn = _make_conn([_SETTINGS_ROW_CACHING_OFF], _SETTINGS_COLS)
        call_count = 0

        def _open(_t: object, **_kw: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ddl_conn
            return read_conn

        with patch("fabric_dw.sql.open_connection", side_effect=_open):
            result = await settings.set_result_set_caching(target, enabled=False)
        assert result.result_set_caching is False

    async def test_alter_database_runs_with_autocommit(self) -> None:
        """ALTER DATABASE must use autocommit=True."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        read_conn = _make_conn([_SETTINGS_ROW], _SETTINGS_COLS)
        call_count = 0
        autocommit_values: list[bool] = []

        def _open(_t: object, **_kw: object) -> object:
            nonlocal call_count
            call_count += 1
            autocommit_values.append(bool(_kw.get("autocommit", False)))
            if call_count == 1:
                return ddl_conn
            return read_conn

        with patch("fabric_dw.sql.open_connection", side_effect=_open):
            await settings.set_result_set_caching(target, enabled=True)
        # First connection (ALTER DATABASE) must be autocommit
        assert autocommit_values[0] is True


# ===========================================================================
# set_time_travel_retention
# ===========================================================================


class TestSetTimeTravelRetention:
    async def test_valid_days_returns_settings(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        row: tuple[object, ...] = ("SalesWarehouse", True, 30, None)
        read_conn = _make_conn([row], _SETTINGS_COLS)
        call_count = 0

        def _open(_t: object, **_kw: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ddl_conn
            return read_conn

        with patch("fabric_dw.sql.open_connection", side_effect=_open):
            result = await settings.set_time_travel_retention(target, days=30)
        assert isinstance(result, WarehouseSettings)
        assert result.time_travel_retention_days == 30

    @pytest.mark.parametrize("days", [0, -1, 121, 200])
    async def test_out_of_range_raises_value_error(self, days: int) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="time_travel_retention_period_days"):
            await settings.set_time_travel_retention(target, days=days)

    async def test_boundary_min_accepted(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        row: tuple[object, ...] = ("SalesWarehouse", True, 1, None)
        read_conn = _make_conn([row], _SETTINGS_COLS)
        call_count = 0

        def _open(_t: object, **_kw: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ddl_conn
            return read_conn

        with patch("fabric_dw.sql.open_connection", side_effect=_open):
            result = await settings.set_time_travel_retention(target, days=1)
        assert result.time_travel_retention_days == 1

    async def test_boundary_max_accepted(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        row: tuple[object, ...] = ("SalesWarehouse", True, 120, None)
        read_conn = _make_conn([row], _SETTINGS_COLS)
        call_count = 0

        def _open(_t: object, **_kw: object) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ddl_conn
            return read_conn

        with patch("fabric_dw.sql.open_connection", side_effect=_open):
            result = await settings.set_time_travel_retention(target, days=120)
        assert result.time_travel_retention_days == 120

    async def test_sql_embeds_int_literal(self) -> None:
        """The DDL template embeds the int directly — no SQL params."""
        ddl = settings._SET_RETENTION_SQL_TEMPLATE.format(n=42)
        assert "42" in ddl
        assert "?" not in ddl
        assert "DAYS" in ddl

    async def test_alter_database_runs_with_autocommit(self) -> None:
        """ALTER DATABASE must use autocommit=True."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        row: tuple[object, ...] = ("SalesWarehouse", True, 7, None)
        read_conn = _make_conn([row], _SETTINGS_COLS)
        call_count = 0
        autocommit_values: list[bool] = []

        def _open(_t: object, **_kw: object) -> object:
            nonlocal call_count
            call_count += 1
            autocommit_values.append(bool(_kw.get("autocommit", False)))
            if call_count == 1:
                return ddl_conn
            return read_conn

        with patch("fabric_dw.sql.open_connection", side_effect=_open):
            await settings.set_time_travel_retention(target, days=7)
        assert autocommit_values[0] is True
