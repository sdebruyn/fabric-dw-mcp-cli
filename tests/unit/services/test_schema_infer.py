"""Unit tests for fabric_dw.services.schema_infer.

Confirms that the inference helpers are importable from their new home and
produce correct results.  Behaviour is identical to what was tested via
tables.py before the extraction; this file pins the new import path.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fabric_dw.services.schema_infer import (
    infer_columns_from_csv,
    infer_columns_from_json,
    infer_columns_from_parquet,
)


class TestInferColumnsFromParquet:
    async def test_importable_from_schema_infer(self, tmp_path: Path) -> None:
        """infer_columns_from_parquet is importable from schema_infer."""
        table = pa.table({"id": pa.array([1], type=pa.int64()), "name": pa.array(["a"])})
        pq_path = tmp_path / "data.parquet"
        pq.write_table(table, str(pq_path))

        cols = await infer_columns_from_parquet(pq_path)
        by_name = {c.name: c.sql_type for c in cols}
        assert by_name["id"] == "BIGINT"
        assert "VARCHAR" in by_name["name"]

    async def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await infer_columns_from_parquet(tmp_path / "nonexistent.parquet")


class TestInferColumnsFromCsv:
    async def test_importable_from_schema_infer(self, tmp_path: Path) -> None:
        """infer_columns_from_csv is importable from schema_infer."""
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("id,name\n1,Alice\n2,Bob\n")

        cols = await infer_columns_from_csv(csv_path)
        by_name = {c.name: c.sql_type for c in cols}
        assert by_name["id"] == "BIGINT"
        assert "VARCHAR" in by_name["name"]
        assert all(c.nullable for c in cols)

    async def test_all_varchar(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("id,value\n1,hello\n")

        cols = await infer_columns_from_csv(csv_path, all_varchar=True)
        assert all("VARCHAR" in c.sql_type for c in cols)

    async def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await infer_columns_from_csv(tmp_path / "nonexistent.csv")


class TestInferColumnsFromJson:
    async def test_importable_from_schema_infer(self, tmp_path: Path) -> None:
        """infer_columns_from_json is importable from schema_infer."""
        json_file = tmp_path / "data.jsonl"
        json_file.write_text(
            '{"id": 1, "name": "Alice", "price": 1.5, "ok": true}\n'
            '{"id": 2, "name": "Bob", "price": 2.0, "ok": false}\n'
        )
        cols = await infer_columns_from_json(json_file)
        by_name = {c.name: c.sql_type for c in cols}
        assert by_name["id"] == "BIGINT"
        assert by_name["price"] == "FLOAT"
        assert by_name["ok"] == "BIT"
        assert "VARCHAR" in by_name["name"]
        assert all(c.nullable for c in cols)

    async def test_json_array_form(self, tmp_path: Path) -> None:
        json_file = tmp_path / "data.json"
        json_file.write_text('[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]')
        cols = await infer_columns_from_json(json_file)
        by_name = {c.name: c.sql_type for c in cols}
        assert by_name["id"] == "BIGINT"
        assert "VARCHAR" in by_name["name"]

    async def test_all_varchar(self, tmp_path: Path) -> None:
        json_file = tmp_path / "data.jsonl"
        json_file.write_text('{"id": 1, "name": "Alice"}\n')
        cols = await infer_columns_from_json(json_file, all_varchar=True)
        assert all("VARCHAR" in c.sql_type for c in cols)

    async def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            await infer_columns_from_json(tmp_path / "nonexistent.jsonl")
