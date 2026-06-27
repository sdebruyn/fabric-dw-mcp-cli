"""Unit tests for W-series code-review fixes in services/.

Covers:
- W01: roll_timestamp timezone correctness
- W07: snapshot rename guard on None parentWarehouseId
- W11: takeover kind guard (ItemKindError on SQL_ENDPOINT)
- W13: create_point Path C numeric (not lexicographic) max
- W15: _serialize_value handles date/time/UUID
- W16: snapshots.create empty-Location guard
"""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from fabric_dw.exceptions import ItemKindError
from fabric_dw.models import CreationModeType, RestorePoint, WarehouseKind
from fabric_dw.services import snapshots as _snapshots_mod
from fabric_dw.services._helpers import coerce_to_utc
from fabric_dw.services.ownership import takeover
from fabric_dw.services.snapshots import create as _snap_create
from fabric_dw.services.snapshots import rename as _snap_rename
from fabric_dw.services.sql_exec import _serialize_value


class TestSerializeValue:
    """_serialize_value must return JSON-serialisable scalars for all driver types."""

    def _s(self, value: object) -> object:
        return _serialize_value(value)

    def test_none_passthrough(self) -> None:
        assert self._s(None) is None

    def test_int_passthrough(self) -> None:
        assert self._s(42) == 42

    def test_float_passthrough(self) -> None:
        assert self._s(3.14) == pytest.approx(3.14)

    def test_str_passthrough(self) -> None:
        assert self._s("hello") == "hello"

    def test_bool_passthrough(self) -> None:
        assert self._s(True) is True  # noqa: FBT003

    def test_datetime_naive_iso(self) -> None:
        dt = datetime(2024, 3, 15, 12, 0, 0)  # noqa: DTZ001
        result = self._s(dt)
        assert result == dt.isoformat()
        assert isinstance(result, str)

    def test_datetime_aware_iso(self) -> None:
        dt = datetime(2024, 3, 15, 12, 0, 0, tzinfo=UTC)
        result = self._s(dt)
        assert result == dt.isoformat()

    def test_date_returns_iso_string(self) -> None:
        """date must NOT fall through — date is NOT a datetime subclass."""
        d = date(2024, 3, 15)
        result = self._s(d)
        assert result == "2024-03-15"
        assert isinstance(result, str)

    def test_date_not_confused_with_datetime(self) -> None:
        """datetime subclasses date; confirm datetime branch is taken for datetime values."""
        dt = datetime(2024, 3, 15, 0, 0, 0)  # noqa: DTZ001
        d = date(2024, 3, 15)
        assert "T" in str(self._s(dt))
        assert "T" not in str(self._s(d))

    def test_time_returns_iso_string(self) -> None:
        t = time(14, 30, 0)
        result = self._s(t)
        assert result == "14:30:00"
        assert isinstance(result, str)

    def test_time_with_microseconds(self) -> None:
        t = time(14, 30, 0, 123456)
        result = self._s(t)
        assert isinstance(result, str)
        assert "14:30:00" in result

    def test_uuid_returns_string(self) -> None:
        u = UUID("12345678-1234-5678-1234-567812345678")
        result = self._s(u)
        assert result == str(u)
        assert isinstance(result, str)
        assert "-" in str(result)

    def test_decimal_returns_string(self) -> None:
        result = self._s(Decimal("3.14"))
        assert result == "3.14"
        assert isinstance(result, str)

    def test_bytes_base64(self) -> None:
        raw = b"\xde\xad\xbe\xef"
        result = self._s(raw)
        assert result == base64.b64encode(raw).decode("ascii")


def _tz_format(new_dt: datetime) -> str:
    """Mirror the tz-conversion logic in roll_timestamp for isolated testing.

    Naive datetimes are treated as UTC (fix #774).
    """
    new_dt_utc = coerce_to_utc(new_dt)
    return new_dt_utc.strftime("%Y-%m-%dT%H:%M:%S.00")


class TestRollTimestampTimezone:
    """roll_timestamp treats naive datetimes as UTC and formats in UTC (fix #774)."""

    @pytest.mark.asyncio
    async def test_naive_dt_accepted_via_service(self) -> None:
        """A naive datetime is accepted by roll_timestamp and treated as UTC (fix #774)."""
        naive_dt = datetime(2024, 6, 1, 12, 0, 0)  # noqa: DTZ001
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        with patch("fabric_dw.sql.open_connection", return_value=conn):
            result = await _snapshots_mod.roll_timestamp(MagicMock(), "snap1", naive_dt)
        # Naive datetime treated as UTC - returned as UTC-aware
        assert result == naive_dt.replace(tzinfo=UTC)

    def test_naive_formats_as_utc_from_helper(self) -> None:
        """Naive datetime passed to format helper is treated as UTC (fix #774)."""
        naive = datetime(2024, 1, 1, 0, 0, 0)  # noqa: DTZ001
        result = _tz_format(naive)
        assert result == "2024-01-01T00:00:00.00"

    def test_utc_aware_formats_correctly(self) -> None:
        dt = datetime(2024, 6, 1, 12, 30, 45, tzinfo=UTC)
        assert _tz_format(dt) == "2024-06-01T12:30:45.00"

    def test_non_utc_aware_converts_to_utc(self) -> None:
        tz_plus2 = timezone(timedelta(hours=2))
        dt_plus2 = datetime(2024, 6, 1, 14, 30, 0, tzinfo=tz_plus2)
        assert _tz_format(dt_plus2) == "2024-06-01T12:30:00.00"

    def test_utc_minus_offset_converts_to_utc(self) -> None:
        tz_minus5 = timezone(timedelta(hours=-5))
        dt = datetime(2024, 6, 1, 7, 0, 0, tzinfo=tz_minus5)
        assert _tz_format(dt) == "2024-06-01T12:00:00.00"


def _make_restore_point(id_str: str) -> RestorePoint:
    return RestorePoint.from_api(
        {
            "id": id_str,
            "creationMode": CreationModeType.USER_DEFINED,
            "createdDateTime": "2024-01-01T00:00:00Z",
        }
    )


def _path_c_select(points: list[RestorePoint]) -> RestorePoint:
    """Replicate Path C selection logic from restore.create_point."""
    return max(points, key=lambda p: int(p.id) if p.id.isdigit() else 0)


class TestCreatePointPathCSelection:
    """Path C of create_point must select by numeric ID, not lexicographic."""

    def test_numeric_max_beats_lexicographic(self) -> None:
        """'9' > '10000' lexicographically, but 10000 > 9 numerically — numeric must win."""
        points = [_make_restore_point("9"), _make_restore_point("10000")]
        result = _path_c_select(points)
        assert result.id == "10000"

    def test_same_length_ids(self) -> None:
        points = [
            _make_restore_point("1726617370000"),
            _make_restore_point("1726617378000"),
            _make_restore_point("1726617365000"),
        ]
        assert _path_c_select(points).id == "1726617378000"

    def test_non_digit_id_treated_as_zero(self) -> None:
        points = [_make_restore_point("not-a-number"), _make_restore_point("1000")]
        assert _path_c_select(points).id == "1000"

    def test_single_point(self) -> None:
        points = [_make_restore_point("1726617378000")]
        assert _path_c_select(points).id == "1726617378000"


class TestSnapshotRenameNoneParentGuard:
    """rename must raise ValueError when parentWarehouseId is missing."""

    @pytest.mark.asyncio
    async def test_raises_when_parent_is_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "id": "aaaaaaaa-0000-0000-0000-000000000000",
            "displayName": "snap",
            "type": "WarehouseSnapshot",
            "properties": {},
        }
        http = MagicMock()
        http.request = AsyncMock(return_value=mock_resp)

        ws_id = UUID("bbbbbbbb-0000-0000-0000-000000000000")
        snap_id = UUID("aaaaaaaa-0000-0000-0000-000000000000")

        with pytest.raises(ValueError, match="parentWarehouseId is not yet populated"):
            await _snap_rename(http, ws_id, snap_id, new_name="new-name")


class TestTakeoverKindGuard:
    """takeover must raise ItemKindError for SQL_ENDPOINT without hitting the API."""

    @pytest.mark.asyncio
    async def test_sql_endpoint_raises_item_kind_error(self) -> None:
        http = MagicMock()
        http.request = AsyncMock()

        with pytest.raises(ItemKindError, match="SQL Analytics Endpoints"):
            await takeover(
                http,
                UUID("cccccccc-0000-0000-0000-000000000000"),
                UUID("dddddddd-0000-0000-0000-000000000000"),
                kind=WarehouseKind.SQL_ENDPOINT,
            )

        http.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_warehouse_kind_proceeds(self) -> None:
        http = MagicMock()
        http.request = AsyncMock(return_value=MagicMock(status_code=200))

        await takeover(
            http,
            UUID("cccccccc-0000-0000-0000-000000000000"),
            UUID("eeeeeeee-0000-0000-0000-000000000000"),
            kind=WarehouseKind.WAREHOUSE,
        )
        http.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_kind_is_warehouse(self) -> None:
        http = MagicMock()
        http.request = AsyncMock(return_value=MagicMock(status_code=200))

        await takeover(
            http,
            UUID("cccccccc-0000-0000-0000-000000000000"),
            UUID("ffffffff-0000-0000-0000-000000000000"),
        )
        http.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_snapshot_kind_raises_item_kind_error(self) -> None:
        http = MagicMock()
        http.request = AsyncMock()

        with pytest.raises(ItemKindError):
            await takeover(
                http,
                UUID("cccccccc-0000-0000-0000-000000000000"),
                UUID("11111111-0000-0000-0000-000000000000"),
                kind=WarehouseKind.SNAPSHOT,
            )

        http.request.assert_not_called()


class TestSnapshotsCreateLocationGuard:
    """create must raise ValueError when the Location header is missing."""

    @pytest.mark.asyncio
    async def test_missing_location_raises_value_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.headers = {}

        http = MagicMock()
        http.request = AsyncMock(return_value=mock_resp)

        with pytest.raises(ValueError, match="Location header is missing"):
            await _snap_create(
                http,
                UUID("aaaaaaaa-0000-0000-0000-000000000000"),
                UUID("bbbbbbbb-0000-0000-0000-000000000000"),
                "my-snapshot",
            )

    @pytest.mark.asyncio
    async def test_naive_snapshot_dt_treated_as_utc(self) -> None:
        """A naive snapshot_dt is accepted and treated as UTC - no longer raises (fix #774)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.headers = {}

        http = MagicMock()
        http.request = AsyncMock(return_value=mock_resp)

        with pytest.raises(ValueError, match="Location header is missing"):
            await _snap_create(
                http,
                UUID("aaaaaaaa-0000-0000-0000-000000000000"),
                UUID("bbbbbbbb-0000-0000-0000-000000000000"),
                "snap",
                snapshot_dt=datetime(2024, 1, 1, 12, 0, 0),  # naive  # noqa: DTZ001
            )

        # HTTP request must have been made (naive dt is no longer rejected)
        http.request.assert_called_once()
        body = http.request.call_args.kwargs.get("json", {})
        # Naive 2024-01-01T12:00:00 treated as UTC - emitted with Z suffix
        snap_dt = body.get("creationPayload", {}).get("snapshotDateTime", "")
        assert snap_dt == "2024-01-01T12:00:00Z"

    @pytest.mark.asyncio
    async def test_aware_snapshot_dt_converted_to_utc(self) -> None:
        """An aware snapshot_dt is converted to UTC and does not raise before the HTTP call."""
        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.headers = {}

        http = MagicMock()
        http.request = AsyncMock(return_value=mock_resp)

        tz_plus2 = timezone(timedelta(hours=2))
        aware_dt = datetime(2024, 6, 1, 14, 30, 0, tzinfo=tz_plus2)

        with pytest.raises(ValueError, match="Location header is missing"):
            await _snap_create(
                http,
                UUID("aaaaaaaa-0000-0000-0000-000000000000"),
                UUID("bbbbbbbb-0000-0000-0000-000000000000"),
                "snap",
                snapshot_dt=aware_dt,
            )

        # The HTTP call must have been made (tz check passed)
        http.request.assert_called_once()
