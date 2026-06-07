"""Tests for _render.render — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from rich.console import Console

from fabric_dw.cli._render import render


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
        data = [{"x": "1"}]
        output = self._render_to_string(data, table_title="My Table")
        assert "My Table" in output

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


class TestConfirm:
    """confirm() helper returns True when yes=True without prompting."""

    def test_yes_flag_skips_prompt(self) -> None:
        from fabric_dw.cli._render import confirm

        result = confirm("Are you sure?", yes=True)
        assert result is True
