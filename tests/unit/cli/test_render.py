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
    _is_guid_column,
    confirm,
    render,
    render_permissions_table,
    render_refresh_table,
)
from fabric_dw.models import (
    ItemAccess,
    ItemAccessDetail,
    ItemAccessPrincipal,
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

    ``sp_get_table_health_metrics`` returns column names such as
    ``FileRowCount[0]`` and ``FileRowCount[1,10)`` that include bracket
    characters.  Rich interprets ``[...]`` as markup tags, so without escaping
    those names are stripped and the column headers appear blank.

    Regression tests for issue #745.
    """

    def _render_to_string(self, data: object, *, width: int = 200) -> str:
        sio = StringIO()
        console = Console(file=sio, width=width, highlight=False, no_color=True)
        render(data, json_output=False, console=console, table_title="Table Health Metrics")
        return sio.getvalue()

    # ------------------------------------------------------------------
    # 1. Column names with brackets render verbatim (the core issue #745 bug)
    # ------------------------------------------------------------------

    def test_bracket_column_name_renders_verbatim(self) -> None:
        """Column headers containing ``[0]`` must appear in the output, not be stripped."""
        data = [
            {
                "FileRowCount[0]": 0,
                "FileRowCount[1,10)": 0,
                "PhysicalRowCount": 1000000,
            }
        ]
        output = self._render_to_string(data)
        assert "FileRowCount[0]" in output
        assert "FileRowCount[1,10)" in output

    def test_plain_column_name_still_renders(self) -> None:
        """Non-bracket column names must continue to render correctly after the fix."""
        data = [{"PhysicalRowCount": 1000000, "FileRowCount[0]": 0}]
        output = self._render_to_string(data)
        assert "PhysicalRowCount" in output

    # ------------------------------------------------------------------
    # 2. Cell values with brackets render verbatim
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
            {"FileRowCount[0]": None, "PhysicalRowCount": 1000000},
            {"FileRowCount[0]": 42, "PhysicalRowCount": 2000000},
        ]
        output = self._render_to_string(data)
        # The visible rendered text "NULL" must appear (dim styling applied by Rich)
        assert "NULL" in output
        # The raw tag must NOT appear literally (that would mean escaping went wrong)
        assert "[dim]NULL[/dim]" not in output


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
    ) -> str:
        sio = StringIO()
        console = Console(file=sio, width=width, highlight=False, no_color=True)
        render(data, json_output=False, console=console, table_title=table_title)
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

    def test_all_none_guid_col_stays_normal(self) -> None:
        """A column that is all-None is dropped entirely (existing behaviour)."""
        data: list[dict[str, object]] = [{"id": None, "displayName": "Alice"}]
        output = self._render_narrow(data, width=50)
        # Column "id" is all-null → dropped; "displayName" remains
        assert "Alice" in output
        assert "id" not in output

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
        """An all-null GUID column is pruned even when other GUID columns are present.

        This test exercises the primary/secondary-GUID code path: ``id`` is the
        primary GUID (non-null), ``workspaceId`` is a secondary non-null GUID, and
        ``capacityId`` is all-null and must be dropped.  After pruning, the table
        has two live GUID columns (primary + secondary) plus ``displayName``; the
        fix must ensure ``displayName`` is NOT starved to zero width at 80 cols.
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
        render(data, json_output=False, console=console)
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
