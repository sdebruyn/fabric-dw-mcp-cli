"""Tests for services.sql_exec — generic SQL execution (TDD)."""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

import fabric_dw.sql as _sql_module
from fabric_dw.exceptions import AuthError, FabricServerError, PermissionDeniedError
from fabric_dw.models import SqlResult
from fabric_dw.services import sql_exec
from fabric_dw.sql import SqlTarget
from tests.unit.services._helpers import (
    _FakeRow,
    _make_conn,
    _make_no_result_conn,
    _make_target,
)

# A real SqlTarget for tests that exercise the physical connect path.
_REAL_TARGET = SqlTarget(
    workspace_id="ws-test",
    database="db-test",
    connection_string="Server=myserver.database.fabric.microsoft.com",
)


# ---------------------------------------------------------------------------
# Patch helper: wrap a single connection mock as a _with_connect_retry return.
# sql_exec.execute now calls sql._with_connect_retry (returns a 4-tuple).
# ---------------------------------------------------------------------------


@contextmanager
def _patch_connect(conn: MagicMock) -> Iterator[None]:
    """Patch ``sql._with_connect_retry`` to return *conn* on the first attempt."""
    with patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)):
        yield


# ---------------------------------------------------------------------------
# SELECT — basic result set
# ---------------------------------------------------------------------------


async def test_execute_select_returns_sql_result() -> None:
    target = _make_target()
    conn = _make_conn([(1, "hello"), (2, "world")], ["id", "name"])

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT id, name FROM t")

    assert isinstance(result, SqlResult)


async def test_execute_select_columns_and_rows() -> None:
    target = _make_target()
    conn = _make_conn([(42, "foo")], ["col_a", "col_b"])

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT col_a, col_b FROM t")

    assert result.columns == ["col_a", "col_b"]
    assert result.rows == [[42, "foo"]]


async def test_execute_select_empty_rows() -> None:
    target = _make_target()
    conn = _make_conn([], ["col_a", "col_b"])

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT col_a FROM t WHERE 1=0")

    assert result.columns == ["col_a", "col_b"]
    assert result.rows == []


async def test_execute_select_rowcount_falls_back_to_len_rows() -> None:
    """When driver returns rowcount=-1 for SELECT, we use len(rows)."""
    target = _make_target()
    conn = _make_conn([(1,), (2,), (3,)], ["id"], rowcount=-1)

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT id FROM t")

    assert result.rowcount == 3


async def test_execute_select_rowcount_from_driver_when_positive() -> None:
    """When driver returns a positive rowcount, use it directly."""
    target = _make_target()
    conn = _make_conn([(1,)], ["id"], rowcount=10)

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT id FROM t")

    assert result.rowcount == 10


# ---------------------------------------------------------------------------
# INSERT / DML — no result set
# ---------------------------------------------------------------------------


async def test_execute_insert_no_rows_returns_empty() -> None:
    target = _make_target()
    conn = _make_no_result_conn(rowcount=3)

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "INSERT INTO t VALUES (1)")

    assert result.columns == []
    assert result.rows == []
    assert result.rowcount == 3


async def test_execute_dml_closes_connection() -> None:
    target = _make_target()
    conn = _make_no_result_conn()

    with _patch_connect(conn):
        await sql_exec.execute(target, "DELETE FROM t")

    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# DDL — no result set
# ---------------------------------------------------------------------------


async def test_execute_alter_no_rows_returns_empty() -> None:
    target = _make_target()
    conn = _make_no_result_conn(rowcount=0)

    with _patch_connect(conn):
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

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT ts FROM t")

    assert result.rows[0][0] == dt.isoformat()


async def test_execute_decimal_column_serialised_to_string() -> None:
    target = _make_target()
    conn = _make_conn([(Decimal("3.14"),)], ["price"])

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT price FROM t")

    assert result.rows[0][0] == "3.14"


async def test_execute_bytes_column_base64_encoded() -> None:
    raw = b"\x00\x01\x02\x03"
    target = _make_target()
    conn = _make_conn([(raw,)], ["data"])

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT data FROM t")

    expected_b64 = base64.b64encode(raw).decode("ascii")
    assert result.rows[0][0] == expected_b64


async def test_execute_bytes_column_name_gets_base64_suffix() -> None:
    target = _make_target()
    conn = _make_conn([(b"\xff",)], ["hash_val"])

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT hash_val FROM t")

    assert result.columns == ["hash_val__base64"]


async def test_execute_non_binary_column_name_unchanged() -> None:
    target = _make_target()
    conn = _make_conn([(42,)], ["score"])

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT score FROM t")

    assert result.columns == ["score"]


# ---------------------------------------------------------------------------
# Syntax error / permission errors
# ---------------------------------------------------------------------------


async def test_execute_non_driver_error_propagates() -> None:
    """Errors without a ddbc_error attribute (not driver SQL errors) are raised as-is."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Incorrect syntax near 'SLECT'")
    conn.cursor.return_value = cursor

    with _patch_connect(conn), pytest.raises(Exception, match="Incorrect syntax"):
        await sql_exec.execute(target, "SLECT 1")


async def test_execute_permission_denied_raises_permission_denied() -> None:
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object SensitiveTable")
    conn.cursor.return_value = cursor

    with _patch_connect(conn), pytest.raises(PermissionDeniedError):
        await sql_exec.execute(target, "SELECT * FROM SensitiveTable")


async def test_execute_permission_denied_message_contains_hint() -> None:
    """PermissionDeniedError message must mention a documentation hint."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object X")
    conn.cursor.return_value = cursor

    with _patch_connect(conn), pytest.raises(PermissionDeniedError, match="Hint"):
        await sql_exec.execute(target, "SELECT * FROM X")


async def test_execute_auth_error_raises_auth_error() -> None:
    """Authentication failures (expired/missing token) are re-raised as AuthError."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("Authentication failed for user ''")
    conn.cursor.return_value = cursor

    with _patch_connect(conn), pytest.raises(AuthError):
        await sql_exec.execute(target, "SELECT 1")


async def test_execute_perm_denied_driver_raises_permission_denied() -> None:
    """SQL permission-denial errors are re-raised as PermissionDeniedError."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object SensitiveTable")
    conn.cursor.return_value = cursor

    with _patch_connect(conn), pytest.raises(PermissionDeniedError):
        await sql_exec.execute(target, "SELECT * FROM SensitiveTable")


# ---------------------------------------------------------------------------
# Multi-statement: nextset() — last result set returned
# ---------------------------------------------------------------------------


async def test_execute_multi_statement_returns_last_result_set() -> None:
    """When the cursor has multiple result sets, the last one is returned.

    Uses a stateful cursor where description and fetchall genuinely change as
    nextset() advances.  This distinguishes the FIRST result set
    (first_col / first_value) from the LAST result set (last_col / last_value).

    The OLD buggy code called ``while nextset(): pass`` first and then read
    ``cursor.description``, returning whatever the cursor exposed after being
    advanced past all sets.  The static-description mock used previously would
    have made that buggy path pass (description was the same before and after
    advancing).  This stateful version fails under the old code because
    description becomes None after nextset() exhausts the sets.
    """
    target = _make_target()
    cursor = _make_stateful_cursor(
        [
            (["first_col"], [("first_value",)]),
            (["last_col"], [("last_value",)]),
        ]
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT 1; SELECT 'last_value' AS last_col")

    # Must be the LAST result set, not the first.
    assert result.columns == ["last_col"]
    assert result.rows == [["last_value"]]


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


async def test_execute_closes_connection_after_success() -> None:
    target = _make_target()
    conn = _make_conn([(1,)], ["n"])

    with _patch_connect(conn):
        await sql_exec.execute(target, "SELECT 1 AS n")

    conn.close.assert_called_once()


async def test_execute_closes_connection_on_error() -> None:
    """Connection must be closed even when cursor.execute raises."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("boom")
    conn.cursor.return_value = cursor

    with _patch_connect(conn), pytest.raises(Exception, match="boom"):
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

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT blob FROM t")

    assert result.columns == ["blob__base64"]


async def test_binary_all_null_rows_not_tagged() -> None:
    """A column that is NULL in every row is not binary and must not be tagged."""
    target = _make_target()
    conn = _make_conn([(None,), (None,)], ["blob"])

    with _patch_connect(conn):
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

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT data FROM t")

    assert result.columns == ["data__base64"]


# ---------------------------------------------------------------------------
# Regression: BUG 1 — single SELECT must return columns+rows (nextset fix)
# ---------------------------------------------------------------------------


def _make_stateful_cursor(
    result_sets: list[tuple[list[str], list[tuple[object, ...]]]],
) -> MagicMock:
    """Build a mock DB-API cursor that models multi-result-set behaviour.

    Each entry in *result_sets* is ``(column_names, rows)``.  After
    ``execute()`` the cursor starts on result_set 0; ``nextset()`` advances
    it and returns True until there are no more sets, then returns False and
    sets ``description = None`` (mirroring real DB-API behaviour).
    """
    cursor = MagicMock()
    cursor.rowcount = -1
    state = {"index": 0}

    def _update_state(idx: int) -> None:
        if idx < len(result_sets):
            cols, rows = result_sets[idx]
            cursor.description = [(c, None) for c in cols] if cols else None
            cursor.fetchall.return_value = rows
            cursor.fetchmany.side_effect = lambda n: rows[:n]
        else:
            cursor.description = None
            cursor.fetchall.return_value = []

    # Position on first result set immediately after execute().
    _update_state(0)

    def _nextset() -> bool:
        state["index"] += 1
        idx = state["index"]
        _update_state(idx)
        return idx < len(result_sets)

    cursor.nextset.side_effect = _nextset
    return cursor


async def test_single_select_returns_columns_and_rows_regression() -> None:
    """Regression: a single-result-set SELECT must return its columns and rows.

    Before the fix, nextset() was called first which advanced past the only
    result set, leaving description=None and fetchall returning [].
    """
    target = _make_target()
    cursor = _make_stateful_cursor(
        [
            (["hello"], [(1,)]),
        ]
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT 1 AS hello")

    assert result.columns == ["hello"]
    assert result.rows == [[1]]


async def test_multi_result_set_returns_last_set_stateful() -> None:
    """A multi-result-set batch returns only the LAST result set."""
    target = _make_target()
    cursor = _make_stateful_cursor(
        [
            (["first_col"], [("first_value",)]),
            (["last_col"], [("last_value",)]),
        ]
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        result = await sql_exec.execute(
            target,
            "SELECT 'first_value' AS first_col; SELECT 'last_value' AS last_col",
        )

    assert result.columns == ["last_col"]
    assert result.rows == [["last_value"]]


async def test_ddl_no_result_set_returns_empty_stateful() -> None:
    """A DDL statement with no result set returns empty columns and rows."""
    target = _make_target()
    # No result sets at all — cursor.description is None throughout.
    cursor = _make_stateful_cursor([])
    cursor.rowcount = 0
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "CREATE TABLE t (id INT)")

    assert result.columns == []
    assert result.rows == []


# ---------------------------------------------------------------------------
# T01: row_limit / truncation path
# ---------------------------------------------------------------------------


async def test_execute_row_limit_calls_fetchmany_plus_one() -> None:
    """When row_limit=N, fetchmany(N+1) must be called (not fetchall)."""
    target = _make_target()
    rows: list[tuple[object, ...]] = [(i,) for i in range(10)]
    cursor = _make_stateful_cursor([(["n"], rows)])
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        await sql_exec.execute(target, "SELECT n FROM t", row_limit=5)

    cursor.fetchmany.assert_called_once_with(6)  # row_limit + 1
    cursor.fetchall.assert_not_called()


async def test_execute_row_limit_zero_calls_fetchmany_one() -> None:
    """row_limit=0 → fetchmany(1) — the +1 sentinel still applies."""
    target = _make_target()
    cursor = _make_stateful_cursor([(["n"], [(1,)])])
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        await sql_exec.execute(target, "SELECT n FROM t", row_limit=0)

    cursor.fetchmany.assert_called_once_with(1)


async def test_execute_row_limit_one_calls_fetchmany_two() -> None:
    """row_limit=1 → fetchmany(2) so caller can detect if there is a second row."""
    target = _make_target()
    cursor = _make_stateful_cursor([(["n"], [(1,), (2,)])])
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        await sql_exec.execute(target, "SELECT n FROM t", row_limit=1)

    cursor.fetchmany.assert_called_once_with(2)


async def test_execute_row_limit_none_uses_fetchall() -> None:
    """Default row_limit=None must use fetchall, not fetchmany."""
    target = _make_target()
    cursor = _make_stateful_cursor([(["n"], [(1,), (2,), (3,)])])
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        await sql_exec.execute(target, "SELECT n FROM t")

    cursor.fetchall.assert_called_once()
    cursor.fetchmany.assert_not_called()


async def test_execute_row_limit_does_not_truncate_rows() -> None:
    """execute does NOT truncate the rows; it returns all rows the driver returned.

    The caller (e.g. the MCP tool) is responsible for truncation: it can detect
    overflow by checking len(rows) > row_limit.
    """
    target = _make_target()
    rows: list[tuple[object, ...]] = [(i,) for i in range(6)]  # 6 rows returned by driver
    cursor = _make_stateful_cursor([(["n"], rows)])
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT n FROM t", row_limit=5)

    # execute returns all 6 rows intact; it's the caller's job to slice at 5
    assert len(result.rows) == 6


async def test_execute_row_limit_rowcount_capped_at_row_limit() -> None:
    """rowcount fallback is capped at row_limit when the driver returns -1.

    When row_limit=N and the driver fetches N+1 rows (the truncation sentinel),
    the fallback rowcount must be min(N+1, N) = N, not N+1.  Reporting N+1
    would inflate rowcount by 1 compared to what the caller asked for.
    """
    target = _make_target()
    # Driver returns 6 rows (row_limit=5 → fetchmany(6) over-fetches by 1).
    rows: list[tuple[object, ...]] = [(i,) for i in range(6)]
    cursor = _make_stateful_cursor([(["n"], rows)])
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT n FROM t", row_limit=5)

    # rowcount must be capped at row_limit (5), not the sentinel count (6).
    assert result.rowcount == 5


async def test_execute_row_limit_rowcount_not_capped_when_driver_provides_it() -> None:
    """When the driver returns a positive rowcount, it is used as-is (no cap applied)."""
    target = _make_target()
    rows: list[tuple[object, ...]] = [(i,) for i in range(6)]
    cursor = _make_stateful_cursor([(["n"], rows)])
    cursor.rowcount = 6  # driver provides a positive count — use it directly
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT n FROM t", row_limit=5)

    # Driver-reported rowcount takes priority; cap only applies to the fallback.
    assert result.rowcount == 6


# ---------------------------------------------------------------------------
# T02: pool-discard on error
# ---------------------------------------------------------------------------


async def test_execute_does_not_mark_discard_on_error() -> None:
    """sql_exec.execute does NOT call mark_discard() after a cursor failure.

    This is a known gap: unlike run_query (which marks connections tainted on
    failure so they are not returned to the pool), sql_exec.execute uses
    closing() which only calls .close() without marking _discard=True.
    This test documents the current behaviour so that a future fix that adds
    mark_discard() to sql_exec.execute would be visible here.
    """
    from fabric_dw.sql import _PooledConnection  # noqa: PLC0415

    target = _make_target()
    underlying = MagicMock()
    key = ("ws-id-sentinel", "test-db", "default")
    pooled = _PooledConnection(underlying, key)

    cursor = MagicMock()
    cursor.execute.side_effect = Exception("boom")
    underlying.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql._with_connect_retry", return_value=(pooled, 0, 1, None)),
        pytest.raises(Exception, match="boom"),
    ):
        await sql_exec.execute(target, "SELECT 1")

    # The connection was closed via closing() but _discard was NOT set by execute.
    # This means if pool is enabled the connection would be returned to the pool.
    # Document: _discard must remain False (current behaviour).
    assert pooled._closed is True  # closing() did call close()
    # TODO: remove/invert this assertion once sql_exec.execute() is fixed to call
    #       mark_discard() on error (same pattern as run_query).  This assertion
    #       intentionally documents the *gap*, not the desired invariant — a future
    #       fix that adds mark_discard() will break this line, which is the signal
    #       that the gap has been closed and this test should be updated.
    assert pooled._discard is False  # execute does NOT mark tainted (known gap)


# ---------------------------------------------------------------------------
# T03: transient login-failed (18456) retry on connect via _with_connect_retry
#
# sql_exec.execute now delegates to sql._with_connect_retry so that transient
# auth-failed errors (SQL 18456 / "Could not login") on the connect/login path
# are retried silently, making the CLI survive Fabric warehouse warm-up.
# ---------------------------------------------------------------------------


def _make_fake_time(*, monotonic_values: list[float] | None = None) -> MagicMock:
    """Return a fake ``time`` module whose sleep is a no-op MagicMock.

    Args:
        monotonic_values: When provided, ``time.monotonic`` returns successive
            values from this list (enabling deterministic deadline testing).
            When ``None``, the real ``time.monotonic`` is used.
    """
    import time as _real_time  # noqa: PLC0415

    fake = MagicMock()
    fake.sleep = MagicMock()
    if monotonic_values is not None:
        values_iter = iter(monotonic_values)
        fake.monotonic = MagicMock(side_effect=lambda: next(values_iter))
    else:
        fake.monotonic = MagicMock(side_effect=_real_time.monotonic)
    return fake


class TestExecuteLoginRetry:
    """sql_exec.execute retries transient 18456 auth-failed on the connect path.

    The connect-phase retry is time-bounded (``_CONNECT_RETRY_TIMEOUT_S``, ~120 s).
    Tests that exercise the deadline use a fake monotonic clock so that the
    deadline check is exercised without real wall-clock delay.

    Fake-clock convention: ``time.monotonic()`` is called once at the start of
    ``_with_connect_retry`` to set the deadline, then once after each failed
    attempt to check whether the deadline has passed.  Supply
    ``1 + N`` values for ``N`` connect failures.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Disable pooling and replace time.sleep so retries are instant."""
        monkeypatch.setenv("FABRIC_CONN_POOLING", "0")
        _sql_module.reset_pool()
        # Default: real monotonic clock (deadline never fires in fast tests).
        monkeypatch.setattr(_sql_module, "time", _make_fake_time())

    @staticmethod
    def _make_auth_exc() -> AuthError:
        """Return a simulated authentication failure (what the connect path surfaces)."""
        return AuthError("Could not login because the authentication failed.")

    @staticmethod
    def _make_good_conn() -> MagicMock:
        """Return a mock connection that executes SELECT 1 successfully."""
        cursor = MagicMock()
        cursor.description = [("n", None)]
        cursor.fetchall.return_value = [(1,)]
        cursor.rowcount = 1
        cursor.nextset.return_value = False
        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn

    async def test_auth_failed_on_connect_retried_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """execute retries an 18456 on the connect path and returns the result.

        The first two connect attempts raise an auth-failed error; the third
        succeeds.  execute must return the result set without leaking any error
        to the caller (no exception, no stderr noise).  Two sleeps occur (one
        before each retry), both within the deadline.
        """
        # Provide a fake clock: t0=0 for deadline, then t=1.0 for each post-failure
        # check (both well within the 120 s budget).
        monkeypatch.setattr(_sql_module, "time", _make_fake_time(monotonic_values=[0.0, 1.0, 1.0]))

        auth_exc = self._make_auth_exc()
        good_conn = self._make_good_conn()

        mock_mssql = MagicMock()
        mock_mssql.connect.side_effect = [auth_exc, auth_exc, good_conn]
        monkeypatch.setattr(_sql_module, "_mssql", mock_mssql)

        result = await sql_exec.execute(_REAL_TARGET, "SELECT 1 AS n")

        assert result.columns == ["n"]
        assert result.rows == [[1]]
        # Three physical connect attempts: 2 failures + 1 success.
        assert mock_mssql.connect.call_count == 3
        # time.sleep called twice (before attempt 2 and 3).
        fake_time = _sql_module.time  # type: ignore[attr-defined]
        assert fake_time.sleep.call_count == 2  # ty: ignore[unresolved-attribute]

    async def test_auth_failed_persistent_propagates_after_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A genuinely wrong credential propagates once the ~120 s deadline expires.

        The fake clock jumps past the deadline immediately after the first
        failure, so the loop raises after exactly 1 connect attempt (no sleep).
        The last retryable exception escapes from _with_connect_retry — downstream
        code (map_driver_error) would wrap it in AuthError.
        """
        timeout = _sql_module._CONNECT_RETRY_TIMEOUT_S
        # call 0: t0=0 → deadline = 0 + timeout
        # call 1: past deadline → raise
        monkeypatch.setattr(
            _sql_module,
            "time",
            _make_fake_time(monotonic_values=[0.0, timeout + 1.0]),
        )

        auth_exc = self._make_auth_exc()

        mock_mssql = MagicMock()
        mock_mssql.connect.side_effect = auth_exc
        monkeypatch.setattr(_sql_module, "_mssql", mock_mssql)

        with pytest.raises(AuthError, match="authentication failed"):
            await sql_exec.execute(_REAL_TARGET, "SELECT 1")

        # Deadline fires after the first attempt — only 1 connect call.
        assert mock_mssql.connect.call_count == 1
        fake_time = _sql_module.time  # type: ignore[attr-defined]
        # No sleep: the loop raises before sleeping when deadline is already exceeded.
        assert fake_time.sleep.call_count == 0  # ty: ignore[unresolved-attribute]

    async def test_non_auth_error_on_connect_propagates_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-auth, non-transient connect error is NOT retried.

        Syntax errors or other non-retryable failures on the connect phase
        must propagate immediately without any sleep or retry.
        """
        non_retryable = Exception("Some unexpected driver initialisation error")

        mock_mssql = MagicMock()
        mock_mssql.connect.side_effect = non_retryable
        monkeypatch.setattr(_sql_module, "_mssql", mock_mssql)

        with pytest.raises(Exception, match="unexpected driver"):
            await sql_exec.execute(_REAL_TARGET, "SELECT 1")

        # Exactly one connect attempt — no retry.
        assert mock_mssql.connect.call_count == 1
        fake_time = _sql_module.time  # type: ignore[attr-defined]
        # No sleep between attempts.
        assert fake_time.sleep.call_count == 0  # ty: ignore[unresolved-attribute]

    async def test_sleep_not_called_on_first_attempt_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the first connect attempt succeeds, time.sleep is never called.

        This guarantees that happy-path execution has zero delay overhead.
        """
        good_conn = self._make_good_conn()

        mock_mssql = MagicMock()
        mock_mssql.connect.return_value = good_conn
        monkeypatch.setattr(_sql_module, "_mssql", mock_mssql)

        await sql_exec.execute(_REAL_TARGET, "SELECT 1 AS n")

        fake_time = _sql_module.time  # type: ignore[attr-defined]
        fake_time.sleep.assert_not_called()  # ty: ignore[unresolved-attribute]


# ---------------------------------------------------------------------------
# get_plan — SHOWPLAN_XML capture
# ---------------------------------------------------------------------------

_PLAN_XML = (
    "<ShowPlanXML xmlns='http://schemas.microsoft.com/sqlserver/2004/07/showplan'>"
    "<Batch><Statements><StmtSimple /></Statements></Batch></ShowPlanXML>"
)


def _make_plan_conn(plan_rows: list[tuple[object, ...]]) -> MagicMock:
    """Return a mock connection whose cursor.execute/fetchall simulate SHOWPLAN_XML."""
    cursor = MagicMock()
    # description is None for SET statements; non-None for the actual plan
    cursor.description = [("Microsoft SQL Server 2005 XML Showplan", None)]
    cursor.fetchall.return_value = plan_rows
    cursor.nextset.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


async def test_get_plan_returns_concatenated_xml() -> None:
    """get_plan concatenates the first column of all rows."""
    target = _make_target()
    conn = _make_plan_conn([(_PLAN_XML,)])

    with patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)):
        result = await sql_exec.get_plan(target, "SELECT 1")

    assert result == _PLAN_XML


async def test_get_plan_concatenates_multiple_rows() -> None:
    """When the driver returns multiple rows, get_plan concatenates them."""
    target = _make_target()
    part1 = "<ShowPlanXML>part1"
    part2 = "part2</ShowPlanXML>"
    conn = _make_plan_conn([(part1,), (part2,)])

    with patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)):
        result = await sql_exec.get_plan(target, "SELECT 1")

    assert result == part1 + part2


async def test_get_plan_reads_first_column_positionally() -> None:
    """get_plan reads row[0] (positional), not by column name."""
    target = _make_target()
    # Simulate a row with multiple columns; only the first should be read
    cursor = MagicMock()
    cursor.description = [("col_0", None), ("col_1", None)]
    cursor.fetchall.return_value = [(_PLAN_XML, "ignored")]
    cursor.nextset.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)):
        result = await sql_exec.get_plan(target, "SELECT 1")

    assert result == _PLAN_XML


async def test_get_plan_issues_showplan_on_then_off() -> None:
    """get_plan must issue SET SHOWPLAN_XML ON before the query and OFF after."""
    target = _make_target()
    conn = _make_plan_conn([(_PLAN_XML,)])
    cursor = conn.cursor()

    with patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)):
        await sql_exec.get_plan(target, "SELECT 1 AS n")

    # Verify the three execute calls: ON, query, OFF
    calls = [c.args[0] for c in cursor.execute.call_args_list]
    assert calls[0] == "SET SHOWPLAN_XML ON"
    assert calls[1] == "SELECT 1 AS n"
    assert calls[2] == "SET SHOWPLAN_XML OFF"


async def test_get_plan_off_called_even_on_query_error() -> None:
    """SET SHOWPLAN_XML OFF must be issued in finally even when the query fails."""
    target = _make_target()
    cursor = MagicMock()
    # First execute (SET ON) succeeds; second (query) fails
    cursor.execute.side_effect = [None, Exception("syntax error"), None]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)),
        pytest.raises(Exception, match="syntax error"),
    ):
        await sql_exec.get_plan(target, "SLECT 1")

    # OFF must still have been called (the 3rd execute call)
    calls = [c.args[0] for c in cursor.execute.call_args_list]
    assert "SET SHOWPLAN_XML OFF" in calls


async def test_get_plan_pool_safety_marks_discard_when_off_fails() -> None:
    """When SET SHOWPLAN_XML OFF raises, the connection must be marked for discard.

    This is the critical pool-safety invariant: a connection that may still have
    SHOWPLAN_XML ON must never be returned to the pool.
    """
    from fabric_dw.sql import _PooledConnection  # noqa: PLC0415

    target = _make_target()
    underlying = MagicMock()
    key = ("ws-id-sentinel", "test-db", "default")
    pooled = _PooledConnection(underlying, key)

    cursor = MagicMock()
    off_error = Exception("SET SHOWPLAN_XML OFF failed")
    # Calls: SET ON → query → fetchall → SET OFF (raises)
    cursor.execute.side_effect = [None, None, None]
    cursor.fetchall.return_value = [(_PLAN_XML,)]

    def _execute_side_effect(sql: str) -> None:
        if "OFF" in sql:
            raise off_error

    cursor.execute.side_effect = _execute_side_effect
    underlying.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql._with_connect_retry", return_value=(pooled, 0, 1, None)),
        pytest.raises(Exception, match="SET SHOWPLAN_XML OFF failed"),
    ):
        await sql_exec.get_plan(target, "SELECT 1")

    # Connection must be marked for discard — pool-safety guarantee
    assert pooled._discard is True


async def test_get_plan_permission_denied_raises_permission_denied() -> None:
    """Permission denial during get_plan is raised as PermissionDeniedError."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on object SensitiveTable")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)),
        pytest.raises(PermissionDeniedError),
    ):
        await sql_exec.get_plan(target, "SELECT * FROM SensitiveTable")


async def test_get_plan_closes_connection() -> None:
    """get_plan closes the connection after use."""
    target = _make_target()
    conn = _make_plan_conn([(_PLAN_XML,)])

    with patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)):
        await sql_exec.get_plan(target, "SELECT 1")

    conn.close.assert_called_once()


async def test_get_plan_empty_result_raises_fabric_error() -> None:
    """get_plan raises FabricError when the driver returns no plan rows.

    Statements such as SET, PRINT, or comment-only batches do not produce
    SHOWPLAN_XML output.  Silently returning an empty string would hide the
    problem; a descriptive error must be raised instead.
    """
    from fabric_dw.exceptions import FabricError  # noqa: PLC0415

    target = _make_target()
    conn = _make_plan_conn([])  # no rows — no plan produced

    with (
        patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)),
        pytest.raises(FabricError, match="No execution plan was returned"),
    ):
        await sql_exec.get_plan(target, "SET NOCOUNT ON")


async def test_get_plan_original_error_preserved_when_off_also_fails() -> None:
    """When the query fails AND SET SHOWPLAN_XML OFF also fails, the original
    query error must propagate and the connection must be marked for discard.

    This is the double-failure path that the _exc_in_flight flag was designed to
    protect: the OFF exception must be suppressed so the caller sees the original
    query error, and mark_discard() must still be called so the poisoned connection
    never re-enters the pool.
    """
    from fabric_dw.sql import _PooledConnection  # noqa: PLC0415

    target = _make_target()
    underlying = MagicMock()
    pooled = _PooledConnection(underlying, ("ws", "db", "default"))

    cursor = MagicMock()
    query_error = Exception("syntax error")
    off_error = Exception("SET SHOWPLAN_XML OFF failed")

    def _execute_side_effect(sql_stmt: str) -> None:
        if "OFF" in sql_stmt:
            raise off_error
        if sql_stmt != "SET SHOWPLAN_XML ON":
            # The user query — raise the original error
            raise query_error

    cursor.execute.side_effect = _execute_side_effect
    underlying.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql._with_connect_retry", return_value=(pooled, 0, 1, None)),
        # The ORIGINAL query error must propagate, not the OFF error.
        pytest.raises(Exception, match="syntax error"),
    ):
        await sql_exec.get_plan(target, "INVALID SQL")

    # Pool-safety: the connection must be marked for discard even though
    # _exc_in_flight was set (double-failure path).
    assert pooled._discard is True


# ---------------------------------------------------------------------------
# Row normalisation: execute() and get_plan() must return real tuples even
# when the driver yields non-tuple Row objects (mssql_python.Row is iterable
# and index-accessible but is NOT a tuple subclass).
#
# These tests are deliberately written so that removing the `[tuple(r) for r
# in ...]` normalisation in sql_exec.py causes AT LEAST ONE test to fail:
#
# - execute() tests spy on _tag_binary_columns to assert that every row
#   passed in is already a real tuple (type is tuple, not _FakeRow).
#   Without the normalisation the spy sees _FakeRow objects and the
#   `all(type(r) is tuple ...)` assertion fails.
#
# - get_plan() tests use _IterOnlyRow: a sequence-like that exposes __iter__
#   but raises TypeError on __getitem__.  Without tuple() conversion the
#   `row[0]` access in get_plan() raises TypeError.  With conversion the
#   row is a real tuple and row[0] works normally.
# ---------------------------------------------------------------------------


class _IterOnlyRow:
    """Sequence-like that supports iteration but NOT index access.

    Used to verify that get_plan() calls tuple() on each fetched row BEFORE
    accessing row[0].  If tuple() is removed, row[0] raises TypeError here.
    """

    def __init__(self, *values: object) -> None:
        self._values = values

    def __iter__(self):  # type: ignore[return]
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __getitem__(self, index: int) -> object:
        raise TypeError("_IterOnlyRow does not support index access — tuple() it first")


async def test_execute_passes_real_tuples_to_tag_binary_columns() -> None:
    """execute() normalises _FakeRow objects to real tuples before _tag_binary_columns.

    Spies on _tag_binary_columns to assert that every row in the `rows`
    argument is a genuine tuple (type is tuple, not _FakeRow).  Removing
    the [tuple(r) for r in ...] normalisation in sql_exec.py makes this fail.
    """
    target = _make_target()
    fake_rows = [_FakeRow(1, "hello"), _FakeRow(2, "world")]

    cursor = MagicMock()
    cursor.description = [("id", None), ("name", None)]
    cursor.fetchall.return_value = fake_rows
    cursor.rowcount = -1
    cursor.nextset.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor

    captured_rows: list[list[tuple[object, ...]]] = []

    original_tag = sql_exec._tag_binary_columns  # type: ignore[attr-defined]

    def _spy_tag(
        raw_columns: list[str],
        rows: list[tuple[object, ...]],
        *,
        description: list[tuple[str, object]] | None = None,
    ) -> tuple[list[str], list[list[object]]]:
        captured_rows.append(list(rows))
        return original_tag(raw_columns, rows, description=description)

    with (
        _patch_connect(conn),
        patch("fabric_dw.services.sql_exec._tag_binary_columns", side_effect=_spy_tag),
    ):
        result = await sql_exec.execute(target, "SELECT id, name FROM t")

    assert len(captured_rows) == 1, "spy was not called"
    assert all(type(r) is tuple for r in captured_rows[0]), (
        "rows passed to _tag_binary_columns must be real tuples, not driver Row objects"
    )
    # Values are preserved after normalisation + serialisation.
    assert result.rows[0] == [1, "hello"]
    assert result.rows[1] == [2, "world"]


async def test_execute_fetchmany_passes_real_tuples_to_tag_binary_columns() -> None:
    """execute() normalises _FakeRow objects on the fetchmany (row_limit) path.

    Same spy approach as the fetchall path, but exercises cursor.fetchmany().
    """
    target = _make_target()
    fake_rows = [_FakeRow(i, f"v{i}") for i in range(3)]

    cursor = MagicMock()
    cursor.description = [("n", None), ("v", None)]
    cursor.fetchmany.return_value = fake_rows
    cursor.rowcount = -1
    cursor.nextset.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor

    captured_rows: list[list[tuple[object, ...]]] = []

    original_tag = sql_exec._tag_binary_columns  # type: ignore[attr-defined]

    def _spy_tag(
        raw_columns: list[str],
        rows: list[tuple[object, ...]],
        *,
        description: list[tuple[str, object]] | None = None,
    ) -> tuple[list[str], list[list[object]]]:
        captured_rows.append(list(rows))
        return original_tag(raw_columns, rows, description=description)

    with (
        _patch_connect(conn),
        patch("fabric_dw.services.sql_exec._tag_binary_columns", side_effect=_spy_tag),
    ):
        result = await sql_exec.execute(target, "SELECT n, v FROM t", row_limit=5)

    assert len(captured_rows) == 1, "spy was not called"
    assert all(type(r) is tuple for r in captured_rows[0]), (
        "fetchmany rows must be normalised to real tuples before _tag_binary_columns"
    )
    assert result.rows[0] == [0, "v0"]
    assert result.rows[2] == [2, "v2"]


async def test_execute_empty_fake_rows_normalised() -> None:
    """execute() handles an empty fetchall result without error."""
    target = _make_target()
    cursor = MagicMock()
    cursor.description = [("id", None)]
    cursor.fetchall.return_value = []
    cursor.rowcount = 0
    cursor.nextset.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with _patch_connect(conn):
        result = await sql_exec.execute(target, "SELECT id FROM t WHERE 1=0")

    assert result.rows == []
    assert result.columns == ["id"]


async def test_get_plan_iter_only_row_normalised_to_tuple() -> None:
    """get_plan() must call tuple() on rows before accessing row[0].

    Uses _IterOnlyRow: supports __iter__ but raises TypeError on __getitem__.
    Without the [tuple(r) for r in cursor.fetchall()] normalisation in
    get_plan(), the `row[0]` access raises TypeError and this test fails.
    With normalisation, row becomes a real tuple and row[0] works correctly.
    """
    target = _make_target()

    cursor = MagicMock()
    cursor.description = [("Microsoft SQL Server 2005 XML Showplan", None)]
    cursor.fetchall.return_value = [_IterOnlyRow(_PLAN_XML)]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)):
        result = await sql_exec.get_plan(target, "SELECT 1")

    assert result == _PLAN_XML


async def test_get_plan_multiple_iter_only_rows_concatenated() -> None:
    """get_plan() concatenates the first column of multiple _IterOnlyRow objects."""
    target = _make_target()
    part1 = "<ShowPlanXML>part1"
    part2 = "part2</ShowPlanXML>"

    cursor = MagicMock()
    cursor.description = [("Microsoft SQL Server 2005 XML Showplan", None)]
    cursor.fetchall.return_value = [_IterOnlyRow(part1), _IterOnlyRow(part2)]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)):
        result = await sql_exec.get_plan(target, "SELECT 1")

    assert result == part1 + part2


async def test_get_plan_iter_only_row_with_none_skipped() -> None:
    """get_plan() skips _IterOnlyRow entries where the first column is None."""
    target = _make_target()

    cursor = MagicMock()
    cursor.description = [("Microsoft SQL Server 2005 XML Showplan", None)]
    # First row has None in position 0; second row carries the real plan.
    cursor.fetchall.return_value = [_IterOnlyRow(None), _IterOnlyRow(_PLAN_XML)]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)):
        result = await sql_exec.get_plan(target, "SELECT 1")

    assert result == _PLAN_XML


# ---------------------------------------------------------------------------
# execute / get_plan — unmapped driver SQL error wrapping (#747)
# ---------------------------------------------------------------------------


class _SqlExecDriverError(Exception):
    """Minimal stand-in for mssql_python driver exception with ddbc_error."""

    def __init__(self, msg: str, ddbc_error: str) -> None:
        super().__init__(msg)
        self.ddbc_error = ddbc_error


async def test_execute_invalid_column_raises_fabric_server_error() -> None:
    """#747: execute() wraps unmapped driver SQL errors as FabricServerError.

    When the driver rejects a statement due to an invalid column name the
    exception carries a ddbc_error attribute.  execute() must surface this
    as FabricServerError so MCP callers catch it via FabricError instead of
    seeing a raw traceback.
    """
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = _SqlExecDriverError(
        "Driver Error: Column not found; DDBC Error: [SQL Server]Invalid column name 'x'.",
        "[Microsoft][SQL Server]Invalid column name 'x'.",
    )
    conn.cursor.return_value = cursor

    with _patch_connect(conn), pytest.raises(FabricServerError) as exc_info:
        await sql_exec.execute(target, "SELECT x FROM t")

    assert "Invalid column name 'x'" in str(exc_info.value)
    assert "Driver Error:" not in str(exc_info.value)


async def test_execute_driver_error_without_ddbc_propagates_unchanged() -> None:
    """An exception with no ddbc_error attribute is not wrapped by execute()."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("something unexpected")
    conn.cursor.return_value = cursor

    with _patch_connect(conn), pytest.raises(Exception, match="something unexpected") as exc_info:
        await sql_exec.execute(target, "SELECT 1")

    assert not isinstance(exc_info.value, FabricServerError)


async def test_get_plan_invalid_column_raises_fabric_server_error() -> None:
    """#747: get_plan() wraps unmapped driver SQL errors as FabricServerError."""
    target = _make_target()
    conn = MagicMock()
    cursor = MagicMock()
    # Simulate driver raising on the plan-capture execute call.
    cursor.execute.side_effect = _SqlExecDriverError(
        "Driver Error: Column not found; DDBC Error: [SQL Server]Invalid column name 'y'.",
        "[Microsoft][SQL Server]Invalid column name 'y'.",
    )
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)),
        pytest.raises(FabricServerError) as exc_info,
    ):
        await sql_exec.get_plan(target, "SELECT y FROM t")

    assert "Invalid column name 'y'" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Verbose SQL logging — execute() and get_plan() (#758)
# ---------------------------------------------------------------------------


async def test_execute_emits_debug_log_with_sql(caplog: pytest.LogCaptureFixture) -> None:
    """execute() must emit a DEBUG record whose r.sql extra attribute holds the query.

    _log.debug("sql execute", extra={"sql": ...}) puts the SQL in r.sql, not in
    getMessage() (which is always the literal "sql execute").  Asserting on r.sql
    directly guards against the call site being removed — the getMessage() check
    would always be False and could mask that regression.
    """
    target = _make_target()
    conn = _make_conn([(1,)], ["n"])

    with _patch_connect(conn), caplog.at_level(logging.DEBUG, logger="fabric_dw.sql"):
        await sql_exec.execute(target, "SELECT 1 AS n")

    debug_records = [
        r for r in caplog.records if r.levelno == logging.DEBUG and r.name == "fabric_dw.sql"
    ]
    assert any(getattr(r, "sql", None) == "SELECT 1 AS n" for r in debug_records), (
        f"Expected r.sql == 'SELECT 1 AS n' in DEBUG records; got sql attrs: "
        f"{[getattr(r, 'sql', None) for r in debug_records]}"
    )


async def test_execute_sql_logged_verbatim(caplog: pytest.LogCaptureFixture) -> None:
    """execute() logs SQL verbatim at DEBUG level - no redaction is applied.

    Operators using -v must treat log output as sensitive.
    """
    target = _make_target()
    raw_secret = "sv=2024&sig=TOPSECRETTOKEN"  # noqa: S105
    copy_sql = (
        f"COPY INTO [dbo].[t] FROM 'https://x.blob.core.windows.net/c/f.parquet' "
        f"WITH (CREDENTIAL = (IDENTITY = 'Shared Access Signature', SECRET = '{raw_secret}'))"
    )
    conn = _make_no_result_conn()

    with _patch_connect(conn), caplog.at_level(logging.DEBUG, logger="fabric_dw.sql"):
        await sql_exec.execute(target, copy_sql)

    sql_attrs = [str(getattr(r, "sql", "")) for r in caplog.records if r.name == "fabric_dw.sql"]
    combined = " ".join(sql_attrs)
    assert "TOPSECRETTOKEN" in combined, "SQL must be logged verbatim at DEBUG"


async def test_get_plan_emits_debug_log_with_sql(caplog: pytest.LogCaptureFixture) -> None:
    """get_plan() must emit a DEBUG record whose r.sql extra attribute holds the user's query.

    _log.debug("sql execute", extra={"sql": ...}) puts the SQL in r.sql, not in
    getMessage() (which is always "sql execute").  Asserting on r.sql directly
    ensures the call site is actually present and routes through _log_sql_execute.
    """
    target = _make_target()
    conn = _make_plan_conn([(_PLAN_XML,)])

    with (
        patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)),
        caplog.at_level(logging.DEBUG, logger="fabric_dw.sql"),
    ):
        await sql_exec.get_plan(target, "SELECT 1 AS n")

    debug_records = [
        r for r in caplog.records if r.levelno == logging.DEBUG and r.name == "fabric_dw.sql"
    ]
    assert any(getattr(r, "sql", None) == "SELECT 1 AS n" for r in debug_records), (
        f"Expected r.sql == 'SELECT 1 AS n' in DEBUG records; got sql attrs: "
        f"{[getattr(r, 'sql', None) for r in debug_records]}"
    )


async def test_get_plan_logs_user_query_not_showplan_control_statements(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """get_plan() must log the user's query exactly once, not the SET SHOWPLAN wrappers."""
    target = _make_target()
    conn = _make_plan_conn([(_PLAN_XML,)])

    with (
        patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)),
        caplog.at_level(logging.DEBUG, logger="fabric_dw.sql"),
    ):
        await sql_exec.get_plan(target, "SELECT 42 AS answer")

    debug_records = [
        r for r in caplog.records if r.levelno == logging.DEBUG and r.name == "fabric_dw.sql"
    ]
    logged_sqls = [getattr(r, "sql", r.getMessage()) for r in debug_records]

    # The user's query must appear exactly once.
    user_query_count = sum(1 for s in logged_sqls if "SELECT 42 AS answer" in s)
    assert user_query_count == 1, (
        f"Expected user query logged once; got {user_query_count}: {logged_sqls}"
    )

    # The control statements must NOT be logged.
    control_seen = any("SHOWPLAN_XML" in s for s in logged_sqls)
    assert not control_seen, (
        f"SET SHOWPLAN_XML control statements must not be logged; got: {logged_sqls}"
    )


async def test_get_plan_sql_logged_verbatim(caplog: pytest.LogCaptureFixture) -> None:
    """get_plan() logs SQL verbatim at DEBUG level - no redaction is applied.

    Operators using -v must treat log output as sensitive.
    """
    target = _make_target()
    raw_secret = "sv=2024&sig=TOPSECRETTOKEN"  # noqa: S105
    copy_sql = (
        f"COPY INTO [dbo].[t] FROM 'https://x.blob.core.windows.net/c/f.parquet' "
        f"WITH (CREDENTIAL = (IDENTITY = 'Shared Access Signature', SECRET = '{raw_secret}'))"
    )
    conn = _make_plan_conn([(_PLAN_XML,)])

    with (
        patch("fabric_dw.sql._with_connect_retry", return_value=(conn, 0, 1, None)),
        caplog.at_level(logging.DEBUG, logger="fabric_dw.sql"),
    ):
        await sql_exec.get_plan(target, copy_sql)

    sql_attrs = [str(getattr(r, "sql", "")) for r in caplog.records if r.name == "fabric_dw.sql"]
    combined = " ".join(sql_attrs)
    assert "TOPSECRETTOKEN" in combined, "SQL must be logged verbatim at DEBUG"
