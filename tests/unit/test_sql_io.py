"""Unit tests for fabric_dw.sql_io — Arrow conversion and format writers."""

from __future__ import annotations

import base64
import io
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_dw.sql_io import OutputFormat, columns_rows_to_arrow, json_safe, write_arrow

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
        dt = datetime(2024, 1, 1, tzinfo=UTC)
        result = columns_rows_to_arrow(["ts"], [(dt,)])
        assert result.num_rows == 1

    def test_bytes_coerced_to_string(self) -> None:
        result = columns_rows_to_arrow(["data"], [(b"\x01\x02",)])
        assert result.num_rows == 1

    def test_mixed_type_column_coerced_to_string(self) -> None:
        result = columns_rows_to_arrow(["val"], [(1,), ("two",), (3.0,)])
        assert result.num_rows == 3

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
