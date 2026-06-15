"""T15: respx-based CLI tests for warehouses — HTTP wire validation.

These tests replace the ``FabricHttpClient`` AsyncMock with a *real* client
backed by a fake credential, and use ``respx`` to intercept HTTP at the httpx
layer.  This validates:

* The exact URL constructed by each CLI command path
* The HTTP method used
* The JSON request body shape (for POST/PATCH)
* The JSON response is parsed and rendered correctly

Pattern
-------
1. Patch ``fabric_dw.cli.commands._utils._auth.get_credential`` to return a
   fake ``AsyncTokenCredential`` (no real token exchange).
2. Open the real ``FabricHttpClient`` inside a ``respx.mock`` context so all
   ``httpx.AsyncClient`` requests are intercepted.
3. Assert on ``respx.calls`` to verify URL, method, and body.

Scope of this file
------------------
Covers the most important "read" and "write" warehouse commands:
  * ``warehouses get`` (GET wire → validates URL and JSON parsing)
  * ``warehouses delete`` (DELETE wire → validates URL and method)
  * ``warehouses rename`` (PATCH wire → validates URL, method, and body shape)

What remains to convert
-----------------------
All other CLI test modules still use ``AsyncMock(FabricHttpClient)`` at the
Python-object layer.  They validate exit codes and rendered output but do NOT
validate wire-level details.  Candidate modules for follow-up conversion:

  * test_snapshots.py  — create (POST LRO), rename (PATCH), delete (DELETE)
  * test_audit.py      — enable (PATCH), disable (PATCH), set-retention (PATCH)
  * test_restore_points.py — create (POST), rename (PATCH)
  * test_workspaces.py — list (GET with pagination), get (GET)

Each follows the same pattern established here.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import respx
from click.testing import CliRunner

from fabric_dw.cli._main import cli
from tests.fixtures.api_payloads import WAREHOUSE_GET_PAYLOAD
from tests.unit.cli.commands.conftest import _real_http_client_cm

# ---------------------------------------------------------------------------
# Constants matching fixtures
# ---------------------------------------------------------------------------

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WH_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"

_BASE = "https://api.fabric.microsoft.com/v1"
_ITEMS_URL = f"{_BASE}/workspaces/{WS_GUID}/items/{WH_GUID}"
_WAREHOUSE_URL = f"{_BASE}/workspaces/{WS_GUID}/warehouses/{WH_GUID}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Generic items response needed by the Resolver GUID fast-path:
# GET /workspaces/{ws}/items/{item} discovers the item type.
_ITEMS_GENERIC_RESPONSE = {
    "id": WH_GUID,
    "displayName": "SalesWarehouse",
    "type": "Warehouse",
    "workspaceId": WS_GUID,
}

_WAREHOUSE_DETAIL = json.loads(WAREHOUSE_GET_PAYLOAD)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T15: warehouses get — wire validation
# ---------------------------------------------------------------------------


class TestWarehousesGetRespx:
    """Validate that ``warehouses get`` issues the correct GET requests.

    Two GET calls are expected:
    1. Resolver GUID fast-path: GET /v1/workspaces/{ws}/items/{wh}
       (discovers item type → "Warehouse")
    2. Resolver type-specific: GET /v1/workspaces/{ws}/warehouses/{wh}
       (fetches full detail including connectionString)
    3. get_warehouse service: GET /v1/workspaces/{ws}/warehouses/{wh}
       (another detail fetch used to render the response)

    Calls 2 and 3 hit the same URL; respx counts them both.
    """

    def test_get_issues_correct_url(self, runner: CliRunner, cache_env: Path) -> None:
        """The CLI must GET the correct Fabric API URL for the warehouse."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            # Resolver generic-items endpoint
            items_route = mock_router.get(_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_ITEMS_GENERIC_RESPONSE)
            )
            # Resolver type-specific + get_warehouse service endpoint (same URL, called 2x)
            warehouse_route = mock_router.get(_WAREHOUSE_URL).mock(
                return_value=httpx.Response(200, json=_WAREHOUSE_DETAIL)
            )

            with patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(cli, ["--json", "warehouses", "get", WS_GUID, WH_GUID])

        assert result.exit_code == 0, result.output
        # Verify the exact URLs were called (wire validation)
        assert items_route.called, f"Expected GET {_ITEMS_URL} to be called"
        assert warehouse_route.called, f"Expected GET {_WAREHOUSE_URL} to be called"
        # Verify the parsed output matches the fixture
        data = json.loads(result.output)
        assert data["displayName"] == "SalesWarehouse"
        assert data["id"] == WH_GUID

    def test_get_uses_bearer_auth(self, runner: CliRunner, cache_env: Path) -> None:
        """Every request must carry an Authorization: Bearer … header."""
        _ = cache_env
        seen_headers: list[dict[str, str]] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            seen_headers.append(dict(request.headers))
            if "items" in str(request.url):
                return httpx.Response(200, json=_ITEMS_GENERIC_RESPONSE)
            return httpx.Response(200, json=_WAREHOUSE_DETAIL)

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(url__regex=r".*/workspaces/.*").mock(side_effect=_capture)

            with patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(cli, ["warehouses", "get", WS_GUID, WH_GUID])

        assert result.exit_code == 0, result.output
        # Every captured request must have an Authorization header
        for headers in seen_headers:
            auth_header = headers.get("authorization", "")
            assert auth_header.startswith("Bearer "), f"Expected Bearer token, got: {auth_header!r}"


# ---------------------------------------------------------------------------
# T15: warehouses delete — wire validation
# ---------------------------------------------------------------------------


class TestWarehousesDeleteRespx:
    """Validate that ``warehouses delete`` issues a DELETE request with the correct URL."""

    def test_delete_issues_delete_method(self, runner: CliRunner, cache_env: Path) -> None:
        """The CLI must DELETE the correct Fabric API URL for warehouses."""
        _ = cache_env
        # warehouses.delete uses DELETE /workspaces/{ws}/warehouses/{wh}
        delete_url = _WAREHOUSE_URL

        with respx.mock(assert_all_called=False) as mock_router:
            # Resolver calls (GUID fast-path)
            mock_router.get(_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_ITEMS_GENERIC_RESPONSE)
            )
            mock_router.get(_WAREHOUSE_URL).mock(
                return_value=httpx.Response(200, json=_WAREHOUSE_DETAIL)
            )
            # The actual delete
            delete_route = mock_router.delete(delete_url).mock(return_value=httpx.Response(204))

            with patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    ["--yes", "warehouses", "delete", WS_GUID, WH_GUID],
                )

        assert result.exit_code == 0, result.output
        # The DELETE must have been called on the correct warehouse URL
        assert delete_route.called, f"Expected DELETE {delete_url} to be called"


# ---------------------------------------------------------------------------
# T15: warehouses rename — wire validation (PATCH body)
# ---------------------------------------------------------------------------


class TestWarehousesRenameRespx:
    """Validate that ``warehouses rename`` issues a PATCH with the correct body."""

    def test_rename_patch_body_contains_new_name(self, runner: CliRunner, cache_env: Path) -> None:
        """PATCH body must include the new displayName."""
        _ = cache_env
        # warehouses.rename uses PATCH /workspaces/{ws}/warehouses/{wh}
        patch_url = _WAREHOUSE_URL
        captured_bodies: list[dict[str, object]] = []

        def _capture_patch(request: httpx.Request) -> httpx.Response:
            captured_bodies.append(json.loads(request.content))
            return httpx.Response(200, json=_WAREHOUSE_DETAIL)

        # Renamed warehouse response (displayName updated)
        _renamed_wh = dict(_WAREHOUSE_DETAIL)
        _renamed_wh["displayName"] = "RenamedWarehouse"

        with respx.mock(assert_all_called=False) as mock_router:
            # Resolver calls (same as other tests)
            mock_router.get(_ITEMS_URL).mock(
                return_value=httpx.Response(200, json=_ITEMS_GENERIC_RESPONSE)
            )
            mock_router.get(_WAREHOUSE_URL).mock(return_value=httpx.Response(200, json=_renamed_wh))
            # PATCH intercept to capture body
            mock_router.patch(patch_url).mock(side_effect=_capture_patch)

            with patch(
                "fabric_dw.cli.commands.warehouses.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(
                    cli,
                    [
                        "--json",
                        "--yes",
                        "warehouses",
                        "rename",
                        WS_GUID,
                        WH_GUID,
                        "RenamedWarehouse",
                    ],
                )

        assert result.exit_code == 0, result.output
        # The PATCH must have been called with the new name in the body
        assert len(captured_bodies) == 1, "Expected exactly one PATCH request"
        body = captured_bodies[0]
        assert body.get("displayName") == "RenamedWarehouse", (
            f"PATCH body missing correct displayName: {body}"
        )
