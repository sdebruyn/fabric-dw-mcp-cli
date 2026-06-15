"""Respx wire-validation tests for workspaces CLI sub-commands.

Validates:
* The exact URL constructed by each CLI command path
* The HTTP method used
* Multi-page pagination for ``workspaces list``
* Parsed output and exit code

Pattern
-------
Patch ``fabric_dw.cli.commands.workspaces.build_http_client`` with
``_real_http_client_cm`` from conftest, then intercept all httpx calls
with ``respx.mock``.

Commands covered
----------------
* ``workspaces list`` — GET /workspaces (paged: asserts the continuation/paged requests)
* ``workspaces get``  — GET /workspaces/{ws}

Resolver notes
--------------
* ``workspaces get WS_GUID``: the resolver recognises the GUID and returns UUID(value)
  directly — no HTTP call for workspace resolution; only the service GET is made.
* ``workspaces list``: no resolver at all; calls ``workspaces.list_all`` which
  paginates GET /workspaces until ``continuationUri`` is absent.

Pagination note
---------------
respx matches routes in registration order, and a route registered for
``/workspaces`` (without query params) also matches ``/workspaces?token=…``
because respx does not require an exact params match unless explicitly
specified.  For pagination tests a single route handler is registered that
inspects the request URL and dispatches to the appropriate page body.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import respx
from click.testing import CliRunner

from fabric_dw.cli._main import cli
from tests.fixtures.api_payloads import WORKSPACE_GET_PAYLOAD
from tests.unit.cli.commands.conftest import _real_http_client_cm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
WS2_GUID = "b2c3d4e5-f6a7-8901-bcde-f01234567891"
WS3_GUID = "c3d4e5f6-a7b8-9012-cdef-012345678901"

_BASE = "https://api.fabric.microsoft.com/v1"
_WORKSPACES_URL = f"{_BASE}/workspaces"
_WS_DETAIL_URL = f"{_BASE}/workspaces/{WS_GUID}"
_CONTINUATION_TOKEN = "eyJ0b2tlbiI6InRlc3QifQ"  # noqa: S105
_CONTINUATION_URI = f"{_WORKSPACES_URL}?continuationToken={_CONTINUATION_TOKEN}"

_WS_DETAIL = json.loads(WORKSPACE_GET_PAYLOAD)

_PAGE1_BODY = {
    "value": [
        {
            "id": WS_GUID,
            "displayName": "AnalyticsWorkspace",
            "description": "Primary analytics workspace",
            "type": "Workspace",
            "capacityId": "cafebabe-dead-beef-cafe-babe12345678",
        },
        {
            "id": WS2_GUID,
            "displayName": "DataScienceWorkspace",
            "description": None,
            "type": "Workspace",
            "capacityId": None,
        },
    ],
    "continuationUri": _CONTINUATION_URI,
}

_PAGE2_BODY = {
    "value": [
        {
            "id": WS3_GUID,
            "displayName": "MLWorkspace",
            "description": "Machine learning workspace",
            "type": "Workspace",
            "capacityId": "cafebabe-dead-beef-cafe-babe12345678",
        }
    ]
    # No continuationUri → last page
}


# ---------------------------------------------------------------------------
# workspaces list — GET with pagination
# ---------------------------------------------------------------------------


class TestWorkspacesListRespx:
    """Wire-validate that ``workspaces list`` issues GET to /workspaces and follows pagination."""

    def test_list_issues_get_to_workspaces_url(self, runner: CliRunner, cache_env: Path) -> None:
        """Initial GET must target /workspaces."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            list_route = mock_router.get(url__regex=r".*/workspaces$").mock(
                return_value=httpx.Response(200, json=_PAGE2_BODY)
            )

            with patch(
                "fabric_dw.cli.commands.workspaces.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(cli, ["--json", "workspaces", "list"])

        assert result.exit_code == 0, result.output
        assert list_route.called, f"Expected GET {_WORKSPACES_URL}"
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == WS3_GUID

    def test_list_follows_pagination_continuation_uri(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """``workspaces list`` must follow the continuationUri across pages.

        A single side-effect handler dispatches to page1 or page2 based on
        whether the request URL contains a continuationToken.  This avoids
        the respx route-ordering ambiguity where the page-1 route (registered
        first) would also absorb the page-2 request.
        """
        _ = cache_env
        request_urls: list[str] = []

        def _paginated_handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            request_urls.append(url_str)
            if "continuationToken" in url_str:
                return httpx.Response(200, json=_PAGE2_BODY)
            return httpx.Response(200, json=_PAGE1_BODY)

        with respx.mock(assert_all_called=False) as mock_router:
            list_route = mock_router.get(url__regex=r".*/workspaces(\?.*)?$").mock(
                side_effect=_paginated_handler
            )

            with patch(
                "fabric_dw.cli.commands.workspaces.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(cli, ["--json", "workspaces", "list"])

        assert result.exit_code == 0, result.output
        assert list_route.called, f"Expected GET {_WORKSPACES_URL}"
        # Must have made at least two GET requests (page1 + page2)
        assert len(request_urls) >= 2, (
            f"Expected at least 2 GET requests (pagination), got {len(request_urls)}: "
            f"{request_urls}"
        )
        # First request must NOT contain a continuation token
        assert "continuationToken" not in request_urls[0], (
            f"First request must be to /workspaces without token: {request_urls[0]}"
        )
        # Second request must contain the continuation token (pagination followed)
        assert any("continuationToken" in u for u in request_urls[1:]), (
            f"Expected at least one paginated request with continuationToken: {request_urls}"
        )
        data = json.loads(result.output)
        assert isinstance(data, list)
        # All three workspaces from both pages must appear
        assert len(data) == 3, f"Expected 3 workspaces (2 + 1 across pages), got {len(data)}"
        ids = {w["id"] for w in data}
        assert WS_GUID in ids
        assert WS2_GUID in ids
        assert WS3_GUID in ids

    def test_list_pagination_stops_when_no_continuation(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """Pagination must stop when the response contains no continuationUri."""
        _ = cache_env
        call_count = 0

        def _single_page(_request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Return a page with NO continuationUri
            return httpx.Response(200, json=_PAGE2_BODY)

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(url__regex=r".*/workspaces(\?.*)?$").mock(side_effect=_single_page)

            with patch(
                "fabric_dw.cli.commands.workspaces.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(cli, ["workspaces", "list"])

        assert result.exit_code == 0, result.output
        assert call_count == 1, f"Expected exactly 1 GET (no continuationUri), got {call_count}"

    def test_list_returns_all_items_across_pages(self, runner: CliRunner, cache_env: Path) -> None:
        """All items from all pages must appear in the JSON output."""
        _ = cache_env

        def _paginated_handler(request: httpx.Request) -> httpx.Response:
            if "continuationToken" in str(request.url):
                return httpx.Response(200, json=_PAGE2_BODY)
            return httpx.Response(200, json=_PAGE1_BODY)

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(url__regex=r".*/workspaces(\?.*)?$").mock(
                side_effect=_paginated_handler
            )

            with patch(
                "fabric_dw.cli.commands.workspaces.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(cli, ["--json", "workspaces", "list"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        names = [w["displayName"] for w in data]
        assert "AnalyticsWorkspace" in names
        assert "DataScienceWorkspace" in names
        assert "MLWorkspace" in names


# ---------------------------------------------------------------------------
# workspaces get — GET wire validation
# ---------------------------------------------------------------------------


class TestWorkspacesGetRespx:
    """Wire-validate that ``workspaces get`` issues the correct GET request."""

    def test_get_issues_correct_url(self, runner: CliRunner, cache_env: Path) -> None:
        """GET must target /workspaces/{ws_id}."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            get_route = mock_router.get(_WS_DETAIL_URL).mock(
                return_value=httpx.Response(200, json=_WS_DETAIL)
            )

            with patch(
                "fabric_dw.cli.commands.workspaces.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(cli, ["--json", "workspaces", "get", WS_GUID])

        assert result.exit_code == 0, result.output
        assert get_route.called, f"Expected GET {_WS_DETAIL_URL}"
        data = json.loads(result.output)
        assert data["id"] == WS_GUID
        assert data["displayName"] == "AnalyticsWorkspace"

    def test_get_uses_get_http_method(self, runner: CliRunner, cache_env: Path) -> None:
        """A wrong HTTP method would miss the registered route."""
        _ = cache_env
        with respx.mock(assert_all_called=False) as mock_router:
            # Only GET is registered — POST/PATCH would not match
            get_route = mock_router.get(_WS_DETAIL_URL).mock(
                return_value=httpx.Response(200, json=_WS_DETAIL)
            )

            with patch(
                "fabric_dw.cli.commands.workspaces.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(cli, ["workspaces", "get", WS_GUID])

        assert result.exit_code == 0, result.output
        assert get_route.called, "GET route must be matched — wrong method would miss this route"

    def test_get_parsed_output_matches_api_response(
        self, runner: CliRunner, cache_env: Path
    ) -> None:
        """The CLI output must reflect the JSON returned by the API."""
        _ = cache_env
        ws_response = {
            "id": WS_GUID,
            "displayName": "AnalyticsWorkspace",
            "description": "Primary analytics workspace for data engineering",
            "type": "Workspace",
            "capacityId": "cafebabe-dead-beef-cafe-babe12345678",
            "defaultDataWarehouseCollation": "Latin1_General_100_BIN2_UTF8",
        }

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(_WS_DETAIL_URL).mock(return_value=httpx.Response(200, json=ws_response))

            with patch(
                "fabric_dw.cli.commands.workspaces.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(cli, ["--json", "workspaces", "get", WS_GUID])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == WS_GUID
        assert data["displayName"] == "AnalyticsWorkspace"
        assert data["defaultDataWarehouseCollation"] == "Latin1_General_100_BIN2_UTF8"

    def test_get_bearer_auth_on_request(self, runner: CliRunner, cache_env: Path) -> None:
        """The GET /workspaces/{ws} request must carry a Bearer Authorization header."""
        _ = cache_env
        seen_headers: list[dict[str, str]] = []

        def _capture(request: httpx.Request) -> httpx.Response:
            seen_headers.append(dict(request.headers))
            return httpx.Response(200, json=_WS_DETAIL)

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(_WS_DETAIL_URL).mock(side_effect=_capture)

            with patch(
                "fabric_dw.cli.commands.workspaces.build_http_client",
                new=_real_http_client_cm,
            ):
                result = runner.invoke(cli, ["workspaces", "get", WS_GUID])

        assert result.exit_code == 0, result.output
        assert len(seen_headers) == 1
        auth = seen_headers[0].get("authorization", "")
        assert auth.startswith("Bearer "), f"Expected Bearer token, got: {auth!r}"
