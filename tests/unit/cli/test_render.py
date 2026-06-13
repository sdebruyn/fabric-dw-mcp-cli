"""Tests for _render.render — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from rich.console import Console

from fabric_dw.cli._render import _cell, confirm, render


class TestRenderJson:
    """render(data, json_output=True) emits valid JSON output."""

    def test_list_of_dicts_is_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = [{"id": "abc", "name": "foo"}, {"id": "def", "name": "bar"}]
        render(data, json_output=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == data

    def test_single_dict_is_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = {"id": "abc", "name": "foo"}
        render(data, json_output=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == data

    def test_empty_dict_is_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        render({}, json_output=True)
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {}

    def test_primitive_is_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        render(42, json_output=True)
        captured = capsys.readouterr()
        assert json.loads(captured.out) == 42

    def test_output_has_indent(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = {"key": "value"}
        render(data, json_output=True)
        captured = capsys.readouterr()
        # Indented JSON has newlines
        assert "\n" in captured.out


class TestRenderTable:
    """render(data, json_output=False) uses Rich for tabular / panel output."""

    def _render_to_string(self, data: object, table_title: str | None = None) -> str:
        sio = StringIO()
        console = Console(file=sio, width=120, highlight=False, no_color=True)
        render(data, json_output=False, console=console, table_title=table_title)
        return sio.getvalue()

    def test_list_of_dicts_contains_column_headers(self) -> None:
        data = [{"id": "abc", "name": "foo"}, {"id": "def", "name": "bar"}]
        output = self._render_to_string(data)
        assert "id" in output
        assert "name" in output

    def test_list_of_dicts_contains_values(self) -> None:
        data = [{"id": "abc", "name": "foo"}]
        output = self._render_to_string(data)
        assert "abc" in output
        assert "foo" in output

    def test_list_title_is_used_when_provided(self) -> None:
        data = [{"x": "1"}, {"x": "2"}, {"x": "3"}, {"x": "4"}, {"x": "5"}]
        output = self._render_to_string(data, table_title="My Table")
        # Rich may wrap the title if the table is narrow; check each word appears
        assert "My" in output
        assert "Table" in output

    def test_single_dict_renders_panel(self) -> None:
        data = {"id": "abc", "name": "foo"}
        output = self._render_to_string(data)
        # Panel border or key present
        assert "abc" in output
        assert "foo" in output

    def test_primitive_renders_repr(self, capsys: pytest.CaptureFixture[str]) -> None:
        render(42, json_output=False)
        captured = capsys.readouterr()
        assert "42" in captured.out

    def test_empty_list_renders_without_error(self) -> None:
        output = self._render_to_string([])
        # Should produce something (empty table) without crashing
        assert output is not None


class TestCellHelper:
    """_cell() converts values to Rich-markup strings for table cells."""

    def test_none_renders_as_dim_null(self) -> None:
        assert _cell(None) == "[dim]NULL[/dim]"

    def test_string_value_is_unchanged(self) -> None:
        assert _cell("hello") == "hello"

    def test_integer_value_is_stringified(self) -> None:
        assert _cell(42) == "42"

    def test_literal_string_none_is_not_affected(self) -> None:
        # The actual string "None" must NOT be changed — only Python None
        assert _cell("None") == "None"


class TestNullRendering:
    """NULL values in table and panel output are rendered as dim 'NULL', not 'None'."""

    def _render_to_string(self, data: object) -> str:
        sio = StringIO()
        console = Console(file=sio, width=120, highlight=False, no_color=True)
        render(data, json_output=False, console=console)
        return sio.getvalue()

    def test_none_in_table_cell_renders_null(self) -> None:
        # Use two rows so the 'score' column is not all-null (it has a non-None
        # value in the second row), meaning it is kept and the None cell renders
        # as NULL.
        data = [{"id": "abc", "score": None}, {"id": "def", "score": 42}]
        output = self._render_to_string(data)
        assert "NULL" in output
        assert "None" not in output

    def test_none_in_panel_value_renders_null(self) -> None:
        data = {"id": "abc", "score": None}
        output = self._render_to_string(data)
        assert "NULL" in output
        assert "None" not in output

    def test_none_in_json_output_stays_null(self, capsys: pytest.CaptureFixture[str]) -> None:
        """JSON serialisation must NOT be affected — null stays null."""
        data = [{"id": "abc", "score": None}]
        render(data, json_output=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed[0]["score"] is None

    def test_literal_string_none_not_changed_in_table(self) -> None:
        """A real string value 'None' must pass through unchanged."""
        data = [{"label": "None"}]
        output = self._render_to_string(data)
        assert "None" in output
        assert "NULL" not in output


class TestSparseRowRendering:
    """Rows missing a key render as blank; only explicit None renders as NULL."""

    def _render_to_string(self, data: object) -> str:
        sio = StringIO()
        console = Console(file=sio, width=120, highlight=False, no_color=True)
        render(data, json_output=False, console=console)
        return sio.getvalue()

    def test_missing_key_renders_as_blank_not_null(self) -> None:
        """A row dict that has no entry for a column renders the cell blank, not NULL."""
        # Row 1 has both keys; row 2 is missing 'score' → should be blank
        data = [{"id": "abc", "score": 42}, {"id": "def"}]
        output = self._render_to_string(data)
        # The column exists (inferred from row 1) but row 2's cell must NOT say NULL
        assert "NULL" not in output

    def test_explicit_none_renders_as_null(self) -> None:
        """A row dict with an explicit None value renders as NULL, not blank.

        Two rows are used so that the 'score' column has at least one non-None
        value and is therefore not dropped by the all-null pruning logic.
        """
        data = [{"id": "abc", "score": None}, {"id": "def", "score": 99}]
        output = self._render_to_string(data)
        assert "NULL" in output
        assert "None" not in output


class TestOmitAllNullColumns:
    """_render_table omits columns that are None in every row (human table only)."""

    def _render_to_string(self, data: object, table_title: str | None = None) -> str:
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        render(data, json_output=False, console=console, table_title=table_title)
        return sio.getvalue()

    # ------------------------------------------------------------------
    # 1. Column that is None in EVERY row → omitted from the table
    # ------------------------------------------------------------------

    def test_all_null_column_is_absent_from_table(self) -> None:
        """A column where every row's value is None must not appear in output."""
        data = [
            {"name": "proc_a", "schema": "dbo", "definition": None},
            {"name": "proc_b", "schema": "dbo", "definition": None},
        ]
        output = self._render_to_string(data)
        assert "name" in output
        assert "schema" in output
        # "definition" column header and NULL cell must both be absent
        assert "definition" not in output

    def test_all_null_column_values_absent_too(self) -> None:
        """When a column is dropped entirely, neither its header nor NULL appears."""
        data = [
            {"qualified_name": "dbo.proc_a", "definition": None},
            {"qualified_name": "dbo.proc_b", "definition": None},
        ]
        output = self._render_to_string(data)
        assert "qualified_name" in output
        # definition not in output (neither as header nor as NULL cell)
        assert "definition" not in output
        # NULL should not appear at all since the only None column was dropped
        assert "NULL" not in output

    # ------------------------------------------------------------------
    # 2. Column that is None in SOME but not all rows → kept, nulls rendered
    # ------------------------------------------------------------------

    def test_partial_null_column_is_kept(self) -> None:
        """A column with at least one non-None value must be present in output."""
        data = [
            {"name": "proc_a", "schema": "dbo", "definition": "CREATE PROC ..."},
            {"name": "proc_b", "schema": "dbo", "definition": None},
        ]
        output = self._render_to_string(data)
        assert "definition" in output

    def test_partial_null_column_null_cells_render_as_null(self) -> None:
        """Null cells in a kept column still render as NULL."""
        data = [
            {"name": "proc_a", "definition": "CREATE PROC ..."},
            {"name": "proc_b", "definition": None},
        ]
        output = self._render_to_string(data)
        assert "NULL" in output

    def test_partial_null_column_non_null_value_shown(self) -> None:
        """Non-null value in a partially-null column is still rendered."""
        data = [
            {"name": "proc_a", "definition": "CREATE PROC ..."},
            {"name": "proc_b", "definition": None},
        ]
        output = self._render_to_string(data)
        assert "CREATE PROC" in output

    # ------------------------------------------------------------------
    # 3. JSON output still emits all fields including all-null columns
    # ------------------------------------------------------------------

    def test_json_output_retains_all_null_column(self, capsys: pytest.CaptureFixture[str]) -> None:
        """json_output=True must never drop any field, even if all values are None."""
        data = [
            {"name": "proc_a", "definition": None},
            {"name": "proc_b", "definition": None},
        ]
        render(data, json_output=True)
        captured = capsys.readouterr()
        parsed: list[dict[str, object]] = json.loads(captured.out)
        assert "definition" in parsed[0]
        assert parsed[0]["definition"] is None
        assert "definition" in parsed[1]
        assert parsed[1]["definition"] is None

    # ------------------------------------------------------------------
    # 4. Edge cases
    # ------------------------------------------------------------------

    def test_empty_rows_not_affected(self) -> None:
        """An empty list renders without error (empty table, no columns)."""
        output = self._render_to_string([])
        assert output is not None

    def test_non_null_columns_preserved_when_some_columns_dropped(self) -> None:
        """Only all-null columns are dropped; other columns remain intact."""
        data = [
            {"id": "1", "name": "foo", "extra": None},
            {"id": "2", "name": "bar", "extra": None},
        ]
        output = self._render_to_string(data)
        assert "id" in output
        assert "name" in output
        assert "extra" not in output

    def test_single_row_all_null_column_dropped(self) -> None:
        """Even with a single row, an all-None column is dropped."""
        data = [{"name": "view_a", "definition": None}]
        output = self._render_to_string(data)
        assert "name" in output
        assert "definition" not in output


class TestConfirm:
    """confirm() helper returns True when yes=True without prompting."""

    def test_yes_flag_skips_prompt(self) -> None:
        result = confirm("Are you sure?", yes=True)
        assert result is True
