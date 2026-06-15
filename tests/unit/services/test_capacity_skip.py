"""TDD tests for issue #401: silently skip paused/no-capacity workspaces in -A scans.

Covers:
- Proactive capacity-state filter via GET /v1/capacities.
- Inactive-capacity workspace is skipped without issuing the warehouses/endpoints call.
- Active-capacity workspace is still aggregated.
- GET /v1/capacities 403 falls back gracefully (no crash, defensive path).
- Defensive: workspace returning 500 isRetriable:false is skipped silently (not fatal).
- HTTP client does NOT retry a non-retriable 5xx (FabricServerError.is_retriable=False).
- Genuine 403/404 still skip as before (WARNING level).
- An unexpected error still surfaces (propagates).
- Null capacityId workspace is proactively skipped.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import httpx
import pytest
import respx

from fabric_dw.exceptions import (
    FabricServerError,
    PermissionDeniedError,
)
from fabric_dw.http_client import HttpBase
from fabric_dw.models import Warehouse, WarehouseKind, Workspace
from tests.unit.services._helpers import _make_client

# ---------------------------------------------------------------------------
# Shared IDs
# ---------------------------------------------------------------------------

_CAP_ACTIVE = UUID("aaaaaaaa-cafe-0000-0000-000000000001")
_CAP_INACTIVE = UUID("bbbbbbbb-cafe-0000-0000-000000000002")

_WS_ACTIVE = UUID("aaaaaaaa-0000-0000-0000-000000000001")
_WS_INACTIVE = UUID("bbbbbbbb-0000-0000-0000-000000000002")
_WS_NO_CAP = UUID("cccccccc-0000-0000-0000-000000000003")

_WH_ACTIVE = UUID("aaaaaaaa-1111-0000-0000-000000000001")

_BASE = "https://api.fabric.microsoft.com/v1"
_CAPACITIES_URL = f"{_BASE}/capacities"
_WORKSPACES_URL = f"{_BASE}/workspaces"
_WAREHOUSES_ACTIVE_URL = f"{_BASE}/workspaces/{_WS_ACTIVE}/warehouses"
_WAREHOUSES_INACTIVE_URL = f"{_BASE}/workspaces/{_WS_INACTIVE}/warehouses"
_SQL_ACTIVE_URL = f"{_BASE}/workspaces/{_WS_ACTIVE}/sqlEndpoints"
_SQL_INACTIVE_URL = f"{_BASE}/workspaces/{_WS_INACTIVE}/sqlEndpoints"

# ---------------------------------------------------------------------------
# Model factories
# ---------------------------------------------------------------------------


def _make_workspace(ws_id: UUID, cap_id: UUID | None = None) -> Workspace:
    return Workspace.model_validate(
        {
            "id": str(ws_id),
            "displayName": f"WS-{ws_id}",
            "description": None,
            "capacityId": str(cap_id) if cap_id else None,
        }
    )


def _make_wh(ws_id: UUID, wh_id: UUID) -> Warehouse:
    return Warehouse.model_validate(
        {
            "id": str(wh_id),
            "displayName": "WH",
            "workspaceId": str(ws_id),
            "kind": WarehouseKind.WAREHOUSE,
            "connectionString": "wh.fabric.microsoft.com",
        }
    )


def _make_capacity_states(
    *,
    active: UUID = _CAP_ACTIVE,
    inactive: UUID = _CAP_INACTIVE,
) -> dict[str, str]:
    """Return a minimal capacity-states dict (as returned by get_capacity_states)."""
    return {
        str(active).lower(): "Active",
        str(inactive).lower(): "Inactive",
    }


# ---------------------------------------------------------------------------
# (a) Proactive skip: inactive-capacity workspace does NOT trigger warehouses call
# ---------------------------------------------------------------------------


async def test_proactive_skip_inactive_capacity_no_warehouses_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A workspace whose capacity is Inactive must be skipped WITHOUT issuing the
    warehouses call (assert no request made for it).
    """
    from fabric_dw.services.warehouses import list_all_workspaces  # noqa: PLC0415

    ws_active = _make_workspace(_WS_ACTIVE, _CAP_ACTIVE)
    ws_inactive = _make_workspace(_WS_INACTIVE, _CAP_INACTIVE)
    wh_active = _make_wh(_WS_ACTIVE, _WH_ACTIVE)

    fetch_call_ids: list[UUID] = []

    async def _fetch_spy(_http: object, ws_id: UUID) -> list[Warehouse]:
        fetch_call_ids.append(ws_id)
        return [wh_active] if ws_id == _WS_ACTIVE else []

    with (
        caplog.at_level(logging.DEBUG, logger="fabric_dw.warehouses"),
        patch(
            "fabric_dw.services.warehouses._list_all_workspaces",
            new=AsyncMock(return_value=[ws_active, ws_inactive]),
        ),
        patch(
            "fabric_dw.services.warehouses.get_capacity_states",
            new=AsyncMock(return_value=_make_capacity_states()),
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(side_effect=_fetch_spy),
        ),
    ):
        result = await list_all_workspaces(AsyncMock())

    # Only the active workspace's warehouse should be returned.
    assert len(result) == 1
    assert result[0].id == _WH_ACTIVE
    # The inactive workspace's fetch must never have been called.
    assert _WS_ACTIVE in fetch_call_ids
    assert _WS_INACTIVE not in fetch_call_ids


# ---------------------------------------------------------------------------
# (b) Active-capacity workspace is still aggregated
# ---------------------------------------------------------------------------


async def test_active_capacity_workspace_is_aggregated() -> None:
    """A workspace with an Active capacity must be included in the result."""
    from fabric_dw.services.warehouses import list_all_workspaces  # noqa: PLC0415

    ws_active = _make_workspace(_WS_ACTIVE, _CAP_ACTIVE)
    wh_active = _make_wh(_WS_ACTIVE, _WH_ACTIVE)

    with (
        patch(
            "fabric_dw.services.warehouses._list_all_workspaces",
            new=AsyncMock(return_value=[ws_active]),
        ),
        patch(
            "fabric_dw.services.warehouses.get_capacity_states",
            new=AsyncMock(return_value=_make_capacity_states()),
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(return_value=[wh_active]),
        ),
    ):
        result = await list_all_workspaces(AsyncMock())

    assert len(result) == 1
    assert result[0].id == _WH_ACTIVE


# ---------------------------------------------------------------------------
# (c) Null capacityId: workspace with no capacity is proactively skipped
# ---------------------------------------------------------------------------


async def test_proactive_skip_null_capacity_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A workspace with capacityId=None must be proactively skipped when
    capacity_states is available.
    """
    from fabric_dw.services.warehouses import list_all_workspaces  # noqa: PLC0415

    ws_no_cap = _make_workspace(_WS_NO_CAP, None)  # no capacity attached
    ws_active = _make_workspace(_WS_ACTIVE, _CAP_ACTIVE)
    wh_active = _make_wh(_WS_ACTIVE, _WH_ACTIVE)

    fetch_call_ids: list[UUID] = []

    async def _fetch_spy(_http: object, ws_id: UUID) -> list[Warehouse]:
        fetch_call_ids.append(ws_id)
        return [wh_active]

    with (
        caplog.at_level(logging.DEBUG, logger="fabric_dw._helpers"),
        patch(
            "fabric_dw.services.warehouses._list_all_workspaces",
            new=AsyncMock(return_value=[ws_no_cap, ws_active]),
        ),
        patch(
            "fabric_dw.services.warehouses.get_capacity_states",
            new=AsyncMock(return_value=_make_capacity_states()),
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(side_effect=_fetch_spy),
        ),
    ):
        result = await list_all_workspaces(AsyncMock())

    # Only ws_active should be in the result.
    assert len(result) == 1
    assert result[0].id == _WH_ACTIVE
    # The no-capacity workspace must not have been fetched.
    assert _WS_NO_CAP not in fetch_call_ids
    assert _WS_ACTIVE in fetch_call_ids


# ---------------------------------------------------------------------------
# (d) GET /v1/capacities 403 → graceful fallback (no crash)
# ---------------------------------------------------------------------------


async def test_capacities_403_falls_back_gracefully() -> None:
    """When GET /v1/capacities returns 403, get_capacity_states returns None and
    list_all_workspaces falls back to the defensive path without crashing.
    """
    from fabric_dw.services.warehouses import list_all_workspaces  # noqa: PLC0415

    ws_active = _make_workspace(_WS_ACTIVE, _CAP_ACTIVE)
    wh_active = _make_wh(_WS_ACTIVE, _WH_ACTIVE)

    with (
        patch(
            "fabric_dw.services.warehouses._list_all_workspaces",
            new=AsyncMock(return_value=[ws_active]),
        ),
        patch(
            "fabric_dw.services.warehouses.get_capacity_states",
            new=AsyncMock(return_value=None),  # 403 fallback → None
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(return_value=[wh_active]),
        ),
    ):
        result = await list_all_workspaces(AsyncMock())

    # Should still return the warehouse when fallback applies.
    assert len(result) == 1
    assert result[0].id == _WH_ACTIVE


# ---------------------------------------------------------------------------
# (d2) get_capacity_states itself: 403 returns None without raising
# ---------------------------------------------------------------------------


async def test_get_capacity_states_403_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """get_capacity_states must return None (not raise) when the API returns 403."""
    from fabric_dw.services.capacities import get_capacity_states  # noqa: PLC0415

    with (
        caplog.at_level(logging.DEBUG, logger="fabric_dw.capacities"),
        respx.mock,
    ):
        respx.get(_CAPACITIES_URL).mock(
            return_value=httpx.Response(403, json={"error": "forbidden"})
        )

        client = await _make_client()
        async with client:
            result = await get_capacity_states(client)

    assert result is None
    assert any(
        "403" in r.message or "proactive capacity filtering unavailable" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# (d3) get_capacity_states: happy path returns correct mapping
# ---------------------------------------------------------------------------


async def test_get_capacity_states_returns_lowercase_id_map() -> None:
    """get_capacity_states must return a dict of {lower-cased-capacity-id: state}."""
    from fabric_dw.services.capacities import get_capacity_states  # noqa: PLC0415

    cap_payload: dict[str, Any] = {
        "value": [
            {
                "id": str(_CAP_ACTIVE).upper(),
                "displayName": "CI-Cap",
                "sku": "F2",
                "state": "Active",
            },
            {
                "id": str(_CAP_INACTIVE),
                "displayName": "Paused-Cap",
                "sku": "F4",
                "state": "Inactive",
            },
        ]
    }

    with respx.mock:
        respx.get(_CAPACITIES_URL).mock(return_value=httpx.Response(200, json=cap_payload))

        client = await _make_client()
        async with client:
            result = await get_capacity_states(client)

    assert result is not None
    assert result[str(_CAP_ACTIVE).lower()] == "Active"
    assert result[str(_CAP_INACTIVE).lower()] == "Inactive"


# ---------------------------------------------------------------------------
# (e) Defensive: non-retriable 500 is skipped silently (not fatal)
# ---------------------------------------------------------------------------


async def test_defensive_non_retriable_500_skipped_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When capacity_states is None (fallback mode), a workspace returning a
    non-retriable FabricServerError must be skipped at DEBUG level, not raised.
    """
    from fabric_dw.services.warehouses import list_all_workspaces  # noqa: PLC0415

    ws_a = _make_workspace(_WS_ACTIVE, _CAP_ACTIVE)
    ws_b = _make_workspace(_WS_INACTIVE, _CAP_INACTIVE)
    wh_a = _make_wh(_WS_ACTIVE, _WH_ACTIVE)

    # ws_b returns a non-retriable 500 (paused-capacity signature).
    non_retriable_err = FabricServerError(
        "Server error 500 ...: An error occured",
        status=500,
        body={
            "errorCode": "InternalServerError",
            "message": "An error occured",
            "isRetriable": False,
        },
        is_retriable=False,
    )

    with (
        caplog.at_level(logging.DEBUG, logger="fabric_dw.warehouses"),
        patch(
            "fabric_dw.services.warehouses._list_all_workspaces",
            new=AsyncMock(return_value=[ws_a, ws_b]),
        ),
        patch(
            "fabric_dw.services.warehouses.get_capacity_states",
            new=AsyncMock(return_value=None),  # defensive path (no capacity filter)
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(side_effect=[[wh_a], non_retriable_err]),
        ),
    ):
        result = await list_all_workspaces(AsyncMock())

    # ws_a's warehouse is returned; ws_b is silently skipped.
    assert len(result) == 1
    assert result[0].id == _WH_ACTIVE
    # The skip must be logged at DEBUG, not WARNING.
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("non-retriable" in r.message for r in debug_records)
    # No WARNING should mention the capacity skip.
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("non-retriable" in r.message for r in warning_records)


# ---------------------------------------------------------------------------
# (f) HTTP client does NOT retry a non-retriable 5xx
# ---------------------------------------------------------------------------


async def test_http_client_does_not_retry_non_retriable_500() -> None:
    """FabricHttpClient must NOT retry a 5xx response when isRetriable is false.

    The non-retriable error should propagate immediately (1 attempt, not 3).
    """
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            500,
            json={
                "errorCode": "InternalServerError",
                "message": "An error occured",
                "isRetriable": False,
            },
        )

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(f"{_WAREHOUSES_ACTIVE_URL}").mock(side_effect=side_effect)

        client = await _make_client()
        async with client:
            with pytest.raises(FabricServerError) as exc_info:
                await client.request(
                    "GET",
                    HttpBase.FABRIC,
                    f"/workspaces/{_WS_ACTIVE}/warehouses",
                )

    # Must fail fast (1 attempt), not retry 3x (which would take ~60-70s in prod).
    assert call_count == 1, f"Expected 1 attempt; got {call_count}"
    assert exc_info.value.is_retriable is False


async def test_http_client_retries_retriable_500() -> None:
    """FabricHttpClient must still retry a 5xx when isRetriable is true (or absent)."""
    call_count = 0

    def side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            500,
            json={
                "errorCode": "ServiceUnavailable",
                "message": "Service temporarily unavailable",
                "isRetriable": True,
            },
        )

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(f"{_WAREHOUSES_ACTIVE_URL}").mock(side_effect=side_effect)

        client = await _make_client()
        async with client:
            with pytest.raises(FabricServerError) as exc_info:
                await client.request(
                    "GET",
                    HttpBase.FABRIC,
                    f"/workspaces/{_WS_ACTIVE}/warehouses",
                )

    # Must retry up to 3 attempts.
    assert call_count == 3, f"Expected 3 attempts; got {call_count}"
    assert exc_info.value.is_retriable is True


# ---------------------------------------------------------------------------
# (g) Genuine 403/404 still skip as before (WARNING level)
# ---------------------------------------------------------------------------


async def test_genuine_403_still_warns(caplog: pytest.LogCaptureFixture) -> None:
    """A genuine PermissionDeniedError must still be skipped at WARNING level."""
    from fabric_dw.services.warehouses import list_all_workspaces  # noqa: PLC0415

    ws_a = _make_workspace(_WS_ACTIVE, _CAP_ACTIVE)
    ws_b = _make_workspace(_WS_INACTIVE, _CAP_INACTIVE)
    wh_a = _make_wh(_WS_ACTIVE, _WH_ACTIVE)

    with (
        caplog.at_level(logging.WARNING, logger="fabric_dw.warehouses"),
        patch(
            "fabric_dw.services.warehouses._list_all_workspaces",
            new=AsyncMock(return_value=[ws_a, ws_b]),
        ),
        patch(
            "fabric_dw.services.warehouses.get_capacity_states",
            new=AsyncMock(return_value=None),  # fallback mode
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(side_effect=[[wh_a], PermissionDeniedError("forbidden")]),
        ),
    ):
        result = await list_all_workspaces(AsyncMock())

    assert len(result) == 1
    assert result[0].id == _WH_ACTIVE
    # The 403 skip must log at WARNING.
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("skipping workspace" in r.message for r in warning_records)


# ---------------------------------------------------------------------------
# (h) An unexpected error still surfaces (propagates)
# ---------------------------------------------------------------------------


async def test_unexpected_error_propagates() -> None:
    """An unexpected exception (not skip_errors, not non-retriable 5xx) must propagate."""
    from fabric_dw.services.warehouses import list_all_workspaces  # noqa: PLC0415

    ws_a = _make_workspace(_WS_ACTIVE, _CAP_ACTIVE)

    class _UnexpectedError(Exception):
        pass

    with (
        patch(
            "fabric_dw.services.warehouses._list_all_workspaces",
            new=AsyncMock(return_value=[ws_a]),
        ),
        patch(
            "fabric_dw.services.warehouses.get_capacity_states",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(side_effect=_UnexpectedError("surprise")),
        ),
        pytest.raises(_UnexpectedError, match="surprise"),
    ):
        await list_all_workspaces(AsyncMock())


# ---------------------------------------------------------------------------
# (i) FabricServerError.is_retriable attribute
# ---------------------------------------------------------------------------


def test_fabric_server_error_default_is_retriable() -> None:
    """FabricServerError.is_retriable must default to True."""
    err = FabricServerError("oops", status=500)
    assert err.is_retriable is True


def test_fabric_server_error_explicit_non_retriable() -> None:
    """FabricServerError(is_retriable=False) must set the flag."""
    err = FabricServerError("oops", status=500, is_retriable=False)
    assert err.is_retriable is False


def test_capacity_unavailable_error_removed() -> None:
    """CapacityUnavailableError has been removed — the is_retriable-flag routing is the
    real mechanism.  Importing it must raise AttributeError (not ImportError).
    """
    import fabric_dw.exceptions as exc_mod  # noqa: PLC0415

    with pytest.raises(AttributeError):
        _ = exc_mod.CapacityUnavailableError  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# (j) http_client parses isRetriable from the Fabric error envelope
# ---------------------------------------------------------------------------


async def test_http_client_parses_is_retriable_false_from_body() -> None:
    """_map_status must parse isRetriable:false from the 5xx JSON body and set
    FabricServerError.is_retriable=False.
    """
    with respx.mock:
        respx.get(f"{_WAREHOUSES_ACTIVE_URL}").mock(
            return_value=httpx.Response(
                500,
                json={
                    "requestId": "abc",
                    "errorCode": "InternalServerError",
                    "message": "An error occured",
                    "isRetriable": False,
                },
            )
        )
        client = await _make_client()
        async with client:
            with pytest.raises(FabricServerError) as exc_info:
                await client.request(
                    "GET",
                    HttpBase.FABRIC,
                    f"/workspaces/{_WS_ACTIVE}/warehouses",
                )

    assert exc_info.value.is_retriable is False
    assert exc_info.value.status == 500


async def test_http_client_parses_is_retriable_true_from_body() -> None:
    """_map_status must parse isRetriable:true from the body and set is_retriable=True."""
    call_count = 0

    def _side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            500,
            json={"errorCode": "ServiceUnavailable", "isRetriable": True},
        )

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(f"{_WAREHOUSES_ACTIVE_URL}").mock(side_effect=_side_effect)

        client = await _make_client()
        async with client:
            with pytest.raises(FabricServerError) as exc_info:
                await client.request(
                    "GET",
                    HttpBase.FABRIC,
                    f"/workspaces/{_WS_ACTIVE}/warehouses",
                )

    # isRetriable:true -> 3 attempts before giving up.
    assert call_count == 3
    assert exc_info.value.is_retriable is True


# ---------------------------------------------------------------------------
# (k) Proactive filter applied to sql-endpoints list_all_workspaces too
# ---------------------------------------------------------------------------


async def test_sql_endpoints_proactive_skip_inactive_capacity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """sql_endpoints.list_all_workspaces must also skip inactive-capacity workspaces."""
    from fabric_dw.services.sql_endpoints import list_all_workspaces as ep_list_all  # noqa: PLC0415

    ws_active = _make_workspace(_WS_ACTIVE, _CAP_ACTIVE)
    ws_inactive = _make_workspace(_WS_INACTIVE, _CAP_INACTIVE)

    ep_active = Warehouse.model_validate(
        {
            "id": str(UUID("aaaaaaaa-2222-0000-0000-000000000001")),
            "displayName": "EP",
            "workspaceId": str(_WS_ACTIVE),
            "kind": WarehouseKind.SQL_ENDPOINT,
            "connectionString": "ep.fabric.microsoft.com",
        }
    )

    fetch_call_ids: list[UUID] = []

    async def _fetch_spy(_http: object, ws_id: UUID) -> list[Warehouse]:
        fetch_call_ids.append(ws_id)
        return [ep_active]

    with (
        caplog.at_level(logging.DEBUG, logger="fabric_dw.sql_endpoints"),
        patch(
            "fabric_dw.services.sql_endpoints._list_all_workspaces",
            new=AsyncMock(return_value=[ws_active, ws_inactive]),
        ),
        patch(
            "fabric_dw.services.sql_endpoints.get_capacity_states",
            new=AsyncMock(return_value=_make_capacity_states()),
        ),
        patch(
            "fabric_dw.services.sql_endpoints.list_endpoints",
            new=AsyncMock(side_effect=_fetch_spy),
        ),
    ):
        result = await ep_list_all(AsyncMock())

    assert len(result) == 1
    assert _WS_INACTIVE not in fetch_call_ids
    assert _WS_ACTIVE in fetch_call_ids


# ---------------------------------------------------------------------------
# (l) _make_should_retry: missing isRetriable key defaults to retry=True
# ---------------------------------------------------------------------------


async def test_make_should_retry_no_is_retriable_key_defaults_to_retry() -> None:
    """When the Fabric error envelope has NO isRetriable key, the HTTP client
    must default is_retriable=True (safe default — keep retrying as usual).
    The server error should be retried up to 3 attempts.
    """
    call_count = 0

    def _side_effect(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        # No "isRetriable" key at all in the error body.
        return httpx.Response(
            500,
            json={"errorCode": "InternalServerError", "message": "transient failure"},
        )

    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.get(f"{_WAREHOUSES_ACTIVE_URL}").mock(side_effect=_side_effect)

        client = await _make_client()
        async with client:
            with pytest.raises(FabricServerError) as exc_info:
                await client.request(
                    "GET",
                    HttpBase.FABRIC,
                    f"/workspaces/{_WS_ACTIVE}/warehouses",
                )

    # Absent isRetriable → defaults to True → 3 retry attempts.
    assert call_count == 3, f"Expected 3 attempts (default retry); got {call_count}"
    assert exc_info.value.is_retriable is True


# ---------------------------------------------------------------------------
# (m) _is_capacity_active: capacity_id present but absent from the map → skip
# ---------------------------------------------------------------------------


def test_is_capacity_active_unknown_capacity_id_returns_false() -> None:
    """A workspace whose capacity_id is set but absent from the capacity map
    must be conservatively skipped (return False).
    """
    from fabric_dw.services._helpers import _is_capacity_active  # noqa: PLC0415

    unknown_cap = UUID("dddddddd-cafe-0000-0000-000000000099")
    ws = _make_workspace(_WS_ACTIVE, unknown_cap)

    # The capacity map only knows about _CAP_ACTIVE and _CAP_INACTIVE.
    states = _make_capacity_states()
    assert str(unknown_cap).lower() not in states

    result = _is_capacity_active(ws, states)
    assert result is False, "Expected False (conservative skip) when capacity_id is not in the map"


# ---------------------------------------------------------------------------
# (n) Retriable FabricServerError that exhausts retries must propagate
# ---------------------------------------------------------------------------


async def test_retriable_server_error_exhausting_retries_propagates() -> None:
    """A FabricServerError(is_retriable=True) that exhausts all retries must
    propagate out of scan_all_workspaces — it must NOT be swallowed by the
    non-retriable defensive skip guard.
    """
    from fabric_dw.services.warehouses import list_all_workspaces  # noqa: PLC0415

    ws_a = _make_workspace(_WS_ACTIVE, _CAP_ACTIVE)

    retriable_err = FabricServerError(
        "Server error 500: transient failure",
        status=500,
        is_retriable=True,  # explicitly retriable — not a paused-capacity error
    )

    with (
        patch(
            "fabric_dw.services.warehouses._list_all_workspaces",
            new=AsyncMock(return_value=[ws_a]),
        ),
        patch(
            "fabric_dw.services.warehouses.get_capacity_states",
            new=AsyncMock(return_value=None),  # defensive path
        ),
        patch(
            "fabric_dw.services.warehouses.list_warehouses",
            new=AsyncMock(side_effect=retriable_err),
        ),
        pytest.raises(FabricServerError) as exc_info,
    ):
        await list_all_workspaces(AsyncMock())

    assert exc_info.value.is_retriable is True, (
        "Retriable FabricServerError must propagate, not be silently swallowed"
    )
