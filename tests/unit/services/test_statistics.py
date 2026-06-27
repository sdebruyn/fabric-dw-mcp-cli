"""Unit tests for services.statistics — SQL construction and injection safety."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fabric_dw.exceptions import ItemKindError, NotFoundError, PermissionDeniedError
from fabric_dw.models import Statistic, StatisticDetails, WarehouseKind
from fabric_dw.services import statistics
from tests.unit.services._helpers import _make_conn, _make_conn_for_ddl, _make_target

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

_LIST_COLS = [
    "stat_name",
    "schema_name",
    "table_name",
    "column_name",
    "auto_created",
    "user_created",
    "last_updated",
    "generation_method",
]
_STAT_ROW_1: tuple[object, ...] = (
    "stat_sales_id",
    "dbo",
    "sales",
    "id",
    False,
    True,
    _NOW,
    None,
)
_STAT_ROW_2: tuple[object, ...] = (
    "_WA_Sys_id",
    "dbo",
    "orders",
    "id",
    True,
    False,
    _NOW,
    None,
)

_HEADER_COLS = [
    "Name",
    "Updated",
    "Rows",
    "Rows Sampled",
    "Steps",
    "Density",
    "Average Key Length",
    "String Index",
    "Filter Expression",
    "Unfiltered Rows",
]
_HEADER_ROW: tuple[object, ...] = (
    "stat_sales_id",
    _NOW,
    1000,
    500,
    10,
    0.001,
    4.0,
    "NO",
    None,
    None,
)

_DENSITY_COLS = ["All density", "Average Length", "Columns"]
_DENSITY_ROW: tuple[object, ...] = (0.001, 4.0, "id")

_HISTOGRAM_COLS = [
    "RANGE_HI_KEY",
    "RANGE_ROWS",
    "EQ_ROWS",
    "DISTINCT_RANGE_ROWS",
    "AVG_RANGE_ROWS",
]
_HISTOGRAM_ROW: tuple[object, ...] = ("100", 50.0, 10.0, 5.0, 2.0)


# ===========================================================================
# list_statistics
# ===========================================================================


class TestListStatistics:
    async def test_returns_empty_when_no_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await statistics.list_statistics(target)
        assert result == []

    async def test_returns_statistic_instances(self) -> None:
        target = _make_target()
        conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await statistics.list_statistics(target)
        assert len(result) == 1
        assert isinstance(result[0], Statistic)

    async def test_parses_fields_correctly(self) -> None:
        target = _make_target()
        conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await statistics.list_statistics(target)
        s = result[0]
        assert s.name == "stat_sales_id"
        assert s.qualified_table == "dbo.sales"
        assert s.column == "id"
        assert s.auto_created is False
        assert s.user_created is True

    async def test_returns_all_rows(self) -> None:
        target = _make_target()
        conn = _make_conn([_STAT_ROW_1, _STAT_ROW_2], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await statistics.list_statistics(target)
        assert len(result) == 2

    async def test_sql_references_sys_stats(self) -> None:
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.list_statistics(target)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "sys.stats" in call_sql

    async def test_schema_filter_uses_parameter(self) -> None:
        target = _make_target()
        conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.list_statistics(target, schema="dbo")
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        call_sql: str = call_args[0][0]
        assert "s.name = ?" in call_sql
        params = call_args[0][1] if len(call_args[0]) > 1 else (call_args[1] or {}).get("params")
        assert params is not None
        assert "dbo" in list(params)

    async def test_table_filter_uses_parameter(self) -> None:
        target = _make_target()
        conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.list_statistics(target, table="sales")
        cursor = conn.cursor.return_value
        call_args = cursor.execute.call_args
        call_sql: str = call_args[0][0]
        assert "t.name = ?" in call_sql
        params = call_args[0][1] if len(call_args[0]) > 1 else (call_args[1] or {}).get("params")
        assert params is not None
        assert "sales" in list(params)

    async def test_user_only_filter(self) -> None:
        target = _make_target()
        conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.list_statistics(target, user_only=True)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "user_created = 1" in call_sql

    async def test_auto_only_filter(self) -> None:
        target = _make_target()
        conn = _make_conn([_STAT_ROW_2], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.list_statistics(target, auto_only=True)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "auto_created = 1" in call_sql

    async def test_user_only_and_auto_only_raises(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="mutually exclusive"):
            await statistics.list_statistics(target, user_only=True, auto_only=True)

    async def test_schema_identifier_validated(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.list_statistics(target, schema="bad]schema")

    async def test_table_identifier_validated(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.list_statistics(target, table="bad--table")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied on object sys.stats")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await statistics.list_statistics(target)

    async def test_last_updated_normalized_to_utc(self) -> None:
        target = _make_target()
        # Strip tzinfo to test UTC normalization — intentionally naive.
        naive_dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)
        row: tuple[object, ...] = (
            "stat_sales_id",
            "dbo",
            "sales",
            "id",
            False,
            True,
            naive_dt,
            None,
        )
        conn = _make_conn([row], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await statistics.list_statistics(target)
        assert result[0].last_updated is not None
        assert result[0].last_updated.tzinfo is not None

    async def test_quasi_naive_last_updated_treated_as_utc(self) -> None:
        """Quasi-naive last_updated (tzinfo present but utcoffset() is None) is treated as UTC."""

        class _NoOffsetTz(tzinfo):
            def utcoffset(self, _dt: object) -> None:
                return None

            def tzname(self, _dt: object) -> str:
                return "NoOffset"

            def dst(self, _dt: object) -> None:
                return None

        quasi_naive = datetime(2024, 6, 1, 12, 0, 0, tzinfo=_NoOffsetTz())
        row: tuple[object, ...] = (
            "stat_sales_id",
            "dbo",
            "sales",
            "id",
            False,
            True,
            quasi_naive,
            None,
        )
        target = _make_target()
        conn = _make_conn([row], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await statistics.list_statistics(target)
        assert result[0].last_updated is not None
        assert result[0].last_updated.tzinfo == UTC
        assert result[0].last_updated == datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


# ===========================================================================
# show_statistics
# ===========================================================================


class TestShowStatistics:
    async def test_returns_statistic_details(self) -> None:
        target = _make_target()
        header_conn = _make_conn([_HEADER_ROW], _HEADER_COLS)
        density_conn = _make_conn([_DENSITY_ROW], _DENSITY_COLS)
        hist_conn = _make_conn([_HISTOGRAM_ROW], _HISTOGRAM_COLS)
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[header_conn, density_conn, hist_conn],
        ):
            result = await statistics.show_statistics(target, "dbo.sales", "stat_sales_id")
        assert isinstance(result, StatisticDetails)
        assert result.stat_header is not None
        assert result.stat_header.name == "stat_sales_id"

    async def test_histogram_only_skips_header_and_density(self) -> None:
        target = _make_target()
        hist_conn = _make_conn([_HISTOGRAM_ROW], _HISTOGRAM_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=hist_conn):
            result = await statistics.show_statistics(
                target, "dbo.sales", "stat_sales_id", histogram_only=True
            )
        assert result.stat_header is None
        assert result.density_vector == []
        assert len(result.histogram) == 1

    async def test_sql_contains_dbcc_show_statistics(self) -> None:
        target = _make_target()
        header_conn = _make_conn([_HEADER_ROW], _HEADER_COLS)
        density_conn = _make_conn([_DENSITY_ROW], _DENSITY_COLS)
        hist_conn = _make_conn([_HISTOGRAM_ROW], _HISTOGRAM_COLS)
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[header_conn, density_conn, hist_conn],
        ):
            await statistics.show_statistics(target, "dbo.sales", "stat_sales_id")
        # All three connections should receive DBCC queries
        for conn in [header_conn, density_conn, hist_conn]:
            call_sql: str = conn.cursor.return_value.execute.call_args[0][0].upper()
            assert "DBCC" in call_sql
            assert "SHOW_STATISTICS" in call_sql

    async def test_sql_uses_string_literal_for_table(self) -> None:
        """show_statistics must pass the table as a string literal 'schema.table'.

        Fabric DW DBCC SHOW_STATISTICS does not accept bracket-quoted identifiers
        ([schema].[table]) in the first argument — that causes 'Incorrect syntax
        near '.''.  The table must be a single-quoted string literal.
        """
        target = _make_target()
        header_conn = _make_conn([_HEADER_ROW], _HEADER_COLS)
        density_conn = _make_conn([_DENSITY_ROW], _DENSITY_COLS)
        hist_conn = _make_conn([_HISTOGRAM_ROW], _HISTOGRAM_COLS)
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[header_conn, density_conn, hist_conn],
        ):
            await statistics.show_statistics(target, "dbo.sales", "stat_sales_id")
        call_sql: str = header_conn.cursor.return_value.execute.call_args[0][0]
        # Table must be a string literal, not bracket-quoted identifiers.
        assert "'dbo.sales'" in call_sql
        # Regression: must NOT have stray dot outside the string (the original bug).
        assert "[dbo].[sales]" not in call_sql

    async def test_sql_uses_string_literal_for_stat_name(self) -> None:
        """show_statistics must pass the stat name as a string literal 'stat_name'.

        Fabric DW DBCC SHOW_STATISTICS does not accept bracket-quoted identifiers
        ([stat_name]) in the second argument — that causes 'Could not locate
        statistics'.  Both arguments must be single-quoted string literals, as
        shown in the official Fabric DW documentation examples.

        All three DBCC variants (STAT_HEADER, DENSITY_VECTOR, HISTOGRAM) are
        asserted so that a regression in any individual template is caught.
        """
        target = _make_target()
        header_conn = _make_conn([_HEADER_ROW], _HEADER_COLS)
        density_conn = _make_conn([_DENSITY_ROW], _DENSITY_COLS)
        hist_conn = _make_conn([_HISTOGRAM_ROW], _HISTOGRAM_COLS)
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[header_conn, density_conn, hist_conn],
        ):
            await statistics.show_statistics(target, "dbo.sales", "stat_sales_id")
        # All three DBCC templates must embed the stat name as a string literal.
        for label, conn in [
            ("STAT_HEADER", header_conn),
            ("DENSITY_VECTOR", density_conn),
            ("HISTOGRAM", hist_conn),
        ]:
            call_sql: str = conn.cursor.return_value.execute.call_args[0][0]
            # Stat name must be a string literal, not a bracket-quoted identifier.
            assert "'stat_sales_id'" in call_sql, (
                f"{label}: expected single-quoted stat name in SQL, got: {call_sql!r}"
            )
            # Regression: must NOT have bracket-quoted stat name (the #403 bug).
            assert "[stat_sales_id]" not in call_sql, (
                f"{label}: bracket-quoted stat name found in SQL (regression): {call_sql!r}"
            )

    async def test_quasi_naive_updated_treated_as_utc(self) -> None:
        """Quasi-naive header Updated (tzinfo present but utcoffset() is None) is treated as UTC."""

        class _NoOffsetTz(tzinfo):
            def utcoffset(self, _dt: object) -> None:
                return None

            def tzname(self, _dt: object) -> str:
                return "NoOffset"

            def dst(self, _dt: object) -> None:
                return None

        quasi_naive = datetime(2024, 6, 1, 12, 0, 0, tzinfo=_NoOffsetTz())
        quasi_header_row: tuple[object, ...] = (
            "stat_sales_id",
            quasi_naive,
            1000,
            500,
            10,
            0.001,
            4.0,
            "NO",
            None,
            None,
        )
        target = _make_target()
        header_conn = _make_conn([quasi_header_row], _HEADER_COLS)
        density_conn = _make_conn([_DENSITY_ROW], _DENSITY_COLS)
        hist_conn = _make_conn([_HISTOGRAM_ROW], _HISTOGRAM_COLS)
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[header_conn, density_conn, hist_conn],
        ):
            result = await statistics.show_statistics(target, "dbo.sales", "stat_sales_id")
        assert result.stat_header is not None
        assert result.stat_header.updated is not None
        assert result.stat_header.updated.tzinfo == UTC
        assert result.stat_header.updated == datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

    async def test_raises_not_found_when_header_empty(self) -> None:
        target = _make_target()
        header_conn = _make_conn([], _HEADER_COLS)
        with (
            patch("fabric_dw.sql.open_connection", return_value=header_conn),
            pytest.raises(NotFoundError),
        ):
            await statistics.show_statistics(target, "dbo.sales", "nonexistent_stat")

    async def test_validates_schema_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(target, "bad]schema.sales", "stat")

    async def test_validates_table_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(target, "dbo.sales--bad", "stat")

    async def test_validates_stat_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(target, "dbo.sales", "stat; DROP TABLE--")

    # --- Injection safety ---

    async def test_injection_via_table_name_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(
                target,
                "dbo.sales]; DROP TABLE users--",
                "stat",
            )

    async def test_injection_via_stat_name_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(
                target,
                "dbo.sales",
                "s]; DROP TABLE users--",
            )

    async def test_injection_via_schema_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(
                target,
                "x]; DROP DATABASE master--.sales",
                "stat",
            )

    async def test_single_quote_in_schema_rejected(self) -> None:
        """Single-quote in schema must be rejected before it can reach the string literal.

        DBCC SHOW_STATISTICS embeds the table as 'schema.table'. validate_identifier
        must block any name containing a quote so that the literal is safe without
        explicit escaping.
        """
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(target, "dbo'.sales", "stat")

    async def test_single_quote_in_table_rejected(self) -> None:
        """Single-quote in table name must be rejected before it can reach the string literal."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(target, "dbo.sales'", "stat")

    async def test_single_quote_in_stat_name_rejected(self) -> None:
        """Single-quote in stat name must be rejected before it can reach the string literal.

        DBCC SHOW_STATISTICS embeds the stat name as 'stat_name' (string literal).
        validate_identifier must block any name containing a quote so that the
        literal is safe without explicit escaping.
        """
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(target, "dbo.sales", "stat'name")

    # --- Eventual-consistency retry (Fabric DW: DBCC SHOW_STATISTICS transient error) ---

    async def test_retries_on_could_not_locate_statistics_then_succeeds(self) -> None:
        """show_statistics must retry when DBCC raises 'Could not locate statistics'.

        Simulate 2 transient "Could not locate statistics" errors followed by a
        successful DBCC result.  The function must retry and ultimately return a
        populated StatisticDetails.
        """
        target = _make_target()
        transient_exc = Exception("Could not locate statistics 'pytest_stat_on_id'.")
        header_conn_ok = _make_conn([_HEADER_ROW], _HEADER_COLS)
        density_conn_ok = _make_conn([_DENSITY_ROW], _DENSITY_COLS)
        hist_conn_ok = _make_conn([_HISTOGRAM_ROW], _HISTOGRAM_COLS)

        call_count = 0

        def _open_connection_side_effect(*_args: object, **_kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            # First two attempts: the header connection raises the transient error.
            # We do this by returning a connection whose cursor.execute raises.
            if call_count in (1, 2):
                bad_conn = MagicMock()
                bad_cursor = MagicMock()
                bad_cursor.execute.side_effect = transient_exc
                bad_conn.cursor.return_value = bad_cursor
                return bad_conn
            # 3rd, 4th, 5th calls: the three successful DBCC queries.
            if call_count == 3:
                return header_conn_ok
            if call_count == 4:
                return density_conn_ok
            return hist_conn_ok

        with (
            patch("fabric_dw.sql.open_connection", side_effect=_open_connection_side_effect),
            patch("asyncio.sleep"),  # suppress real sleep in tests
        ):
            result = await statistics.show_statistics(
                target,
                "dbo.sales",
                "stat_sales_id",
                # Use a generous timeout so the test never races against the wall clock.
            )

        assert isinstance(result, StatisticDetails)
        assert result.stat_header is not None
        assert result.stat_header.name == "stat_sales_id"

    async def test_retries_histogram_only_on_could_not_locate_statistics(self) -> None:
        """show_statistics must retry the histogram_only path on the transient DBCC error.

        The retry loop wraps the entire _run_once, including the histogram_only
        early-return branch.  Verify that a transient "Could not locate statistics"
        error on the histogram query is retried and ultimately returns a result.
        """
        target = _make_target()
        transient_exc = Exception("Could not locate statistics 'stat_sales_id'.")
        hist_conn_ok = _make_conn([_HISTOGRAM_ROW], _HISTOGRAM_COLS)

        call_count = 0

        def _open_connection_side_effect(*_args: object, **_kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            # First attempt: histogram query raises the transient error.
            if call_count == 1:
                bad_conn = MagicMock()
                bad_cursor = MagicMock()
                bad_cursor.execute.side_effect = transient_exc
                bad_conn.cursor.return_value = bad_cursor
                return bad_conn
            # Second attempt: successful histogram query.
            return hist_conn_ok

        with (
            patch("fabric_dw.sql.open_connection", side_effect=_open_connection_side_effect),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await statistics.show_statistics(
                target,
                "dbo.sales",
                "stat_sales_id",
                histogram_only=True,
            )

        assert isinstance(result, StatisticDetails)
        assert result.stat_header is None
        assert result.density_vector == []
        assert len(result.histogram) == 1

    async def test_retries_promoted_not_found_error_on_could_not_locate_statistics(self) -> None:
        """show_statistics must retry even when the driver error is promoted to NotFoundError.

        map_driver_error() inside run_query can promote a raw driver exception to
        NotFoundError before it reaches show_statistics.  The retry predicate checks
        the message on ANY exception type, so a NotFoundError whose message contains
        "could not locate statistics" must still be retried.
        """
        target = _make_target()
        # Simulate map_driver_error promoting the transient DBCC error to NotFoundError.
        promoted_exc = NotFoundError("Could not locate statistics 'stat_sales_id'.")
        hist_conn_ok = _make_conn([_HISTOGRAM_ROW], _HISTOGRAM_COLS)
        header_conn_ok = _make_conn([_HEADER_ROW], _HEADER_COLS)
        density_conn_ok = _make_conn([_DENSITY_ROW], _DENSITY_COLS)

        call_count = 0

        def _open_connection_side_effect(*_args: object, **_kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            # First call: raises promoted NotFoundError with the transient message.
            if call_count == 1:
                bad_conn = MagicMock()
                bad_cursor = MagicMock()
                bad_cursor.execute.side_effect = promoted_exc
                bad_conn.cursor.return_value = bad_cursor
                return bad_conn
            # Subsequent calls: successful queries.
            if call_count == 2:
                return header_conn_ok
            if call_count == 3:
                return density_conn_ok
            return hist_conn_ok

        with (
            patch("fabric_dw.sql.open_connection", side_effect=_open_connection_side_effect),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await statistics.show_statistics(target, "dbo.sales", "stat_sales_id")

        assert isinstance(result, StatisticDetails)
        assert result.stat_header is not None

    async def test_does_not_retry_on_unrelated_error(self) -> None:
        """show_statistics must NOT retry on errors unrelated to eventual consistency."""
        target = _make_target()
        unrelated_exc = Exception("Some other database error")
        bad_conn = MagicMock()
        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = unrelated_exc
        bad_conn.cursor.return_value = bad_cursor

        sleep_calls: list[float] = []

        async def _fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        with (
            patch("fabric_dw.sql.open_connection", return_value=bad_conn),
            patch("asyncio.sleep", side_effect=_fake_sleep),
            pytest.raises(Exception, match="Some other database error"),
        ):
            await statistics.show_statistics(target, "dbo.sales", "stat_sales_id")

        # No sleep should have been called — we propagated immediately.
        assert sleep_calls == []

    async def test_raises_not_found_after_timeout(self) -> None:
        """show_statistics must raise NotFoundError once the retry deadline is exhausted."""
        target = _make_target()
        transient_exc = Exception("Could not locate statistics 'stat'.")
        bad_conn = MagicMock()
        bad_cursor = MagicMock()
        bad_cursor.execute.side_effect = transient_exc
        bad_conn.cursor.return_value = bad_cursor

        # Monkeypatch time.monotonic to advance past the deadline on the first retry.
        _start = 1_000_000.0  # arbitrary fixed start
        _call = 0

        def _fake_monotonic() -> float:
            nonlocal _call
            _call += 1
            # First call (deadline = start + timeout): return _start.
            # Every subsequent call: return _start + timeout + 1 (past deadline).
            if _call == 1:
                return _start
            return _start + statistics._DBCC_STAT_TIMEOUT + 1

        with (
            patch("fabric_dw.sql.open_connection", return_value=bad_conn),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("fabric_dw.services.statistics.time") as mock_time,
        ):
            mock_time.monotonic.side_effect = _fake_monotonic
            with pytest.raises(NotFoundError):
                await statistics.show_statistics(target, "dbo.sales", "stat")

        # Deadline was already exhausted after the first attempt — sleep must not
        # have been called (we raise NotFoundError, not sleep-and-retry).
        mock_sleep.assert_not_called()

    async def test_not_found_from_empty_rows_is_not_retried(self) -> None:
        """Empty DBCC result (stat simply does not exist) must raise NotFoundError immediately.

        This is distinct from the 'Could not locate statistics' driver error —
        an empty result set means the stat definitively does not exist and must
        not trigger the eventual-consistency retry loop.
        """
        target = _make_target()
        # Cursor returns no rows for the header query (stat absent from DBCC output).
        header_conn = _make_conn([], _HEADER_COLS)

        sleep_calls: list[float] = []

        async def _fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        with (
            patch("fabric_dw.sql.open_connection", return_value=header_conn),
            patch("asyncio.sleep", side_effect=_fake_sleep),
            pytest.raises(NotFoundError),
        ):
            await statistics.show_statistics(target, "dbo.sales", "nonexistent_stat")

        # No sleep — NotFoundError from empty rows is not retriable.
        assert sleep_calls == []


# ===========================================================================
# create_statistics
# ===========================================================================


class TestCreateStatistics:
    async def test_emits_create_statistics(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await statistics.create_statistics(target, "dbo.sales", "id", name="my_stat")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "CREATE STATISTICS" in call_sql

    async def test_bracket_quotes_table_identifier(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await statistics.create_statistics(target, "dbo.sales", "id", name="my_stat")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo].[sales]" in call_sql

    async def test_bracket_quotes_column_identifier(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await statistics.create_statistics(target, "dbo.sales", "id", name="my_stat")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[id]" in call_sql

    async def test_bracket_quotes_stat_name(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await statistics.create_statistics(target, "dbo.sales", "id", name="my_stat")
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[my_stat]" in call_sql

    async def test_fullscan_sql(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await statistics.create_statistics(target, "dbo.sales", "id", name="s", fullscan=True)
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "FULLSCAN" in call_sql

    async def test_sample_percent_sql(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await statistics.create_statistics(
                target, "dbo.sales", "id", name="s", sample_percent=50
            )
        cursor = ddl_conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "SAMPLE" in call_sql
        assert "50" in call_sql
        assert "PERCENT" in call_sql

    async def test_sample_percent_out_of_range_raises(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="sample_percent"):
            await statistics.create_statistics(
                target, "dbo.sales", "id", name="s", sample_percent=0
            )

    async def test_sample_percent_101_raises(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="sample_percent"):
            await statistics.create_statistics(
                target, "dbo.sales", "id", name="s", sample_percent=101
            )

    async def test_name_required_raises(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="name"):
            await statistics.create_statistics(target, "dbo.sales", "id", name=None)

    async def test_returns_statistic_object(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            result = await statistics.create_statistics(
                target, "dbo.sales", "id", name="stat_sales_id"
            )
        assert isinstance(result, Statistic)

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await statistics.create_statistics(target, "dbo.sales", "id", name="s")
        ddl_conn.commit.assert_called_once()

    # --- Injection safety ---

    async def test_injection_via_table_schema_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.create_statistics(
                target,
                "dbo]; DROP TABLE users--.sales",
                "id",
                name="s",
            )

    async def test_injection_via_table_name_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.create_statistics(
                target,
                "dbo.sales]; DROP TABLE users--",
                "id",
                name="s",
            )

    async def test_injection_via_column_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.create_statistics(
                target,
                "dbo.sales",
                "id]; DROP TABLE users--",
                name="s",
            )

    async def test_injection_via_stat_name_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.create_statistics(
                target,
                "dbo.sales",
                "id",
                name="s]; DROP TABLE users--",
            )


# ===========================================================================
# update_statistics
# ===========================================================================


class TestUpdateStatistics:
    async def test_emits_update_statistics(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.update_statistics(target, "dbo.sales", "my_stat")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "UPDATE STATISTICS" in call_sql

    async def test_bracket_quotes_identifiers(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.update_statistics(target, "dbo.sales", "my_stat")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo].[sales]" in call_sql
        assert "[my_stat]" in call_sql

    async def test_fullscan_sql(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.update_statistics(target, "dbo.sales", "s", fullscan=True)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "FULLSCAN" in call_sql

    async def test_sample_percent_sql(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.update_statistics(target, "dbo.sales", "s", sample_percent=25)
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "SAMPLE" in call_sql
        assert "25" in call_sql

    async def test_sample_percent_range_validated(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="sample_percent"):
            await statistics.update_statistics(target, "dbo.sales", "s", sample_percent=0)

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.update_statistics(target, "dbo.sales", "s")
        conn.commit.assert_called_once()

    async def test_validates_stat_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.update_statistics(target, "dbo.sales", "s--bad")

    # --- Injection safety ---

    async def test_injection_via_stat_name_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.update_statistics(target, "dbo.sales", "s]; DROP TABLE users--")

    async def test_injection_via_table_schema_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.update_statistics(target, "bad]schema.sales", "s")


# ===========================================================================
# drop_statistics
# ===========================================================================


class TestDropStatistics:
    async def test_emits_drop_statistics(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.drop_statistics(target, "dbo.sales", "my_stat")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0].upper()
        assert "DROP STATISTICS" in call_sql

    async def test_bracket_quotes_identifiers(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.drop_statistics(target, "dbo.sales", "my_stat")
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert "[dbo].[sales]" in call_sql
        assert "[my_stat]" in call_sql

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.drop_statistics(target, "dbo.sales", "my_stat")
        conn.commit.assert_called_once()

    async def test_validates_stat_name_identifier(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.drop_statistics(target, "dbo.sales", "bad;stat")

    async def test_maps_permission_denied(self) -> None:
        target = _make_target()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("permission was denied to drop statistics")
        conn.cursor.return_value = cursor
        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(PermissionDeniedError),
        ):
            await statistics.drop_statistics(target, "dbo.sales", "my_stat")

    # --- Injection safety ---

    async def test_injection_via_stat_name_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.drop_statistics(target, "dbo.sales", "s]; DROP TABLE users--")

    async def test_injection_via_table_name_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.drop_statistics(target, "dbo.sales]; DROP TABLE x--", "s")

    async def test_injection_via_schema_rejected(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.drop_statistics(target, "x]; DROP DATABASE master--.sales", "s")


# ===========================================================================
# SQL Endpoint guard — service layer
# ===========================================================================


class TestSqlEndpointGuard:
    """Verify that create/update/drop reject SQL Endpoint items before any I/O."""

    async def test_create_statistics_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        with pytest.raises(ItemKindError, match="read-only"):
            await statistics.create_statistics(
                target,
                "dbo.sales",
                "id",
                name="s",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    async def test_update_statistics_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        with pytest.raises(ItemKindError, match="read-only"):
            await statistics.update_statistics(
                target,
                "dbo.sales",
                "s",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    async def test_drop_statistics_rejects_sql_endpoint(self) -> None:
        target = _make_target()
        with pytest.raises(ItemKindError, match="read-only"):
            await statistics.drop_statistics(
                target,
                "dbo.sales",
                "s",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    async def test_list_statistics_allows_sql_endpoint(self) -> None:
        """list_statistics must NOT be guarded — SQL endpoints are read-only OK."""
        target = _make_target()
        conn = _make_conn([], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await statistics.list_statistics(target)
        assert isinstance(result, list)

    async def test_guard_fires_before_identifier_validation(self) -> None:
        """ItemKindError must fire even when identifiers are invalid."""
        target = _make_target()
        with pytest.raises(ItemKindError):
            await statistics.create_statistics(
                target,
                "bad]schema.sales",
                "id",
                name="s",
                kind=WarehouseKind.SQL_ENDPOINT,
            )

    async def test_warehouse_kind_allowed(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[conn, fetch_conn]):
            result = await statistics.create_statistics(
                target,
                "dbo.sales",
                "id",
                name="stat_sales_id",
                kind=WarehouseKind.WAREHOUSE,
            )
        assert isinstance(result, Statistic)


# ===========================================================================
# Row-parser zero-value correctness
# ===========================================================================


class TestRowParserZeroValues:
    """Verify that zero int/float DB values are not corrupted to None by the parsers."""

    async def test_header_zero_rows_preserved(self) -> None:
        """Rows=0 must survive as 0, not fall through to None."""
        target = _make_target()
        zero_row: tuple[object, ...] = (
            "stat_empty",  # Name
            _NOW,  # Updated
            0,  # Rows        ← zero int
            0,  # Rows Sampled← zero int
            0,  # Steps       ← zero int
            0.0,  # Density    ← zero float
            0.0,  # Average Key Length ← zero float
            "NO",  # String Index
            None,  # Filter Expression
            0,  # Unfiltered Rows ← zero int
        )
        header_conn = _make_conn([zero_row], _HEADER_COLS)
        density_conn = _make_conn([], _DENSITY_COLS)
        hist_conn = _make_conn([], _HISTOGRAM_COLS)
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[header_conn, density_conn, hist_conn],
        ):
            result = await statistics.show_statistics(target, "dbo.sales", "stat_empty")
        hdr = result.stat_header
        assert hdr is not None
        assert hdr.rows == 0, f"Expected 0, got {hdr.rows!r}"
        assert hdr.rows_sampled == 0, f"Expected 0, got {hdr.rows_sampled!r}"
        assert hdr.steps == 0, f"Expected 0, got {hdr.steps!r}"
        assert hdr.density == 0.0, f"Expected 0.0, got {hdr.density!r}"
        assert hdr.average_key_length == 0.0, f"Expected 0.0, got {hdr.average_key_length!r}"
        assert hdr.unfiltered_rows == 0, f"Expected 0, got {hdr.unfiltered_rows!r}"

    async def test_density_zero_values_preserved(self) -> None:
        """all_density=0.0 and average_length=0.0 must survive as 0.0, not None."""
        target = _make_target()
        zero_density_row: tuple[object, ...] = (0.0, 0.0, "id")  # all_density, avg_length, cols
        header_conn = _make_conn([_HEADER_ROW], _HEADER_COLS)
        density_conn = _make_conn([zero_density_row], _DENSITY_COLS)
        hist_conn = _make_conn([], _HISTOGRAM_COLS)
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[header_conn, density_conn, hist_conn],
        ):
            result = await statistics.show_statistics(target, "dbo.sales", "stat_sales_id")
        assert len(result.density_vector) == 1
        dv = result.density_vector[0]
        assert dv.all_density == 0.0, f"Expected 0.0, got {dv.all_density!r}"
        assert dv.average_length == 0.0, f"Expected 0.0, got {dv.average_length!r}"

    async def test_histogram_zero_float_values_preserved(self) -> None:
        """RANGE_ROWS=0.0 and EQ_ROWS=0.0 must survive as 0.0, not None."""
        target = _make_target()
        zero_hist_row: tuple[object, ...] = (
            "100",  # RANGE_HI_KEY
            0.0,  # RANGE_ROWS       ← zero float
            0.0,  # EQ_ROWS          ← zero float
            0.0,  # DISTINCT_RANGE_ROWS ← zero float
            0.0,  # AVG_RANGE_ROWS   ← zero float
        )
        hist_conn = _make_conn([zero_hist_row], _HISTOGRAM_COLS)
        with patch("fabric_dw.sql.open_connection", return_value=hist_conn):
            result = await statistics.show_statistics(
                target, "dbo.sales", "stat_sales_id", histogram_only=True
            )
        assert len(result.histogram) == 1
        step = result.histogram[0]
        assert step.range_rows == 0.0, f"Expected 0.0, got {step.range_rows!r}"
        assert step.eq_rows == 0.0, f"Expected 0.0, got {step.eq_rows!r}"
        assert step.distinct_range_rows == 0.0, f"Expected 0.0, got {step.distinct_range_rows!r}"
        assert step.avg_range_rows == 0.0, f"Expected 0.0, got {step.avg_range_rows!r}"


# ===========================================================================
# Injection safety — embedded ] in a single identifier segment
# ===========================================================================


class TestBracketInjection:
    """Test that a bare ] inside a single identifier segment is rejected by validate_identifier."""

    async def test_bracket_in_stat_name_rejected(self) -> None:
        """show_statistics: stat_name containing ] must raise ValueError."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(target, "dbo.sales", "my]stat")

    async def test_bracket_in_schema_rejected(self) -> None:
        """show_statistics: schema part containing ] must raise ValueError."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(target, "my]schema.sales", "stat")

    async def test_bracket_in_table_rejected(self) -> None:
        """show_statistics: table part containing ] must raise ValueError."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.show_statistics(target, "dbo.my]table", "stat")

    async def test_bracket_in_create_stat_name_rejected(self) -> None:
        """create_statistics: stat name containing ] must raise ValueError."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.create_statistics(target, "dbo.sales", "id", name="my]stat")

    async def test_bracket_in_update_stat_name_rejected(self) -> None:
        """update_statistics: stat name containing ] must raise ValueError."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.update_statistics(target, "dbo.sales", "my]stat")

    async def test_bracket_in_drop_stat_name_rejected(self) -> None:
        """drop_statistics: stat name containing ] must raise ValueError."""
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await statistics.drop_statistics(target, "dbo.sales", "my]stat")


# ===========================================================================
# Regression: issue-371 — exact SQL for roundtrip identifiers
# ===========================================================================
#
# The integration test uses schema='pytest_d1e50944', table='pytest_stat_roundtrip',
# stat='pytest_stat_on_id'.  These tests pin the exact SQL each operation
# produces so that any future regression (stray '.', wrong quoting) is caught
# without a Fabric round-trip.
# ===========================================================================


class TestExactSqlRoundtripRegression:
    """Pin the exact SQL for the integration roundtrip identifiers (issue-371)."""

    _SCHEMA = "pytest_d1e50944"
    _TABLE = "pytest_stat_roundtrip"
    _COL = "id"
    _STAT = "pytest_stat_on_id"
    _QUALIFIED = f"{_SCHEMA}.{_TABLE}"

    async def test_show_statistics_exact_sql_no_stray_dot(self) -> None:
        """DBCC SHOW_STATISTICS must use string literals for both table and stat name.

        Regression #371: passing [schema].[table] caused 'Incorrect syntax near .'
        on real Fabric DW.  The first argument must be 'schema.table' (string literal).

        Regression #403: passing [stat_name] caused 'Could not locate statistics'
        on real Fabric DW.  The second argument must also be 'stat_name' (string
        literal), matching the official Fabric DW documentation examples.

        All three DBCC templates (STAT_HEADER, DENSITY_VECTOR, HISTOGRAM) are pinned.
        """
        target = _make_target()
        header_conn = _make_conn([_HEADER_ROW], _HEADER_COLS)
        density_conn = _make_conn([_DENSITY_ROW], _DENSITY_COLS)
        hist_conn = _make_conn([_HISTOGRAM_ROW], _HISTOGRAM_COLS)
        with patch(
            "fabric_dw.sql.open_connection",
            side_effect=[header_conn, density_conn, hist_conn],
        ):
            await statistics.show_statistics(target, self._QUALIFIED, self._STAT)

        table_literal = f"'{self._SCHEMA}.{self._TABLE}'"
        stat_literal = f"'{self._STAT}'"

        # --- STAT_HEADER ---
        header_sql: str = header_conn.cursor.return_value.execute.call_args[0][0]
        expected_header = (
            f"DBCC SHOW_STATISTICS ({table_literal}, {stat_literal}) WITH STAT_HEADER;"
        )
        assert header_sql == expected_header, (
            f"STAT_HEADER SQL mismatch.\nGot:      {header_sql!r}\nExpected: {expected_header!r}"
        )
        assert ".." not in header_sql
        assert f"[{self._SCHEMA}].[{self._TABLE}]" not in header_sql
        # Regression #403: stat name must NOT be bracket-quoted.
        assert f"[{self._STAT}]" not in header_sql

        # --- DENSITY_VECTOR ---
        density_sql: str = density_conn.cursor.return_value.execute.call_args[0][0]
        expected_density = (
            f"DBCC SHOW_STATISTICS ({table_literal}, {stat_literal}) WITH DENSITY_VECTOR;"
        )
        assert density_sql == expected_density, (
            f"DENSITY_VECTOR SQL mismatch.\n"
            f"Got:      {density_sql!r}\nExpected: {expected_density!r}"
        )
        assert ".." not in density_sql
        assert f"[{self._SCHEMA}].[{self._TABLE}]" not in density_sql
        assert f"[{self._STAT}]" not in density_sql

        # --- HISTOGRAM ---
        histogram_sql: str = hist_conn.cursor.return_value.execute.call_args[0][0]
        expected_histogram = (
            f"DBCC SHOW_STATISTICS ({table_literal}, {stat_literal}) WITH HISTOGRAM;"
        )
        assert histogram_sql == expected_histogram, (
            f"HISTOGRAM SQL mismatch.\n"
            f"Got:      {histogram_sql!r}\nExpected: {expected_histogram!r}"
        )
        assert ".." not in histogram_sql
        assert f"[{self._SCHEMA}].[{self._TABLE}]" not in histogram_sql
        assert f"[{self._STAT}]" not in histogram_sql

    async def test_create_statistics_exact_sql(self) -> None:
        """CREATE STATISTICS SQL must be bracket-quoted with no stray dots."""
        target = _make_target()
        ddl_conn = _make_conn_for_ddl()
        fetch_conn = _make_conn([_STAT_ROW_1], _LIST_COLS)
        with patch("fabric_dw.sql.open_connection", side_effect=[ddl_conn, fetch_conn]):
            await statistics.create_statistics(
                target,
                self._QUALIFIED,
                self._COL,
                name=self._STAT,
                fullscan=True,
            )
        call_sql: str = ddl_conn.cursor.return_value.execute.call_args[0][0]
        expected = (
            f"CREATE STATISTICS [{self._STAT}]"
            f" ON [{self._SCHEMA}].[{self._TABLE}]"
            f" ([{self._COL}]) WITH FULLSCAN;"
        )
        assert call_sql == expected, f"Got: {call_sql!r}\nExpected: {expected!r}"
        assert ".." not in call_sql

    async def test_update_statistics_exact_sql(self) -> None:
        """UPDATE STATISTICS SQL must be bracket-quoted with no stray dots."""
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.update_statistics(target, self._QUALIFIED, self._STAT, fullscan=True)
        call_sql: str = conn.cursor.return_value.execute.call_args[0][0]
        expected = (
            f"UPDATE STATISTICS [{self._SCHEMA}].[{self._TABLE}] ([{self._STAT}]) WITH FULLSCAN;"
        )
        assert call_sql == expected, f"Got: {call_sql!r}\nExpected: {expected!r}"
        assert ".." not in call_sql

    async def test_drop_statistics_exact_sql(self) -> None:
        """DROP STATISTICS SQL must use three-part bracket-quoted name with no stray dots."""
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await statistics.drop_statistics(target, self._QUALIFIED, self._STAT)
        call_sql: str = conn.cursor.return_value.execute.call_args[0][0]
        expected = f"DROP STATISTICS [{self._SCHEMA}].[{self._TABLE}].[{self._STAT}];"
        assert call_sql == expected, f"Got: {call_sql!r}\nExpected: {expected!r}"
        assert ".." not in call_sql
