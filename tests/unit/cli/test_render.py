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
        data = [{"id": "abc", "score": None}]
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
        """A row dict with an explicit None value renders as NULL, not blank."""
        data = [{"id": "abc", "score": None}]
        output = self._render_to_string(data)
        assert "NULL" in output
        assert "None" not in output


class TestConfirm:
    """confirm() helper returns True when yes=True without prompting."""

    def test_yes_flag_skips_prompt(self) -> None:
        result = confirm("Are you sure?", yes=True)
        assert result is True
