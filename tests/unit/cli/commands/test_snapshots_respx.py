"""Respx wire-validation tests for snapshots CLI sub-commands.

Validates:
* The exact URL constructed by each CLI command path
* The HTTP method used
* The JSON request body shape (for POST/PATCH)
* LRO flow for create (POST → 202 → poll → typed detail GET)
* Parsed output and exit code

Pattern
-------
Patch ``fabric_dw.cli.commands.snapshots.build_http_client`` with
``_real_http_client_cm`` from conftest, then intercept all httpx calls
with ``respx.mock``.

Commands covered
----------------
* ``snapshots list``   — GET /workspaces/{ws}/warehouseSnapshots (paginated, 2 pages)
* ``snapshots create`` — POST /workspaces/{ws}/items (LRO: 202+Location, poll, typed GET)
* ``snapshots rename`` — PATCH /workspaces/{ws}/items/{snap} (body: displayName + creationPayload)
* ``snapshots delete`` — DELETE /workspaces/{ws}/items/{snap}

Resolver paths
--------------
* ``snapshots list`` / ``create``: resolver uses GUID fast-path for the warehouse:
  - GET /workspaces/{ws}/items/{wh}  (generic discovery → type=Warehouse)
  - GET /workspaces/{ws}/warehouses/{wh} (type-specific detail)
* ``snapshots rename`` / ``delete``: resolver uses GUID fast-path for the snapshot:
  - GET /workspaces/{ws}/items/{snap}  (generic discovery → type=WarehouseSnapshot)
  - No type-specific detail (WarehouseSnapshot has no dedicated endpoint)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import respx
from click.testing import CliRunner

from fabric_dw.cli._main import cli
from tests.unit.cli.commands.conftest import _real_http_client_cm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"
SNAP_GUID = "f6a7b8c9-d0e1-2345-f012-34567890abcd"
SNAP2_GUID = "e5f6a7b8-c9d0-1234-e012-34567890abce"
LRO_OP_ID = "op-12345678-abcd-ef01-2345-678901234567"

_BASE = "https://api.fabric.microsoft.com/v1"
# Warehouse resolver paths
_WH_ITEMS_URL = f"{_BASE}/workspaces/{WS_GUID}/items/{WH_GUID}"
_WH_WAREHOUSE_URL = f"{_BASE}/workspaces/{WS_GUID}/warehouses/{WH_GUID}"
# Snapshot resolver path (no type-specific endpoint)
_SNAP_ITEMS_URL = f"{_BASE}/workspaces/{WS_GUID}/items/{SNAP_GUID}"
# Operational endpoints
_ITEMS_CREATE_URL = f"{_BASE}/workspaces/{WS_GUID}/items"
_SNAP_ITEM_URL = f"{_BASE}/workspaces/{WS_GUID}/items/{SNAP_GUID}"
_SNAP_TYPED_URL = f"{_BASE}/workspaces/{WS_GUID}/warehouseSnapshots/{SNAP_GUID}"
_SNAPS_LIST_URL = f"{_BASE}/workspaces/{WS_GUID}/warehouseSnapshots"
_LRO_POLL_URL = f"{_BASE}/operations/{LRO_OP_ID}"
_LOCATION_HEADER = f"{_BASE}/operations/{LRO_OP_ID}"

# Resolver stub responses
_WH_ITEMS_GENERIC = {
    "id": WH_GUID,
    "displayName": "SalesWarehouse",
    "type": "Warehouse",
    "workspaceId": WS_GUID,
}
_WH_DETAIL = {
    "id": WH_GUID,
    "displayName": "SalesWarehouse",
    "type": "Warehouse",
    "workspaceId": WS_GUID,
    "properties": {
        "connectionString": "saleswarehouse.datawarehouse.fabric.microsoft.com",
    },
}
_SNAP_ITEMS_GENERIC = {
    "id": SNAP_GUID,
    "displayName": "SalesWarehouse_Snapshot_20240315",
    "type": "WarehouseSnapshot",
    "workspaceId": WS_GUID,
}
_SNAP_TYPED_DETAIL = {
    "id": SNAP_GUID,
    "displayName": "SalesWarehouse_Snapshot_20240315",
    "type": "WarehouseSnapshot",
    "workspaceId": WS_GUID,
    "properties": {
        "parentWarehouseId": WH_GUID,
        "snapshotDateTime": "2024-03-15T08:00:00Z",
    },
}
# LRO succeeded body — resourceId lets resolve_lro_item_id find the snap ID directly.
_LRO_SUCCEEDED = {
    "status": "Succeeded",
    "percentComplete": 100,
    "resourceId": SNAP_GUID,
    "error": None,
}

# ---------------------------------------------------------------------------
# Pagination test data for snapshots list
# ---------------------------------------------------------------------------

_CONTINUATION_TOKEN = "eyJ0b2tlbiI6InRlc3QifQ"  # noqa: S105
_CONTINUATION_URI = f"{_SNAPS_LIST_URL}?continuationToken={_CONTINUATION_TOKEN}"

_LIST_PAGE1_BODY = {
    "value": [
        {
            "id": SNAP_GUID,
            "displayName": "SalesWarehouse_Snapshot_20240315",
            "type": "WarehouseSnapshot",
            "workspaceId": WS_GUID,
            "properties": {
                "parentWarehouseId": WH_GUID,
                "snapshotDateTime": "2024-03-15T08:00:00Z",
            },
        }
    ],
    "continuationUri": _CONTINUATION_URI,
}

_LIST_PAGE2_BODY = {
    "value": [
        {
            "id": SNAP2_GUID,
            "displayName": "SalesWarehouse_Snapshot_20240316",
            "type": "WarehouseSnapshot",
            "workspaceId": WS_GUID,
            "properties": {
                "parentWarehouseId": WH_GUID,
                "snapshotDateTime": "2024-03-16T08:00:00Z",
            },
        }
    ]
    # No continuationUri → last page
}


# ---------------------------------------------------------------------------
# snapshots list — GET wire + pagination
# ---------------------------------------------------------------------------


class TestSnapshotsListRespx:
    """Wire-validate that ``snapshots list`` GETs /warehouseSnapshots and follows pagination.

    The list command:
    1. Resolves the warehouse GUID via the GUID fast-path:
       - GET /workspaces/{ws}/items/{wh}      (generic discovery → Warehouse)
       - GET /workspaces/{ws}/warehouses/{wh} (type-specific detail for connectionString)
    2. Calls list_snapshots which uses iter_paginated on
       GET /workspaces/{ws}/warehouseSnapshots, following continuationUri across pages.
    """

    def test_list_issues_get_to_warehouse_snapshots_url(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """GET must target /workspaces/{ws}/warehouseSnapshots."""
        _ = cache_env
        request_urls: list[str] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            request_urls.append(str(request.url))
            return httpx.Response(200, json=_LIST_PAGE2_BODY)

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(_WH_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_WH_ITEMS_GENERIC)
            )
            mock_router.get(_WH_WAREHOUSE_URL).mock(
                return_value=httpx.Response(200, json=_WH_DETAIL)
            )
            list_route = mock_router.get(url__regex=r".*/warehouseSnapshots(\?.*)?$").mock(
                side_effect=_handler
            )

            with patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["--json", "snapshots", "list", WS_GUID, WH_GUID],
                )

        assert result.exit_code == 0, result.output
        assert list_route.called, f"Expected GET {_SNAPS_LIST_URL}"
        assert any(_SNAPS_LIST_URL in u for u in request_urls), (
            f"Expected a request to {_SNAPS_LIST_URL}: {request_urls}"
        )

    def test_list_follows_pagination_continuation_uri(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """``snapshots list`` must follow continuationUri across pages and aggregate both.

        A single side-effect handler dispatches to page1 or page2 based on whether
        the request URL contains a continuationToken.
        """
        _ = cache_env
        request_urls: list[str] = []

        def _paginated_handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            request_urls.append(url_str)
            if "continuationToken" in url_str:
                return httpx.Response(200, json=_LIST_PAGE2_BODY)
            return httpx.Response(200, json=_LIST_PAGE1_BODY)

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(_WH_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_WH_ITEMS_GENERIC)
            )
            mock_router.get(_WH_WAREHOUSE_URL).mock(
                return_value=httpx.Response(200, json=_WH_DETAIL)
            )
            list_route = mock_router.get(url__regex=r".*/warehouseSnapshots(\?.*)?$").mock(
                side_effect=_paginated_handler
            )

            with patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["--json", "snapshots", "list", WS_GUID, WH_GUID],
                )

        assert result.exit_code == 0, result.output
        assert list_route.called, f"Expected GET {_SNAPS_LIST_URL}"
        # Must have made at least two GET requests (page1 + page2)
        assert len(request_urls) >= 2, (
            f"Expected at least 2 GET requests (pagination), got {len(request_urls)}: "
            f"{request_urls}"
        )
        # First request must be to the base URL without a continuation token
        assert "continuationToken" not in request_urls[0], (
            f"First request must not contain continuationToken: {request_urls[0]}"
        )
        # Subsequent request must include the continuation token
        assert any("continuationToken" in u for u in request_urls[1:]), (
            f"Expected a paginated request with continuationToken: {request_urls}"
        )
        # Both snapshots from both pages must appear in output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2, (
            f"Expected 2 snapshots aggregated from both pages, got {len(data)}: {data}"
        )
        ids = {s["id"] for s in data}
        assert SNAP_GUID in ids, f"Snapshot from page 1 missing: {ids}"
        assert SNAP2_GUID in ids, f"Snapshot from page 2 missing: {ids}"


# ---------------------------------------------------------------------------
# snapshots create — POST wire + LRO flow
# ---------------------------------------------------------------------------


class TestSnapshotsCreateRespx:
    """Wire-validate that ``snapshots create`` POSTs to the correct URL with correct body.

    The LRO flow:
    1. POST /workspaces/{ws}/items  → 202 + Location header
    2. GET  /operations/{op_id}     → {"status":"Succeeded","resourceId":"<snap_id>"}
    3. GET  /workspaces/{ws}/warehouseSnapshots/{snap_id}  (typed detail)
    """

    def test_create_posts_to_items_endpoint(self, runner: CliRunner, cache_env: Path) -> None:
        """POST must target /workspaces/{ws}/items (not a warehouseSnapshots endpoint)."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture_post(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(
                202,
                json={},
                headers={"Location": _LOCATION_HEADER},
            )

        with respx.mock(assert_all_called=False) as mock_router:
            # Resolver: WH GUID fast-path
            mock_router.get(_WH_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_WH_ITEMS_GENERIC)
            )
            mock_router.get(_WH_WAREHOUSE_URL).mock(
                return_value=httpx.Response(200, json=_WH_DETAIL)
            )
            # Step 1 — POST create (LRO kicks off)
            create_route = mock_router.post(_ITEMS_CREATE_URL).mock(side_effect=_capture_post)
            # Step 2 — LRO poll → Succeeded with resourceId
            mock_router.get(_LRO_POLL_URL).mock(
                return_value=httpx.Response(200, json=_LRO_SUCCEEDED)
            )
            # Step 3 — typed detail fetch
            mock_router.get(_SNAP_TYPED_URL).mock(
                return_value=httpx.Response(200, json=_SNAP_TYPED_DETAIL)
            )

            with patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "snapshots",
                        "create",
                        WS_GUID,
                        WH_GUID,
                        "SalesWarehouse_Snapshot_20240315",
                    ],
                )

        assert result.exit_code == 0, result.output
        assert create_route.called, f"Expected POST {_ITEMS_CREATE_URL}"
        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        assert body.get("type") == "WarehouseSnapshot", (
            f"POST body must include type=WarehouseSnapshot: {body}"
        )
        assert body.get("displayName") == "SalesWarehouse_Snapshot_20240315", (
            f"POST body must include displayName: {body}"
        )
        payload = body.get("creationPayload", {})
        assert isinstance(payload, dict)
        assert payload.get("parentWarehouseId") == WH_GUID, (
            f"creationPayload must include parentWarehouseId={WH_GUID}: {payload}"
        )

    def test_create_lro_poll_is_called(self, runner: CliRunner, cache_env: Path) -> None:
        """After the 202 response the LRO poll URL must be called."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(_WH_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_WH_ITEMS_GENERIC)
            )
            mock_router.get(_WH_WAREHOUSE_URL).mock(
                return_value=httpx.Response(200, json=_WH_DETAIL)
            )
            mock_router.post(_ITEMS_CREATE_URL).mock(
                return_value=httpx.Response(
                    202,
                    json={},
                    headers={"Location": _LOCATION_HEADER},
                )
            )
            lro_route = mock_router.get(_LRO_POLL_URL).mock(
                return_value=httpx.Response(200, json=_LRO_SUCCEEDED)
            )
            mock_router.get(_SNAP_TYPED_URL).mock(
                return_value=httpx.Response(200, json=_SNAP_TYPED_DETAIL)
            )

            with patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "snapshots",
                        "create",
                        WS_GUID,
                        WH_GUID,
                        "SalesWarehouse_Snapshot_20240315",
                    ],
                )

        assert result.exit_code == 0, result.output
        assert lro_route.called, f"Expected GET {_LRO_POLL_URL} (LRO poll) to be called"
        data = json.loads(result.output)
        assert data["id"] == SNAP_GUID

    def test_create_typed_detail_fetched_after_lro(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """After the LRO succeeds, the typed warehouseSnapshots endpoint must be fetched."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(_WH_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_WH_ITEMS_GENERIC)
            )
            mock_router.get(_WH_WAREHOUSE_URL).mock(
                return_value=httpx.Response(200, json=_WH_DETAIL)
            )
            mock_router.post(_ITEMS_CREATE_URL).mock(
                return_value=httpx.Response(
                    202,
                    json={},
                    headers={"Location": _LOCATION_HEADER},
                )
            )
            mock_router.get(_LRO_POLL_URL).mock(
                return_value=httpx.Response(200, json=_LRO_SUCCEEDED)
            )
            typed_route = mock_router.get(_SNAP_TYPED_URL).mock(
                return_value=httpx.Response(200, json=_SNAP_TYPED_DETAIL)
            )

            with patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "snapshots",
                        "create",
                        WS_GUID,
                        WH_GUID,
                        "SalesWarehouse_Snapshot_20240315",
                    ],
                )

        assert result.exit_code == 0, result.output
        assert typed_route.called, f"Expected GET {_SNAP_TYPED_URL} to be called after LRO"


# ---------------------------------------------------------------------------
# snapshots rename — PATCH wire validation
# ---------------------------------------------------------------------------


class TestSnapshotsRenameRespx:
    """Wire-validate that ``snapshots rename`` issues PATCH with the correct body.

    The rename command (via resolve_item_with_cache + resolver GUID fast-path):
    1. GET /workspaces/{ws}/items/{snap}     (generic discovery → WarehouseSnapshot)
    2. GET /workspaces/{ws}/warehouseSnapshots/{snap}  (re-fetched for parentWarehouseId)
    3. PATCH /workspaces/{ws}/items/{snap}  (rename body)
    4. GET /workspaces/{ws}/warehouseSnapshots/{snap}  (re-fetch updated detail)
    """

    def test_rename_patch_body_contains_new_name(self, runner: CliRunner, cache_env: Path) -> None:
        """PATCH body must include the new displayName."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture_patch(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={})

        _renamed_detail = {**_SNAP_TYPED_DETAIL, "displayName": "RenamedSnapshot"}

        with respx.mock(assert_all_called=False) as mock_router:
            # Resolver: SNAP GUID fast-path (WarehouseSnapshot has no type-specific endpoint)
            mock_router.get(_SNAP_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_SNAP_ITEMS_GENERIC)
            )
            # rename service: GET current snap for parentWarehouseId + GET after rename
            mock_router.get(_SNAP_TYPED_URL).mock(
                return_value=httpx.Response(200, json=_renamed_detail)
            )
            rename_route = mock_router.patch(_SNAP_ITEM_URL).mock(side_effect=_capture_patch)

            with patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "--yes",
                        "snapshots",
                        "rename",
                        SNAP_GUID,
                        "RenamedSnapshot",
                        WS_GUID,
                    ],
                )

        assert result.exit_code == 0, result.output
        assert rename_route.called, f"Expected PATCH {_SNAP_ITEM_URL}"
        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        assert body.get("displayName") == "RenamedSnapshot", (
            f"PATCH body missing correct displayName: {body}"
        )

    def test_rename_patch_body_includes_creation_payload(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """PATCH body must include creationPayload.parentWarehouseId (Fabric requirement)."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture_patch(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={})

        _renamed_detail = {**_SNAP_TYPED_DETAIL, "displayName": "RenamedSnapshot"}

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(_SNAP_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_SNAP_ITEMS_GENERIC)
            )
            mock_router.get(_SNAP_TYPED_URL).mock(
                return_value=httpx.Response(200, json=_renamed_detail)
            )
            mock_router.patch(_SNAP_ITEM_URL).mock(side_effect=_capture_patch)

            with patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "--yes",
                        "snapshots",
                        "rename",
                        SNAP_GUID,
                        "RenamedSnapshot",
                        WS_GUID,
                    ],
                )

        assert result.exit_code == 0, result.output
        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        payload = body.get("creationPayload", {})
        assert isinstance(payload, dict), f"creationPayload missing from PATCH body: {body}"
        assert "parentWarehouseId" in payload, (
            f"creationPayload must include parentWarehouseId: {payload}"
        )

    def test_rename_targets_items_endpoint_not_typed(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Rename must PATCH /workspaces/{ws}/items/{snap}, not /warehouseSnapshots/{snap}."""
        _ = cache_env
        _renamed_detail = {**_SNAP_TYPED_DETAIL, "displayName": "RenamedSnapshot"}

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(_SNAP_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_SNAP_ITEMS_GENERIC)
            )
            mock_router.get(_SNAP_TYPED_URL).mock(
                return_value=httpx.Response(200, json=_renamed_detail)
            )
            # Only the /items/{snap} PATCH route — if code patches /warehouseSnapshots this misses
            patch_route = mock_router.patch(_SNAP_ITEM_URL).mock(
                return_value=httpx.Response(200, json={})
            )

            with patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "--yes",
                        "snapshots",
                        "rename",
                        SNAP_GUID,
                        "RenamedSnapshot",
                        WS_GUID,
                    ],
                )

        assert result.exit_code == 0, result.output
        assert patch_route.called, (
            f"Expected PATCH {_SNAP_ITEM_URL} — rename must use the generic /items endpoint"
        )


# ---------------------------------------------------------------------------
# snapshots delete — DELETE wire validation
# ---------------------------------------------------------------------------


class TestSnapshotsDeleteRespx:
    """Wire-validate that ``snapshots delete`` issues DELETE to the correct URL."""

    def test_delete_issues_delete_to_items_endpoint(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """DELETE must target /workspaces/{ws}/items/{snap}."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            # Resolver: SNAP GUID fast-path
            mock_router.get(_SNAP_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_SNAP_ITEMS_GENERIC)
            )
            delete_route = mock_router.delete(_SNAP_ITEM_URL).mock(return_value=httpx.Response(204))

            with patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--yes",
                        "snapshots",
                        "delete",
                        SNAP_GUID,
                        WS_GUID,
                    ],
                )

        assert result.exit_code == 0, result.output
        assert delete_route.called, f"Expected DELETE {_SNAP_ITEM_URL}"

    def test_delete_uses_delete_http_method(self, runner: CliRunner, cache_env: Path) -> None:
        """A wrong HTTP method (POST/PATCH) would fail to match the delete route."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(_SNAP_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_SNAP_ITEMS_GENERIC)
            )
            delete_route = mock_router.delete(_SNAP_ITEM_URL).mock(return_value=httpx.Response(204))

            with patch(
                "fabric_dw.cli.commands.snapshots.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--yes",
                        "--json",
                        "snapshots",
                        "delete",
                        SNAP_GUID,
                        WS_GUID,
                    ],
                )

        assert result.exit_code == 0, result.output
        assert delete_route.called, "DELETE route must be matched — wrong method would miss it"
        data = json.loads(result.output)
        assert data["status"] == "deleted"
        assert data["id"] == SNAP_GUID
