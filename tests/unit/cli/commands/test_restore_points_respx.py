"""Respx wire-validation tests for restore-points CLI sub-commands.

Validates:
* The exact URL constructed by each CLI command path
* The HTTP method used
* The JSON request body shape (for POST/PATCH)
* Parsed output and exit code

Pattern
-------
Patch ``fabric_dw.cli.commands.restore_points.build_http_client`` with
``_real_http_client_cm`` from conftest, then intercept all httpx calls
with ``respx.mock``.

Commands covered
----------------
* ``restore-points create`` — POST /workspaces/{ws}/warehouses/{wh}/restorePoints
  Both the synchronous 201 path and the async 202 LRO path are exercised.
* ``restore-points rename`` — PATCH /workspaces/{ws}/warehouses/{wh}/restorePoints/{id}
  with body containing displayName; followed by a GET to re-fetch the full resource.

Resolver paths
--------------
Both commands resolve the warehouse GUID via the GUID fast-path:
  1. GET /workspaces/{ws}/items/{wh}      (generic discovery → Warehouse)
  2. GET /workspaces/{ws}/warehouses/{wh} (type-specific detail for connectionString)
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
RP_ID = "1726617378000"
LRO_OP_ID = "op-rp-12345678-abcd-ef01-2345-678901234567"

_BASE = "https://api.fabric.microsoft.com/v1"
# Warehouse resolver paths
_WH_ITEMS_URL = f"{_BASE}/workspaces/{WS_GUID}/items/{WH_GUID}"
_WH_WAREHOUSE_URL = f"{_BASE}/workspaces/{WS_GUID}/warehouses/{WH_GUID}"
# Restore-point paths
_RP_BASE_URL = f"{_BASE}/workspaces/{WS_GUID}/warehouses/{WH_GUID}/restorePoints"
_RP_ITEM_URL = f"{_RP_BASE_URL}/{RP_ID}"
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

_RP_DETAIL = {
    "id": RP_ID,
    "displayName": "RestorePoint_20240315",
    "description": "Automated restore point before schema migration",
    "creationMode": "UserDefined",
    "creationDetails": {
        "eventDateTime": "2024-03-15T06:00:00Z",
        "eventInitiator": {
            "id": "f3052d1c-61a9-46fb-8df9-0d78916ae041",
            "displayName": "Jacob Hancock",
            "type": "User",
            "userDetails": {"userPrincipalName": "jacob@contoso.com"},
        },
    },
}
_RP_RENAMED = {**_RP_DETAIL, "displayName": "RenamedRestorePoint"}

# LRO Succeeded body for async create — includes resourceId for Path A resolution
_LRO_SUCCEEDED_WITH_ID = {
    "status": "Succeeded",
    "percentComplete": 100,
    "resourceId": RP_ID,
    "error": None,
}


def _setup_wh_resolver(mock_router: respx.MockRouter) -> None:
    """Register the warehouse GUID fast-path resolver mocks."""
    mock_router.get(_WH_ITEMS_URL).mock(return_value=httpx.Response(200, json=_WH_ITEMS_GENERIC))
    mock_router.get(_WH_WAREHOUSE_URL).mock(return_value=httpx.Response(200, json=_WH_DETAIL))


# ---------------------------------------------------------------------------
# restore-points create — POST wire validation (synchronous 201 path)
# ---------------------------------------------------------------------------


class TestRestorePointsCreateRespx:
    """Wire-validate that ``restore-points create`` POSTs to the correct URL."""

    def test_create_posts_to_restore_points_url(self, runner: CliRunner, cache_env: Path) -> None:
        """POST must target /workspaces/{ws}/warehouses/{wh}/restorePoints."""
        _ = cache_env
        captured_bodies: list[dict[str, object] | None] = []

        def _capture_post(request: httpx.Request) -> httpx.Response:
            body = request.content
            captured_bodies.append(json.loads(body) if body else None)
            return httpx.Response(201, json=_RP_DETAIL)

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_wh_resolver(mock_router)
            create_route = mock_router.post(_RP_BASE_URL).mock(side_effect=_capture_post)

            with patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "-w",
                        WS_GUID,
                        "restore-points",
                        "create",
                        WH_GUID,
                        "--name",
                        "RestorePoint_20240315",
                    ],
                )

        assert result.exit_code == 0, result.output
        assert create_route.called, f"Expected POST {_RP_BASE_URL}"
        data = json.loads(result.output)
        assert data["id"] == RP_ID

    def test_create_with_name_sends_display_name_in_body(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """POST body must include displayName when --name is provided."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture_post(request: httpx.Request) -> httpx.Response:
            if request.content:
                captured_bodies.append(json.loads(request.content))
            return httpx.Response(201, json=_RP_DETAIL)

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_wh_resolver(mock_router)
            mock_router.post(_RP_BASE_URL).mock(side_effect=_capture_post)

            with patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "-w",
                        WS_GUID,
                        "restore-points",
                        "create",
                        WH_GUID,
                        "--name",
                        "RestorePoint_20240315",
                    ],
                )

        assert result.exit_code == 0, result.output
        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        assert body.get("displayName") == "RestorePoint_20240315", (
            f"POST body must include displayName: {body}"
        )

    def test_create_without_name_sends_no_body(self, runner: CliRunner, cache_env: Path) -> None:
        """POST without --name must send no body (compact() strips None values)."""
        _ = cache_env
        body_contents: list[bytes] = []

        def _capture_post(request: httpx.Request) -> httpx.Response:
            body_contents.append(request.content)
            return httpx.Response(201, json=_RP_DETAIL)

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_wh_resolver(mock_router)
            mock_router.post(_RP_BASE_URL).mock(side_effect=_capture_post)

            with patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["--json", "-w", WS_GUID, "restore-points", "create", WH_GUID],
                )

        assert result.exit_code == 0, result.output
        assert len(body_contents) == 1
        # compact({}) → {} → None body → httpx sends empty body (no content)
        assert not body_contents[0], (
            f"POST without --name must have empty body; got: {body_contents[0]!r}"
        )

    def test_create_202_lro_path_polls_and_fetches(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """When the API returns 202, the LRO must be polled and the result fetched."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            _setup_wh_resolver(mock_router)
            mock_router.post(_RP_BASE_URL).mock(
                return_value=httpx.Response(
                    202,
                    json={},
                    headers={"Location": _LOCATION_HEADER},
                )
            )
            lro_route = mock_router.get(_LRO_POLL_URL).mock(
                return_value=httpx.Response(200, json=_LRO_SUCCEEDED_WITH_ID)
            )
            # After LRO resolves ID via Path A (resourceId), it GETs the restore point
            rp_get_route = mock_router.get(_RP_ITEM_URL).mock(
                return_value=httpx.Response(200, json=_RP_DETAIL)
            )

            with patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["--json", "-w", WS_GUID, "restore-points", "create", WH_GUID],
                )

        assert result.exit_code == 0, result.output
        assert lro_route.called, f"Expected LRO poll GET {_LRO_POLL_URL}"
        assert rp_get_route.called, f"Expected GET {_RP_ITEM_URL} after LRO"
        data = json.loads(result.output)
        assert data["id"] == RP_ID


# ---------------------------------------------------------------------------
# restore-points rename — PATCH wire validation
# ---------------------------------------------------------------------------


class TestRestorePointsRenameRespx:
    """Wire-validate that ``restore-points rename`` issues PATCH with the correct body.

    The rename command (via update_point):
    1. PATCH /workspaces/{ws}/warehouses/{wh}/restorePoints/{id}  (body: displayName)
    2. GET   /workspaces/{ws}/warehouses/{wh}/restorePoints/{id}  (re-fetch full resource)
    """

    def test_rename_patch_body_contains_new_name(self, runner: CliRunner, cache_env: Path) -> None:
        """PATCH body must include the new displayName."""
        _ = cache_env
        captured_bodies: list[dict[str, object]] = []

        def _capture_patch(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={})

        with respx.mock(assert_all_called=False) as mock_router:
            _setup_wh_resolver(mock_router)
            rename_route = mock_router.patch(_RP_ITEM_URL).mock(side_effect=_capture_patch)
            mock_router.get(_RP_ITEM_URL).mock(return_value=httpx.Response(200, json=_RP_RENAMED))

            with patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "-w",
                        WS_GUID,
                        "restore-points",
                        "rename",
                        WH_GUID,
                        RP_ID,
                        "RenamedRestorePoint",
                    ],
                )

        assert result.exit_code == 0, result.output
        assert rename_route.called, f"Expected PATCH {_RP_ITEM_URL}"
        assert len(captured_bodies) == 1
        body = captured_bodies[0]
        assert body.get("displayName") == "RenamedRestorePoint", (
            f"PATCH body missing correct displayName: {body}"
        )

    def test_rename_targets_correct_url_with_restore_point_id(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """PATCH URL must include the restore point ID in the path."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            _setup_wh_resolver(mock_router)
            # Only the exact URL including RP_ID is registered — wrong URL would miss
            rename_route = mock_router.patch(_RP_ITEM_URL).mock(
                return_value=httpx.Response(200, json={})
            )
            mock_router.get(_RP_ITEM_URL).mock(return_value=httpx.Response(200, json=_RP_RENAMED))

            with patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "-w",
                        WS_GUID,
                        "restore-points",
                        "rename",
                        WH_GUID,
                        RP_ID,
                        "RenamedRestorePoint",
                    ],
                )

        assert result.exit_code == 0, result.output
        assert rename_route.called, (
            f"Expected PATCH {_RP_ITEM_URL} — wrong URL would not match this route"
        )

    def test_rename_refetches_full_resource_after_patch(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """After the PATCH, a GET must be issued to return the complete resource."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            _setup_wh_resolver(mock_router)
            mock_router.patch(_RP_ITEM_URL).mock(return_value=httpx.Response(200, json={}))
            get_route = mock_router.get(_RP_ITEM_URL).mock(
                return_value=httpx.Response(200, json=_RP_RENAMED)
            )

            with patch(
                "fabric_dw.cli.commands.restore_points.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "-w",
                        WS_GUID,
                        "restore-points",
                        "rename",
                        WH_GUID,
                        RP_ID,
                        "RenamedRestorePoint",
                    ],
                )

        assert result.exit_code == 0, result.output
        assert get_route.called, f"Expected GET {_RP_ITEM_URL} after PATCH (re-fetch)"
        data = json.loads(result.output)
        assert data["id"] == RP_ID
        assert data["displayName"] == "RenamedRestorePoint"
