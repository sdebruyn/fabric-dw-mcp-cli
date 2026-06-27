"""Unit tests for services._helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from fabric_dw.services._helpers import (
    coerce_to_utc,
    compact,
    reject_non_select,
)

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


def test_reject_non_select_leading_comment_then_select_passes() -> None:
    """Block and line comments before SELECT are allowed."""
    reject_non_select("/* comment */ SELECT 1")
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
