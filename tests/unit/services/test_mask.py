"""Unit tests for dynamic data masking service functions in fabric_dw.services.mask."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from fabric_dw.models import MaskedColumn
from fabric_dw.services import mask as mask_svc
from fabric_dw.sql import SqlTarget

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TARGET = SqlTarget(
    workspace_id="ws-1",
    database="SalesDW",
    connection_string="server.datawarehouse.fabric.microsoft.com",
)

_LIST_COLS = ["schema_name", "table_name", "column_name", "masking_function"]


# ---------------------------------------------------------------------------
# _validate_and_escape_padding
# ---------------------------------------------------------------------------


class TestValidateAndEscapePadding:
    def test_simple_string_unchanged(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        assert _validate_and_escape_padding("XXXX") == "XXXX"

    def test_hyphen_allowed(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        assert _validate_and_escape_padding("-") == "-"

    def test_single_quote_doubled(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        assert _validate_and_escape_padding("O'Reilly") == "O''Reilly"

    def test_empty_raises(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="must not be empty"):
            _validate_and_escape_padding("")

    def test_too_long_raises(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="must not exceed 128"):
            _validate_and_escape_padding("X" * 129)

    def test_exactly_128_chars_ok(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        result = _validate_and_escape_padding("X" * 128)
        assert result == "X" * 128

    def test_double_quote_raises(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="double quote"):
            _validate_and_escape_padding('X"Y')

    def test_close_paren_raises(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match=r"'\)'"):
            _validate_and_escape_padding("X)Y")

    def test_control_char_raises(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="control characters"):
            _validate_and_escape_padding("X\x00Y")

    def test_tab_raises(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="control characters"):
            _validate_and_escape_padding("X\tY")

    def test_del_raises(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="control characters"):
            _validate_and_escape_padding("X\x7fY")

    def test_unicode_nel_raises(self) -> None:
        """U+0085 (NEL) must be rejected."""
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="Unicode line separators"):
            _validate_and_escape_padding("X\x85Y")

    def test_unicode_line_sep_raises(self) -> None:
        """U+2028 (LINE SEPARATOR) must be rejected."""
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="Unicode line separators"):
            _validate_and_escape_padding("X\u2028Y")

    def test_unicode_para_sep_raises(self) -> None:
        """U+2029 (PARAGRAPH SEPARATOR) must be rejected."""
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="Unicode line separators"):
            _validate_and_escape_padding("X\u2029Y")

    def test_sql_comment_raises(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="comment"):
            _validate_and_escape_padding("X--Y")

    def test_semicolon_raises(self) -> None:
        from fabric_dw.services.mask import _validate_and_escape_padding  # noqa: PLC0415

        with pytest.raises(ValueError, match="separator"):
            _validate_and_escape_padding("X;Y")


# ---------------------------------------------------------------------------
# _build_mask_function
# ---------------------------------------------------------------------------


class TestBuildMaskFunction:
    def test_default(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        assert _build_mask_function("default") == "default()"

    def test_default_case_insensitive(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        assert _build_mask_function("DEFAULT") == "default()"

    def test_email(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        assert _build_mask_function("email") == "email()"

    def test_random(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        assert _build_mask_function("random", start=1, end=12) == "random(1, 12)"

    def test_random_missing_start_raises(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="--start"):
            _build_mask_function("random", end=12)

    def test_random_missing_end_raises(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="--end"):
            _build_mask_function("random", start=1)

    def test_random_start_gt_end_raises(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="start <= end"):
            _build_mask_function("random", start=10, end=5)

    def test_random_start_eq_end_ok(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        assert _build_mask_function("random", start=5, end=5) == "random(5, 5)"

    def test_partial_basic(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        result = _build_mask_function("partial", prefix=2, padding="XXXX", suffix=2)
        assert result == 'partial(2,"XXXX",2)'

    def test_partial_phone_number(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        result = _build_mask_function("partial", prefix=1, padding="XXXXXXX", suffix=0)
        assert result == 'partial(1,"XXXXXXX",0)'

    def test_partial_ssn_prefix(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        result = _build_mask_function("partial", prefix=0, padding="XXX-XX-", suffix=4)
        assert result == 'partial(0,"XXX-XX-",4)'

    def test_partial_single_quote_in_padding_escaped(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        # single quote in padding must be doubled for T-SQL string escaping
        result = _build_mask_function("partial", prefix=1, padding="X'X", suffix=1)
        assert result == "partial(1,\"X''X\",1)"

    def test_partial_missing_prefix_raises(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="--prefix"):
            _build_mask_function("partial", padding="X", suffix=1)

    def test_partial_missing_padding_raises(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="--padding"):
            _build_mask_function("partial", prefix=1, suffix=1)

    def test_partial_missing_suffix_raises(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="--suffix"):
            _build_mask_function("partial", prefix=1, padding="X")

    def test_unknown_type_raises(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="Invalid mask function type"):
            _build_mask_function("full")

    # SF-1: cross-arg validation

    def test_default_rejects_start(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("default", start=1)

    def test_default_rejects_end(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("default", end=10)

    def test_default_rejects_prefix(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("default", prefix=2)

    def test_default_rejects_padding(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("default", padding="X")

    def test_default_rejects_suffix(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("default", suffix=2)

    def test_email_rejects_start(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("email", start=1)

    def test_email_rejects_padding(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("email", padding="X")

    def test_random_rejects_prefix(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("random", start=1, end=10, prefix=2)

    def test_random_rejects_padding(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("random", start=1, end=10, padding="X")

    def test_random_rejects_suffix(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("random", start=1, end=10, suffix=2)

    def test_partial_rejects_start(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("partial", prefix=1, padding="X", suffix=1, start=1)

    def test_partial_rejects_end(self) -> None:
        from fabric_dw.services.mask import _build_mask_function  # noqa: PLC0415

        with pytest.raises(ValueError, match="does not accept"):
            _build_mask_function("partial", prefix=1, padding="X", suffix=1, end=10)


# ---------------------------------------------------------------------------
# list_masked_columns
# ---------------------------------------------------------------------------


class TestListMaskedColumns:
    async def test_returns_masked_column_objects(self) -> None:
        rows = [
            ("dbo", "Employees", "Email", "email()"),
            ("dbo", "Employees", "Phone", 'partial(0,"XXX-XXX-",4)'),
        ]

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return _LIST_COLS, rows

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            result = await mask_svc.list_masked_columns(_TARGET)

        assert len(result) == 2
        col = result[0]
        assert isinstance(col, MaskedColumn)
        assert col.schema_name == "dbo"
        assert col.table_name == "Employees"
        assert col.column_name == "Email"
        assert col.masking_function == "email()"

    async def test_returns_empty_list_when_no_masks(self) -> None:
        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return _LIST_COLS, []

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            result = await mask_svc.list_masked_columns(_TARGET)

        assert result == []

    async def test_filters_by_schema(self) -> None:
        rows = [
            ("dbo", "T1", "col1", "default()"),
            ("hr", "T2", "col2", "email()"),
        ]

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return _LIST_COLS, rows

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            result = await mask_svc.list_masked_columns(_TARGET, table_schema="hr")

        assert len(result) == 1
        assert result[0].schema_name == "hr"

    async def test_filters_by_table_name(self) -> None:
        rows = [
            ("dbo", "Employees", "col1", "default()"),
            ("dbo", "Customers", "col2", "email()"),
        ]

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return _LIST_COLS, rows

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            result = await mask_svc.list_masked_columns(_TARGET, table_name="Customers")

        assert len(result) == 1
        assert result[0].table_name == "Customers"

    async def test_filter_is_case_insensitive(self) -> None:
        rows = [
            ("dbo", "Employees", "col1", "default()"),
        ]

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return _LIST_COLS, rows

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            result = await mask_svc.list_masked_columns(_TARGET, table_schema="DBO")

        assert len(result) == 1

    async def test_exact_sql_query(self) -> None:
        """list_masked_columns must issue exactly _LIST_MASKED_COLUMNS_SQL."""
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return _LIST_COLS, []

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            await mask_svc.list_masked_columns(_TARGET)

        assert captured[0] == mask_svc._LIST_MASKED_COLUMNS_SQL


# ---------------------------------------------------------------------------
# set_column_mask - SQL shape (S1: structured args, N-1: space in random)
# ---------------------------------------------------------------------------


class TestSetColumnMask:
    async def test_default_mask_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            result = await mask_svc.set_column_mask(
                _TARGET,
                "dbo",
                "Employees",
                "LastName",
                "default",
            )

        assert result == "default()"
        assert captured[0] == (
            "ALTER TABLE [dbo].[Employees] ALTER COLUMN [LastName] "
            "ADD MASKED WITH (FUNCTION = 'default()');"
        )

    async def test_email_mask_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            result = await mask_svc.set_column_mask(
                _TARGET,
                "dbo",
                "Employees",
                "Email",
                "email",
            )

        assert result == "email()"
        assert captured[0] == (
            "ALTER TABLE [dbo].[Employees] ALTER COLUMN [Email] "
            "ADD MASKED WITH (FUNCTION = 'email()');"
        )

    async def test_random_mask_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            result = await mask_svc.set_column_mask(
                _TARGET,
                "dbo",
                "Orders",
                "Amount",
                "random",
                start=1,
                end=12,
            )

        assert result == "random(1, 12)"
        assert captured[0] == (
            "ALTER TABLE [dbo].[Orders] ALTER COLUMN [Amount] "
            "ADD MASKED WITH (FUNCTION = 'random(1, 12)');"
        )

    async def test_partial_mask_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            result = await mask_svc.set_column_mask(
                _TARGET,
                "dbo",
                "Employees",
                "Phone",
                "partial",
                prefix=0,
                padding="XXX-XXX-",
                suffix=4,
            )

        assert result == 'partial(0,"XXX-XXX-",4)'
        assert captured[0] == (
            "ALTER TABLE [dbo].[Employees] ALTER COLUMN [Phone] "
            "ADD MASKED WITH (FUNCTION = 'partial(0,\"XXX-XXX-\",4)');"
        )

    async def test_returns_mask_fn_literal(self) -> None:
        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            return [], []

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            literal = await mask_svc.set_column_mask(_TARGET, "dbo", "T", "col", "email")

        assert literal == "email()"

    async def test_invalid_schema_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            await mask_svc.set_column_mask(
                _TARGET,
                "bad schema",
                "T",
                "col",
                "default",
            )

    async def test_invalid_table_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            await mask_svc.set_column_mask(
                _TARGET,
                "dbo",
                "bad; table",
                "col",
                "default",
            )

    async def test_invalid_column_raises(self) -> None:
        with pytest.raises(ValueError, match="Column name"):
            await mask_svc.set_column_mask(
                _TARGET,
                "dbo",
                "T",
                "",
                "default",
            )

    async def test_invalid_fn_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid mask function type"):
            await mask_svc.set_column_mask(
                _TARGET,
                "dbo",
                "T",
                "col",
                "full",
            )


# ---------------------------------------------------------------------------
# drop_column_mask - SQL shape
# ---------------------------------------------------------------------------


class TestDropColumnMask:
    async def test_drop_mask_exact_sql(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            await mask_svc.drop_column_mask(
                _TARGET,
                "dbo",
                "Employees",
                "Email",
            )

        assert captured[0] == ("ALTER TABLE [dbo].[Employees] ALTER COLUMN [Email] DROP MASKED;")

    async def test_different_schema_and_table(self) -> None:
        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.mask.run_query", side_effect=_mock):
            await mask_svc.drop_column_mask(
                _TARGET,
                "hr",
                "Personnel",
                "SSN",
            )

        assert captured[0] == ("ALTER TABLE [hr].[Personnel] ALTER COLUMN [SSN] DROP MASKED;")

    async def test_invalid_schema_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            await mask_svc.drop_column_mask(
                _TARGET,
                "bad schema",
                "T",
                "col",
            )

    async def test_invalid_table_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid"):
            await mask_svc.drop_column_mask(
                _TARGET,
                "dbo",
                "bad; table",
                "col",
            )

    async def test_invalid_column_raises(self) -> None:
        with pytest.raises(ValueError, match="Column name"):
            await mask_svc.drop_column_mask(
                _TARGET,
                "dbo",
                "T",
                "",
            )


# ---------------------------------------------------------------------------
# UNMASK in permissions allowlists
# ---------------------------------------------------------------------------


class TestUnmaskInPermissionsAllowlists:
    def test_unmask_in_database_permissions(self) -> None:
        from fabric_dw.services.permissions import DATABASE_PERMISSIONS  # noqa: PLC0415

        assert "UNMASK" in DATABASE_PERMISSIONS

    def test_unmask_in_object_permissions(self) -> None:
        from fabric_dw.services.permissions import OBJECT_PERMISSIONS  # noqa: PLC0415

        assert "UNMASK" in OBJECT_PERMISSIONS

    def test_unmask_in_column_applicable_permissions(self) -> None:
        from fabric_dw.services.permissions import COLUMN_APPLICABLE_PERMISSIONS  # noqa: PLC0415

        assert "UNMASK" in COLUMN_APPLICABLE_PERMISSIONS

    def test_unmask_not_in_schema_permissions(self) -> None:
        """UNMASK is not a schema-level permission - schemas contain no masked columns."""
        from fabric_dw.services.permissions import SCHEMA_PERMISSIONS  # noqa: PLC0415

        assert "UNMASK" not in SCHEMA_PERMISSIONS


# ---------------------------------------------------------------------------
# UNMASK grant SQL shapes (pinning tests)
# ---------------------------------------------------------------------------


class TestUnmaskGrantSql:
    async def test_grant_unmask_database_scope(self) -> None:
        """GRANT UNMASK TO [principal]; (DATABASE scope - no ON clause)."""
        from fabric_dw.services import permissions as perm_svc  # noqa: PLC0415

        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock):
            await perm_svc.grant_permission(
                _TARGET,
                "UNMASK",
                "alice@contoso.com",
                "DATABASE",
            )

        assert captured[0] == "GRANT UNMASK TO [alice@contoso.com];"

    async def test_grant_unmask_object_scope_with_column(self) -> None:
        """GRANT UNMASK ON OBJECT::[dbo].[t] ([c]) TO [principal];"""
        from fabric_dw.services import permissions as perm_svc  # noqa: PLC0415

        captured: list[str] = []

        def _mock(_target: object, _sql: str, **_kw: object) -> tuple:
            captured.append(_sql)
            return [], []

        with patch("fabric_dw.services.permissions.run_query", side_effect=_mock):
            await perm_svc.grant_permission(
                _TARGET,
                "UNMASK",
                "alice@contoso.com",
                "OBJECT",
                object_name="dbo.t",
                columns=["c"],
            )

        assert captured[0] == "GRANT UNMASK ON OBJECT::[dbo].[t] ([c]) TO [alice@contoso.com];"
