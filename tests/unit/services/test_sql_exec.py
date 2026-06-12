"""Tests for services.sql_exec — generic SQL execution (TDD)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from fabric_dw.exceptions import AuthError, PermissionDenied
from fabric_dw.models import SqlResult
from fabric_dw.services import sql_exec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_target() -> MagicMock:
    return MagicMock()


def _make_conn(
    rows: list[tuple[object, ...]],
    columns: list[str],
    *,
    rowcount: int = -1,
) -> MagicMock:
    cursor = MagicMock()
    cursor.description = [(c, None) for c in columns] if columns else None
    cursor.fetchall.return_value = rows
    cursor.rowcount = rowcount
    # nextset() returns False by default — single result set
    cursor.nextset.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def _make_no_result_conn(*, rowcount: int = 1) -> MagicMock:
    """Connection whose cursor has no description (DDL/DML)."""
    cursor = MagicMock()
    cursor.description = None
    cursor.rowcount = rowcount
    cursor.nextset.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# SELECT — basic result set
# ---------------------------------------------------------------------------


async def test_execute_select_returns_sql_result() -> None:
    target = _make_target()
    conn = _make_conn([(1, "hello"), (2, "world")], ["id", "name"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT id, name FROM t")

    assert isinstance(result, SqlResult)


async def test_execute_select_columns_and_rows() -> None:
    target = _make_target()
    conn = _make_conn([(42, "foo")], ["col_a", "col_b"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT col_a, col_b FROM t")

    assert result.columns == ["col_a", "col_b"]
    assert result.rows == [[42, "foo"]]


async def test_execute_select_empty_rows() -> None:
    target = _make_target()
    conn = _make_conn([], ["col_a", "col_b"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT col_a FROM t WHERE 1=0")

    assert result.columns == ["col_a", "col_b"]
    assert result.rows == []


async def test_execute_select_rowcount_falls_back_to_len_rows() -> None:
    """When driver returns rowcount=-1 for SELECT, we use len(rows)."""
    target = _make_target()
    conn = _make_conn([(1,), (2,), (3,)], ["id"], rowcount=-1)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT id FROM t")

    assert result.rowcount == 3


async def test_execute_select_rowcount_from_driver_when_positive() -> None:
    """When driver returns a positive rowcount, use it directly."""
    target = _make_target()
    conn = _make_conn([(1,)], ["id"], rowcount=10)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT id FROM t")

    assert result.rowcount == 10


# ---------------------------------------------------------------------------
# INSERT / DML — no result set
# ---------------------------------------------------------------------------


async def test_execute_insert_no_rows_returns_empty() -> None:
    target = _make_target()
    conn = _make_no_result_conn(rowcount=3)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "INSERT INTO t VALUES (1)")

    assert result.columns == []
    assert result.rows == []
    assert result.rowcount == 3


async def test_execute_dml_closes_connection() -> None:
    target = _make_target()
    conn = _make_no_result_conn()

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await sql_exec.execute(target, "DELETE FROM t")

    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# DDL — no result set
# ---------------------------------------------------------------------------


async def test_execute_alter_no_rows_returns_empty() -> None:
    target = _make_target()
    conn = _make_no_result_conn(rowcount=0)

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "ALTER TABLE t ADD col INT")

    assert result.columns == []
    assert result.rows == []


# ---------------------------------------------------------------------------
# Serialisation: datetime, Decimal, bytes
# ---------------------------------------------------------------------------


async def test_execute_datetime_column_serialised_to_iso() -> None:
    dt = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    target = _make_target()
    conn = _make_conn([(dt,)], ["ts"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT ts FROM t")

    assert result.rows[0][0] == dt.isoformat()


async def test_execute_decimal_column_serialised_to_string() -> None:
    target = _make_target()
    conn = _make_conn([(Decimal("3.14"),)], ["price"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT price FROM t")

    assert result.rows[0][0] == "3.14"


async def test_execute_bytes_column_base64_encoded() -> None:
    raw = b"\x00\x01\x02\x03"
    target = _make_target()
    conn = _make_conn([(raw,)], ["data"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT data FROM t")

    expected_b64 = base64.b64encode(raw).decode("ascii")
    assert result.rows[0][0] == expected_b64


async def test_execute_bytes_column_name_gets_base64_suffix() -> None:
    target = _make_target()
    conn = _make_conn([(b"\xff",)], ["hash_val"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT hash_val FROM t")

    assert result.columns == ["hash_val__base64"]


async def test_execute_non_binary_column_name_unchanged() -> None:
    target = _make_target()
    conn = _make_conn([(42,)], ["score"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT score FROM t")

    assert result.columns == ["score"]


# ---------------------------------------------------------------------------
# Syntax error / permission errors
# ---------------------------------------------------------------------------


async def test_execute_syntax_error_propagates() -> None:
    """Non-mapped driver errors are raised as-is."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Incorrect syntax near 'SLECT'")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(Exception, match="Incorrect syntax"),
    ):
        await sql_exec.execute(target, "SLECT 1")


async def test_execute_permission_denied_raises_permission_denied() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object SensitiveTable")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDenied),
    ):
        await sql_exec.execute(target, "SELECT * FROM SensitiveTable")


async def test_execute_permission_denied_message_contains_hint() -> None:
    """PermissionDenied message must mention a documentation hint."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object X")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDenied, match="Hint"),
    ):
        await sql_exec.execute(target, "SELECT * FROM X")


async def test_execute_auth_error_raises_auth_error() -> None:
    """Authentication failures (expired/missing token) are re-raised as AuthError."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Authentication failed for user ''")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(AuthError),
    ):
        await sql_exec.execute(target, "SELECT 1")


async def test_execute_perm_denied_driver_raises_permission_denied() -> None:
    """SQL permission-denial errors are re-raised as PermissionDenied."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object SensitiveTable")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDenied),
    ):
        await sql_exec.execute(target, "SELECT * FROM SensitiveTable")


# ---------------------------------------------------------------------------
# Multi-statement: nextset() — last result set returned
# ---------------------------------------------------------------------------


async def test_execute_multi_statement_returns_last_result_set() -> None:
    """When the cursor has multiple result sets, the last one is returned."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = -1

    # First call to nextset() → True (advance to result set 2)
    # Second call to nextset() → False (no more result sets)
    cursor.nextset.side_effect = [True, False]

    # After advancing, description and fetchall reflect the second result set.
    cursor.description = [("last_col", None)]
    cursor.fetchall.return_value = [("last_value",)]

    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT 1; SELECT 'last_value' AS last_col")

    assert result.columns == ["last_col"]
    assert result.rows == [["last_value"]]


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


async def test_execute_closes_connection_after_success() -> None:
    target = _make_target()
    conn = _make_conn([(1,)], ["n"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await sql_exec.execute(target, "SELECT 1 AS n")

    conn.close.assert_called_once()


async def test_execute_closes_connection_on_error() -> None:
    """Connection must be closed even when cursor.execute raises."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("boom")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(Exception, match="boom"),
    ):
        await sql_exec.execute(target, "SELECT 1")

    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Binary detection: cursor.description type codes + all-row fallback
# ---------------------------------------------------------------------------


async def test_binary_null_first_row_detected_via_later_rows() -> None:
    """If the first row has NULL for a binary column, later rows with bytes
    must still cause the column to be tagged.  The old first-row-only heuristic
    would miss this; the all-row-scan fallback must catch it."""
    target = _make_target()
    # Column 0 is NULL in row 0 but bytes in row 1.
    conn = _make_conn([(None,), (b"\x01\x02",)], ["blob"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT blob FROM t")

    assert result.columns == ["blob__base64"]


async def test_binary_all_null_rows_not_tagged() -> None:
    """A column that is NULL in every row is not binary and must not be tagged."""
    target = _make_target()
    conn = _make_conn([(None,), (None,)], ["blob"])

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT blob FROM t")

    assert result.columns == ["blob"]


async def test_binary_type_code_in_description_tags_column() -> None:
    """When cursor.description carries a type_code whose __name__ contains
    'binary', the column is tagged without scanning any row."""
    target = _make_target()

    # Simulate a binary type object that the driver would expose.
    class _BinaryType:
        __name__ = "Binary"

    cursor = MagicMock()
    # description entry: (name, type_code, ...)
    cursor.description = [("data", _BinaryType(), None, None, None, None, None)]
    cursor.fetchall.return_value = [(None,)]  # first row is NULL
    cursor.nextset.return_value = False
    cursor.rowcount = -1

    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        result = await sql_exec.execute(target, "SELECT data FROM t")

    assert result.columns == ["data__base64"]
