"""Unit tests for fabric_dw.types — Arrow→T-SQL type mapping."""

from __future__ import annotations

import pyarrow as pa
import pytest

from fabric_dw.types import arrow_type_to_tsql, validate_tsql_type

# ===========================================================================
# validate_tsql_type
# ===========================================================================


class TestValidateTsqlType:
    def test_simple_types_accepted(self) -> None:
        for t in ["INT", "BIGINT", "SMALLINT", "TINYINT", "BIT", "FLOAT", "REAL", "DATE"]:
            assert validate_tsql_type(t) == t

    def test_case_insensitive(self) -> None:
        assert validate_tsql_type("int") == "int"
        assert validate_tsql_type("Varchar(255)") == "Varchar(255)"

    def test_parameterised_types_accepted(self) -> None:
        assert validate_tsql_type("VARCHAR(255)") == "VARCHAR(255)"
        assert validate_tsql_type("DECIMAL(18,4)") == "DECIMAL(18,4)"
        assert validate_tsql_type("VARBINARY(8000)") == "VARBINARY(8000)"
        assert validate_tsql_type("DATETIME2(7)") == "DATETIME2(7)"
        assert validate_tsql_type("TIME(7)") == "TIME(7)"
        assert validate_tsql_type("NVARCHAR(100)") == "NVARCHAR(100)"

    def test_injection_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported or unsafe"):
            validate_tsql_type("INT; DROP TABLE users--")

    def test_text_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported or unsafe"):
            validate_tsql_type("TEXT")

    def test_xml_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported or unsafe"):
            validate_tsql_type("XML")

    def test_money_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported or unsafe"):
            validate_tsql_type("MONEY")

    def test_image_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported or unsafe"):
            validate_tsql_type("IMAGE")

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported or unsafe"):
            validate_tsql_type("")

    def test_whitespace_stripped(self) -> None:
        assert validate_tsql_type("  INT  ") == "INT"


# ===========================================================================
# arrow_type_to_tsql — supported types
# ===========================================================================


class TestArrowTypeToTsqlSupported:
    """Each supported Arrow type maps to the correct T-SQL type."""

    # --- integers ---
    def test_int8_to_smallint(self) -> None:
        assert arrow_type_to_tsql(pa.int8()) == "SMALLINT"

    def test_int16_to_smallint(self) -> None:
        assert arrow_type_to_tsql(pa.int16()) == "SMALLINT"

    def test_uint8_to_smallint(self) -> None:
        assert arrow_type_to_tsql(pa.uint8()) == "SMALLINT"

    def test_int32_to_int(self) -> None:
        assert arrow_type_to_tsql(pa.int32()) == "INT"

    def test_uint16_to_int(self) -> None:
        assert arrow_type_to_tsql(pa.uint16()) == "INT"

    def test_int64_to_bigint(self) -> None:
        assert arrow_type_to_tsql(pa.int64()) == "BIGINT"

    def test_uint32_to_bigint(self) -> None:
        assert arrow_type_to_tsql(pa.uint32()) == "BIGINT"

    def test_uint64_to_bigint(self) -> None:
        assert arrow_type_to_tsql(pa.uint64()) == "BIGINT"

    # --- floats ---
    def test_float16_to_real(self) -> None:
        assert arrow_type_to_tsql(pa.float16()) == "REAL"

    def test_float32_to_real(self) -> None:
        assert arrow_type_to_tsql(pa.float32()) == "REAL"

    def test_float64_to_float(self) -> None:
        assert arrow_type_to_tsql(pa.float64()) == "FLOAT"

    # --- boolean ---
    def test_bool_to_bit(self) -> None:
        assert arrow_type_to_tsql(pa.bool_()) == "BIT"

    # --- decimal ---
    def test_decimal128_to_decimal(self) -> None:
        assert arrow_type_to_tsql(pa.decimal128(18, 4)) == "DECIMAL(18,4)"

    def test_decimal128_zero_scale(self) -> None:
        assert arrow_type_to_tsql(pa.decimal128(10, 0)) == "DECIMAL(10,0)"

    # --- date ---
    def test_date32_to_date(self) -> None:
        assert arrow_type_to_tsql(pa.date32()) == "DATE"

    def test_date64_to_date(self) -> None:
        assert arrow_type_to_tsql(pa.date64()) == "DATE"

    # --- time ---
    def test_time32_to_time(self) -> None:
        assert arrow_type_to_tsql(pa.time32("s")) == "TIME(7)"

    def test_time64_to_time(self) -> None:
        assert arrow_type_to_tsql(pa.time64("us")) == "TIME(7)"

    # --- timestamp ---
    def test_timestamp_to_datetime2(self) -> None:
        assert arrow_type_to_tsql(pa.timestamp("us")) == "DATETIME2(7)"

    def test_timestamp_tz_to_datetime2(self) -> None:
        assert arrow_type_to_tsql(pa.timestamp("us", tz="UTC")) == "DATETIME2(7)"

    # --- duration ---
    def test_duration_to_bigint(self) -> None:
        assert arrow_type_to_tsql(pa.duration("s")) == "BIGINT"

    # --- strings ---
    def test_string_to_varchar_default(self) -> None:
        assert arrow_type_to_tsql(pa.string()) == "VARCHAR(8000)"

    def test_large_string_to_varchar_default(self) -> None:
        assert arrow_type_to_tsql(pa.large_string()) == "VARCHAR(8000)"

    def test_string_to_varchar_custom_length(self) -> None:
        assert arrow_type_to_tsql(pa.string(), varchar_length=255) == "VARCHAR(255)"

    # --- binary ---
    def test_binary_to_varbinary(self) -> None:
        assert arrow_type_to_tsql(pa.binary()) == "VARBINARY(8000)"

    def test_large_binary_to_varbinary(self) -> None:
        assert arrow_type_to_tsql(pa.large_binary()) == "VARBINARY(8000)"

    # --- null ---
    def test_null_to_varchar1(self) -> None:
        assert arrow_type_to_tsql(pa.null()) == "VARCHAR(1)"

    # --- dictionary (categorical) ---
    def test_dict_string_values_to_varchar(self) -> None:
        dict_type = pa.dictionary(pa.int8(), pa.string())
        assert arrow_type_to_tsql(dict_type) == "VARCHAR(8000)"

    def test_dict_int32_values_to_int(self) -> None:
        dict_type = pa.dictionary(pa.int8(), pa.int32())
        assert arrow_type_to_tsql(dict_type) == "INT"


# ===========================================================================
# arrow_type_to_tsql — unsupported types
# ===========================================================================


class TestArrowTypeToTsqlUnsupported:
    """Unsupported Arrow types raise ValueError naming the column."""

    def test_list_type_raises(self) -> None:
        with pytest.raises(ValueError, match="col_a"):
            arrow_type_to_tsql(pa.list_(pa.int32()), "col_a")

    def test_struct_type_raises(self) -> None:
        struct_type = pa.struct([pa.field("x", pa.int32())])
        with pytest.raises(ValueError, match="col_b"):
            arrow_type_to_tsql(struct_type, "col_b")

    def test_map_type_raises(self) -> None:
        map_type = pa.map_(pa.string(), pa.int32())
        with pytest.raises(ValueError, match="col_c"):
            arrow_type_to_tsql(map_type, "col_c")

    def test_error_mentions_all_varchar_hint(self) -> None:
        with pytest.raises(ValueError, match="all-varchar"):
            arrow_type_to_tsql(pa.list_(pa.int32()), "bad_col")

    def test_fixed_size_list_raises(self) -> None:
        with pytest.raises(ValueError, match=r"(?i)nested"):
            arrow_type_to_tsql(pa.list_(pa.float32(), 3), "vec")
