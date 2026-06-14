"""Tests for services.snapshots — written BEFORE implementation (TDD)."""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest
import respx

from fabric_dw.cache import ItemEntry, LookupCache
from fabric_dw.exceptions import FabricError, NotFoundError, PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import WarehouseKind, WarehouseSnapshot
from fabric_dw.services import snapshots
from fabric_dw.sql import SqlTarget
from tests.unit.services._helpers import _make_credential

# ---------------------------------------------------------------------------
# Constants & Fixtures
# ---------------------------------------------------------------------------

_WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_PARENT_WH_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
_SNAP_ID = UUID("f6a7b8c9-d0e1-2345-f012-34567890abcd")
_OTHER_WH_ID = UUID("11111111-2222-3333-4444-555555555555")


_BASE_URL = "https://api.fabric.microsoft.com/v1"
_ITEMS_URL = f"{_BASE_URL}/workspaces/{_WS_ID}/items"
_SNAP_DETAIL_URL = f"{_BASE_URL}/workspaces/{_WS_ID}/items/{_SNAP_ID}"
# Dedicated warehouseSnapshots endpoint URLs
_TYPED_SNAPS_URL = f"{_BASE_URL}/workspaces/{_WS_ID}/warehouseSnapshots"
_TYPED_SNAP_URL = f"{_BASE_URL}/workspaces/{_WS_ID}/warehouseSnapshots/{_SNAP_ID}"

# A single snapshot whose parent matches _PARENT_WH_ID (flat model format)
WAREHOUSE_SNAPSHOT_PAYLOAD: dict[str, Any] = {
    "id": str(_SNAP_ID),
    "displayName": "SalesWarehouse_Snapshot_20240315",
    "parentWarehouseId": str(_PARENT_WH_ID),
    "snapshotDateTime": "2024-03-15T08:00:00Z",
}

# Detail payload as returned by GET /workspaces/{ws}/warehouseSnapshots/{id}
# (type-specific endpoint — uses "properties" not "creationPayload")
WAREHOUSE_SNAPSHOT_TYPED_PAYLOAD: dict[str, Any] = {
    "id": str(_SNAP_ID),
    "displayName": "SalesWarehouse_Snapshot_20240315",
    "type": "WarehouseSnapshot",
    "workspaceId": str(_WS_ID),
    "properties": {
        "parentWarehouseId": str(_PARENT_WH_ID),
        "snapshotDateTime": "2024-03-15T08:00:00Z",
        "connectionString": "snap.datawarehouse.fabric.microsoft.com",
    },
}

# Legacy detail payload as returned by GET /workspaces/{ws}/items/{id}
# (generic items endpoint — uses "creationPayload")
WAREHOUSE_SNAPSHOT_DETAIL_PAYLOAD: dict[str, Any] = {
    "id": str(_SNAP_ID),
    "displayName": "SalesWarehouse_Snapshot_20240315",
    "type": "WarehouseSnapshot",
    "workspaceId": str(_WS_ID),
    "creationPayload": {
        "parentWarehouseId": str(_PARENT_WH_ID),
        "snapshotDateTime": "2024-03-15T08:00:00Z",
    },
}

# Snapshot whose parent does NOT match _PARENT_WH_ID (should be filtered out)
_OTHER_SNAP_ID = UUID("22222222-3333-4444-5555-666666666666")
WAREHOUSE_SNAPSHOT_OTHER_PARENT_TYPED_PAYLOAD: dict[str, Any] = {
    "id": str(_OTHER_SNAP_ID),
    "displayName": "OtherWarehouse_Snapshot",
    "type": "WarehouseSnapshot",
    "workspaceId": str(_WS_ID),
    "properties": {
        "parentWarehouseId": str(_OTHER_WH_ID),
        "snapshotDateTime": "2024-03-15T09:00:00Z",
        "connectionString": "other.datawarehouse.fabric.microsoft.com",
    },
}

# warehouseSnapshots list page 1 (two snapshots — matching + non-matching parent)
TYPED_SNAPS_LIST_PAYLOAD: dict[str, Any] = {
    "value": [
        {
            "id": str(_SNAP_ID),
            "displayName": "SalesWarehouse_Snapshot_20240315",
            "type": "WarehouseSnapshot",
            "workspaceId": str(_WS_ID),
            "properties": {
                "parentWarehouseId": str(_PARENT_WH_ID),
                "snapshotDateTime": "2024-03-15T08:00:00Z",
                "connectionString": "snap.datawarehouse.fabric.microsoft.com",
            },
        },
        {
            "id": str(_OTHER_SNAP_ID),
            "displayName": "OtherWarehouse_Snapshot",
            "type": "WarehouseSnapshot",
            "workspaceId": str(_WS_ID),
            "properties": {
                "parentWarehouseId": str(_OTHER_WH_ID),
                "snapshotDateTime": "2024-03-15T09:00:00Z",
                "connectionString": "other.datawarehouse.fabric.microsoft.com",
            },
        },
    ]
}

# Create operation LRO payload (202 response with Location header, then polling)
WAREHOUSE_SNAPSHOT_CREATE_OPERATION_PAYLOAD: dict[str, Any] = {
    "status": "Succeeded",
    "createdTimeUtc": "2024-03-15T10:00:00Z",
    "lastUpdatedTimeUtc": "2024-03-15T10:01:00Z",
    "percentComplete": 100,
    "error": None,
}

_LRO_LOCATION = f"{_BASE_URL}/operations/op-abc-123"


def _make_sql_target() -> SqlTarget:
    return SqlTarget(
        workspace_id=str(_WS_ID),
        database="SalesWarehouse",
        connection_string="Server=saleswarehouse.datawarehouse.fabric.microsoft.com",
    )


def _make_mock_conn() -> MagicMock:
    """Return a mock connection with execute_nonquery-like behaviour."""
    cursor = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress asyncio.sleep for all snapshot unit tests to keep the suite fast."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


async def test_list_pages_through_and_filters_by_parent() -> None:
    """list should use /warehouseSnapshots, page through results, and filter by parent."""
    page2_url = f"{_TYPED_SNAPS_URL}?continuationToken=page2"

    page1_payload = dict(TYPED_SNAPS_LIST_PAYLOAD)
    page1_payload["continuationUri"] = page2_url
    page2_payload: dict[str, Any] = {"value": []}

    call_count = 0

    def _side_effect(_request: Any) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=page1_payload)
        return httpx.Response(200, json=page2_payload)

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(
            url__regex=rf"https://api\.fabric\.microsoft\.com/v1/workspaces/{_WS_ID}/warehouseSnapshots(\?.*)?$"
        ).mock(side_effect=_side_effect)

        async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
            result = await snapshots.list_snapshots(http, _WS_ID, _PARENT_WH_ID)

    assert len(result) == 1
    snap = result[0]
    assert isinstance(snap, WarehouseSnapshot)
    assert snap.id == _SNAP_ID
    assert snap.parent_warehouse_id == _PARENT_WH_ID
    assert snap.name == "SalesWarehouse_Snapshot_20240315"


async def test_list_returns_empty_when_no_snapshots() -> None:
    """list returns empty list when the workspace has no warehouseSnapshots."""
    payload: dict[str, Any] = {"value": []}
    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(
            url__regex=rf"https://api\.fabric\.microsoft\.com/v1/workspaces/{_WS_ID}/warehouseSnapshots(\?.*)?$"
        ).mock(return_value=httpx.Response(200, json=payload))

        async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
            result = await snapshots.list_snapshots(http, _WS_ID, _PARENT_WH_ID)

    assert result == []


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@respx.mock
async def test_create_happy_path_with_snapshot_dt() -> None:
    """create should POST with snapshotDateTime, poll LRO, GET typed endpoint, return snapshot."""
    snap_dt = datetime(2024, 3, 15, 8, 0, 0, tzinfo=UTC)

    # POST → 202 with Location header
    respx.post(_ITEMS_URL).mock(
        return_value=httpx.Response(202, headers={"Location": _LRO_LOCATION})
    )
    # LRO poll → Succeeded
    respx.get(_LRO_LOCATION).mock(
        return_value=httpx.Response(200, json=WAREHOUSE_SNAPSHOT_CREATE_OPERATION_PAYLOAD)
    )
    # GET newly created snapshot via typed endpoint
    respx.get(_TYPED_SNAP_URL).mock(
        return_value=httpx.Response(200, json=WAREHOUSE_SNAPSHOT_TYPED_PAYLOAD)
    )

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        # Patch poll_operation to avoid sleeping
        with patch.object(http, "poll_operation", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {"status": "Succeeded", "resourceId": str(_SNAP_ID)}

            result = await snapshots.create(
                http,
                _WS_ID,
                _PARENT_WH_ID,
                "NewSnapshot",
                description="A new snapshot",
                snapshot_dt=snap_dt,
            )

    assert isinstance(result, WarehouseSnapshot)
    assert result.parent_warehouse_id == _PARENT_WH_ID


@respx.mock
async def test_create_happy_path_without_snapshot_dt() -> None:
    """create without snapshot_dt should omit snapshotDateTime from body."""
    captured_requests: list[Any] = []

    def _capture(request: Any) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(202, headers={"Location": _LRO_LOCATION})

    respx.post(_ITEMS_URL).mock(side_effect=_capture)
    respx.get(_TYPED_SNAP_URL).mock(
        return_value=httpx.Response(200, json=WAREHOUSE_SNAPSHOT_TYPED_PAYLOAD)
    )

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with patch.object(http, "poll_operation", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {"status": "Succeeded", "resourceId": str(_SNAP_ID)}

            result = await snapshots.create(
                http,
                _WS_ID,
                _PARENT_WH_ID,
                "NewSnapshot",
            )

    assert isinstance(result, WarehouseSnapshot)
    # Verify snapshotDateTime not sent
    assert len(captured_requests) == 1
    body = _json.loads(captured_requests[0].content)
    assert "snapshotDateTime" not in body.get("creationPayload", {})


@respx.mock
async def test_create_empty_name_raises_value_error() -> None:
    """create with an empty or whitespace name should raise ValueError."""
    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(ValueError, match="name"):
            await snapshots.create(http, _WS_ID, _PARENT_WH_ID, "")

        with pytest.raises(ValueError, match="name"):
            await snapshots.create(http, _WS_ID, _PARENT_WH_ID, "   ")


@respx.mock
async def test_create_posts_correct_body_with_snapshot_dt() -> None:
    """create should send type, displayName, description, and creationPayload."""
    snap_dt = datetime(2024, 3, 15, 8, 0, 0, tzinfo=UTC)
    captured_requests: list[Any] = []

    def _capture(request: Any) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(202, headers={"Location": _LRO_LOCATION})

    respx.post(_ITEMS_URL).mock(side_effect=_capture)
    respx.get(_TYPED_SNAP_URL).mock(
        return_value=httpx.Response(200, json=WAREHOUSE_SNAPSHOT_TYPED_PAYLOAD)
    )

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with patch.object(http, "poll_operation", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {"status": "Succeeded", "resourceId": str(_SNAP_ID)}

            await snapshots.create(
                http,
                _WS_ID,
                _PARENT_WH_ID,
                "MySnap",
                description="desc",
                snapshot_dt=snap_dt,
            )

    body = _json.loads(captured_requests[0].content)
    assert body["type"] == "WarehouseSnapshot"
    assert body["displayName"] == "MySnap"
    assert body["description"] == "desc"
    assert body["creationPayload"]["parentWarehouseId"] == str(_PARENT_WH_ID)
    assert body["creationPayload"]["snapshotDateTime"] == "2024-03-15T08:00:00Z"


@respx.mock
async def test_create_polls_lro_and_fetches_result() -> None:
    """create should call poll_operation with the Location header URL."""
    respx.post(_ITEMS_URL).mock(
        return_value=httpx.Response(202, headers={"Location": _LRO_LOCATION})
    )
    respx.get(_TYPED_SNAP_URL).mock(
        return_value=httpx.Response(200, json=WAREHOUSE_SNAPSHOT_TYPED_PAYLOAD)
    )

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with patch.object(http, "poll_operation", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {"status": "Succeeded", "resourceId": str(_SNAP_ID)}

            await snapshots.create(http, _WS_ID, _PARENT_WH_ID, "MySnap")

        mock_poll.assert_awaited_once_with(_LRO_LOCATION)


_LRO_OP_ID = "b80e135a-adca-42e7-aaf0-59849af2ed78"
_LRO_RESULT_URL = f"{_BASE_URL}/operations/{_LRO_OP_ID}/result"
_LRO_LOCATION_BY_OP_ID = f"{_BASE_URL}/operations/{_LRO_OP_ID}"


@respx.mock
async def test_create_retries_detail_when_creation_payload_missing() -> None:
    """create retries GET /warehouseSnapshots/{id} when parentWarehouseId is initially None.

    Regression: Fabric occasionally returns the item detail without properties.parentWarehouseId
    populated immediately after the LRO completes (provisioning lag). create() must
    retry the detail GET until parentWarehouseId is present.
    """
    # First detail response has properties but parentWarehouseId is None
    detail_without_parent: dict[str, Any] = {
        "id": str(_SNAP_ID),
        "displayName": "NewSnapshot",
        "type": "WarehouseSnapshot",
        "workspaceId": str(_WS_ID),
        "properties": {
            "parentWarehouseId": None,
            "snapshotDateTime": None,
            "connectionString": None,
        },
    }

    detail_call_count = 0

    def _detail_side_effect(_request: Any) -> httpx.Response:
        nonlocal detail_call_count
        detail_call_count += 1
        if detail_call_count < 2:
            return httpx.Response(200, json=detail_without_parent)
        return httpx.Response(200, json=WAREHOUSE_SNAPSHOT_TYPED_PAYLOAD)

    respx.post(_ITEMS_URL).mock(
        return_value=httpx.Response(202, headers={"Location": _LRO_LOCATION})
    )
    respx.get(_TYPED_SNAP_URL).mock(side_effect=_detail_side_effect)

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with patch.object(http, "poll_operation", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {"status": "Succeeded", "resourceId": str(_SNAP_ID)}
            result = await snapshots.create(http, _WS_ID, _PARENT_WH_ID, "NewSnapshot")

    assert detail_call_count == 2
    assert isinstance(result, WarehouseSnapshot)
    assert result.parent_warehouse_id == _PARENT_WH_ID


@respx.mock
async def test_create_uses_operation_result_endpoint_when_no_resource_id() -> None:
    """create falls back to GET /operations/{id}/result when LRO status body has no resourceId.

    Regression: Fabric LRO status bodies only contain status metadata; the created item
    ID is available at GET /operations/{op_id}/result, not in the status body.
    """
    # Status body as Fabric actually returns — no resourceId, no createdItemId
    lro_status_body = {
        "status": "Succeeded",
        "createdTimeUtc": "2026-06-08T06:33:58.6740792",
        "lastUpdatedTimeUtc": "2026-06-08T06:34:09.4083015",
        "percentComplete": 100,
        "error": None,
    }
    # /result endpoint returns the newly created item
    lro_result_body = {
        "id": str(_SNAP_ID),
        "type": "WarehouseSnapshot",
        "displayName": "NewSnapshot",
        "workspaceId": str(_WS_ID),
    }

    respx.post(_ITEMS_URL).mock(
        return_value=httpx.Response(202, headers={"Location": _LRO_LOCATION_BY_OP_ID})
    )
    respx.get(_LRO_RESULT_URL).mock(return_value=httpx.Response(200, json=lro_result_body))
    respx.get(f"{_BASE_URL}/workspaces/{_WS_ID}/warehouseSnapshots/{_SNAP_ID}").mock(
        return_value=httpx.Response(200, json=WAREHOUSE_SNAPSHOT_TYPED_PAYLOAD)
    )

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with patch.object(http, "poll_operation", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = lro_status_body

            result = await snapshots.create(http, _WS_ID, _PARENT_WH_ID, "NewSnapshot")

    assert isinstance(result, WarehouseSnapshot)
    assert result.id == _SNAP_ID


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


@respx.mock
async def test_rename_fetches_existing_then_patches_with_all_fields() -> None:
    """rename should GET /warehouseSnapshots/{id}, PATCH /items/{id} with all required fields."""
    patch_url = f"{_ITEMS_URL}/{_SNAP_ID}"

    captured_patch_requests: list[Any] = []

    def _patch_capture(request: Any) -> httpx.Response:
        captured_patch_requests.append(request)
        return httpx.Response(200, json={})

    # GET to fetch existing snapshot detail (typed endpoint)
    respx.get(_TYPED_SNAP_URL).mock(
        return_value=httpx.Response(200, json=WAREHOUSE_SNAPSHOT_TYPED_PAYLOAD)
    )
    # PATCH for rename
    respx.patch(patch_url).mock(side_effect=_patch_capture)

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await snapshots.rename(
            http,
            _WS_ID,
            _SNAP_ID,
            new_name="RenamedSnapshot",
            description="new desc",
        )

    assert isinstance(result, WarehouseSnapshot)

    # Verify PATCH was called
    assert len(captured_patch_requests) == 1
    body = _json.loads(captured_patch_requests[0].content)
    assert body["type"] == "WarehouseSnapshot"
    assert body["displayName"] == "RenamedSnapshot"
    assert body["description"] == "new desc"
    assert "creationPayload" in body
    assert body["creationPayload"]["parentWarehouseId"] == str(_PARENT_WH_ID)


@respx.mock
async def test_rename_empty_new_name_raises_value_error() -> None:
    """rename with an empty new_name should raise ValueError."""
    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(ValueError, match="new_name"):
            await snapshots.rename(http, _WS_ID, _SNAP_ID, new_name="")

        with pytest.raises(ValueError, match="new_name"):
            await snapshots.rename(http, _WS_ID, _SNAP_ID, new_name="   ")


@respx.mock
async def test_rename_returns_updated_warehouse_snapshot() -> None:
    """rename should return the refreshed WarehouseSnapshot after PATCH."""
    updated_typed = {
        **WAREHOUSE_SNAPSHOT_TYPED_PAYLOAD,
        "displayName": "RenamedSnapshot",
    }

    snap_url = _TYPED_SNAP_URL
    call_count = 0

    def _get_side_effect(_request: Any) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=WAREHOUSE_SNAPSHOT_TYPED_PAYLOAD)
        return httpx.Response(200, json=updated_typed)

    respx.get(snap_url).mock(side_effect=_get_side_effect)
    respx.patch(f"{_ITEMS_URL}/{_SNAP_ID}").mock(return_value=httpx.Response(200, json={}))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        result = await snapshots.rename(
            http,
            _WS_ID,
            _SNAP_ID,
            new_name="RenamedSnapshot",
        )

    assert isinstance(result, WarehouseSnapshot)
    assert result.name == "RenamedSnapshot"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@respx.mock
async def test_delete_204_returns_none() -> None:
    """delete should issue DELETE and return None on 204."""
    respx.delete(f"{_ITEMS_URL}/{_SNAP_ID}").mock(return_value=httpx.Response(204))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        await snapshots.delete(http, _WS_ID, _SNAP_ID)


@respx.mock
async def test_delete_404_raises_not_found() -> None:
    """delete should propagate NotFoundError on 404."""
    respx.delete(f"{_ITEMS_URL}/{_SNAP_ID}").mock(return_value=httpx.Response(404))

    async with FabricHttpClient(credential=_make_credential(), rps=100) as http:
        with pytest.raises(NotFoundError):
            await snapshots.delete(http, _WS_ID, _SNAP_ID)


# ---------------------------------------------------------------------------
# roll_timestamp
# ---------------------------------------------------------------------------


async def test_roll_timestamp_without_new_dt_uses_current_timestamp() -> None:
    """roll_timestamp with new_dt=None should use CURRENT_TIMESTAMP on an autocommit connection.

    The ODBC driver wraps every cursor.execute() in an explicit BEGIN/COMMIT
    transaction when autocommit=False (the default).  SQL Server error 226 fires
    on ALTER DATABASE inside any explicit transaction.  The fix is to open the
    connection with autocommit=True at the ODBC layer, which bypasses all
    transaction wrapping.

    This test pins that open_connection is called with autocommit=True and that
    exactly ONE SQL statement is executed (the ALTER DATABASE — no preceding
    SET IMPLICIT_TRANSACTIONS OFF is needed or present).
    """
    target = _make_sql_target()
    conn = _make_mock_conn()

    with patch("fabric_dw.sql.open_connection", return_value=conn) as mock_open:
        await snapshots.roll_timestamp(target, "MySnapshot")

    # Verify ODBC-level autocommit was requested — this is the critical assertion.
    mock_open.assert_called_once_with(
        target, mode=snapshots.CredentialMode.DEFAULT, autocommit=True
    )

    cursor = conn.cursor.return_value
    # Only ONE statement: the ALTER DATABASE itself (no SET IMPLICIT_TRANSACTIONS OFF).
    assert cursor.execute.call_count == 1
    executed_sql = cursor.execute.call_args_list[0][0][0]
    assert "ALTER DATABASE [MySnapshot] SET TIMESTAMP = CURRENT_TIMESTAMP;" in executed_sql
    assert "SET IMPLICIT_TRANSACTIONS" not in executed_sql


async def test_roll_timestamp_with_new_dt_formats_correctly() -> None:
    """roll_timestamp with a datetime should format as YYYY-MM-DDTHH:MM:SS.SS.

    Verifies the connection is opened with autocommit=True and the ALTER DATABASE
    is the sole executed statement (no SET IMPLICIT_TRANSACTIONS OFF preamble).
    """
    target = _make_sql_target()
    conn = _make_mock_conn()
    new_dt = datetime(2024, 3, 15, 8, 30, 45, tzinfo=UTC)

    with patch("fabric_dw.sql.open_connection", return_value=conn) as mock_open:
        await snapshots.roll_timestamp(target, "MySnapshot", new_dt=new_dt)

    # Autocommit must be True — the ODBC driver must not wrap ALTER DATABASE
    # in an explicit transaction (that would trigger SQL Server error 226).
    mock_open.assert_called_once_with(
        target, mode=snapshots.CredentialMode.DEFAULT, autocommit=True
    )

    cursor = conn.cursor.return_value
    assert cursor.execute.call_count == 1
    executed_sql = cursor.execute.call_args_list[0][0][0]
    assert "ALTER DATABASE [MySnapshot] SET TIMESTAMP = '2024-03-15T08:30:45.00';" in executed_sql
    assert "SET IMPLICIT_TRANSACTIONS" not in executed_sql


async def test_roll_timestamp_name_injection_bracket() -> None:
    """snapshot_name containing ] should raise ValueError."""
    target = _make_sql_target()
    conn = _make_mock_conn()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="snapshot_name"),
    ):
        await snapshots.roll_timestamp(target, "My]Snapshot")


async def test_roll_timestamp_name_injection_semicolon() -> None:
    """snapshot_name containing ; should raise ValueError."""
    target = _make_sql_target()
    conn = _make_mock_conn()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="snapshot_name"),
    ):
        await snapshots.roll_timestamp(target, "My;Snapshot")


async def test_roll_timestamp_name_injection_backslash() -> None:
    """snapshot_name containing \\ should raise ValueError."""
    target = _make_sql_target()
    conn = _make_mock_conn()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="snapshot_name"),
    ):
        await snapshots.roll_timestamp(target, "My\\Snapshot")


async def test_roll_timestamp_name_injection_single_quote() -> None:
    """snapshot_name containing ' should raise ValueError."""
    target = _make_sql_target()
    conn = _make_mock_conn()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="snapshot_name"),
    ):
        await snapshots.roll_timestamp(target, "My'Snapshot")


async def test_roll_timestamp_name_injection_double_quote() -> None:
    """snapshot_name containing \" should raise ValueError."""
    target = _make_sql_target()
    conn = _make_mock_conn()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="snapshot_name"),
    ):
        await snapshots.roll_timestamp(target, 'My"Snapshot')


async def test_roll_timestamp_name_injection_double_dash() -> None:
    """snapshot_name containing -- should raise ValueError."""
    target = _make_sql_target()
    conn = _make_mock_conn()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="snapshot_name"),
    ):
        await snapshots.roll_timestamp(target, "My--Snapshot")


async def test_roll_timestamp_name_injection_newline() -> None:
    """snapshot_name containing newline should raise ValueError."""
    target = _make_sql_target()
    conn = _make_mock_conn()

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(ValueError, match="snapshot_name"),
    ):
        await snapshots.roll_timestamp(target, "My\nSnapshot")


async def test_roll_timestamp_maps_permission_error() -> None:
    """roll_timestamp should map driver permission failures to PermissionDeniedError."""
    target = _make_sql_target()
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("permission was denied on ALTER DATABASE")
    conn.cursor.return_value = cursor

    with (
        patch("fabric_dw.sql.open_connection", return_value=conn),
        pytest.raises(PermissionDeniedError),
    ):
        await snapshots.roll_timestamp(target, "MySnapshot")


async def test_roll_timestamp_closes_connection() -> None:
    """roll_timestamp should close the connection after use."""
    target = _make_sql_target()
    conn = _make_mock_conn()

    with patch("fabric_dw.sql.open_connection", return_value=conn):
        await snapshots.roll_timestamp(target, "MySnapshot")

    conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# roll_timestamp — snapshot-not-ready retry
# ---------------------------------------------------------------------------

# The transient error message the Fabric TDS endpoint emits while a freshly
# created snapshot DB is still provisioning at the SQL layer.
_SNAP_NOT_READY_MSG = (
    "User does not have permission to alter database 'my-snap', "
    "the database does not exist, or the database is not in a state "
    "that allows access checks."
)


async def test_roll_timestamp_retries_once_on_snapshot_not_ready() -> None:
    """roll_timestamp retries once when run_statements raises the not-ready error, then succeeds."""
    target = _make_sql_target()
    transient_exc = PermissionDeniedError(_SNAP_NOT_READY_MSG)

    call_count = 0

    def _run_side_effect(*_args: object, **_kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise transient_exc

    # Freeze monotonic time so the deadline is never hit.
    fake_now = [0.0]

    def _fake_monotonic() -> float:
        return fake_now[0]

    with (
        patch("fabric_dw.services.snapshots.run_statements", side_effect=_run_side_effect),
        patch("time.monotonic", side_effect=_fake_monotonic),
    ):
        await snapshots.roll_timestamp(target, "MySnapshot")

    assert call_count == 2


async def test_roll_timestamp_retries_twice_on_snapshot_not_ready() -> None:
    """roll_timestamp retries up to N times, succeeding on the third attempt."""
    target = _make_sql_target()
    transient_exc = PermissionDeniedError(_SNAP_NOT_READY_MSG)

    call_count = 0

    def _run_side_effect(*_args: object, **_kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise transient_exc

    fake_now = [0.0]

    def _fake_monotonic() -> float:
        return fake_now[0]

    with (
        patch("fabric_dw.services.snapshots.run_statements", side_effect=_run_side_effect),
        patch("time.monotonic", side_effect=_fake_monotonic),
    ):
        await snapshots.roll_timestamp(target, "MySnapshot")

    assert call_count == 3


async def test_roll_timestamp_raises_fabric_error_on_timeout() -> None:
    """roll_timestamp raises FabricError once the timeout budget is exhausted."""
    target = _make_sql_target()
    transient_exc = PermissionDeniedError(_SNAP_NOT_READY_MSG)

    # Always raise the transient error.
    def _run_always_fails(*_args: object, **_kwargs: object) -> None:
        raise transient_exc

    # Simulate time advancing past the deadline on the second monotonic() call
    # (first call sets the deadline, second call is checked inside the loop).
    # Return a large value for any additional calls (teardown etc.).
    timeout = snapshots._SNAP_READY_TIMEOUT_S
    call_counter = [0]

    def _fake_monotonic() -> float:
        call_counter[0] += 1
        if call_counter[0] == 1:
            return 0.0  # initial deadline computation
        return timeout + 1.0  # past deadline on every subsequent check

    with (
        patch("fabric_dw.services.snapshots.run_statements", side_effect=_run_always_fails),
        patch("time.monotonic", side_effect=_fake_monotonic),
        pytest.raises(FabricError, match="did not become ready"),
    ):
        await snapshots.roll_timestamp(target, "MySnapshot")


async def test_roll_timestamp_does_not_retry_real_permission_error() -> None:
    """roll_timestamp propagates real PermissionDeniedError without retrying."""
    target = _make_sql_target()
    real_permission_exc = PermissionDeniedError("permission was denied on ALTER DATABASE")

    call_count = 0

    def _run_side_effect(*_args: object, **_kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        raise real_permission_exc

    fake_now = [0.0]

    def _fake_monotonic() -> float:
        return fake_now[0]

    with (
        patch("fabric_dw.services.snapshots.run_statements", side_effect=_run_side_effect),
        patch("time.monotonic", side_effect=_fake_monotonic),
        pytest.raises(PermissionDeniedError),
    ):
        await snapshots.roll_timestamp(target, "MySnapshot")

    # Must NOT retry a real permission error.
    assert call_count == 1


# ---------------------------------------------------------------------------
# rename — cache eviction
# ---------------------------------------------------------------------------


def _make_snap_cache_entry(tmp_path: Path) -> tuple[LookupCache, ItemEntry]:
    cache = LookupCache(path=tmp_path / "lookup.json")
    entry = ItemEntry(
        id=_SNAP_ID,
        kind=WarehouseKind.SNAPSHOT,
        connection_string=None,
        fetched_at=datetime.now(tz=UTC),
        display_name="MySnapshot",
    )
    cache.put_item(_WS_ID, "MySnapshot", entry)
    cache.put_item(_WS_ID, str(_SNAP_ID), entry)
    return cache, entry


_RENAME_TYPED_RESP: dict[str, object] = {
    "id": str(_SNAP_ID),
    "displayName": "RenamedSnapshot",
    "type": "WarehouseSnapshot",
    "workspaceId": str(_WS_ID),
    "properties": {
        "parentWarehouseId": str(_PARENT_WH_ID),
        "snapshotDateTime": "2024-03-15T08:00:00Z",
    },
}


async def test_rename_evicts_old_name_and_inserts_new_name(tmp_path: Path) -> None:
    """rename with cache must evict old name and populate new name."""
    cache, _entry = _make_snap_cache_entry(tmp_path)

    with respx.mock:
        # GET current snapshot (to read parentWarehouseId)
        respx.get(_TYPED_SNAP_URL).mock(return_value=httpx.Response(200, json=_RENAME_TYPED_RESP))
        # PATCH rename
        respx.patch(f"{_BASE_URL}/workspaces/{_WS_ID}/items/{_SNAP_ID}").mock(
            return_value=httpx.Response(200, json={})
        )

        cred = _make_credential()
        async with FabricHttpClient(credential=cred) as http:
            await snapshots.rename(
                http,
                _WS_ID,
                _SNAP_ID,
                new_name="RenamedSnapshot",
                cache=cache,
                old_name="MySnapshot",
            )

    assert cache.get_item(_WS_ID, "MySnapshot") is None
    assert cache.get_item(_WS_ID, "RenamedSnapshot") is not None
    renamed_entry = cache.get_item(_WS_ID, str(_SNAP_ID))
    assert renamed_entry is not None
    assert renamed_entry.display_name == "RenamedSnapshot"


async def test_rename_without_cache_does_not_raise(tmp_path: Path) -> None:
    """rename without cache= must still complete successfully."""
    _ = tmp_path

    with respx.mock:
        respx.get(_TYPED_SNAP_URL).mock(return_value=httpx.Response(200, json=_RENAME_TYPED_RESP))
        respx.patch(f"{_BASE_URL}/workspaces/{_WS_ID}/items/{_SNAP_ID}").mock(
            return_value=httpx.Response(200, json={})
        )

        cred = _make_credential()
        async with FabricHttpClient(credential=cred) as http:
            result = await snapshots.rename(http, _WS_ID, _SNAP_ID, new_name="RenamedSnapshot")

    assert result.name == "RenamedSnapshot"


# ---------------------------------------------------------------------------
# delete — cache eviction
# ---------------------------------------------------------------------------


async def test_delete_evicts_name_from_cache(tmp_path: Path) -> None:
    """delete with cache= must evict both the name entry and the GUID entry."""
    cache, _entry = _make_snap_cache_entry(tmp_path)

    with respx.mock:
        respx.delete(f"{_BASE_URL}/workspaces/{_WS_ID}/items/{_SNAP_ID}").mock(
            return_value=httpx.Response(204)
        )

        cred = _make_credential()
        async with FabricHttpClient(credential=cred) as http:
            await snapshots.delete(
                http,
                _WS_ID,
                _SNAP_ID,
                cache=cache,
                name="MySnapshot",
            )

    assert cache.get_item(_WS_ID, "MySnapshot") is None
    assert cache.get_item(_WS_ID, str(_SNAP_ID)) is None


async def test_delete_without_cache_does_not_raise(tmp_path: Path) -> None:
    """delete without cache= must still complete successfully."""
    _ = tmp_path

    with respx.mock:
        respx.delete(f"{_BASE_URL}/workspaces/{_WS_ID}/items/{_SNAP_ID}").mock(
            return_value=httpx.Response(204)
        )

        cred = _make_credential()
        async with FabricHttpClient(credential=cred) as http:
            await snapshots.delete(http, _WS_ID, _SNAP_ID)
