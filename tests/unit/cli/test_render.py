"""Tests for _render.render — written BEFORE the implementation (TDD)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from uuid import UUID

import pytest
from rich.console import Console

from fabric_dw.cli._render import (
    _cell,
    _format_nested,
    _is_guid_column,
    _make_bar,
    confirm,
    render,
    render_permissions_table,
    render_refresh_table,
    render_statistic_details,
    sanitise_json,
)
from fabric_dw.models import (
    ItemAccess,
    ItemAccessDetail,
    ItemAccessPrincipal,
    StatisticDensityRow,
    StatisticDetails,
    StatisticHeaderRow,
    StatisticHistogramStep,
    TableSyncError,
    TableSyncStatus,
)


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

    def test_primitive_renders_str(self, capsys: pytest.CaptureFixture[str]) -> None:
        render(42, json_output=False)
        captured = capsys.readouterr()
        assert "42" in captured.out

    def test_string_primitive_renders_without_quotes(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """str() is used, not repr() — so string primitives have no surrounding quotes."""
        render("hello", json_output=False)
        captured = capsys.readouterr()
        assert "hello" in captured.out
        assert "'hello'" not in captured.out

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

    def test_integral_float_renders_without_dot_zero(self) -> None:
        """Whole-number float values must render as integers (no spurious '.0')."""
        assert _cell(1500.0) == "1500"

    def test_fractional_float_renders_with_decimal(self) -> None:
        """Fractional float values must retain their decimal component."""
        assert _cell(1234.5) == "1234.5"

    def test_negative_integral_float_renders_without_dot_zero(self) -> None:
        """Negative whole-number floats also suppress the '.0' suffix."""
        assert _cell(-42.0) == "-42"

    def test_zero_float_renders_as_zero(self) -> None:
        """0.0 must render as '0', not '0.0'."""
        assert _cell(0.0) == "0"


class TestRichMarkupEscape:
    """Column names and cell values containing Rich markup characters render verbatim.

    Rich interprets ``[<alpha>...]`` sequences as markup tags and strips them.
    Column names like ``FileRowCount[avg]`` or ``Status[current]`` (alpha-prefixed
    bracket content) are silently stripped without escaping — leaving blank headers.

    Regression tests for issue #745.
    """

    def _render_to_string(self, data: object, *, width: int = 200) -> str:
        sio = StringIO()
        console = Console(file=sio, width=width, highlight=False, no_color=True)
        render(data, json_output=False, console=console, table_title="Table Health Metrics")
        return sio.getvalue()

    # ------------------------------------------------------------------
    # 1. Column names with alpha-prefixed brackets ARE stripped by Rich without escaping
    # ------------------------------------------------------------------

    def test_alpha_bracket_column_name_renders_verbatim(self) -> None:
        """Column headers like ``FileRowCount[avg]`` must appear verbatim, not stripped.

        Rich treats ``[avg]`` as a (failed) markup tag and strips it, leaving
        ``FileRowCount`` without the bracket suffix — or an empty string if the
        whole name is ``[something]``.  Without ``_escape_markup`` the bracket
        content disappears silently.
        """
        data = [
            {
                "FileRowCount[avg]": 0,
                "Status[current]": "ok",
                "PhysicalRowCount": 1000000,
            }
        ]
        output = self._render_to_string(data)
        assert "FileRowCount[avg]" in output
        assert "Status[current]" in output

    def test_plain_column_name_still_renders(self) -> None:
        """Non-bracket column names must continue to render correctly after the fix."""
        data = [{"PhysicalRowCount": 1000000, "FileRowCount[avg]": 0}]
        output = self._render_to_string(data)
        assert "PhysicalRowCount" in output

    # ------------------------------------------------------------------
    # 2. Cell values with alpha-bracketed content render verbatim
    # ------------------------------------------------------------------

    def test_bracket_cell_value_renders_verbatim(self) -> None:
        """A string cell value like ``[redacted]`` must appear verbatim, not stripped."""
        data = [{"label": "[redacted]", "count": 5}]
        output = self._render_to_string(data)
        assert "[redacted]" in output

    # ------------------------------------------------------------------
    # 3. Intentional NULL styling is preserved (no double-escape)
    # ------------------------------------------------------------------

    def test_null_cell_still_renders_as_dim_null(self) -> None:
        """Escaping data strings must not break the intentional ``[dim]NULL[/dim]`` styling.

        A ``None`` value in a kept column must still render as the visible text
        ``NULL`` (styled dim) — not as the literal tag string ``[dim]NULL[/dim]``.
        Two rows are used so the column is not all-null and is therefore kept.
        """
        data = [
            {"FileRowCount[avg]": None, "PhysicalRowCount": 1000000},
            {"FileRowCount[avg]": 42, "PhysicalRowCount": 2000000},
        ]
        output = self._render_to_string(data)
        # The visible rendered text "NULL" must appear (dim styling applied by Rich)
        assert "NULL" in output
        # The raw tag must NOT appear literally (that would mean escaping went wrong)
        assert "[dim]NULL[/dim]" not in output

    # ------------------------------------------------------------------
    # 4. render_permissions_table escapes data-derived values
    # ------------------------------------------------------------------

    def test_permissions_table_escapes_group_name_in_display(self) -> None:
        """A principal display name containing brackets must appear verbatim."""
        principal = ItemAccessPrincipal.model_validate(
            {
                "id": "12345678-1234-5678-1234-567812345678",
                "displayName": "[sg-devs]",
                "type": "Group",
                "userDetails": {"userPrincipalName": ""},
            }
        )
        detail = ItemAccessDetail.model_validate(
            {"permissions": ["Read"], "additionalPermissions": []}
        )
        access = ItemAccess.model_validate(
            {
                "principal": principal.model_dump(by_alias=True, mode="json"),
                "itemAccessDetails": detail.model_dump(by_alias=True, mode="json"),
            }
        )
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        render_permissions_table([access], title="Test", console=console)
        output = sio.getvalue()
        assert "[sg-devs]" in output

    def test_refresh_table_escapes_table_name(self) -> None:
        """A table name containing brackets must appear verbatim in the refresh table."""
        s = TableSyncStatus.model_validate(
            {
                "tableName": "dbo.[weird table]",
                "status": "Success",
                "endDateTime": None,
                "error": None,
            }
        )
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        render_refresh_table([s], console=console)
        output = sio.getvalue()
        assert "dbo.[weird table]" in output


# ---------------------------------------------------------------------------
# Wide-table vertical fallback (issue #745 / #749)
# ---------------------------------------------------------------------------

#: Simulated health-check row (~16 columns) — the real sp_get_table_health_metrics
#: shape that triggered #745.  Width-starvation at 80 cols, not markup, was the
#: cause of all-blank headers in practice.
_HEALTH_CHECK_ROW: dict[str, object] = {
    "PotentialAnomalyType": 0,
    "PhysicalRowCount": 1000000,
    "FileRowCount[0]": 0,
    "FileRowCount[1,10)": 0,
    "FileRowCount[10,100)": 5,
    "FileRowCount[100,1000)": 12,
    "FileRowCount[1000,10000)": 40,
    "FileRowCount[ten_thousand_plus]": 0,
    "DeletedRowCount": 3,
    "TotalFileCount": 57,
    "ActiveFileCount": 57,
    "InactiveFileCount": 0,
    "CompressedFileSize": 4096000,
    "UncompressedFileSize": 8192000,
    "TableName": "dbo.taxi_trips",
    "SchemaName": "dbo",
}

#: Simulated queries/sessions shape (~12 columns).
_QUERIES_ROWS: list[dict[str, object]] = [
    {
        "session_id": "abc123",
        "login_name": "user@example.com",
        "status": "running",
        "command": "SELECT",
        "start_time": "2026-06-25T10:00:00",
        "cpu_time": 1200,
        "total_elapsed_time": 1500,
        "reads": 9000,
        "writes": 0,
        "logical_reads": 18000,
        "blocking_session_id": None,
        "wait_type": "ASYNC_NETWORK_IO",
    },
    {
        "session_id": "def456",
        "login_name": "svc@example.com",
        "status": "sleeping",
        "command": "AWAITING COMMAND",
        "start_time": "2026-06-25T09:55:00",
        "cpu_time": 0,
        "total_elapsed_time": 300000,
        "reads": 0,
        "writes": 0,
        "logical_reads": 0,
        "blocking_session_id": None,
        "wait_type": None,
    },
]


class TestWideTableVerticalFallback:
    """Wide-schema tables fall back to a vertical layout when they cannot fit horizontally.

    The fallback criterion is header-legibility based: ``_table_fits`` estimates
    the minimum width needed to show each column header (GUID columns get their
    fixed width; non-GUID columns get ``min(len(header), _HEADER_MAX_WIDTH)``
    chars) and triggers vertical rendering when that estimate exceeds the console
    width.  This correctly catches 10-, 12-, 14-, and 16-column shapes at width=80
    while leaving the #743 GUID-heavy shape (2 GUIDs + displayName + kind) horizontal.
    """

    def _render_at_width(
        self,
        data: object,
        width: int,
        *,
        table_title: str | None = None,
        drop_columns: tuple[str, ...] | None = None,
        prune_null_columns: bool = False,
    ) -> str:
        sio = StringIO()
        console = Console(file=sio, width=width, highlight=False, no_color=True)
        render(
            data,
            json_output=False,
            console=console,
            table_title=table_title,
            drop_columns=drop_columns,
            prune_null_columns=prune_null_columns,
        )
        return sio.getvalue()

    # ------------------------------------------------------------------
    # 1. Health-check shape (~16 cols, 1 row) at width=80 → vertical
    # ------------------------------------------------------------------

    def test_health_check_vertical_at_width_80_shows_all_field_names(self) -> None:
        """All ~16 field names appear in full in the vertical layout at width=80."""
        output = self._render_at_width(
            [_HEALTH_CHECK_ROW], width=80, table_title="Table Health Metrics"
        )
        for col in _HEALTH_CHECK_ROW:
            assert col in output, f"Field '{col}' missing from vertical output"

    def test_health_check_vertical_at_width_80_shows_bracketed_field_names(self) -> None:
        """Alpha-bracket column names appear verbatim in the vertical fallback at width=80.

        ``FileRowCount[ten_thousand_plus]`` has alpha-prefixed bracket content —
        Rich strips ``[ten_thousand_plus]`` as a (failed) markup tag on unpatched code,
        making this a genuine guard for the ``_escape_markup`` call on column names.
        ``FileRowCount[1,10)`` is digit-prefixed and also checked for completeness.
        """
        output = self._render_at_width(
            [_HEALTH_CHECK_ROW], width=80, table_title="Table Health Metrics"
        )
        # Alpha-bracket — stripped by unpatched Rich, so this guards the escape fix
        assert "FileRowCount[ten_thousand_plus]" in output
        # Also present for completeness (digit-bracket, not stripped by Rich)
        assert "FileRowCount[1,10)" in output

    def test_health_check_vertical_at_width_80_shows_title(self) -> None:
        """The table title is printed in the vertical fallback output."""
        output = self._render_at_width(
            [_HEALTH_CHECK_ROW], width=80, table_title="Table Health Metrics"
        )
        assert "Table Health Metrics" in output

    def test_health_check_no_truncated_header_artifacts(self) -> None:
        """At width=80 the vertical output must NOT contain the three-separator artifact."""
        output = self._render_at_width(
            [_HEALTH_CHECK_ROW], width=80, table_title="Table Health Metrics"
        )
        assert "┃┃┃" not in output

    def test_bracket_title_renders_verbatim_in_vertical_fallback(self) -> None:
        """A table title containing brackets appears verbatim in the vertical fallback.

        Rich parses ``Table(title=...)`` as markup.  Without ``_escape_markup`` a
        title like ``Stats [2024]`` would render as ``Stats`` with ``[2024]`` dropped.
        """
        output = self._render_at_width([_HEALTH_CHECK_ROW], width=80, table_title="Stats [2024]")
        assert "Stats [2024]" in output

    def test_bracket_title_renders_verbatim_in_horizontal_table(self) -> None:
        """A table title containing brackets appears verbatim in the horizontal layout.

        Uses a 2-column table that fits at width=200 to stay in the horizontal path.
        """
        data = [{"id": "1", "name": "foo"}]
        output = self._render_at_width(data, width=200, table_title="Stats [2024]")
        assert "Stats [2024]" in output

    # ------------------------------------------------------------------
    # 2. 10-col and 14-col health-check shapes → vertical at width=80
    # ------------------------------------------------------------------

    def test_10col_health_check_vertical_at_width_80(self) -> None:
        """A 10-column health-check subset goes vertical at width=80."""
        data = [dict(list(_HEALTH_CHECK_ROW.items())[:10])]
        output = self._render_at_width(data, width=80, table_title="Health Check")
        for col in data[0]:
            assert col in output, f"Field '{col}' missing from 10-col vertical output"

    def test_14col_health_check_vertical_at_width_80(self) -> None:
        """A 14-column health-check subset goes vertical at width=80."""
        data = [dict(list(_HEALTH_CHECK_ROW.items())[:14])]
        output = self._render_at_width(data, width=80, table_title="Health Check")
        for col in data[0]:
            assert col in output, f"Field '{col}' missing from 14-col vertical output"

    # ------------------------------------------------------------------
    # 3. 12-col queries shape at width=80 → vertical (issue #749)
    # ------------------------------------------------------------------

    def test_queries_vertical_at_width_80_shows_all_field_names(self) -> None:
        """All non-null field names appear in the vertical layout at width=80.

        With the header-based threshold, the 11 visible columns (blocking_session_id
        is all-null and pruned when prune_null_columns=True) have headers averaging
        ~10 chars, which far exceeds 80 columns, triggering the vertical fallback.
        """
        output = self._render_at_width(_QUERIES_ROWS, width=80, prune_null_columns=True)
        # blocking_session_id is all-null and pruned; check the non-null columns only
        visible_cols = [c for c in _QUERIES_ROWS[0] if c != "blocking_session_id"]
        for col in visible_cols:
            assert col in output, f"Field '{col}' missing from vertical output"

    def test_queries_vertical_at_width_80_shows_values(self) -> None:
        """Cell values from multiple rows appear in the vertical output at width=80."""
        output = self._render_at_width(_QUERIES_ROWS, width=80)
        assert "abc123" in output
        assert "user@example.com" in output

    # ------------------------------------------------------------------
    # 4. #743 shape stays horizontal — regression guard
    # ------------------------------------------------------------------

    def test_guid_shape_stays_horizontal_at_width_80(self) -> None:
        """The #743 GUID-heavy shape (2 GUIDs + displayName + kind) stays horizontal.

        Estimated fit: primary GUID (36) + secondary GUID (10) + displayName (11)
        + kind (4) + borders (3*4+1=13) = 74 <= 80, so the horizontal layout is kept.
        """
        guid1 = "eb85cc99-5ad8-4f89-85b8-2a46eaa410d6"
        guid2 = "4c18bf4e-86dd-47da-8602-87184ff16c13"
        data = [
            {"id": guid1, "displayName": "My Workspace", "workspaceId": guid2, "kind": "Warehouse"}
        ]
        output = self._render_at_width(data, width=80)
        # Horizontal table uses box-drawing column separators
        assert "┃" in output or "│" in output
        # Primary GUID must appear in full (no_wrap)
        assert guid1 in output

    # ------------------------------------------------------------------
    # 5. Small (fits) tables keep horizontal layout — regression guard
    # ------------------------------------------------------------------

    def test_small_table_stays_horizontal(self) -> None:
        """A 2-column table at width=80 stays horizontal (no panel-style output)."""
        data = [{"id": "1", "name": "foo"}, {"id": "2", "name": "bar"}]
        output = self._render_at_width(data, width=80)
        assert "┃" in output or "│" in output

    def test_small_table_headers_present(self) -> None:
        """Column headers appear in the horizontal layout for small tables."""
        data = [{"id": "1", "name": "foo"}]
        output = self._render_at_width(data, width=120)
        assert "id" in output
        assert "name" in output

    # ------------------------------------------------------------------
    # 6. Existing multi-GUID width tests are unaffected (PR #743 regression guard)
    # ------------------------------------------------------------------

    def test_two_guid_columns_still_work_at_width_120(self) -> None:
        """Two-GUID-column table renders correctly at width=120 (horizontal layout)."""
        guid1 = "eb85cc99-5ad8-4f89-85b8-2a46eaa410d6"
        guid2 = "4c18bf4e-86dd-47da-8602-87184ff16c13"
        data = [{"id": guid1, "displayName": "Adventure Works Finance", "capacityId": guid2}]
        output = self._render_at_width(data, width=120)
        assert guid1 in output
        assert "displayName" in output


class TestNarrowTableTitleNotWrapped:
    """_render_table prints a wide title on one line even when the table is narrower.

    Regression tests for issue #756: Rich wraps ``Table(title=...)`` to the
    table's content width, producing multi-line title fragments for narrow tables.
    The fix prints the title as a separate bold line above a title-less ``Table``.
    """

    def _render_to_string(
        self,
        data: object,
        *,
        table_title: str | None,
        width: int = 120,
    ) -> str:
        sio = StringIO()
        console = Console(file=sio, width=width, highlight=False, no_color=True)
        render(data, json_output=False, console=console, table_title=table_title)
        return sio.getvalue()

    def test_narrow_table_title_appears_intact(self) -> None:
        """A 1-column table with a wide title renders the title as a single intact string.

        ``SELECT 1 AS n`` produces a 5-char-wide table body.  ``"SQL Result"``
        (10 chars) would be wrapped char-by-char by Rich when passed as
        ``Table(title=...)``.  The fix prints it on one line before the table.
        """
        output = self._render_to_string([{"n": 1}], table_title="SQL Result")
        assert "SQL Result" in output

    def test_narrow_table_title_not_char_wrapped(self) -> None:
        """The title must NOT appear split across lines (e.g. ``Resul\\n``)."""
        output = self._render_to_string([{"n": 1}], table_title="SQL Result")
        assert "Resul\n" not in output

    def test_narrow_table_column_and_value_still_present(self) -> None:
        """The table body (column header + cell value) must still render correctly.

        Non-regression check: the title-fix must not break normal table content.
        """
        output = self._render_to_string([{"n": 1}], table_title="SQL Result")
        assert "n" in output
        assert "1" in output

    def test_bracket_title_intact_in_horizontal_table(self) -> None:
        """A bracket title (e.g. ``Stats [2024]``) renders verbatim without stripping.

        This verifies that the markup-escape behaviour introduced in #753 is
        preserved now that the title is printed as a separate line instead of
        being passed to ``Table(title=...)``.
        """
        output = self._render_to_string([{"n": 1}], table_title="Stats [2024]")
        assert "Stats [2024]" in output

    def test_empty_rows_with_title_prints_title_on_one_line(self) -> None:
        """Empty result set: the title is still printed on one line (not dropped)."""
        output = self._render_to_string([], table_title="SQL Result")
        assert "SQL Result" in output

    def test_no_title_does_not_print_blank_line(self) -> None:
        """When title is None, no spurious blank line is inserted before the table."""
        output = self._render_to_string([{"x": 1}], table_title=None)
        # Strip leading whitespace then verify no double-newline at the start
        assert "\n\n" not in output.lstrip()
        assert "x" in output


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
    """_render_table omits columns that are None in every row when prune_null_columns=True."""

    def _render_to_string(self, data: object, table_title: str | None = None) -> str:
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        render(
            data,
            json_output=False,
            console=console,
            table_title=table_title,
            prune_null_columns=True,
        )
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


class TestRawSqlNullColumns:
    """Raw sql exec results must show every column, including all-NULL ones.

    The prune_null_columns flag defaults to False so that raw query output
    (sql exec) never silently hides a column the user asked for.
    """

    def _render_to_string(self, data: object) -> str:
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        # prune_null_columns defaults to False - raw SQL callers do not set it
        render(data, json_output=False, console=console)
        return sio.getvalue()

    def test_all_null_column_present_when_pruning_disabled(self) -> None:
        """A column that is NULL in every row must appear when prune_null_columns=False."""
        data = [
            {"a": None, "b": 5},
            {"a": None, "b": 10},
        ]
        output = self._render_to_string(data)
        assert "a" in output
        assert "b" in output

    def test_all_null_column_header_shown(self) -> None:
        """SELECT NULL AS a, 5 AS b: column header 'a' must be visible."""
        data = [{"a": None, "b": 5}]
        output = self._render_to_string(data)
        assert "a" in output

    def test_all_null_cell_renders_as_null(self) -> None:
        """The all-NULL column's cell must render as NULL, not disappear."""
        data = [{"a": None, "b": 5}]
        output = self._render_to_string(data)
        assert "NULL" in output

    def test_single_row_all_null_result_shows_column(self) -> None:
        """A single-row result where every column is NULL still shows the header."""
        data = [{"x": None}]
        output = self._render_to_string(data)
        assert "x" in output

    def test_prune_false_is_default(self) -> None:
        """Passing prune_null_columns=False explicitly has the same effect as omitting it."""
        data = [{"col": None}]
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        render(data, json_output=False, console=console, prune_null_columns=False)
        output = sio.getvalue()
        assert "col" in output


class TestDropColumns:
    """render(..., drop_columns=...) omits the named columns from the table only."""

    def _render_to_string(
        self,
        data: object,
        *,
        drop_columns: tuple[str, ...] | None = None,
    ) -> str:
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        render(data, json_output=False, console=console, drop_columns=drop_columns)
        return sio.getvalue()

    def test_dropped_column_absent_from_table(self) -> None:
        data = [
            {"id": "1", "name": "foo", "workspaceId": "ws-1"},
            {"id": "2", "name": "bar", "workspaceId": "ws-1"},
        ]
        output = self._render_to_string(data, drop_columns=("workspaceId",))
        assert "name" in output
        assert "workspaceId" not in output
        # The dropped column's values must also be absent.
        assert "ws-1" not in output

    def test_other_columns_preserved_when_one_dropped(self) -> None:
        data = [{"id": "1", "name": "foo", "workspaceId": "ws-1"}]
        output = self._render_to_string(data, drop_columns=("workspaceId",))
        assert "id" in output
        assert "name" in output
        assert "foo" in output

    def test_none_drop_columns_keeps_all(self) -> None:
        data = [{"id": "1", "workspaceId": "ws-1"}]
        output = self._render_to_string(data, drop_columns=None)
        assert "workspaceId" in output
        assert "ws-1" in output

    def test_drop_columns_ignored_for_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        """drop_columns must NOT affect JSON output (machine-readable, never pruned)."""
        data = [{"id": "1", "workspaceId": "ws-1"}]
        render(data, json_output=True, drop_columns=("workspaceId",))
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed[0]["workspaceId"] == "ws-1"

    def test_drop_unknown_column_is_noop(self) -> None:
        """Dropping a column that does not exist leaves the table unchanged."""
        data = [{"id": "1", "name": "foo"}]
        output = self._render_to_string(data, drop_columns=("nonexistent",))
        assert "id" in output
        assert "name" in output


class TestNonDictListRows:
    """_render_table handles list rows that are not dicts (lines 113, 135)."""

    def _render_to_string(self, data: object) -> str:
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        render(data, json_output=False, console=console)
        return sio.getvalue()

    def test_list_of_scalars_renders_without_error(self) -> None:
        """A list of plain strings renders as a single-column table."""
        output = self._render_to_string(["alpha", "beta", "gamma"])
        assert "alpha" in output
        assert "beta" in output
        assert "gamma" in output

    def test_list_of_ints_renders_without_error(self) -> None:
        output = self._render_to_string([1, 2, 3])
        assert "1" in output
        assert "2" in output

    def test_mixed_dict_and_scalar_rows_renders_without_error(self) -> None:
        """Mixed rows: dict first (adds columns), then scalar (renders via _cell)."""
        # Non-dict rows appended at line 113; rendered at line 135
        output = self._render_to_string([{"name": "foo"}, "bar"])
        assert output is not None


class TestConfirm:
    """confirm() helper returns True when yes=True without prompting."""

    def test_yes_flag_skips_prompt(self) -> None:
        result = confirm("Are you sure?", yes=True)
        assert result is True


# ---------------------------------------------------------------------------
# Helpers for render_permissions_table / render_refresh_table tests
# ---------------------------------------------------------------------------


def _make_item_access(
    display_name: str = "Alice",
    upn: str = "alice@example.com",
    principal_type: str = "User",
    permissions: list[str] | None = None,
    additional_permissions: list[str] | None = None,
) -> ItemAccess:
    principal = ItemAccessPrincipal.model_validate(
        {
            "id": str(UUID("12345678-1234-5678-1234-567812345678")),
            "displayName": display_name,
            "type": principal_type,
            "userDetails": {"userPrincipalName": upn},
        }
    )
    detail = ItemAccessDetail.model_validate(
        {
            "permissions": permissions or ["Read"],
            "additionalPermissions": additional_permissions or [],
        }
    )
    return ItemAccess.model_validate(
        {
            "principal": principal.model_dump(by_alias=True, mode="json"),
            "itemAccessDetails": detail.model_dump(by_alias=True, mode="json"),
        }
    )


def _make_table_sync_status(
    table_name: str = "dbo.Sales",
    status: str = "Success",
    end_date_time: datetime | None = None,
    error: TableSyncError | None = None,
) -> TableSyncStatus:
    return TableSyncStatus.model_validate(
        {
            "tableName": table_name,
            "status": status,
            "endDateTime": end_date_time.isoformat() if end_date_time else None,
            "error": error.model_dump(by_alias=True, mode="json") if error else None,
        }
    )


class TestRenderPermissionsTable:
    """render_permissions_table renders ItemAccess records correctly."""

    def _render_to_string(
        self,
        accesses: list[ItemAccess],
        title: str = "Permissions",
        *,
        json_output: bool = False,
    ) -> str:
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        render_permissions_table(accesses, title=title, json_output=json_output, console=console)
        return sio.getvalue()

    def test_renders_display_name(self) -> None:
        access = _make_item_access(display_name="Alice Smith")
        output = self._render_to_string([access])
        assert "Alice Smith" in output

    def test_renders_upn(self) -> None:
        access = _make_item_access(upn="alice@example.com")
        output = self._render_to_string([access])
        assert "alice@example.com" in output

    def test_renders_principal_type(self) -> None:
        access = _make_item_access(principal_type="User")
        output = self._render_to_string([access])
        assert "User" in output

    def test_renders_permissions(self) -> None:
        access = _make_item_access(permissions=["Read", "Write"])
        output = self._render_to_string([access])
        assert "Read" in output
        assert "Write" in output

    def test_renders_additional_permissions(self) -> None:
        access = _make_item_access(additional_permissions=["Execute"])
        output = self._render_to_string([access])
        assert "Execute" in output

    def test_renders_table_title(self) -> None:
        access = _make_item_access()
        output = self._render_to_string([access], title="Warehouse Permissions")
        assert "Warehouse" in output or "Permissions" in output

    def test_empty_list_renders_without_error(self) -> None:
        output = self._render_to_string([])
        assert output is not None

    def test_json_output_emits_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        access = _make_item_access(display_name="Bob", permissions=["Read"])
        render_permissions_table([access], title="Test", json_output=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)
        assert len(parsed) == 1

    def test_service_principal_uses_aad_app_id(self) -> None:
        """Service principal: identity column shows AAD app ID, not UPN."""
        aad_id = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
        principal = ItemAccessPrincipal.model_validate(
            {
                "id": str(UUID("12345678-1234-5678-1234-567812345678")),
                "displayName": "MyApp",
                "type": "ServicePrincipal",
                "servicePrincipalDetails": {"aadAppId": aad_id},
            }
        )
        detail = ItemAccessDetail.model_validate(
            {"permissions": ["Read"], "additionalPermissions": []}
        )
        access = ItemAccess.model_validate(
            {
                "principal": principal.model_dump(by_alias=True, mode="json"),
                "itemAccessDetails": detail.model_dump(by_alias=True, mode="json"),
            }
        )
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        render_permissions_table([access], title="Test", console=console)
        output = sio.getvalue()
        assert aad_id in output

    def test_uses_default_console_when_none_given(self) -> None:
        """When console=None, a fresh Console() is created (no crash)."""
        access = _make_item_access()
        # Should not raise; output goes to stdout (captured)
        render_permissions_table([access], title="Test", console=None)

    def test_narrow_console_title_not_char_wrapped(self) -> None:
        """A wide title must appear intact on one line even on a narrow console.

        ``render_permissions_table`` previously used ``Table(title=...)`` which
        wraps to the table's content width.  The fix prints the title as a
        separate bold line before the table.
        """
        access = _make_item_access()
        sio = StringIO()
        # Console narrower than "Warehouse Permissions" (20 chars) — the five-column
        # table body is wide so Rich normally wraps a Table title at the body width;
        # at width=30 the body fits but "Warehouse Permissions" would be split.
        console = Console(file=sio, width=30, highlight=False, no_color=True)
        render_permissions_table([access], title="Warehouse Permissions", console=console)
        output = sio.getvalue()
        assert "Warehouse Permissions" in output
        assert "Permissi\n" not in output


class TestRenderRefreshTable:
    """render_refresh_table renders TableSyncStatus records correctly."""

    def _render_to_string(self, statuses: list[TableSyncStatus]) -> str:
        sio = StringIO()
        console = Console(file=sio, width=200, highlight=False, no_color=True)
        render_refresh_table(statuses, console=console)
        return sio.getvalue()

    def test_renders_table_name(self) -> None:
        s = _make_table_sync_status(table_name="dbo.Orders")
        output = self._render_to_string([s])
        assert "dbo.Orders" in output

    def test_renders_success_status(self) -> None:
        s = _make_table_sync_status(status="Success")
        output = self._render_to_string([s])
        assert "Success" in output

    def test_renders_failure_status(self) -> None:
        s = _make_table_sync_status(status="Failure")
        output = self._render_to_string([s])
        assert "Failure" in output

    def test_renders_notrun_status(self) -> None:
        s = _make_table_sync_status(status="NotRun")
        output = self._render_to_string([s])
        assert "NotRun" in output

    def test_renders_unknown_status_without_style(self) -> None:
        """An unknown status value does not raise — just renders as plain text."""
        s = _make_table_sync_status(status="InProgress")
        output = self._render_to_string([s])
        assert "InProgress" in output

    def test_renders_end_time(self) -> None:
        dt = datetime(2024, 3, 15, 10, 30, 0, tzinfo=UTC)
        s = _make_table_sync_status(end_date_time=dt)
        output = self._render_to_string([s])
        assert "2024" in output

    def test_renders_error_code_and_message(self) -> None:
        error = TableSyncError.model_validate(
            {"errorCode": "ERR001", "message": "Something went wrong"}
        )
        s = _make_table_sync_status(status="Failure", error=error)
        output = self._render_to_string([s])
        assert "ERR001" in output
        assert "Something went wrong" in output

    def test_renders_error_code_only(self) -> None:
        error = TableSyncError.model_validate({"errorCode": "ERR002", "message": None})
        s = _make_table_sync_status(status="Failure", error=error)
        output = self._render_to_string([s])
        assert "ERR002" in output

    def test_renders_error_message_only(self) -> None:
        error = TableSyncError.model_validate({"errorCode": None, "message": "Something failed"})
        s = _make_table_sync_status(status="Failure", error=error)
        output = self._render_to_string([s])
        assert "Something failed" in output

    def test_renders_no_error(self) -> None:
        s = _make_table_sync_status(status="Success", error=None)
        output = self._render_to_string([s])
        assert "Success" in output

    def test_empty_list_renders_without_error(self) -> None:
        output = self._render_to_string([])
        assert output is not None

    def test_uses_default_console_when_none_given(self) -> None:
        """When console=None, a fresh Console() is created (no crash)."""
        s = _make_table_sync_status()
        # Should not raise
        render_refresh_table([s], console=None)

    def test_title_appears_in_output(self) -> None:
        s = _make_table_sync_status()
        output = self._render_to_string([s])
        assert "Metadata Refresh Results" in output


# ---------------------------------------------------------------------------
# GUID column detection (_is_guid_column) unit tests
# ---------------------------------------------------------------------------

#: A canonical GUID string used across GUID tests.
_SAMPLE_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
#: A second GUID (uppercase) to verify case-insensitive matching.
_SAMPLE_GUID_UPPER = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"


class TestIsGuidColumn:
    """_is_guid_column returns True iff every non-None cell is a bare GUID."""

    def test_all_guid_values_returns_true(self) -> None:
        rows: list[dict[str, object] | object] = [
            {"id": _SAMPLE_GUID},
            {"id": _SAMPLE_GUID_UPPER},
        ]
        assert _is_guid_column("id", rows) is True

    def test_non_guid_string_returns_false(self) -> None:
        rows: list[dict[str, object] | object] = [{"name": "my-workspace"}]
        assert _is_guid_column("name", rows) is False

    def test_all_none_column_returns_false(self) -> None:
        rows: list[dict[str, object] | object] = [{"id": None}, {"id": None}]
        assert _is_guid_column("id", rows) is False

    def test_mixed_none_and_guid_returns_true(self) -> None:
        """None cells are ignored; as long as all non-None values are GUIDs → True."""
        rows: list[dict[str, object] | object] = [
            {"id": None},
            {"id": _SAMPLE_GUID},
        ]
        assert _is_guid_column("id", rows) is True

    def test_one_non_guid_among_guids_returns_false(self) -> None:
        rows: list[dict[str, object] | object] = [
            {"id": _SAMPLE_GUID},
            {"id": "not-a-guid"},
        ]
        assert _is_guid_column("id", rows) is False

    def test_arm_resource_path_returns_false(self) -> None:
        """A full ARM resource path containing a GUID is NOT a bare GUID column."""
        arm_path = (
            f"/subscriptions/{_SAMPLE_GUID}"
            "/resourceGroups/rg/providers/Microsoft.Fabric/capacities/cap1"
        )
        rows: list[dict[str, object] | object] = [{"capacityId": arm_path}]
        assert _is_guid_column("capacityId", rows) is False

    def test_non_dict_rows_are_ignored(self) -> None:
        """Scalar rows never contribute; only dict rows matter."""
        rows: list[dict[str, object] | object] = ["scalar-value"]
        # No dict rows → no evidence of GUID → False
        assert _is_guid_column("id", rows) is False

    def test_empty_rows_returns_false(self) -> None:
        assert _is_guid_column("id", []) is False

    def test_mixed_case_guid_returns_true(self) -> None:
        mixed = "A1b2C3d4-E5f6-7890-AbCd-EF1234567890"
        rows: list[dict[str, object] | object] = [{"id": mixed}]
        assert _is_guid_column("id", rows) is True

    def test_guid_with_trailing_newline_returns_false(self) -> None:
        """A GUID followed by a trailing newline must NOT be detected as a GUID.

        Python's ``$`` anchor matches just before ``\\n``, so ``re.match`` with
        ``^...$`` would falsely accept ``"<guid>\\n"``.  Using ``fullmatch``
        without anchors rejects such strings correctly.
        """
        rows: list[dict[str, object] | object] = [{"id": f"{_SAMPLE_GUID}\n"}]
        assert _is_guid_column("id", rows) is False


# ---------------------------------------------------------------------------
# Narrow-console rendering: GUID columns survive truncation, text columns don't
# ---------------------------------------------------------------------------

#: Long display name that will definitely be cropped in a narrow console.
_LONG_NAME = "This is a very long workspace display name that exceeds sixty characters easily"


class TestGuidColumnWidthInNarrowConsole:
    """GUID columns get no_wrap/min_width=36 so they survive narrow terminals."""

    def _render_narrow(
        self,
        data: list[dict[str, object]],
        *,
        width: int = 50,
        table_title: str | None = None,
        prune_null_columns: bool = False,
    ) -> str:
        sio = StringIO()
        console = Console(file=sio, width=width, highlight=False, no_color=True)
        render(
            data,
            json_output=False,
            console=console,
            table_title=table_title,
            prune_null_columns=prune_null_columns,
        )
        return sio.getvalue()

    def test_guid_survives_narrow_console(self) -> None:
        """The full GUID string must be present verbatim in the narrow output."""
        data: list[dict[str, object]] = [{"id": _SAMPLE_GUID, "displayName": _LONG_NAME}]
        output = self._render_narrow(data, width=50)
        assert _SAMPLE_GUID in output

    def test_long_text_is_truncated_in_narrow_console(self) -> None:
        """GUID survives narrow output while the long text column is truncated."""
        data: list[dict[str, object]] = [{"id": _SAMPLE_GUID, "displayName": _LONG_NAME}]
        output = self._render_narrow(data, width=50)
        assert _SAMPLE_GUID in output
        assert _LONG_NAME not in output

    def test_two_guid_columns_primary_survives_secondary_yields(self) -> None:
        """Only the first (primary) GUID column keeps no_wrap/min_width=36.

        Secondary GUID columns are rendered without a forced min_width so they
        can yield space to human-readable columns.  At width=80 — the narrow
        default for piped/non-TTY output — the primary GUID must appear verbatim
        while the readable column must not be collapsed to zero width.
        """
        guid2 = "ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb"
        data: list[dict[str, object]] = [
            {
                "workspaceId": _SAMPLE_GUID,
                "displayName": "My Workspace",
                "warehouseId": guid2,
            }
        ]
        output = self._render_narrow(data, width=80)
        # Primary GUID (first GUID column) must appear in full
        assert _SAMPLE_GUID in output
        # Human-readable column must not be starved to zero width
        assert "displ" in output.lower()
        # No zero-width-column artifact
        assert "┃┃┃" not in output

    def test_non_guid_column_not_given_min_width(self) -> None:
        """A non-GUID id-like string column does not get special treatment."""
        data: list[dict[str, object]] = [{"id": "short-non-guid", "displayName": _LONG_NAME}]
        output = self._render_narrow(data, width=50)
        # The short non-GUID id should still appear (it fits), but the long name
        # is truncated — the point is we don't crash and the output is produced.
        assert "short-non-guid" in output

    def test_all_none_guid_col_dropped_when_pruning_enabled(self) -> None:
        """An all-None column is dropped when prune_null_columns=True."""
        data: list[dict[str, object]] = [{"id": None, "displayName": "Alice"}]
        output = self._render_narrow(data, width=50, prune_null_columns=True)
        # Column "id" is all-null and pruned; "displayName" remains
        assert "Alice" in output
        assert "id" not in output

    def test_all_none_guid_col_visible_when_pruning_disabled(self) -> None:
        """An all-None column is kept when prune_null_columns=False (the default)."""
        data: list[dict[str, object]] = [{"id": None, "displayName": "Alice"}]
        output = self._render_narrow(data, width=50, prune_null_columns=False)
        assert "Alice" in output
        assert "id" in output

    def test_guid_column_with_some_nulls_survives(self) -> None:
        """A partially-null GUID column still gets full width."""
        data: list[dict[str, object]] = [
            {"id": _SAMPLE_GUID, "displayName": "Alice"},
            {"id": None, "displayName": "Bob"},
        ]
        output = self._render_narrow(data, width=50)
        assert _SAMPLE_GUID in output


# ---------------------------------------------------------------------------
# Two-GUID-column width starvation regression tests (issue #737)
# ---------------------------------------------------------------------------

#: Two sample GUIDs used in the multi-GUID-column tests.
_GUID_ID = "eb85cc99-5ad8-4f89-85b8-2a46eaa410d6"
_GUID_CAPACITY = "4c18bf4e-86dd-47da-8602-87184ff16c13"

#: Rows that exercise the two-GUID-column layout (workspaces list shape).
_WORKSPACE_ROWS: list[dict[str, object]] = [
    {
        "id": _GUID_ID,
        "displayName": "Adventure Works Finance",
        "description": "Finance",
        "capacityId": _GUID_CAPACITY,
        "defaultDataWarehouseCollation": "Latin1_General_100_BIN2_UTF8",
    },
    {
        "id": "51bb6021-07a7-4846-aa54-b9a081d767d8",
        "displayName": "My workspace",
        "description": "",
        "capacityId": None,
        "defaultDataWarehouseCollation": "Latin1_General_100_BIN2_UTF8",
    },
]


class TestMultiGuidColumnWidthStarvation:
    """Two GUID columns must not collapse human-readable columns to zero width.

    Regression tests for the bug described in issue #737: when a table has two
    GUID-valued columns (e.g. ``id`` and ``capacityId``), both previously got
    ``no_wrap=True, min_width=36``.  Together they exceed 80 columns (36+36+borders),
    so Rich collapsed the remaining human-readable columns to zero width, producing
    empty ``┃┃┃`` headers and invisible content.

    The fix: only the *first* GUID column keeps ``no_wrap + min_width``; secondary
    GUID columns are rendered without a forced min_width so they can yield space.
    """

    def _render_at_width(self, width: int) -> str:
        sio = StringIO()
        console = Console(file=sio, width=width, highlight=False, no_color=True)
        render(_WORKSPACE_ROWS, json_output=False, console=console, table_title="Workspaces")
        return sio.getvalue()

    # ------------------------------------------------------------------
    # 1. displayName header and value must be visible at width=80
    # ------------------------------------------------------------------

    def test_displayname_header_visible_at_width_80(self) -> None:
        """'displayName' column header must appear (possibly truncated) at width=80.

        At 80 columns there is not enough room for the full header text, so Rich
        will truncate it to e.g. ``displa…``.  The requirement is that the column
        is *present* — not collapsed to zero width — so we check for the first few
        characters of the name rather than the full string.
        """
        output = self._render_at_width(80)
        # "displ" covers both "displayName" and any truncation like "displa…"
        assert "displ" in output.lower()

    def test_displayname_value_visible_at_width_80(self) -> None:
        """At least the start of the displayName value must appear at width=80."""
        output = self._render_at_width(80)
        # "Advent" covers "Adventure" even when the cell is truncated to "Advent…"
        assert "Advent" in output

    # ------------------------------------------------------------------
    # 2. No adjacent empty column separators (┃┃┃ artifact)
    # ------------------------------------------------------------------

    def test_no_empty_column_separators_at_width_80(self) -> None:
        """Three consecutive column separators with nothing between them must not appear."""
        output = self._render_at_width(80)
        # The zero-width-column artifact looks like ┃┃┃ (three separators in a row).
        # Stripping ANSI and checking for adjacent-separator sequences:
        assert "┃┃┃" not in output

    # ------------------------------------------------------------------
    # 3. Primary GUID column (id) still renders correctly
    # ------------------------------------------------------------------

    def test_primary_guid_intact_and_readable_column_survives_at_width_80(self) -> None:
        """Primary GUID renders verbatim AND human-readable column survives at width=80.

        Under the old code both GUID columns (``id`` + ``capacityId``) got
        ``no_wrap + min_width=36``, consuming ≥80 chars and starving
        ``displayName`` to zero width.  Under the fixed code only the first
        GUID column keeps its full width, so ``displayName`` gets remaining
        space and is visible.

        This assertion pair fails on the pre-fix code (``displayName`` was
        starved) and passes on the post-fix code — making it a genuine guard.
        """
        output = self._render_at_width(80)
        # Primary GUID must be present verbatim (no_wrap keeps it intact)
        assert _GUID_ID in output
        # Readable column must NOT be starved — this is the discriminating assertion
        assert "displ" in output.lower()
        assert "┃┃┃" not in output

    # ------------------------------------------------------------------
    # 4. Wider consoles still render correctly
    # ------------------------------------------------------------------

    def test_all_columns_visible_at_width_100(self) -> None:
        """At width=100 all column headers must be present."""
        output = self._render_at_width(100)
        assert "displayName" in output
        assert "id" in output

    def test_all_columns_visible_at_width_120(self) -> None:
        """At width=120 all column headers and values are fully visible."""
        output = self._render_at_width(120)
        assert "displayName" in output
        # Value may wrap across lines; check that the first word is present
        assert "Adventure" in output
        assert "id" in output
        assert _GUID_ID in output

    # ------------------------------------------------------------------
    # 5. Existing behaviour: single-GUID-column tables unaffected
    # ------------------------------------------------------------------

    def test_single_guid_column_still_gets_full_width(self) -> None:
        """A table with only one GUID column still has it rendered no_wrap at 50 cols."""
        data: list[dict[str, object]] = [
            {"id": _GUID_ID, "displayName": "Adventure Works Finance"},
        ]
        sio = StringIO()
        console = Console(file=sio, width=50, highlight=False, no_color=True)
        render(data, json_output=False, console=console)
        output = sio.getvalue()
        # Primary GUID must survive (no_wrap keeps it intact)
        assert _GUID_ID in output

    # ------------------------------------------------------------------
    # 6. drop_columns still works alongside the multi-GUID fix
    # ------------------------------------------------------------------

    def test_drop_columns_still_works_with_two_guid_columns(self) -> None:
        """drop_columns must remove the named column even when ≥2 GUID columns present."""
        sio = StringIO()
        console = Console(file=sio, width=80, highlight=False, no_color=True)
        render(
            _WORKSPACE_ROWS,
            json_output=False,
            console=console,
            drop_columns=("capacityId",),
        )
        output = sio.getvalue()
        assert "capacityId" not in output
        # displayName must be visible (possibly truncated) now that capacityId is dropped
        assert "displ" in output.lower()

    # ------------------------------------------------------------------
    # 7. All-null GUID column still pruned
    # ------------------------------------------------------------------

    def test_all_null_guid_column_still_pruned_in_multi_guid_table(self) -> None:
        """An all-null GUID column is pruned when prune_null_columns=True.

        This test exercises the primary/secondary-GUID code path: ``id`` is the
        primary GUID (non-null), ``workspaceId`` is a secondary non-null GUID, and
        ``capacityId`` is all-null and must be dropped when pruning is enabled.
        After pruning, the table has two live GUID columns (primary + secondary)
        plus ``displayName``; the fix must ensure ``displayName`` is NOT starved
        to zero width at 80 cols.
        """
        data: list[dict[str, object]] = [
            {
                "id": _GUID_ID,
                "displayName": "Foo",
                "workspaceId": _GUID_CAPACITY,
                "capacityId": None,
            },
            {
                "id": "51bb6021-07a7-4846-aa54-b9a081d767d8",
                "displayName": "Bar",
                "workspaceId": _GUID_CAPACITY,
                "capacityId": None,
            },
        ]
        sio = StringIO()
        console = Console(file=sio, width=80, highlight=False, no_color=True)
        render(data, json_output=False, console=console, prune_null_columns=True)
        output = sio.getvalue()
        # All-null column pruned
        assert "capacityId" not in output
        # Primary GUID intact (no_wrap)
        assert _GUID_ID in output
        # Human-readable column must not be starved to zero width
        assert "displ" in output.lower()
        # No zero-width-column artifact
        assert "┃┃┃" not in output


# ---------------------------------------------------------------------------
# Warehouses list -A shape regression test (issue #739)
# ---------------------------------------------------------------------------

#: Rows that mirror the ``warehouses list -A`` column layout: two GUID columns
#: (``id`` + ``workspaceId``) plus human-readable columns (``displayName``,
#: ``kind``).  This is the same ≥2-GUID starvation bug as issue #737.
_WAREHOUSE_ALL_ROWS: list[dict[str, object]] = [
    {
        "id": "d4e5f6a7-b8c9-0123-def0-123456789abc",
        "displayName": "SalesWarehouse",
        "description": "Main sales DWH",
        "kind": "Warehouse",
        "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    },
    {
        "id": "11111111-2222-3333-4444-555555555555",
        "displayName": "MarketingLakehouse",
        "description": "",
        "kind": "Lakehouse",
        "workspaceId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    },
]


class TestWarehousesAllShapeWidthStarvation:
    """``warehouses list -A`` with id+workspaceId (two GUIDs) must not collapse displayName/kind.

    Regression test for issue #739 — the same shared renderer bug as #737 but
    exercised with the warehouses column shape (``id``, ``displayName``,
    ``description``, ``kind``, ``workspaceId``).
    """

    def _render_at_width(self, width: int) -> str:
        sio = StringIO()
        console = Console(file=sio, width=width, highlight=False, no_color=True)
        render(_WAREHOUSE_ALL_ROWS, json_output=False, console=console, table_title="Warehouses")
        return sio.getvalue()

    def test_displayname_header_visible_at_width_80(self) -> None:
        """'displayName' column header must appear (possibly truncated) at width=80."""
        output = self._render_at_width(80)
        assert "displ" in output.lower()

    def test_displayname_value_visible_at_width_80(self) -> None:
        """At least a fragment of a displayName value must appear at width=80."""
        output = self._render_at_width(80)
        # "Sales" covers "SalesWarehouse" even when the cell is truncated
        assert "Sales" in output

    def test_kind_header_visible_at_width_80(self) -> None:
        """'kind' column header must appear at width=80 (it is a short non-GUID column)."""
        output = self._render_at_width(80)
        assert "kind" in output

    def test_kind_value_visible_at_width_80(self) -> None:
        """A kind cell value must be present in the output at width=80."""
        output = self._render_at_width(80)
        assert "Warehouse" in output

    def test_no_empty_column_separators_at_width_80(self) -> None:
        """The zero-width-column artifact (┃┃┃) must not appear at width=80."""
        output = self._render_at_width(80)
        assert "┃┃┃" not in output

    def test_primary_guid_id_visible_at_width_80(self) -> None:
        """The first GUID column (id) must still render without truncation at width=80."""
        output = self._render_at_width(80)
        assert "d4e5f6a7-b8c9-0123-def0-123456789abc" in output

    def test_all_columns_visible_at_width_120(self) -> None:
        """At width=120 all column headers must be present."""
        output = self._render_at_width(120)
        assert "displayName" in output
        assert "kind" in output
        assert "id" in output


class TestSanitiseJson:
    """Unit tests for the sanitise_json helper."""

    def test_finite_float_unchanged(self) -> None:
        assert sanitise_json(1.5) == 1.5

    def test_positive_infinity_becomes_none(self) -> None:
        assert sanitise_json(float("inf")) is None

    def test_negative_infinity_becomes_none(self) -> None:
        assert sanitise_json(float("-inf")) is None

    def test_nan_becomes_none(self) -> None:
        assert sanitise_json(float("nan")) is None

    def test_non_float_scalars_unchanged(self) -> None:
        assert sanitise_json(42) == 42
        assert sanitise_json("hello") == "hello"
        assert sanitise_json(None) is None
        assert sanitise_json(True) is True  # noqa: FBT003

    def test_dict_with_non_finite_value_coerced(self) -> None:
        result = sanitise_json({"a": float("inf"), "b": 1.0})
        assert result == {"a": None, "b": 1.0}

    def test_list_with_non_finite_value_coerced(self) -> None:
        result = sanitise_json([float("nan"), 2.0, float("-inf")])
        assert result == [None, 2.0, None]

    def test_nested_structure_coerced(self) -> None:
        data = {"rows": [{"cost": float("inf"), "label": "x"}], "total": float("nan")}
        result = sanitise_json(data)
        assert result == {"rows": [{"cost": None, "label": "x"}], "total": None}

    def test_no_copy_when_no_non_finite(self) -> None:
        """Finite-only data roundtrips without mutation."""
        data = {"a": 1.0, "b": [2.5, 3.0]}
        result = sanitise_json(data)
        assert result == data


class TestRenderJsonNonFinite:
    """render(data, json_output=True) must emit strict JSON even for non-finite floats."""

    def test_infinity_in_dict_emits_null(self, capsys: pytest.CaptureFixture[str]) -> None:
        render({"cost": float("inf")}, json_output=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["cost"] is None

    def test_nan_in_dict_emits_null(self, capsys: pytest.CaptureFixture[str]) -> None:
        render({"value": float("nan")}, json_output=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["value"] is None

    def test_negative_infinity_in_list_emits_null(self, capsys: pytest.CaptureFixture[str]) -> None:
        render([float("-inf"), 1.0], json_output=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed[0] is None
        assert parsed[1] == 1.0

    def test_output_is_strict_json_parseable(self, capsys: pytest.CaptureFixture[str]) -> None:
        """json.loads must succeed: no Infinity / NaN tokens in the output."""
        render({"a": float("inf"), "b": float("nan"), "c": 2.5}, json_output=True)
        captured = capsys.readouterr()
        # json.loads raises if the output is not strict JSON
        parsed = json.loads(captured.out)
        assert parsed["c"] == 2.5


# ---------------------------------------------------------------------------
# Nested dict/list panel rendering (issue #830)
# ---------------------------------------------------------------------------


class TestNestedPanelRendering:
    """Nested dict/list values in panel output render recursively, not as Python reprs.

    Regression tests for issue #830: _render_panel previously called _cell()
    on nested values, producing Python repr strings like ``{'workspace': None, ...}``.
    The fix routes nested dict and list values through _format_nested(), which
    expands them into indented key: value blocks.
    """

    def _render_to_string(self, data: object) -> str:
        sio = StringIO()
        console = Console(file=sio, width=120, highlight=False, no_color=True)
        render(data, json_output=False, console=console)
        return sio.getvalue()

    def test_nested_dict_no_repr_substring(self) -> None:
        """Nested dict values must not appear as Python dict repr strings."""
        data = {"config": {"workspace": None, "server": "myserver"}}
        output = self._render_to_string(data)
        assert "{'workspace'" not in output
        assert "{'workspace': None" not in output

    def test_nested_dict_keys_appear_in_output(self) -> None:
        """Keys inside a nested dict must appear in the panel output."""
        data = {"config": {"workspace": None, "server": "myserver"}}
        output = self._render_to_string(data)
        assert "workspace" in output
        assert "server" in output
        assert "myserver" in output

    def test_nested_dict_null_value_renders_as_null(self) -> None:
        """A None value inside a nested dict must render as NULL, not None."""
        data = {"config": {"workspace": None}}
        output = self._render_to_string(data)
        assert "NULL" in output
        assert "None" not in output

    def test_nested_list_no_repr_substring(self) -> None:
        """Nested list values must not appear as Python list repr strings."""
        data = {"items": ["alpha", "beta"]}
        output = self._render_to_string(data)
        assert "['alpha'" not in output
        assert "alpha" in output
        assert "beta" in output

    def test_empty_nested_dict_renders_without_error(self) -> None:
        """An empty nested dict must render without error."""
        data = {"settings": {}}
        output = self._render_to_string(data)
        assert "settings" in output

    def test_empty_nested_list_renders_without_error(self) -> None:
        """An empty nested list must render without error."""
        data = {"tags": []}
        output = self._render_to_string(data)
        assert "tags" in output

    def test_deeply_nested_renders_without_error(self) -> None:
        """Deeply nested structures must render without error and no Python reprs."""
        data = {"a": {"b": {"c": {"d": 42}}}}
        output = self._render_to_string(data)
        assert "a" in output
        assert "42" in output
        assert "{'b'" not in output
        assert "{'c'" not in output

    def test_mixed_scalar_and_nested_render_correctly(self) -> None:
        """A dict mixing scalar and nested values must render all of them."""
        data = {"name": "test", "defaults": {"workspace": None}, "count": 5}
        output = self._render_to_string(data)
        assert "name" in output
        assert "test" in output
        assert "defaults" in output
        assert "workspace" in output
        assert "count" in output
        assert "5" in output
        assert "{'workspace'" not in output

    def test_scalar_rendering_unchanged(self) -> None:
        """Scalar panel values must still render correctly after the fix."""
        data = {"id": "abc", "score": 42, "active": "true"}
        output = self._render_to_string(data)
        assert "abc" in output
        assert "42" in output
        assert "true" in output

    def test_nested_list_with_dict_items_no_repr(self) -> None:
        """A list containing dicts must render each item recursively, not as repr."""
        data = {"servers": [{"host": "s1", "port": 5432}, {"host": "s2", "port": 5433}]}
        output = self._render_to_string(data)
        assert "{'host'" not in output
        assert "s1" in output
        assert "s2" in output

    def test_list_of_dicts_no_blank_indent_lines(self) -> None:
        """_format_nested must not produce lines that are only whitespace.

        Before the fix, the list branch did ``f"{indent}{child}"`` where child
        started with ``"\\n"`` for nested dict/list items, producing a line of
        trailing indent spaces before the actual nested content.  Each such
        line rendered as a blank line in the panel.
        """
        result = _format_nested([{"host": "s1", "port": 5432}, {"host": "s2", "port": 5433}])
        lines = result.split("\n")
        # No line should be non-empty but consist only of whitespace
        blank_indent_lines = [line for line in lines if line and not line.strip()]
        assert blank_indent_lines == [], f"Blank indent lines found: {blank_indent_lines!r}"


# ===========================================================================
# _make_bar unit tests
# ===========================================================================


class TestMakeBar:
    """Unit tests for the _make_bar helper."""

    def test_none_value_returns_empty(self) -> None:
        assert _make_bar(None, 100.0, 10) == ""

    def test_zero_value_returns_empty(self) -> None:
        assert _make_bar(0.0, 100.0, 10) == ""

    def test_negative_value_returns_empty(self) -> None:
        assert _make_bar(-5.0, 100.0, 10) == ""

    def test_zero_max_value_returns_empty(self) -> None:
        assert _make_bar(50.0, 0.0, 10) == ""

    def test_negative_max_value_returns_empty(self) -> None:
        assert _make_bar(50.0, -10.0, 10) == ""

    def test_zero_max_cells_returns_empty(self) -> None:
        assert _make_bar(50.0, 100.0, 0) == ""

    def test_negative_max_cells_returns_empty(self) -> None:
        assert _make_bar(50.0, 100.0, -1) == ""

    def test_nan_value_returns_empty(self) -> None:

        assert _make_bar(float("nan"), 100.0, 10) == ""

    def test_inf_value_returns_empty(self) -> None:
        assert _make_bar(float("inf"), 100.0, 10) == ""

    def test_nan_max_value_returns_empty(self) -> None:

        assert _make_bar(50.0, float("nan"), 10) == ""

    def test_inf_max_value_returns_empty(self) -> None:
        assert _make_bar(50.0, float("inf"), 10) == ""

    def test_full_value_fills_all_cells(self) -> None:
        bar = _make_bar(100.0, 100.0, 10)
        assert bar == "█" * 10

    def test_half_value_fills_half_cells(self) -> None:
        bar = _make_bar(50.0, 100.0, 10)
        assert bar == "█" * 5

    def test_one_eighth_produces_partial_block(self) -> None:
        # 1/8 of 1 cell = 1/8 block character ▏
        bar = _make_bar(1.0, 8.0, 1)
        assert bar == "▏"

    def test_bar_length_never_exceeds_max_cells(self) -> None:
        # value > max_value is clamped to max_cells full blocks
        bar = _make_bar(200.0, 100.0, 5)
        assert len(bar) <= 5

    def test_small_value_rounds_to_one_eighth(self) -> None:
        # Very small non-zero value should either return "" (rounds to 0)
        # or a partial block; crucially, never crash.
        bar = _make_bar(0.001, 1000.0, 10)
        # Rounds to 0 eighths → empty bar
        assert bar == ""

    def test_bar_contains_only_block_chars(self) -> None:
        bar = _make_bar(75.0, 100.0, 8)
        block_chars = set("█▏▎▍▌▋▊▉")
        assert all(c in block_chars for c in bar)


# ===========================================================================
# render_statistic_details integration tests
# ===========================================================================


def _make_wide_console() -> tuple[Console, StringIO]:
    """Return a (Console, StringIO) pair at width=160 (bars always visible)."""
    sio = StringIO()
    con = Console(file=sio, width=160, highlight=False, no_color=True)
    return con, sio


def _make_narrow_console() -> tuple[Console, StringIO]:
    """Return a (Console, StringIO) pair at width=70 (bars always omitted)."""
    sio = StringIO()
    con = Console(file=sio, width=70, highlight=False, no_color=True)
    return con, sio


def _make_details(
    *,
    steps: list[StatisticHistogramStep] | None = None,
    with_header: bool = False,
    with_density: bool = False,
) -> StatisticDetails:
    """Build a minimal StatisticDetails for testing."""
    if steps is None:
        steps = [
            StatisticHistogramStep(
                range_hi_key="100",
                range_rows=50.0,
                eq_rows=10.0,
                distinct_range_rows=5.0,
                avg_range_rows=10.0,
            ),
            StatisticHistogramStep(
                range_hi_key="200",
                range_rows=100.0,
                eq_rows=20.0,
                distinct_range_rows=10.0,
                avg_range_rows=10.0,
            ),
        ]
    header = (
        StatisticHeaderRow(
            name="stat_sales_id",
            updated=None,
            rows=1000,
            rows_sampled=1000,
            steps=2,
            density=0.001,
            average_key_length=4.0,
            string_index="NO",
            filter_expression=None,
            unfiltered_rows=None,
        )
        if with_header
        else None
    )
    density = (
        [StatisticDensityRow(all_density=0.001, average_length=4.0, columns="id")]
        if with_density
        else []
    )
    return StatisticDetails(stat_header=header, density_vector=density, histogram=steps)


class TestRenderStatisticDetails:
    """Integration tests for render_statistic_details."""

    # ------------------------------------------------------------------
    # Wide terminal: bar columns present
    # ------------------------------------------------------------------

    def test_wide_terminal_shows_histogram_column_headers(self) -> None:
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(), json_output=False, console=con)
        output = sio.getvalue()
        assert "RANGE_HI_KEY" in output
        assert "EQ_ROWS" in output
        assert "RANGE_ROWS" in output

    def test_wide_terminal_shows_bar_columns(self) -> None:
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(), json_output=False, console=con)
        output = sio.getvalue()
        assert "EQ bar" in output
        assert "Range bar" in output

    def test_wide_terminal_bars_contain_block_char(self) -> None:
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(), json_output=False, console=con)
        output = sio.getvalue()
        assert "█" in output

    # ------------------------------------------------------------------
    # Narrow terminal: bar columns omitted, data still rendered
    # ------------------------------------------------------------------

    def test_narrow_terminal_no_bar_columns(self) -> None:
        con, sio = _make_narrow_console()
        render_statistic_details(_make_details(), json_output=False, console=con)
        output = sio.getvalue()
        assert "EQ bar" not in output
        assert "Range bar" not in output

    def test_narrow_terminal_data_columns_still_present(self) -> None:
        con, sio = _make_narrow_console()
        render_statistic_details(_make_details(), json_output=False, console=con)
        output = sio.getvalue()
        assert "RANGE_HI_KEY" in output
        assert "EQ_ROWS" in output

    # ------------------------------------------------------------------
    # Bar visibility threshold: pins the exact width where bars appear
    # ------------------------------------------------------------------

    def test_bar_columns_absent_at_width_100(self) -> None:
        """At width 100 the budget is < 2 so bar columns must not appear."""
        sio = StringIO()
        con = Console(file=sio, width=100, highlight=False, no_color=True)
        render_statistic_details(_make_details(), json_output=False, console=con)
        output = sio.getvalue()
        assert "EQ bar" not in output
        assert "Range bar" not in output

    def test_bar_columns_present_at_width_101(self) -> None:
        """At width 101 the budget reaches 2, giving 1 bar cell each side."""
        sio = StringIO()
        con = Console(file=sio, width=101, highlight=False, no_color=True)
        render_statistic_details(_make_details(), json_output=False, console=con)
        output = sio.getvalue()
        assert "EQ bar" in output
        assert "Range bar" in output

    # ------------------------------------------------------------------
    # All-zero and all-None columns: no block chars
    # ------------------------------------------------------------------

    def test_all_zero_eq_rows_no_block_char_in_eq_column(self) -> None:
        # Both eq_rows and range_rows are zero so no bar chars appear anywhere.
        # This directly asserts that an all-zero column never produces a block char.
        steps = [
            StatisticHistogramStep(
                range_hi_key="100",
                range_rows=0.0,
                eq_rows=0.0,
                distinct_range_rows=None,
                avg_range_rows=None,
            ),
        ]
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(steps=steps), json_output=False, console=con)
        output = sio.getvalue()
        assert "RANGE_HI_KEY" in output
        assert "█" not in output

    def test_all_none_eq_rows_no_crash(self) -> None:
        steps = [
            StatisticHistogramStep(
                range_hi_key="100",
                range_rows=None,
                eq_rows=None,
                distinct_range_rows=None,
                avg_range_rows=None,
            ),
        ]
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(steps=steps), json_output=False, console=con)
        output = sio.getvalue()
        assert "RANGE_HI_KEY" in output

    # ------------------------------------------------------------------
    # Empty histogram
    # ------------------------------------------------------------------

    def test_empty_histogram_renders_without_error(self) -> None:
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(steps=[]), json_output=False, console=con)
        output = sio.getvalue()
        # No crash; a short "no steps" notice is printed instead of an empty table.
        assert "no steps" in output

    # ------------------------------------------------------------------
    # stat_header and density_vector
    # ------------------------------------------------------------------

    def test_stat_header_panel_shown_when_present(self) -> None:
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(with_header=True), json_output=False, console=con)
        output = sio.getvalue()
        assert "Stat Header" in output
        assert "stat_sales_id" in output

    def test_stat_header_absent_when_none(self) -> None:
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(with_header=False), json_output=False, console=con)
        output = sio.getvalue()
        assert "Stat Header" not in output

    def test_density_vector_shown_when_non_empty(self) -> None:
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(with_density=True), json_output=False, console=con)
        output = sio.getvalue()
        assert "Density Vector" in output

    def test_density_vector_absent_when_empty(self) -> None:
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(with_density=False), json_output=False, console=con)
        output = sio.getvalue()
        assert "Density Vector" not in output

    # ------------------------------------------------------------------
    # None range_hi_key renders as NULL
    # ------------------------------------------------------------------

    def test_none_range_hi_key_renders_null(self) -> None:
        steps = [
            StatisticHistogramStep(
                range_hi_key=None,
                range_rows=10.0,
                eq_rows=5.0,
                distinct_range_rows=2.0,
                avg_range_rows=5.0,
            ),
        ]
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(steps=steps), json_output=False, console=con)
        output = sio.getvalue()
        assert "NULL" in output

    def test_range_hi_key_markup_brackets_rendered_verbatim(self) -> None:
        """Rich markup in range_hi_key must be escaped, not interpreted."""
        steps = [
            StatisticHistogramStep(
                range_hi_key="[red]x[/red]",
                range_rows=10.0,
                eq_rows=5.0,
                distinct_range_rows=2.0,
                avg_range_rows=5.0,
            ),
        ]
        con, sio = _make_wide_console()
        render_statistic_details(_make_details(steps=steps), json_output=False, console=con)
        output = sio.getvalue()
        # The brackets must appear as literal characters, not trigger a red colour span.
        assert "[red]x[/red]" in output

    # ------------------------------------------------------------------
    # histogram-only (--histogram flag): no header or density sections
    # ------------------------------------------------------------------

    def test_histogram_only_no_header_no_density(self) -> None:
        """When stat_header is None and density_vector is empty, only histogram renders."""
        details = StatisticDetails(
            stat_header=None,
            density_vector=[],
            histogram=[
                StatisticHistogramStep(
                    range_hi_key="50",
                    range_rows=25.0,
                    eq_rows=5.0,
                    distinct_range_rows=3.0,
                    avg_range_rows=8.0,
                ),
            ],
        )
        con, sio = _make_wide_console()
        render_statistic_details(details, json_output=False, console=con)
        output = sio.getvalue()
        assert "Stat Header" not in output
        assert "Density Vector" not in output
        assert "RANGE_HI_KEY" in output

    # ------------------------------------------------------------------
    # JSON regression: byte-for-byte identical to raw render() call
    # ------------------------------------------------------------------

    def test_json_output_identical_to_raw_render(self, capsys: pytest.CaptureFixture[str]) -> None:
        """JSON branch must produce output character-for-character identical to
        render(details.model_dump(by_alias=True, mode=\"json\"), json_output=True)."""
        details = _make_details(with_header=True, with_density=True)
        dumped = details.model_dump(by_alias=True, mode="json")

        # Capture render_statistic_details output
        render_statistic_details(details, json_output=True)
        captured_new = capsys.readouterr().out

        # Capture raw render() output with identical serialization
        render(dumped, json_output=True)
        captured_old = capsys.readouterr().out

        assert captured_new == captured_old

    def test_json_output_is_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        details = _make_details(with_header=True, with_density=True)
        render_statistic_details(details, json_output=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "histogram" in parsed
        assert isinstance(parsed["histogram"], list)
