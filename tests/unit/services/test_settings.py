"""Unit tests for services.settings — SQL construction and injection safety."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from fabric_dw.exceptions import FabricError, ItemKindError
from fabric_dw.models import WarehouseKind, WarehouseSettings
from fabric_dw.services import settings
from tests.unit.services._helpers import _make_conn, _make_conn_for_ddl, _make_target, _NoOffsetTz

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

_SETTINGS_COLS = [
    "name",
    "is_result_set_caching_on",
    "time_travel_retention_period_days",
    "time_travel_retention_cutoff_date",
    "data_lake_log_publishing_desc",
]

_SETTINGS_ROW: tuple[object, ...] = (
    "SalesWarehouse",
    True,
    7,
    _NOW,
    "AUTO",
)

_SETTINGS_ROW_CACHING_OFF: tuple[object, ...] = (
    "SalesWarehouse",
    False,
    7,
    _NOW,
    "AUTO",
)

_SETTINGS_ROW_NO_CUTOFF: tuple[object, ...] = (
    "SalesWarehouse",
    True,
    30,
    None,
    "AUTO",
)

_SETTINGS_ROW_NULL_DAYS: tuple[object, ...] = (
    "SalesWarehouse",
    True,
    None,  # NULL time_travel_retention_period_days (SQL Analytics Endpoint)
    None,
    None,  # NULL data_lake_log_publishing_desc (SQL Analytics Endpoint)
)

_SETTINGS_ROW_DLLP_PAUSED: tuple[object, ...] = (
    "SalesWarehouse",
    True,
    7,
    _NOW,
    "PAUSED",
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
        assert result.data_lake_log_publishing is True

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
        """Verify the SQL selects the expected columns including the new one."""
        sql = settings._GET_SETTINGS_SQL
        assert "is_result_set_caching_on" in sql
        assert "time_travel_retention_period_days" in sql
        assert "time_travel_retention_cutoff_date" in sql
        assert "data_lake_log_publishing_desc" in sql
        assert "DB_ID()" in sql

    async def test_naive_cutoff_converted_to_utc(self) -> None:
        naive_ts = datetime(2024, 6, 1, 12, 0, 0)  # noqa: DTZ001
        row: tuple[object, ...] = ("SalesWarehouse", True, 7, naive_ts, "AUTO")
        target = _make_target()
        conn = _make_conn([row], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert result.time_travel_retention_cutoff_date is not None
        assert result.time_travel_retention_cutoff_date.tzinfo == UTC

    async def test_null_days_returns_none(self) -> None:
        """NULL time_travel_retention_period_days (SQL Analytics Endpoint) must not raise."""
        target = _make_target()
        conn = _make_conn([_SETTINGS_ROW_NULL_DAYS], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert result.time_travel_retention_days is None

    async def test_empty_rows_raises_fabric_error(self) -> None:
        """If sys.databases returns no rows, a FabricError must be raised."""
        target = _make_target()
        conn = _make_conn([], _SETTINGS_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(FabricError, match=r"sys\.databases returned no rows"),
        ):
            await settings.get_settings(target)

    async def test_bare_date_cutoff_promoted_to_midnight_utc(self) -> None:
        """A bare datetime.date returned by the driver must be promoted to midnight UTC."""
        bare = date(2024, 6, 1)
        row: tuple[object, ...] = ("SalesWarehouse", True, 7, bare, "AUTO")
        target = _make_target()
        conn = _make_conn([row], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert result.time_travel_retention_cutoff_date == datetime(2024, 6, 1, tzinfo=UTC)

    async def test_quasi_naive_cutoff_treated_as_utc(self) -> None:
        """A quasi-naive cutoff (tzinfo present but utcoffset() returns None) is treated as UTC."""
        quasi_naive = datetime(2024, 6, 1, 12, 0, 0, tzinfo=_NoOffsetTz())
        row: tuple[object, ...] = ("SalesWarehouse", True, 7, quasi_naive, "AUTO")
        target = _make_target()
        conn = _make_conn([row], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert result.time_travel_retention_cutoff_date is not None
        assert result.time_travel_retention_cutoff_date.tzinfo == UTC
        cutoff = result.time_travel_retention_cutoff_date
        assert cutoff == datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

    async def test_dllp_auto_maps_to_true(self) -> None:
        """data_lake_log_publishing_desc == 'AUTO' maps to data_lake_log_publishing=True."""
        target = _make_target()
        conn = _make_conn([_SETTINGS_ROW], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert result.data_lake_log_publishing is True

    async def test_dllp_paused_maps_to_false(self) -> None:
        """data_lake_log_publishing_desc == 'PAUSED' maps to data_lake_log_publishing=False."""
        target = _make_target()
        conn = _make_conn([_SETTINGS_ROW_DLLP_PAUSED], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert result.data_lake_log_publishing is False

    async def test_dllp_null_maps_to_false(self) -> None:
        """NULL data_lake_log_publishing_desc (e.g. SQL Analytics Endpoint) maps to False."""
        target = _make_target()
        conn = _make_conn([_SETTINGS_ROW_NULL_DAYS], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await settings.get_settings(target)
        assert result.data_lake_log_publishing is False


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

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, read_conn]):
            result = await settings.set_result_set_caching(target, enabled=True)
        assert isinstance(result, WarehouseSettings)
        assert result.result_set_caching is True

    async def test_disable_returns_settings(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        read_conn = _make_conn([_SETTINGS_ROW_CACHING_OFF], _SETTINGS_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, read_conn]):
            result = await settings.set_result_set_caching(target, enabled=False)
        assert result.result_set_caching is False

    async def test_alter_database_runs_with_autocommit(self) -> None:
        """ALTER DATABASE must use autocommit=True."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        read_conn = _make_conn([_SETTINGS_ROW], _SETTINGS_COLS)
        autocommit_values: list[bool] = []

        def _open(_t: object, **_kw: object) -> object:
            autocommit_values.append(bool(_kw.get("autocommit", False)))
            return ddl_conn if not autocommit_values[:-1] else read_conn

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
        row: tuple[object, ...] = ("SalesWarehouse", True, 30, None, "AUTO")
        read_conn = _make_conn([row], _SETTINGS_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, read_conn]):
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
        row: tuple[object, ...] = ("SalesWarehouse", True, 1, None, "AUTO")
        read_conn = _make_conn([row], _SETTINGS_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, read_conn]):
            result = await settings.set_time_travel_retention(target, days=1)
        assert result.time_travel_retention_days == 1

    async def test_boundary_max_accepted(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        row: tuple[object, ...] = ("SalesWarehouse", True, 120, None, "AUTO")
        read_conn = _make_conn([row], _SETTINGS_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, read_conn]):
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
        row: tuple[object, ...] = ("SalesWarehouse", True, 7, None, "AUTO")
        read_conn = _make_conn([row], _SETTINGS_COLS)
        autocommit_values: list[bool] = []

        def _open(_t: object, **_kw: object) -> object:
            autocommit_values.append(bool(_kw.get("autocommit", False)))
            return ddl_conn if not autocommit_values[:-1] else read_conn

        with patch("fabric_dw.sql.open_connection", side_effect=_open):
            await settings.set_time_travel_retention(target, days=7)
        assert autocommit_values[0] is True


# ===========================================================================
# set_data_lake_log_publishing
# ===========================================================================


class TestSetDataLakeLogPublishing:
    async def test_enable_uses_auto_sql(self) -> None:
        """AUTO keyword (with =) is used when enabled=True."""
        assert "AUTO" in settings._SET_DLLP_SQL_AUTO
        assert "=" in settings._SET_DLLP_SQL_AUTO
        assert "PAUSED" not in settings._SET_DLLP_SQL_AUTO

    async def test_disable_uses_paused_sql(self) -> None:
        """PAUSED keyword (with =) is used when enabled=False."""
        assert "PAUSED" in settings._SET_DLLP_SQL_PAUSED
        assert "=" in settings._SET_DLLP_SQL_PAUSED
        assert "AUTO" not in settings._SET_DLLP_SQL_PAUSED

    async def test_enable_returns_settings(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        read_conn = _make_conn([_SETTINGS_ROW], _SETTINGS_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, read_conn]):
            result = await settings.set_data_lake_log_publishing(target, enabled=True)
        assert isinstance(result, WarehouseSettings)
        assert result.data_lake_log_publishing is True

    async def test_disable_returns_settings(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        read_conn = _make_conn([_SETTINGS_ROW_DLLP_PAUSED], _SETTINGS_COLS)

        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, read_conn]):
            result = await settings.set_data_lake_log_publishing(target, enabled=False)
        assert result.data_lake_log_publishing is False

    async def test_alter_database_runs_with_autocommit(self) -> None:
        """ALTER DATABASE must use autocommit=True."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        read_conn = _make_conn([_SETTINGS_ROW], _SETTINGS_COLS)
        autocommit_values: list[bool] = []

        def _open(_t: object, **_kw: object) -> object:
            autocommit_values.append(bool(_kw.get("autocommit", False)))
            return ddl_conn if not autocommit_values[:-1] else read_conn

        with patch("fabric_dw.sql.open_connection", side_effect=_open):
            await settings.set_data_lake_log_publishing(target, enabled=True)
        # First connection (ALTER DATABASE) must be autocommit.
        assert autocommit_values[0] is True

    async def test_rejects_sql_endpoint(self) -> None:
        """set_data_lake_log_publishing must raise ItemKindError for SQL_ENDPOINT."""
        target = _make_target()
        with pytest.raises(ItemKindError, match="SQL Analytics Endpoints are read-only"):
            await settings.set_data_lake_log_publishing(
                target, enabled=True, kind=WarehouseKind.SQL_ENDPOINT
            )

    async def test_warehouse_allowed(self) -> None:
        """set_data_lake_log_publishing must not raise for WAREHOUSE (the default)."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        read_conn = _make_conn([_SETTINGS_ROW], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, read_conn]):
            result = await settings.set_data_lake_log_publishing(
                target, enabled=True, kind=WarehouseKind.WAREHOUSE
            )
        assert isinstance(result, WarehouseSettings)


# ===========================================================================
# Public constants
# ===========================================================================


class TestPublicConstants:
    def test_retention_min_is_public(self) -> None:
        """RETENTION_MIN must be accessible without underscore prefix."""
        assert settings.RETENTION_MIN == 1

    def test_retention_max_is_public(self) -> None:
        """RETENTION_MAX must be accessible without underscore prefix."""
        assert settings.RETENTION_MAX == 120

    def test_retention_constants_in_all(self) -> None:
        assert "RETENTION_MIN" in settings.__all__
        assert "RETENTION_MAX" in settings.__all__


# ===========================================================================
# SQL Analytics Endpoint guard — write ops must reject SQL_ENDPOINT
# ===========================================================================


class TestSqlEndpointGuard:
    """Write operations (set_result_set_caching, set_time_travel_retention, etc.) are DWH-only."""

    async def test_set_result_set_caching_rejects_sql_endpoint(self) -> None:
        """set_result_set_caching must raise ItemKindError for SQL_ENDPOINT."""
        target = _make_target()
        with pytest.raises(ItemKindError, match="SQL Analytics Endpoints are read-only"):
            await settings.set_result_set_caching(
                target, enabled=True, kind=WarehouseKind.SQL_ENDPOINT
            )

    async def test_set_time_travel_retention_rejects_sql_endpoint(self) -> None:
        """set_time_travel_retention must raise ItemKindError for SQL_ENDPOINT."""
        target = _make_target()
        with pytest.raises(ItemKindError, match="SQL Analytics Endpoints are read-only"):
            await settings.set_time_travel_retention(
                target, days=7, kind=WarehouseKind.SQL_ENDPOINT
            )

    async def test_set_result_set_caching_warehouse_allowed(self) -> None:
        """set_result_set_caching must not raise for WAREHOUSE (the default)."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        read_conn = _make_conn([_SETTINGS_ROW], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, read_conn]):
            result = await settings.set_result_set_caching(
                target, enabled=True, kind=WarehouseKind.WAREHOUSE
            )
        assert isinstance(result, WarehouseSettings)

    async def test_set_time_travel_retention_warehouse_allowed(self) -> None:
        """set_time_travel_retention must not raise for WAREHOUSE (the default)."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        row: tuple[object, ...] = ("SalesWarehouse", True, 7, None, "AUTO")
        read_conn = _make_conn([row], _SETTINGS_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, read_conn]):
            result = await settings.set_time_travel_retention(
                target, days=7, kind=WarehouseKind.WAREHOUSE
            )
        assert isinstance(result, WarehouseSettings)
