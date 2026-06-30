"""Unit tests for services._helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from fabric_dw.exceptions import ItemKindError, NotFoundError
from fabric_dw.models import WarehouseKind
from fabric_dw.services._helpers import (
    _alter_schema_transfer,
    _assert_not_sql_endpoint,
    _other_object_labels_phrase,
    _transfer_object,
    _TransferableObjectLabel,
    build_time_travel_option,
    coerce_to_utc,
    compact,
    reject_non_select,
)
from fabric_dw.services.schemas import _SYSTEM_SCHEMAS
from tests.unit.services._helpers import _make_conn_for_ddl, _make_target

# ---------------------------------------------------------------------------
# coerce_to_utc
# ---------------------------------------------------------------------------


def test_coerce_to_utc_naive_becomes_utc() -> None:
    """coerce_to_utc treats a naive datetime as UTC."""
    naive = datetime(2026, 3, 1, 12, 0, 0)  # noqa: DTZ001
    result = coerce_to_utc(naive)
    assert result.tzinfo is UTC
    assert result == datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


def test_coerce_to_utc_utc_aware_is_unchanged() -> None:
    """coerce_to_utc returns a UTC-aware datetime unchanged."""
    utc_dt = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    result = coerce_to_utc(utc_dt)
    assert result == utc_dt
    assert result.tzinfo is UTC


def test_coerce_to_utc_non_utc_aware_is_converted() -> None:
    """coerce_to_utc converts a non-UTC tz-aware datetime to UTC."""
    plus2 = timezone(timedelta(hours=2))
    aware = datetime(2026, 3, 1, 14, 0, 0, tzinfo=plus2)  # 14:00+02:00 = 12:00 UTC
    result = coerce_to_utc(aware)
    assert result.tzinfo is UTC
    assert result == datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


def test_coerce_to_utc_preserves_sub_second_precision() -> None:
    """coerce_to_utc preserves microseconds when coercing a naive datetime."""
    naive = datetime(2026, 3, 1, 12, 0, 0, 123456)  # noqa: DTZ001
    result = coerce_to_utc(naive)
    assert result.microsecond == 123456
    assert result.tzinfo is UTC


# ---------------------------------------------------------------------------
# compact
# ---------------------------------------------------------------------------


def test_compact_removes_none_values() -> None:
    """compact should drop keys whose value is None."""
    result = compact({"a": 1, "b": None, "c": "hello"})
    assert result == {"a": 1, "c": "hello"}


def test_compact_empty_dict() -> None:
    """compact of an empty dict should return an empty dict."""
    assert compact({}) == {}


def test_compact_all_none() -> None:
    """compact with all-None values should return an empty dict."""
    assert compact({"x": None, "y": None}) == {}


def test_compact_no_none() -> None:
    """compact with no None values should return a copy of the mapping."""
    data: dict[str, object] = {"a": 1, "b": "two", "c": False}
    result = compact(data)
    assert result == data


def test_compact_preserves_falsy_non_none_values() -> None:
    """compact should keep 0, False, '', and [] — only None is removed."""
    result = compact({"zero": 0, "false": False, "empty_str": "", "empty_list": [], "none": None})
    assert "none" not in result
    assert result["zero"] == 0
    assert result["false"] is False
    assert result["empty_str"] == ""
    assert result["empty_list"] == []


def test_compact_does_not_mutate_input() -> None:
    """compact should return a new dict and not modify the input."""
    original: dict[str, object | None] = {"a": 1, "b": None}
    original_copy = dict(original)
    compact(original)
    assert original == original_copy


# ---------------------------------------------------------------------------
# reject_non_select (canonical location in _helpers)
# ---------------------------------------------------------------------------


def test_reject_non_select_plain_select_passes() -> None:
    """SELECT … body passes without raising."""
    reject_non_select("SELECT id FROM dbo.foo")


def test_reject_non_select_with_cte_passes() -> None:
    """WITH … SELECT body passes without raising."""
    reject_non_select("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte")


def test_reject_non_select_case_insensitive() -> None:
    """Keyword check is case-insensitive."""
    reject_non_select("select 1")
    reject_non_select("with cte as (select 1) select * from cte")


def test_reject_non_select_leading_comment_then_select_rejected() -> None:
    """FLIPPED: leading comments before SELECT are now REJECTED (fail-closed raw scan).

    The first raw word token comes from inside the comment ('comment', 'line'),
    not from SELECT or WITH.  Reformulate the body to omit the leading comment.
    """
    with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
        reject_non_select("/* comment */ SELECT 1")
    with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
        reject_non_select("-- line comment\nSELECT 1")


def test_reject_non_select_insert_raises() -> None:
    """INSERT body raises ValueError."""
    with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
        reject_non_select("INSERT INTO dbo.t SELECT 1")


def test_reject_non_select_drop_raises() -> None:
    """DROP body raises ValueError."""
    with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
        reject_non_select("DROP TABLE dbo.t")


def test_reject_non_select_empty_raises() -> None:
    """Empty string raises ValueError."""
    with pytest.raises(ValueError, match="must begin with SELECT or WITH"):
        reject_non_select("")


# ---------------------------------------------------------------------------
# _assert_not_sql_endpoint (centralised from four service modules)
# ---------------------------------------------------------------------------


class TestAssertNotSqlEndpoint:
    """_assert_not_sql_endpoint is the single guard for write-only operations."""

    def test_warehouse_does_not_raise(self) -> None:
        """WAREHOUSE kind must pass without raising."""
        _assert_not_sql_endpoint(WarehouseKind.WAREHOUSE)  # no error

    def test_sql_endpoint_raises_item_kind_error(self) -> None:
        """SQL_ENDPOINT kind must raise ItemKindError."""
        with pytest.raises(ItemKindError):
            _assert_not_sql_endpoint(WarehouseKind.SQL_ENDPOINT)

    def test_sql_endpoint_error_message_mentions_read_only(self) -> None:
        """The error message must clearly state the endpoint is read-only."""
        with pytest.raises(ItemKindError, match="read-only"):
            _assert_not_sql_endpoint(WarehouseKind.SQL_ENDPOINT)

    def test_sql_endpoint_error_message_mentions_data_warehouse(self) -> None:
        """The error message must direct the user to a Fabric Data Warehouse."""
        with pytest.raises(ItemKindError, match="Fabric Data Warehouse"):
            _assert_not_sql_endpoint(WarehouseKind.SQL_ENDPOINT)

    def test_all_four_callers_use_same_function(self) -> None:
        """All four service modules must import the same guard function object.

        This test pins the deduplication: a regression where a module defines
        its own local copy would not be caught by import-time checks alone.
        """
        from fabric_dw.services.load import _assert_not_sql_endpoint as load_guard  # noqa: PLC0415
        from fabric_dw.services.settings import (  # noqa: PLC0415
            _assert_not_sql_endpoint as settings_guard,
        )
        from fabric_dw.services.statistics import (  # noqa: PLC0415
            _assert_not_sql_endpoint as stats_guard,
        )
        from fabric_dw.services.tables import (  # noqa: PLC0415
            _assert_not_sql_endpoint as tables_guard,
        )

        assert load_guard is _assert_not_sql_endpoint
        assert settings_guard is _assert_not_sql_endpoint
        assert stats_guard is _assert_not_sql_endpoint
        assert tables_guard is _assert_not_sql_endpoint


# ---------------------------------------------------------------------------
# _alter_schema_transfer (shared by table/view/function/procedure transfer)
# ---------------------------------------------------------------------------


class TestAlterSchemaTransfer:
    """_alter_schema_transfer builds and runs the ALTER SCHEMA TRANSFER DDL."""

    async def test_emits_exact_ddl_shape(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await _alter_schema_transfer(
                target,
                source_schema="dbo",
                object_name="sales",
                target_schema="archive",
            )
        cursor = conn.cursor.return_value
        call_sql: str = cursor.execute.call_args[0][0]
        assert call_sql == "ALTER SCHEMA [archive] TRANSFER OBJECT::[dbo].[sales]"

    async def test_commits_after_execute(self) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            await _alter_schema_transfer(
                target,
                source_schema="dbo",
                object_name="sales",
                target_schema="archive",
            )
        conn.commit.assert_called_once()

    async def test_rejects_invalid_source_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await _alter_schema_transfer(
                target,
                source_schema="bad--schema",
                object_name="sales",
                target_schema="archive",
            )

    async def test_rejects_invalid_object_name(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await _alter_schema_transfer(
                target,
                source_schema="dbo",
                object_name="bad;name",
                target_schema="archive",
            )

    async def test_rejects_invalid_target_schema(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            await _alter_schema_transfer(
                target,
                source_schema="dbo",
                object_name="sales",
                target_schema="bad]schema",
            )

    @pytest.mark.parametrize("reserved", sorted(_SYSTEM_SCHEMAS))
    async def test_rejects_every_system_schema_as_target(self, reserved: str) -> None:
        """Every name in the canonical _SYSTEM_SCHEMAS list must be rejected as a
        TRANSFER target.  This is the single source of truth that all four
        transfer operations (table/view/function/procedure) inherit by
        calling this shared helper -- a sibling service does not need to
        re-test the full enumeration.
        """
        target = _make_target()
        with pytest.raises(ValueError, match="reserved system schema"):
            await _alter_schema_transfer(
                target,
                source_schema="dbo",
                object_name="sales",
                target_schema=reserved,
            )

    async def test_rejects_system_schema_case_insensitively(self) -> None:
        target = _make_target()
        with pytest.raises(ValueError, match="reserved system schema"):
            await _alter_schema_transfer(
                target,
                source_schema="dbo",
                object_name="sales",
                target_schema="SYS",
            )

    async def test_reserved_target_schema_check_fires_before_any_connection(self) -> None:
        """The reserved-schema rejection must happen before any network I/O."""
        target = _make_target()
        with (
            patch("fabric_dw.sql.open_connection") as mock_open,
            pytest.raises(ValueError, match="reserved system schema"),
        ):
            await _alter_schema_transfer(
                target,
                source_schema="dbo",
                object_name="sales",
                target_schema="sys",
            )
        mock_open.assert_not_called()


# ---------------------------------------------------------------------------
# _other_object_labels_phrase / _transfer_object
# ---------------------------------------------------------------------------


class TestOtherObjectLabelsPhrase:
    """_other_object_labels_phrase builds the "not only X -- if ..." enumeration."""

    @pytest.mark.parametrize(
        ("object_label", "expected"),
        [
            ("table", "a view, function, or procedure"),
            ("view", "a table, function, or procedure"),
            ("function", "a table, view, or procedure"),
            ("procedure", "a table, view, or function"),
        ],
    )
    def test_renders_the_other_three_labels(
        self, object_label: _TransferableObjectLabel, expected: str
    ) -> None:
        assert _other_object_labels_phrase(object_label) == expected

    def test_rejects_unknown_label(self) -> None:
        """A label outside _TRANSFERABLE_OBJECT_LABELS must fail loudly rather
        than silently keeping all four entries and rendering a wrong list.
        """
        with pytest.raises(ValueError, match="Unknown object_label"):
            _other_object_labels_phrase("Table")  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


class TestTransferObjectNotFoundMessage:
    """_transfer_object's post-transfer NotFoundError message, pinned in full.

    These assert the COMPLETE message string (not just a substring) for every
    object kind, so the exact wording -- the Oxford comma, the "--", and the
    pluralisation -- can never silently drift.  This is the text every
    transfer_* function in tables.py/views.py/functions.py/procedures.py
    relied on before the #950 consolidation; the values below were
    reconstructed from the pre-refactor source to confirm byte-for-byte
    equivalence.
    """

    @pytest.mark.parametrize(
        ("object_label", "expected"),
        [
            (
                "table",
                "No table named [archive].[sales] was found after the transfer. "
                "ALTER SCHEMA TRANSFER moves any schema-scoped object with that "
                "name, not only tables -- if a view, function, or procedure "
                "shared this name, check whether it was moved instead.",
            ),
            (
                "view",
                "No view named [archive].[sales] was found after the transfer. "
                "ALTER SCHEMA TRANSFER moves any schema-scoped object with that "
                "name, not only views -- if a table, function, or procedure "
                "shared this name, check whether it was moved instead.",
            ),
            (
                "function",
                "No function named [archive].[sales] was found after the transfer. "
                "ALTER SCHEMA TRANSFER moves any schema-scoped object with that "
                "name, not only functions -- if a table, view, or procedure "
                "shared this name, check whether it was moved instead.",
            ),
            (
                "procedure",
                "No procedure named [archive].[sales] was found after the transfer. "
                "ALTER SCHEMA TRANSFER moves any schema-scoped object with that "
                "name, not only procedures -- if a table, view, or function "
                "shared this name, check whether it was moved instead.",
            ),
        ],
    )
    async def test_pins_full_message_per_object_label(
        self, object_label: _TransferableObjectLabel, expected: str
    ) -> None:
        target = _make_target()
        conn = _make_conn_for_ddl()

        async def _raise_not_found() -> None:
            raise NotFoundError("not found")

        with (
            patch("fabric_dw.sql.open_connection", return_value=conn),
            pytest.raises(NotFoundError) as excinfo,
        ):
            await _transfer_object(
                target,
                source_schema="dbo",
                object_name="sales",
                target_schema="archive",
                object_label=object_label,
                fetch=_raise_not_found,
            )
        assert str(excinfo.value) == expected


# ---------------------------------------------------------------------------
# build_time_travel_option
# ---------------------------------------------------------------------------


class TestBuildTimeTravelOption:
    """Unit tests for build_time_travel_option and its _format_ms_literal helper."""

    def test_carry_rolls_into_next_second(self) -> None:
        """999_750 us rounds to 1000 ms; the timedelta carry must produce ...:01.000.

        Without the timedelta carry the naive f-string approach would emit
        ``...:00.1000``, which is an invalid literal.  This test specifically
        covers the >=999_500 us branch that was previously untested.
        """
        # 2024-01-15T12:00:00.999750 UTC rounds to 2024-01-15T12:00:01.000
        dt = datetime(2024, 1, 15, 12, 0, 0, 999_750, tzinfo=UTC)
        result = build_time_travel_option(dt)
        assert result == " OPTION (FOR TIMESTAMP AS OF '2024-01-15T12:00:01.000')"

    def test_carry_rolls_into_next_minute(self) -> None:
        """999_750 us on a 59-second boundary must carry all the way to :00.000."""
        dt = datetime(2024, 1, 15, 12, 0, 59, 999_750, tzinfo=UTC)
        result = build_time_travel_option(dt)
        assert result == " OPTION (FOR TIMESTAMP AS OF '2024-01-15T12:01:00.000')"

    def test_none_returns_empty_string(self) -> None:
        assert build_time_travel_option(None) == ""
