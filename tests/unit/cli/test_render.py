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
