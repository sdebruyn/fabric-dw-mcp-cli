"""Unit tests for services._helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from fabric_dw.exceptions import ItemKindError
from fabric_dw.models import WarehouseKind
from fabric_dw.services._helpers import (
    _assert_not_sql_endpoint,
    coerce_to_utc,
    compact,
    normalize_object_definition,
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


# ---------------------------------------------------------------------------
# Hardening: edge cases added after review
# ---------------------------------------------------------------------------


def test_normalize_whitespace_after_dot() -> None:
    """Extra whitespace between dot and bracketed name is tolerated (item 1)."""
    # `[dbo].  [vw_sales]` — two spaces between dot and name token.
    result = normalize_object_definition(
        "CREATE VIEW [dbo].  [vw_sales] AS SELECT 1", "dbo", "vw_sales"
    )
    # Both parts are non-empty so the definition is returned unchanged.
    assert "CREATE VIEW [dbo]" in result
    assert "[vw_sales]" in result


def test_normalize_whitespace_between_dot_and_empty_name() -> None:
    """Extra whitespace between dot and empty slot is handled (item 1)."""
    # Schema missing, name present, whitespace after dot.
    result = normalize_object_definition("CREATE VIEW .  [vw_sales] AS SELECT 1", "dbo", "vw_sales")
    assert "[dbo].[vw_sales]" in result


def test_normalize_leading_comment_returns_unchanged() -> None:
    """A definition starting with a leading comment is returned unchanged (item 2)."""
    defn = "-- header comment\nCREATE VIEW . AS SELECT 1"
    result = normalize_object_definition(defn, "dbo", "vw_sales")
    assert result == defn


def test_normalize_empty_catalog_schema_returns_unchanged() -> None:
    """Empty catalog schema_name → return unchanged to avoid `[].[x]` (item 3)."""
    defn = "CREATE VIEW . AS SELECT 1"
    assert normalize_object_definition(defn, "", "vw_sales") == defn


def test_normalize_blank_catalog_name_returns_unchanged() -> None:
    """Blank catalog name → return unchanged to avoid `[dbo].[]` (item 3)."""
    defn = "CREATE VIEW . AS SELECT 1"
    assert normalize_object_definition(defn, "dbo", "   ") == defn


def test_normalize_function_schema_present_name_missing() -> None:
    """FUNCTION: schema present, name missing — name is substituted (item 4)."""
    result = normalize_object_definition(
        "CREATE FUNCTION [dbo]. (@x INT) RETURNS INT AS BEGIN RETURN @x END",
        "dbo",
        "fn_clean",
    )
    assert "[dbo].[fn_clean]" in result


def test_normalize_function_name_present_schema_missing() -> None:
    """FUNCTION: name present, schema missing — schema is substituted (item 4)."""
    result = normalize_object_definition(
        "CREATE FUNCTION .[fn_clean] (@x INT) RETURNS INT AS BEGIN RETURN @x END",
        "dbo",
        "fn_clean",
    )
    assert "[dbo].[fn_clean]" in result


def test_normalize_procedure_schema_present_name_missing() -> None:
    """PROCEDURE: schema present, name missing — name is substituted (item 4)."""
    result = normalize_object_definition(
        "CREATE PROCEDURE [dbo]. AS BEGIN SELECT 1 END", "dbo", "usp_load"
    )
    assert "[dbo].[usp_load]" in result


def test_normalize_procedure_name_present_schema_missing() -> None:
    """PROCEDURE: name present, schema missing — schema is substituted (item 4)."""
    result = normalize_object_definition(
        "CREATE PROCEDURE .[usp_load] AS BEGIN SELECT 1 END", "dbo", "usp_load"
    )
    assert "[dbo].[usp_load]" in result


def test_normalize_bracket_in_catalog_name_escaped() -> None:
    """Catalog name containing `]` is escaped to `]]` in the output (item 6)."""
    result = normalize_object_definition("CREATE VIEW . AS SELECT 1", "dbo", "vw_tricky]name")
    # `]` in the name must be doubled so the bracket-quoted DDL is valid.
    assert "[vw_tricky]]name]" in result
    assert "[dbo]" in result


def test_normalize_bracket_in_catalog_schema_escaped() -> None:
    """Catalog schema containing `]` is escaped to `]]` in the output (item 6)."""
    result = normalize_object_definition("CREATE VIEW . AS SELECT 1", "sch]ema", "vw_ok")
    assert "[sch]]ema]" in result
    assert "[vw_ok]" in result


# ---------------------------------------------------------------------------
# Regression: #746 — bare-dot form "CREATE VIEW . AS" reported on live tenant
# ---------------------------------------------------------------------------


def test_normalize_regression_746_exact_live_input() -> None:
    """Regression #746: exact live-stored text 'CREATE VIEW . AS' is fixed.

    Fabric's sys.sql_modules stores the definition with a bare dot between
    CREATE VIEW and AS when the object was created without a fully-qualified
    name in the DDL header (observed on fabric-dw 2026.6.0b1.dev15).
    The helper must replace the bare dot schema/name slot with the catalog
    values even when both parts are empty plain-form tokens (not bracket-quoted).
    """
    raw = "CREATE VIEW . AS SELECT id, label FROM fdw_qa.t_ctas"
    result = normalize_object_definition(raw, "fdw_qa", "vw_dwh")
    assert result == "CREATE VIEW [fdw_qa].[vw_dwh] AS SELECT id, label FROM fdw_qa.t_ctas"


def test_normalize_regression_746_create_or_alter_bare_dot() -> None:
    """Regression #746: 'CREATE OR ALTER VIEW . AS' bare-dot form is also fixed."""
    raw = "CREATE OR ALTER VIEW . AS SELECT id, label FROM fdw_qa.t_ctas"
    result = normalize_object_definition(raw, "fdw_qa", "vw_dwh")
    expected = "CREATE OR ALTER VIEW [fdw_qa].[vw_dwh] AS SELECT id, label FROM fdw_qa.t_ctas"
    assert result == expected


def test_normalize_regression_746_procedure_bare_dot_form() -> None:
    """Regression #746: bare-dot 'CREATE PROCEDURE . AS ...' is fixed for procedures."""
    raw = "CREATE PROCEDURE . AS BEGIN SELECT id, label FROM fdw_qa.t_ctas END"
    result = normalize_object_definition(raw, "fdw_qa", "usp_load")
    expected = (
        "CREATE PROCEDURE [fdw_qa].[usp_load] AS BEGIN SELECT id, label FROM fdw_qa.t_ctas END"
    )
    assert result == expected


def test_normalize_regression_746_function_bare_dot_form() -> None:
    """Regression #746: bare-dot 'CREATE FUNCTION . (...) ...' is fixed for functions."""
    raw = "CREATE FUNCTION . (@x INT) RETURNS INT AS BEGIN RETURN @x END"
    result = normalize_object_definition(raw, "fdw_qa", "fn_compute")
    assert "CREATE FUNCTION [fdw_qa].[fn_compute]" in result
    assert ". (" not in result


def test_normalize_regression_746_tester_captured_exact_raw() -> None:
    """Regression #746: pinned to the EXACT raw string captured from the live tenant.

    The tester ran ``sql exec`` against Fabric DW to read sys.sql_modules directly
    (bypassing the normalizer) on build 2026.6.0b1.dev15 and observed this verbatim
    string.  It must survive any future refactor of normalize_object_definition.
    """
    raw = "CREATE VIEW . AS SELECT 1 AS id"
    result = normalize_object_definition(raw, "dbo", "vw_probe746")
    assert result == "CREATE VIEW [dbo].[vw_probe746] AS SELECT 1 AS id"


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
