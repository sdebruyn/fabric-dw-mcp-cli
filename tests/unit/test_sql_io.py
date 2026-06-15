"""Unit tests for fabric_dw.sql_io — Arrow conversion and format writers."""

from __future__ import annotations

import base64
import io
import json
import logging
import math
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_dw.sql_io import (
    OutputFormat,
    _disambiguate_columns,
    columns_rows_to_arrow,
    json_safe,
    write_arrow,
)

# ===========================================================================
# columns_rows_to_arrow
# ===========================================================================


class TestColumnsRowsToArrow:
    def test_empty_columns_returns_empty_table(self) -> None:
        result = columns_rows_to_arrow([], [])
        assert result.num_columns == 0
        assert result.num_rows == 0

    def test_no_rows_returns_table_with_columns(self) -> None:
        result = columns_rows_to_arrow(["id", "name"], [])
        assert result.num_columns == 2
        assert result.num_rows == 0
        assert result.column_names == ["id", "name"]

    def test_integer_column(self) -> None:
        result = columns_rows_to_arrow(["id"], [(1,), (2,), (3,)])
        assert result.num_rows == 3
        assert result.column("id")[0].as_py() == 1

    def test_string_column(self) -> None:
        result = columns_rows_to_arrow(["name"], [("Alice",), ("Bob",)])
        assert result.column("name")[0].as_py() == "Alice"

    def test_multiple_columns(self) -> None:
        result = columns_rows_to_arrow(["id", "name"], [(1, "Alice"), (2, "Bob")])
        assert result.num_columns == 2
        assert result.num_rows == 2

    def test_none_values_preserved(self) -> None:
        result = columns_rows_to_arrow(["val"], [(None,), (42,)])
        assert result.column("val")[0].as_py() is None
        assert result.column("val")[1].as_py() == 42

    def test_datetime_column(self) -> None:
        """datetime values are stored in Arrow as timestamp[us, tz=UTC] and round-trip exactly."""
        dt = datetime(2024, 1, 1, tzinfo=UTC)
        result = columns_rows_to_arrow(["ts"], [(dt,)])
        assert result.num_rows == 1
        stored = result.column("ts")[0].as_py()
        assert stored is not None
        # Normalise both to UTC for comparison (Arrow returns zoneinfo.ZoneInfo('UTC'))
        assert stored.replace(tzinfo=UTC) == dt

    def test_bytes_stored_as_binary(self) -> None:
        """bytes values are stored as Arrow binary — the value is preserved, not coerced."""
        raw = b"\x01\x02"
        result = columns_rows_to_arrow(["data"], [(raw,)])
        assert result.num_rows == 1
        stored = result.column("data")[0].as_py()
        # Arrow stores bytes as binary — the round-trip value must equal the original.
        assert stored == raw, f"Expected {raw!r}, got {stored!r}"

    def test_mixed_type_column_coerced_to_string(self) -> None:
        result = columns_rows_to_arrow(["val"], [(1,), ("two",), (3.0,)])
        assert result.num_rows == 3
        # Mixed column falls back to string; every value must be a string.
        col = result.column("val")
        assert col[0].as_py() == "1"
        assert col[1].as_py() == "two"
        assert col[2].as_py() == "3.0"

    def test_mixed_type_column_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="fabric_dw.sql_io"):
            columns_rows_to_arrow(["mixed_col"], [(1,), ("two",), (3.0,)])
        assert any("mixed_col" in msg for msg in caplog.messages)

    def test_preserves_column_order(self) -> None:
        result = columns_rows_to_arrow(["c", "a", "b"], [(1, 2, 3)])
        assert result.column_names == ["c", "a", "b"]

    def test_float_column(self) -> None:
        result = columns_rows_to_arrow(["amount"], [(1.5,), (2.75,)])
        assert result.column("amount")[1].as_py() == pytest.approx(2.75)

    def test_boolean_column(self) -> None:
        result = columns_rows_to_arrow(["flag"], [(True,), (False,)])
        assert result.column("flag")[0].as_py() is True

    def test_returns_arrow_table_type(self) -> None:
        result = columns_rows_to_arrow(["x"], [(1,)])
        assert isinstance(result, pa.Table)

    # D14 — duplicate column names must be preserved positionally
    def test_duplicate_column_names_all_columns_present(self) -> None:
        """columns=['id','id'] must produce 2 columns, not 1."""
        result = columns_rows_to_arrow(["id", "id"], [(1, 2), (3, 4)])
        assert result.num_columns == 2
        assert result.num_rows == 2

    def test_duplicate_column_names_values_not_merged(self) -> None:
        """Values from the two 'id' columns must remain in separate columns."""
        result = columns_rows_to_arrow(["id", "id"], [(1, 2), (3, 4)])
        # column(0) == first 'id'; column(1) == second 'id'
        assert result.column(0).to_pylist() == [1, 3]
        assert result.column(1).to_pylist() == [2, 4]

    def test_duplicate_column_names_schema_names(self) -> None:
        """Schema field names are disambiguated (first occurrence kept, later ones suffixed)."""
        result = columns_rows_to_arrow(["x", "x", "y"], [(1, 2, 3)])
        assert result.schema.names == ["x", "x_2", "y"]

    def test_row_length_mismatch_too_short_raises(self) -> None:
        """A row with fewer values than columns must raise ValueError."""
        with pytest.raises(ValueError, match="Row 0 has 1 value"):
            columns_rows_to_arrow(["a", "b"], [(1,)])

    def test_row_length_mismatch_too_long_raises(self) -> None:
        """A row with more values than columns must raise ValueError."""
        with pytest.raises(ValueError, match="Row 1 has 3 value"):
            columns_rows_to_arrow(["a", "b"], [(1, 2), (1, 2, 3)])

    def test_duplicate_column_names_disambiguated_in_schema(self) -> None:
        """columns=['id','id'] must produce unique schema names ['id','id_2']."""
        result = columns_rows_to_arrow(["id", "id"], [(1, 2), (3, 4)])
        assert result.schema.names == ["id", "id_2"]

    def test_duplicate_column_names_triple_disambiguated(self) -> None:
        """Three identical column names become ['id', 'id_2', 'id_3']."""
        result = columns_rows_to_arrow(["id", "id", "id"], [(1, 2, 3)])
        assert result.schema.names == ["id", "id_2", "id_3"]


# ===========================================================================
# _disambiguate_columns
# ===========================================================================


class TestDisambiguateColumns:
    def test_no_duplicates_unchanged(self) -> None:
        assert _disambiguate_columns(["a", "b", "c"]) == ["a", "b", "c"]

    def test_empty_list(self) -> None:
        assert _disambiguate_columns([]) == []

    def test_single_duplicate(self) -> None:
        assert _disambiguate_columns(["id", "id"]) == ["id", "id_2"]

    def test_triple_duplicate(self) -> None:
        assert _disambiguate_columns(["id", "id", "id"]) == ["id", "id_2", "id_3"]

    def test_collision_with_existing_name(self) -> None:
        """'id', 'id_2', 'id' — third becomes 'id_3' since 'id_2' is taken."""
        assert _disambiguate_columns(["id", "id_2", "id"]) == ["id", "id_2", "id_3"]

    def test_mixed_names(self) -> None:
        result = _disambiguate_columns(["x", "x", "y"])
        assert result == ["x", "x_2", "y"]


# ===========================================================================
# write_arrow — JSON format
# ===========================================================================


class TestWriteArrowJson:
    def test_writes_json_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        table = columns_rows_to_arrow(["id", "name"], [(1, "Alice"), (2, "Bob")])
        write_arrow(table, OutputFormat.JSON)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert len(parsed) == 2
        assert parsed[0]["id"] == 1
        assert parsed[0]["name"] == "Alice"

    def test_json_handles_none(self, capsys: pytest.CaptureFixture[str]) -> None:
        table = columns_rows_to_arrow(["val"], [(None,)])
        write_arrow(table, OutputFormat.JSON)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed[0]["val"] is None

    def test_json_writes_to_file(self, tmp_path: Path) -> None:
        table = columns_rows_to_arrow(["x"], [(42,)])
        out_file = tmp_path / "out.json"
        write_arrow(table, OutputFormat.JSON, output=out_file)
        parsed = json.loads(out_file.read_text(encoding="utf-8"))
        assert parsed[0]["x"] == 42

    def test_json_empty_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        table = columns_rows_to_arrow(["id"], [])
        write_arrow(table, OutputFormat.JSON)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == []

    def test_json_bytes_serialised_as_base64(self, capsys: pytest.CaptureFixture[str]) -> None:
        raw = b"\xde\xad"
        table = pa.table({"data": pa.array([raw], type=pa.large_binary())})
        write_arrow(table, OutputFormat.JSON)
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed[0]["data"] == base64.b64encode(raw).decode("ascii")

    def test_json_writes_to_out_stream(self) -> None:
        table = columns_rows_to_arrow(["id"], [(1,)])
        buf = io.StringIO()
        write_arrow(table, OutputFormat.JSON, out=buf)
        parsed = json.loads(buf.getvalue())
        assert parsed[0]["id"] == 1

    def test_json_output_takes_priority_over_out_stream(self, tmp_path: Path) -> None:
        """When both output (file) and out (stream) are supplied for JSON format,
        output wins: the file is written and the stream is left untouched."""
        table = columns_rows_to_arrow(["id"], [(99,)])
        out_file = tmp_path / "out.json"
        buf = io.StringIO()
        write_arrow(table, OutputFormat.JSON, output=out_file, out=buf)
        # File must contain the payload
        parsed = json.loads(out_file.read_text(encoding="utf-8"))
        assert parsed[0]["id"] == 99
        # Stream must not have been written to
        assert buf.getvalue() == ""


# ===========================================================================
# write_arrow — CSV format
# ===========================================================================


class TestWriteArrowCsv:
    def test_writes_csv_to_file(self, tmp_path: Path) -> None:
        table = columns_rows_to_arrow(["id", "name"], [(1, "Alice"), (2, "Bob")])
        out_file = tmp_path / "out.csv"
        write_arrow(table, OutputFormat.CSV, output=out_file)
        content = out_file.read_text(encoding="utf-8")
        assert "id" in content
        assert "Alice" in content

    def test_csv_requires_output_path(self) -> None:
        table = columns_rows_to_arrow(["x"], [(1,)])
        with pytest.raises(ValueError, match="--output PATH is required"):
            write_arrow(table, OutputFormat.CSV)

    def test_csv_header_present(self, tmp_path: Path) -> None:
        table = columns_rows_to_arrow(["col_a", "col_b"], [(10, 20)])
        out_file = tmp_path / "out.csv"
        write_arrow(table, OutputFormat.CSV, output=out_file)
        header = out_file.read_text(encoding="utf-8").splitlines()[0]
        assert "col_a" in header
        assert "col_b" in header


# ===========================================================================
# write_arrow — Parquet format
# ===========================================================================


class TestWriteArrowParquet:
    def test_writes_parquet_to_file(self, tmp_path: Path) -> None:
        table = columns_rows_to_arrow(["id", "name"], [(1, "Alice")])
        out_file = tmp_path / "out.parquet"
        write_arrow(table, OutputFormat.PARQUET, output=out_file)
        result = pq.read_table(str(out_file))
        assert result.num_rows == 1

    def test_parquet_requires_output_path(self) -> None:
        table = columns_rows_to_arrow(["x"], [(1,)])
        with pytest.raises(ValueError, match="--output PATH is required"):
            write_arrow(table, OutputFormat.PARQUET)

    def test_parquet_roundtrip(self, tmp_path: Path) -> None:
        table = columns_rows_to_arrow(["id", "value"], [(1, 100), (2, 200)])
        out_file = tmp_path / "round.parquet"
        write_arrow(table, OutputFormat.PARQUET, output=out_file)
        result = pq.read_table(str(out_file))
        assert result.num_rows == 2
        assert result.column_names == ["id", "value"]


# ===========================================================================
# write_arrow — unknown format
# ===========================================================================


class TestWriteArrowUnknownFormat:
    def test_unknown_format_raises(self) -> None:
        table = columns_rows_to_arrow(["x"], [(1,)])
        with pytest.raises(ValueError, match="Unknown output format"):
            write_arrow(table, "xlsx")


# ===========================================================================
# write_arrow — duplicate column names end-to-end
# ===========================================================================


class TestWriteArrowDuplicateColumns:
    """End-to-end tests: write_arrow with duplicate source columns must preserve
    ALL column data across every output format."""

    def test_json_all_values_preserved(self) -> None:
        """Both duplicate columns survive in JSON output as disambiguated keys."""
        table = columns_rows_to_arrow(["id", "id"], [(1, 2), (3, 4)])
        buf = io.StringIO()
        write_arrow(table, OutputFormat.JSON, out=buf)
        parsed = json.loads(buf.getvalue())
        assert len(parsed) == 2
        # First 'id' column: values 1, 3
        assert parsed[0]["id"] == 1
        assert parsed[1]["id"] == 3
        # Second 'id' column disambiguated to 'id_2': values 2, 4
        assert parsed[0]["id_2"] == 2
        assert parsed[1]["id_2"] == 4

    def test_json_triple_duplicate_all_values_preserved(self) -> None:
        """Three duplicate columns produce three distinct JSON keys with all values."""
        table = columns_rows_to_arrow(["v", "v", "v"], [(10, 20, 30)])
        buf = io.StringIO()
        write_arrow(table, OutputFormat.JSON, out=buf)
        parsed = json.loads(buf.getvalue())
        assert parsed[0]["v"] == 10
        assert parsed[0]["v_2"] == 20
        assert parsed[0]["v_3"] == 30

    def test_parquet_roundtrip_with_duplicate_columns(self, tmp_path: Path) -> None:
        """Parquet file written from a duplicate-column table must be readable."""
        table = columns_rows_to_arrow(["id", "id"], [(1, 2), (3, 4)])
        out_file = tmp_path / "dup.parquet"
        write_arrow(table, OutputFormat.PARQUET, output=out_file)
        # Must not raise ArrowInvalid: Can't unify schema with duplicate field names
        result = pq.read_table(str(out_file))
        assert result.num_rows == 2
        assert result.column_names == ["id", "id_2"]
        assert result.column("id").to_pylist() == [1, 3]
        assert result.column("id_2").to_pylist() == [2, 4]

    def test_csv_all_columns_present_with_duplicate_source(self, tmp_path: Path) -> None:
        """CSV output must include both disambiguated headers and all values."""
        table = columns_rows_to_arrow(["id", "id"], [(1, 2), (3, 4)])
        out_file = tmp_path / "dup.csv"
        write_arrow(table, OutputFormat.CSV, output=out_file)
        content = out_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        # Header line must contain both unique column names
        assert "id" in lines[0]
        assert "id_2" in lines[0]
        # Data rows must contain both values from each original row
        assert "1" in lines[1]
        assert "2" in lines[1]
        assert "3" in lines[2]
        assert "4" in lines[2]


# ===========================================================================
# json_safe — binary encoding contract
# ===========================================================================


class TestJsonSafe:
    def test_none_returns_none(self) -> None:
        assert json_safe(None) is None

    def test_bool_passthrough(self) -> None:
        val = True
        assert json_safe(val) is True

    def test_int_passthrough(self) -> None:
        assert json_safe(42) == 42

    def test_float_passthrough(self) -> None:
        assert json_safe(3.14) == pytest.approx(3.14)

    def test_str_passthrough(self) -> None:
        assert json_safe("hello") == "hello"

    def test_bytes_base64_encoded(self) -> None:
        raw = b"\xde\xad\xbe\xef"
        result = json_safe(raw)
        assert result == base64.b64encode(raw).decode("ascii")

    def test_bytearray_base64_encoded(self) -> None:
        raw = bytearray(b"\x01\x02\x03")
        result = json_safe(raw)
        assert result == base64.b64encode(raw).decode("ascii")

    def test_memoryview_base64_encoded(self) -> None:
        raw = memoryview(b"\xff\x00")
        result = json_safe(raw)
        assert result == base64.b64encode(raw).decode("ascii")

    def test_other_type_stringified(self) -> None:
        assert json_safe(Decimal("3.14")) == "3.14"

    # D15 — nan/inf must become None (not leaked as non-JSON NaN/Infinity)
    def test_nan_becomes_none(self) -> None:
        assert json_safe(math.nan) is None

    def test_inf_becomes_none(self) -> None:
        assert json_safe(math.inf) is None

    def test_neg_inf_becomes_none(self) -> None:
        assert json_safe(-math.inf) is None

    def test_finite_float_unchanged(self) -> None:
        assert json_safe(1.5) == pytest.approx(1.5)

    def test_nan_produces_valid_json(self) -> None:
        """End-to-end: a table with NaN values must round-trip through json.loads."""
        table = columns_rows_to_arrow(["v"], [(math.nan,), (1.0,)])
        buf = io.StringIO()
        write_arrow(table, OutputFormat.JSON, out=buf)
        parsed = json.loads(buf.getvalue())
        assert parsed[0]["v"] is None
        assert parsed[1]["v"] == pytest.approx(1.0)


# ===========================================================================
# OutputFormat — StrEnum contract
# ===========================================================================


class TestOutputFormat:
    def test_is_str_enum(self) -> None:
        assert issubclass(OutputFormat, StrEnum)

    def test_values(self) -> None:
        assert OutputFormat.JSON == "json"
        assert OutputFormat.CSV == "csv"
        assert OutputFormat.PARQUET == "parquet"

    def test_iterable(self) -> None:
        values = [f.value for f in OutputFormat]
        assert set(values) == {"json", "csv", "parquet"}

    def test_string_comparison(self) -> None:
        assert OutputFormat.JSON == "json"
        assert "parquet" == OutputFormat.PARQUET  # noqa: SIM300
