"""Unit tests for services._helpers."""

from __future__ import annotations

from fabric_dw.services._helpers import compact

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
