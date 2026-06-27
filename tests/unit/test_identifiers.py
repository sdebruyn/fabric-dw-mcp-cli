"""Tests for fabric_dw.identifiers — validate/quote/parse helpers."""

from __future__ import annotations

import pytest

from fabric_dw.identifiers import (
    parse_qualified_name,
    quote_identifier,
    validate_identifier,
)

# ---------------------------------------------------------------------------
# validate_identifier
# ---------------------------------------------------------------------------


class TestValidateIdentifier:
    def test_simple_name_is_valid(self) -> None:
        assert validate_identifier("my_table") == "my_table"

    def test_leading_underscore_is_valid(self) -> None:
        assert validate_identifier("_private") == "_private"

    def test_alphanumeric_with_underscores_is_valid(self) -> None:
        assert validate_identifier("Sales_2024_Q1") == "Sales_2024_Q1"

    def test_max_length_128_is_valid(self) -> None:
        name = "a" * 128
        assert validate_identifier(name) == name

    def test_over_128_chars_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("a" * 129)

    def test_leading_digit_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("1table")

    def test_bracket_close_raises(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_identifier("my]table")

    def test_semicolon_raises(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_identifier("my;table")

    def test_double_dash_raises(self) -> None:
        with pytest.raises(ValueError, match="forbidden"):
            validate_identifier("my--table")

    def test_space_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("my table")

    def test_hyphen_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("my-table")

    def test_dot_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("schema.table")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("")

    def test_unicode_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("tëst")

    def test_trailing_newline_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("mytable\n")

    def test_embedded_newline_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("my\ntable")

    def test_embedded_carriage_return_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("my\rtable")

    def test_trailing_carriage_return_raises(self) -> None:
        with pytest.raises(ValueError, match="must match"):
            validate_identifier("mytable\r")


# ---------------------------------------------------------------------------
# quote_identifier
# ---------------------------------------------------------------------------


class TestQuoteIdentifier:
    def test_wraps_name_in_brackets(self) -> None:
        assert quote_identifier("my_table") == "[my_table]"

    def test_escapes_closing_bracket(self) -> None:
        # The ] inside name should be doubled.
        assert quote_identifier("my]table") == "[my]]table]"

    def test_preserves_mixed_case(self) -> None:
        assert quote_identifier("MySchema") == "[MySchema]"

    def test_empty_string_produces_empty_brackets(self) -> None:
        assert quote_identifier("") == "[]"

    def test_multiple_brackets_all_escaped(self) -> None:
        assert quote_identifier("a]b]c") == "[a]]b]]c]"


# ---------------------------------------------------------------------------
# parse_qualified_name
# ---------------------------------------------------------------------------


class TestParseQualifiedName:
    def test_splits_on_first_dot(self) -> None:
        schema, obj = parse_qualified_name("dbo.my_table")
        assert schema == "dbo"
        assert obj == "my_table"

    def test_multiple_dots_split_on_first(self) -> None:
        schema, obj = parse_qualified_name("schema.table.extra")
        assert schema == "schema"
        assert obj == "table.extra"

    def test_no_dot_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="missing dot"):
            parse_qualified_name("nodothere")

    def test_leading_dot_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="schema part must not be empty"):
            parse_qualified_name(".table")

    def test_trailing_dot_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="object part must not be empty"):
            parse_qualified_name("schema.")

    def test_only_dot_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="schema part must not be empty"):
            parse_qualified_name(".")

    def test_whitespace_schema_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="schema part must not be empty"):
            parse_qualified_name("  .table")

    def test_whitespace_object_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="object part must not be empty"):
            parse_qualified_name("schema.  ")

    def test_valid_dbo_table_returns_tuple(self) -> None:
        schema, obj = parse_qualified_name("dbo.t")
        assert schema == "dbo"
        assert obj == "t"

    # ------------------------------------------------------------------
    # kind parameter (added for #826 — single canonical parse_qualified_name)
    # ------------------------------------------------------------------

    def test_kind_appears_in_missing_dot_message(self) -> None:
        with pytest.raises(ValueError, match=r"<schema>\.<table>"):
            parse_qualified_name("nodot", kind="table")

    def test_kind_appears_in_missing_dot_message_view(self) -> None:
        with pytest.raises(ValueError, match=r"<schema>\.<view>"):
            parse_qualified_name("nodot", kind="view")

    def test_kind_appears_in_object_part_empty_message(self) -> None:
        with pytest.raises(ValueError, match="table part must not be empty"):
            parse_qualified_name("schema.", kind="table")

    def test_kind_default_is_object(self) -> None:
        with pytest.raises(ValueError, match=r"<schema>\.<object>"):
            parse_qualified_name("nodot")

    def test_kind_does_not_affect_successful_parse(self) -> None:
        assert parse_qualified_name("dbo.my_table", kind="table") == ("dbo", "my_table")

    def test_whitespace_schema_raises_regardless_of_kind(self) -> None:
        with pytest.raises(ValueError, match="schema part must not be empty"):
            parse_qualified_name("  .name", kind="view")

    def test_whitespace_object_raises_with_kind_label(self) -> None:
        with pytest.raises(ValueError, match="view part must not be empty"):
            parse_qualified_name("dbo.  ", kind="view")
