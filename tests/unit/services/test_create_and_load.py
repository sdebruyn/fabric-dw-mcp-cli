"""Unit tests for create_and_load in fabric_dw.services.load.

Tests cover:
- The full --if-exists decision matrix (fail/append/truncate/replace x table exists/not).
- cleanup_on_failure drops ONLY a table we created (never a pre-existing one).
- SQL Endpoint guard rejection.
- Existence-check logic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fabric_dw.exceptions import ItemKindError
from fabric_dw.models import CopyIntoResult, WarehouseKind
from fabric_dw.services.load import IfExistsPolicy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WS_ID = "00000000-0000-0000-0000-000000000001"
_SCHEMA = "dbo"
_TABLE = "sales"


def _make_result(rows: int = 5) -> CopyIntoResult:
    return CopyIntoResult(rows_loaded=rows, rows_rejected=0, target=f"{_SCHEMA}.{_TABLE}")


async def _call_create_and_load(
    tmp_path: Path,
    *,
    if_exists: IfExistsPolicy = "fail",
    table_exists: bool = False,
    cleanup_on_failure: bool = False,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
) -> tuple:
    """Call create_and_load with heavy mocking; returns (result, mocks...)."""
    import uuid  # noqa: PLC0415

    from fabric_dw.models import ColumnSpec  # noqa: PLC0415
    from fabric_dw.services.load import create_and_load  # noqa: PLC0415

    parquet_file = tmp_path / "data.parquet"
    parquet_file.write_bytes(b"PAR1")

    mock_http = AsyncMock()
    mock_cred = AsyncMock()
    ws_id = uuid.UUID(_WS_ID)
    mock_target = MagicMock()
    _columns = [ColumnSpec(name="id", sql_type="INT", nullable=False)]

    with (
        patch(
            "fabric_dw.services.load._infer_columns_from_local",
            new=AsyncMock(return_value=_columns),
        ),
        patch(
            "fabric_dw.services.load._table_exists",
            new=AsyncMock(return_value=table_exists),
        ),
        patch(
            "fabric_dw.services.load._drop_table_sql",
            new=AsyncMock(),
        ) as mock_drop,
        patch(
            "fabric_dw.services.load._truncate_table_sql",
            new=AsyncMock(),
        ) as mock_truncate,
        patch(
            "fabric_dw.services.load._create_table_from_columns",
            new=AsyncMock(),
        ) as mock_create,
        patch(
            "fabric_dw.services.load.load_local_file",
            new=AsyncMock(return_value=_make_result()),
        ) as mock_load,
    ):
        result = await create_and_load(
            mock_http,
            mock_cred,
            ws_id,
            mock_target,
            _SCHEMA,
            _TABLE,
            parquet_file,
            if_exists=if_exists,
            file_format="parquet",
            kind=kind,
            cleanup_on_failure=cleanup_on_failure,
        )

    return result, mock_create, mock_truncate, mock_drop, mock_load


# ---------------------------------------------------------------------------
# SQL Endpoint guard
# ---------------------------------------------------------------------------


async def test_create_and_load_rejects_sql_endpoint(tmp_path: Path) -> None:
    """SQL Analytics Endpoints must be rejected before any I/O."""
    import uuid  # noqa: PLC0415

    from fabric_dw.services.load import create_and_load  # noqa: PLC0415

    parquet_file = tmp_path / "data.parquet"
    parquet_file.write_bytes(b"PAR1")

    with pytest.raises(ItemKindError):
        await create_and_load(
            AsyncMock(),
            AsyncMock(),
            uuid.UUID(_WS_ID),
            MagicMock(),
            _SCHEMA,
            _TABLE,
            parquet_file,
            file_format="parquet",
            kind=WarehouseKind.SQL_ENDPOINT,
        )


# ---------------------------------------------------------------------------
# --if-exists x table exists/not matrix
# ---------------------------------------------------------------------------


async def test_if_exists_fail_table_not_exists_creates_and_loads(tmp_path: Path) -> None:
    """if_exists=fail, table not present: CREATE + COPY INTO."""
    result, mock_create, mock_truncate, mock_drop, mock_load = await _call_create_and_load(
        tmp_path, if_exists="fail", table_exists=False
    )

    mock_create.assert_called_once()
    mock_truncate.assert_not_called()
    mock_drop.assert_not_called()
    mock_load.assert_called_once()
    assert result.rows_loaded == 5


async def test_if_exists_fail_table_exists_raises(tmp_path: Path) -> None:
    """if_exists=fail, table already present: ValueError."""
    import uuid  # noqa: PLC0415

    from fabric_dw.models import ColumnSpec  # noqa: PLC0415
    from fabric_dw.services.load import create_and_load  # noqa: PLC0415

    parquet_file = tmp_path / "data.parquet"
    parquet_file.write_bytes(b"PAR1")
    _columns = [ColumnSpec(name="id", sql_type="INT", nullable=False)]

    with (
        patch(
            "fabric_dw.services.load._infer_columns_from_local",
            new=AsyncMock(return_value=_columns),
        ),
        patch(
            "fabric_dw.services.load._table_exists",
            new=AsyncMock(return_value=True),
        ),
        pytest.raises(ValueError, match="already exists"),
    ):
        await create_and_load(
            AsyncMock(),
            AsyncMock(),
            uuid.UUID(_WS_ID),
            MagicMock(),
            _SCHEMA,
            _TABLE,
            parquet_file,
            file_format="parquet",
            if_exists="fail",
        )


async def test_if_exists_append_table_not_exists_creates_and_loads(tmp_path: Path) -> None:
    """if_exists=append, table absent: CREATE + COPY INTO."""
    result, mock_create, mock_truncate, mock_drop, mock_load = await _call_create_and_load(
        tmp_path, if_exists="append", table_exists=False
    )

    mock_create.assert_called_once()
    mock_truncate.assert_not_called()
    mock_drop.assert_not_called()
    mock_load.assert_called_once()
    assert result.rows_loaded == 5


async def test_if_exists_append_table_exists_skips_create(tmp_path: Path) -> None:
    """if_exists=append, table present: skip CREATE; COPY INTO only."""
    result, mock_create, mock_truncate, mock_drop, mock_load = await _call_create_and_load(
        tmp_path, if_exists="append", table_exists=True
    )

    mock_create.assert_not_called()
    mock_truncate.assert_not_called()
    mock_drop.assert_not_called()
    mock_load.assert_called_once()
    assert result.rows_loaded == 5


async def test_if_exists_truncate_table_exists_truncates_then_loads(tmp_path: Path) -> None:
    """if_exists=truncate, table present: TRUNCATE + COPY INTO (no create/drop)."""
    result, mock_create, mock_truncate, mock_drop, mock_load = await _call_create_and_load(
        tmp_path, if_exists="truncate", table_exists=True
    )

    mock_truncate.assert_called_once()
    mock_create.assert_not_called()
    mock_drop.assert_not_called()
    mock_load.assert_called_once()
    assert result.rows_loaded == 5


async def test_if_exists_truncate_table_not_exists_creates_and_loads(tmp_path: Path) -> None:
    """if_exists=truncate, table absent: CREATE + COPY INTO (no truncate)."""
    _result, mock_create, mock_truncate, mock_drop, mock_load = await _call_create_and_load(
        tmp_path, if_exists="truncate", table_exists=False
    )

    mock_create.assert_called_once()
    mock_truncate.assert_not_called()
    mock_drop.assert_not_called()
    mock_load.assert_called_once()


async def test_if_exists_replace_table_exists_drops_recreates_loads(tmp_path: Path) -> None:
    """if_exists=replace, table present: DROP + CREATE + COPY INTO."""
    result, mock_create, mock_truncate, mock_drop, mock_load = await _call_create_and_load(
        tmp_path, if_exists="replace", table_exists=True
    )

    mock_drop.assert_called_once()
    mock_create.assert_called_once()
    mock_truncate.assert_not_called()
    mock_load.assert_called_once()
    assert result.rows_loaded == 5


async def test_if_exists_replace_table_not_exists_creates_and_loads(tmp_path: Path) -> None:
    """if_exists=replace, table absent: CREATE + COPY INTO (no drop)."""
    _result, mock_create, mock_truncate, mock_drop, mock_load = await _call_create_and_load(
        tmp_path, if_exists="replace", table_exists=False
    )

    mock_drop.assert_not_called()
    mock_create.assert_called_once()
    mock_truncate.assert_not_called()
    mock_load.assert_called_once()


# ---------------------------------------------------------------------------
# cleanup_on_failure
# ---------------------------------------------------------------------------


async def test_cleanup_on_failure_drops_created_table(tmp_path: Path) -> None:
    """cleanup_on_failure=True, WE created the table, load fails: DROP is called."""
    import uuid  # noqa: PLC0415

    from fabric_dw.models import ColumnSpec  # noqa: PLC0415
    from fabric_dw.services.load import create_and_load  # noqa: PLC0415

    parquet_file = tmp_path / "data.parquet"
    parquet_file.write_bytes(b"PAR1")
    ws_id = uuid.UUID(_WS_ID)
    _columns = [ColumnSpec(name="id", sql_type="INT", nullable=False)]

    with (
        patch(
            "fabric_dw.services.load._infer_columns_from_local",
            new=AsyncMock(return_value=_columns),
        ),
        patch(
            "fabric_dw.services.load._table_exists",
            new=AsyncMock(return_value=False),  # table absent -> we create it
        ),
        patch(
            "fabric_dw.services.load._create_table_from_columns",
            new=AsyncMock(),
        ),
        patch(
            "fabric_dw.services.load._drop_table_sql",
            new=AsyncMock(),
        ) as mock_drop,
        patch(
            "fabric_dw.services.load.load_local_file",
            new=AsyncMock(side_effect=RuntimeError("load failed")),
        ),
        pytest.raises(RuntimeError, match="load failed"),
    ):
        await create_and_load(
            AsyncMock(),
            AsyncMock(),
            ws_id,
            MagicMock(),
            _SCHEMA,
            _TABLE,
            parquet_file,
            file_format="parquet",
            cleanup_on_failure=True,
        )

    mock_drop.assert_called_once()


async def test_cleanup_on_failure_does_not_drop_preexisting_table(tmp_path: Path) -> None:
    """cleanup_on_failure=True, table PRE-EXISTED, load fails: DROP is NOT called."""
    import uuid  # noqa: PLC0415

    from fabric_dw.models import ColumnSpec  # noqa: PLC0415
    from fabric_dw.services.load import create_and_load  # noqa: PLC0415

    parquet_file = tmp_path / "data.parquet"
    parquet_file.write_bytes(b"PAR1")
    ws_id = uuid.UUID(_WS_ID)
    _columns = [ColumnSpec(name="id", sql_type="INT", nullable=False)]

    with (
        patch(
            "fabric_dw.services.load._infer_columns_from_local",
            new=AsyncMock(return_value=_columns),
        ),
        patch(
            "fabric_dw.services.load._table_exists",
            new=AsyncMock(return_value=True),  # table EXISTS -> we don't create it
        ),
        patch(
            "fabric_dw.services.load._drop_table_sql",
            new=AsyncMock(),
        ) as mock_drop,
        patch(
            "fabric_dw.services.load.load_local_file",
            new=AsyncMock(side_effect=RuntimeError("load failed")),
        ),
        pytest.raises(RuntimeError, match="load failed"),
    ):
        await create_and_load(
            AsyncMock(),
            AsyncMock(),
            ws_id,
            MagicMock(),
            _SCHEMA,
            _TABLE,
            parquet_file,
            file_format="parquet",
            if_exists="append",
            cleanup_on_failure=True,
        )

    # Pre-existing table must NEVER be dropped.
    mock_drop.assert_not_called()


async def test_cleanup_on_failure_false_does_not_drop_on_failure(tmp_path: Path) -> None:
    """cleanup_on_failure=False (default), load fails: DROP is NOT called."""
    import uuid  # noqa: PLC0415

    from fabric_dw.models import ColumnSpec  # noqa: PLC0415
    from fabric_dw.services.load import create_and_load  # noqa: PLC0415

    parquet_file = tmp_path / "data.parquet"
    parquet_file.write_bytes(b"PAR1")
    ws_id = uuid.UUID(_WS_ID)
    _columns = [ColumnSpec(name="id", sql_type="INT", nullable=False)]

    with (
        patch(
            "fabric_dw.services.load._infer_columns_from_local",
            new=AsyncMock(return_value=_columns),
        ),
        patch(
            "fabric_dw.services.load._table_exists",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "fabric_dw.services.load._create_table_from_columns",
            new=AsyncMock(),
        ),
        patch(
            "fabric_dw.services.load._drop_table_sql",
            new=AsyncMock(),
        ) as mock_drop,
        patch(
            "fabric_dw.services.load.load_local_file",
            new=AsyncMock(side_effect=RuntimeError("load failed")),
        ),
        pytest.raises(RuntimeError, match="load failed"),
    ):
        await create_and_load(
            AsyncMock(),
            AsyncMock(),
            ws_id,
            MagicMock(),
            _SCHEMA,
            _TABLE,
            parquet_file,
            file_format="parquet",
            cleanup_on_failure=False,
        )

    mock_drop.assert_not_called()


async def test_cleanup_on_failure_replace_policy_two_drops(tmp_path: Path) -> None:
    """if_exists=replace + cleanup_on_failure=True + load fails: two drop calls.

    First drop is the 'replace' DROP; second is the cleanup_on_failure drop.
    Both are expected because we recreated the table (we_created=True).
    """
    import uuid  # noqa: PLC0415

    from fabric_dw.models import ColumnSpec  # noqa: PLC0415
    from fabric_dw.services.load import create_and_load  # noqa: PLC0415

    parquet_file = tmp_path / "data.parquet"
    parquet_file.write_bytes(b"PAR1")
    ws_id = uuid.UUID(_WS_ID)
    _columns = [ColumnSpec(name="id", sql_type="INT", nullable=False)]
    drop_calls: list[str] = []

    async def _track_drop(*_args, **_kwargs) -> None:
        drop_calls.append("drop")

    with (
        patch(
            "fabric_dw.services.load._infer_columns_from_local",
            new=AsyncMock(return_value=_columns),
        ),
        patch(
            "fabric_dw.services.load._table_exists",
            new=AsyncMock(return_value=True),  # pre-existing -> replace path
        ),
        patch(
            "fabric_dw.services.load._create_table_from_columns",
            new=AsyncMock(),
        ),
        patch(
            "fabric_dw.services.load._drop_table_sql",
            new=AsyncMock(side_effect=_track_drop),
        ),
        patch(
            "fabric_dw.services.load.load_local_file",
            new=AsyncMock(side_effect=RuntimeError("load failed")),
        ),
        pytest.raises(RuntimeError, match="load failed"),
    ):
        await create_and_load(
            AsyncMock(),
            AsyncMock(),
            ws_id,
            MagicMock(),
            _SCHEMA,
            _TABLE,
            parquet_file,
            file_format="parquet",
            if_exists="replace",
            cleanup_on_failure=True,
        )

    assert len(drop_calls) == 2


# ---------------------------------------------------------------------------
# File-not-found guard
# ---------------------------------------------------------------------------


async def test_create_and_load_file_not_found(tmp_path: Path) -> None:
    """FileNotFoundError is raised when local_path does not exist."""
    import uuid  # noqa: PLC0415

    from fabric_dw.services.load import create_and_load  # noqa: PLC0415

    missing = tmp_path / "nope.parquet"

    with pytest.raises(FileNotFoundError):
        await create_and_load(
            AsyncMock(),
            AsyncMock(),
            uuid.UUID(_WS_ID),
            MagicMock(),
            _SCHEMA,
            _TABLE,
            missing,
            file_format="parquet",
        )


# ---------------------------------------------------------------------------
# _table_exists helper
# ---------------------------------------------------------------------------


async def test_table_exists_returns_true_when_row_found() -> None:
    """_table_exists returns True when sys.tables contains the table."""
    from fabric_dw.services.load import _table_exists  # noqa: PLC0415

    mock_target = MagicMock()

    def _fake_run_query(_target, _sql, **_kw) -> tuple:
        return ["col1"], [(1,)]

    with patch("fabric_dw.services.load.run_query", side_effect=_fake_run_query):
        result = await _table_exists(mock_target, "dbo", "sales")

    assert result is True


async def test_table_exists_returns_false_when_no_rows() -> None:
    """_table_exists returns False when sys.tables has no matching row."""
    from fabric_dw.services.load import _table_exists  # noqa: PLC0415

    mock_target = MagicMock()

    def _fake_run_query(_target, _sql, **_kw) -> tuple:
        return [], []

    with patch("fabric_dw.services.load.run_query", side_effect=_fake_run_query):
        result = await _table_exists(mock_target, "dbo", "sales")

    assert result is False
