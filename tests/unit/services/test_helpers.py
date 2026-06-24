"""Unit tests for services._helpers."""

from __future__ import annotations

import pytest

from fabric_dw.services._helpers import compact, normalize_object_definition, reject_non_select

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


# ---------------------------------------------------------------------------
# normalize_object_definition (shared by views, functions, procedures)
# ---------------------------------------------------------------------------


def test_normalize_view_empty_schema_and_name() -> None:
    """Canonical Fabric bug for VIEW: 'CREATE VIEW . AS ...' is fixed."""
    result = normalize_object_definition("CREATE VIEW . AS SELECT 1", "dbo", "vw_sales")
    assert "CREATE VIEW [dbo].[vw_sales]" in result
    assert " AS SELECT 1" in result


def test_normalize_function_empty_schema_and_name() -> None:
    """Canonical Fabric bug for FUNCTION: 'CREATE FUNCTION . (' is fixed."""
    raw = "CREATE FUNCTION . (@x INT) RETURNS INT AS BEGIN RETURN @x END"
    result = normalize_object_definition(raw, "dbo", "fn_clean")
    assert "CREATE FUNCTION [dbo].[fn_clean]" in result
    assert ". (" not in result


def test_normalize_procedure_empty_schema_and_name() -> None:
    """Canonical Fabric bug for PROCEDURE: 'CREATE PROCEDURE . AS ...' is fixed."""
    raw = "CREATE PROCEDURE . AS BEGIN SELECT 1 END"
    result = normalize_object_definition(raw, "fdw_qa", "usp_load")
    assert "CREATE PROCEDURE [fdw_qa].[usp_load]" in result
    assert ". AS" not in result


def test_normalize_create_or_alter_view() -> None:
    """CREATE OR ALTER VIEW header is also normalised."""
    result = normalize_object_definition("CREATE OR ALTER VIEW . AS SELECT 1", "dbo", "vw_sales")
    assert "[dbo].[vw_sales]" in result


def test_normalize_already_correct_unchanged() -> None:
    """When both parts are non-empty, the definition is returned unchanged."""
    defn = "CREATE VIEW [dbo].[vw_sales] AS SELECT 1"
    assert normalize_object_definition(defn, "dbo", "vw_sales") == defn


def test_normalize_no_match_returns_unchanged() -> None:
    """A string with no CREATE <TYPE> header is returned as-is."""
    defn = "SELECT 1 AS col"
    assert normalize_object_definition(defn, "dbo", "vw_sales") == defn


def test_normalize_body_preserved_verbatim() -> None:
    """The body after the header is preserved byte-for-byte."""
    body = "SELECT id, label FROM fdw_qa.t_ctas"
    result = normalize_object_definition(f"CREATE VIEW . AS {body}", "fdw_qa", "vw_dwh")
    assert result.endswith(body)
    assert result == f"CREATE VIEW [fdw_qa].[vw_dwh] AS {body}"
