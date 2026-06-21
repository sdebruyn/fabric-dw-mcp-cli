"""Tests for tenant_from_connection_string_host — TDD-first.

These tests are written BEFORE the implementation and must fail until the
helper is added to fabric_dw.sql.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential

from fabric_dw.cache import LookupCache
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver
from fabric_dw.sql import tenant_from_connection_string_host


def _live_tel() -> types.ModuleType:
    """Return the live telemetry module from sys.modules.

    test_telemetry.py reloads the module between tests via _reload_telemetry(),
    which replaces the object in sys.modules with a fresh one.  The module-level
    ``_tel`` alias in this file may point to a stale object after such a reload.
    Always using the sys.modules entry ensures monkeypatch and patch.object
    operate on the same object that the resolver's deferred
    ``import fabric_dw.telemetry`` will resolve to at call time.
    """
    return sys.modules["fabric_dw.telemetry"]


# ---------------------------------------------------------------------------
# Verified example from the issue description
# ---------------------------------------------------------------------------

# Host decoded from the issue body:
# tenant  → 9064c167-4885-40ef-9f34-1853218aea86
# workspace → 78b883eb-50f8-4ca3-8732-e4043dffb635
_VERIFIED_HOST = (
    "m7awjeefjdxubhzudbjsdcxkqy-5ob3q6hykcruzbzs4qcd375wgu.datawarehouse.fabric.microsoft.com"
)
_VERIFIED_TENANT = "9064c167-4885-40ef-9f34-1853218aea86"
_VERIFIED_WORKSPACE = "78b883eb-50f8-4ca3-8732-e4043dffb635"

# ---------------------------------------------------------------------------
# Unit tests: tenant_from_connection_string_host
# ---------------------------------------------------------------------------


class TestTenantFromConnectionStringHost:
    """Pure helper — no network, no auth, no telemetry side-effects."""

    def test_verified_example_returns_correct_tenant(self) -> None:
        """The verified host decodes to the expected tenant GUID."""
        assert tenant_from_connection_string_host(_VERIFIED_HOST) == _VERIFIED_TENANT

    def test_server_prefix_stripped(self) -> None:
        """A bare 'Server=<host>' prefix is stripped before decoding."""
        assert tenant_from_connection_string_host(f"Server={_VERIFIED_HOST}") == _VERIFIED_TENANT

    def test_server_prefix_with_space_stripped(self) -> None:
        """'Server= <host>' (space after '=') is also stripped correctly."""
        assert tenant_from_connection_string_host(f"Server= {_VERIFIED_HOST}") == _VERIFIED_TENANT

    def test_garbage_host_returns_none(self) -> None:
        """A completely garbage input returns None and never raises."""
        assert tenant_from_connection_string_host("not-a-real-host.example.com") is None

    def test_non_fabric_host_returns_none(self) -> None:
        """A valid-looking host that is not *.datawarehouse.fabric.microsoft.com returns None."""
        assert tenant_from_connection_string_host("something.database.windows.net") is None

    def test_wrong_segment_count_returns_none(self) -> None:
        """A fabric host with only one base32 segment (no '-') returns None."""
        # Only one segment — can't split on '-' into two 26-char parts
        host = "m7awjeefjdxubhzudbjsdcxkqy.datawarehouse.fabric.microsoft.com"
        assert tenant_from_connection_string_host(host) is None

    def test_wrong_segment_length_returns_none(self) -> None:
        """A fabric host whose first segment has the wrong length returns None."""
        # First segment is only 10 chars — too short to be a valid b32-encoded GUID
        host = "shortvalue-5ob3q6hykcruzbzs4qcd375wgu.datawarehouse.fabric.microsoft.com"
        assert tenant_from_connection_string_host(host) is None

    def test_empty_string_returns_none(self) -> None:
        """Empty input returns None without raising."""
        assert tenant_from_connection_string_host("") is None

    def test_garbage_base32_returns_none(self) -> None:
        """A 26-char non-base32 string in the first segment returns None."""
        # '!' is not valid base32
        bad_first = "!!!!!!!!!!!!!!!!!!!!!!!!!!"
        host = f"{bad_first}-5ob3q6hykcruzbzs4qcd375wgu.datawarehouse.fabric.microsoft.com"
        assert tenant_from_connection_string_host(host) is None

    def test_none_input_returns_none(self) -> None:
        """None input returns None without raising."""
        assert tenant_from_connection_string_host(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Integration: resolver feeds decoded tenant into telemetry.set_tenant_id
# ---------------------------------------------------------------------------

_FAKE_TOKEN = "fake-token"  # noqa: S105


def _make_credential() -> AsyncTokenCredential:
    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(
        return_value=AccessToken(token=_FAKE_TOKEN, expires_on=int(time.time()) + 3600)
    )
    return cred


def _make_resolver(tmp_path: Path) -> tuple[Resolver, FabricHttpClient, LookupCache]:
    cache = LookupCache(path=tmp_path / "lookup.json")
    client = FabricHttpClient(credential=_make_credential(), rps=100)
    resolver = Resolver(http=client, cache=cache)
    return resolver, client, cache


_WS_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_ITEM_GUID = "d4e5f6a7-b8c9-0123-def0-123456789abc"

_FABRIC_ITEM_GENERIC_URL = (
    f"https://api.fabric.microsoft.com/v1/workspaces/{_WS_GUID}/items/{_ITEM_GUID}"
)
_FABRIC_WAREHOUSE_URL = (
    f"https://api.fabric.microsoft.com/v1/workspaces/{_WS_GUID}/warehouses/{_ITEM_GUID}"
)


@pytest.mark.asyncio
async def test_resolver_feeds_tenant_to_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When item() resolves a connection string whose host encodes a tenant,
    set_tenant_id() is called with the decoded tenant GUID."""
    # Resolve the live module object at call time — test_telemetry.py may have
    # reloaded it between tests, making the module-level _tel alias stale.
    tel = _live_tel()

    # Safe telemetry setup: dummy instrumentation key, isolated XDG dir, reset override
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.setattr(tel, "_tenant_id_override", None)
    # Reset the warm in-memory cache sentinel so XDG redirection takes full effect.
    monkeypatch.setattr(tel, "_tenant_id_cache", tel._UNSET)
    resolver, client, _cache = _make_resolver(tmp_path)

    recorded_tenant: list[str] = []

    def _capture_set_tenant(tid: str) -> None:
        recorded_tenant.append(tid)

    with (
        respx.mock(assert_all_called=False) as mock,
        patch.object(tel, "set_tenant_id", side_effect=_capture_set_tenant),
    ):
        # Generic item endpoint returns Warehouse type
        mock.get(_FABRIC_ITEM_GENERIC_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": _ITEM_GUID,
                    "displayName": "MyWarehouse",
                    "type": "Warehouse",
                    "workspaceId": _WS_GUID,
                },
            )
        )
        # Warehouse detail endpoint returns the verified Fabric host as connection string
        mock.get(_FABRIC_WAREHOUSE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": _ITEM_GUID,
                    "displayName": "MyWarehouse",
                    "type": "Warehouse",
                    "workspaceId": _WS_GUID,
                    "properties": {
                        "connectionString": _VERIFIED_HOST,
                    },
                },
            )
        )

        async with client:
            entry = await resolver._fetch_item_detail(UUID(_WS_GUID), UUID(_ITEM_GUID))

    assert entry.connection_string == _VERIFIED_HOST
    assert recorded_tenant == [_VERIFIED_TENANT], (
        f"Expected set_tenant_id({_VERIFIED_TENANT!r}), got {recorded_tenant!r}"
    )


@pytest.mark.asyncio
async def test_resolver_does_not_override_token_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When _tenant_id_override is already set (e.g. from a JWT tid), the
    host-derived tenant must NOT overwrite it — token tid (identity) takes precedence."""
    existing_token_tenant = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # noqa: S105

    # Resolve the live module object at call time (see test_resolver_feeds_tenant_to_telemetry).
    tel = _live_tel()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    # Baseline: reset override first, then set it to the simulated token value.
    monkeypatch.setattr(tel, "_tenant_id_override", None)
    monkeypatch.setattr(tel, "_tenant_id_cache", tel._UNSET)
    # Now set the simulated token tid — must survive connection-string resolution.
    monkeypatch.setattr(tel, "_tenant_id_override", existing_token_tenant)

    resolver, client, _cache = _make_resolver(tmp_path)

    recorded_tenant: list[str] = []

    def _capture_set_tenant(tid: str) -> None:
        recorded_tenant.append(tid)

    with (
        respx.mock(assert_all_called=False) as mock,
        patch.object(tel, "set_tenant_id", side_effect=_capture_set_tenant),
    ):
        mock.get(_FABRIC_ITEM_GENERIC_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": _ITEM_GUID,
                    "displayName": "MyWarehouse",
                    "type": "Warehouse",
                    "workspaceId": _WS_GUID,
                },
            )
        )
        mock.get(_FABRIC_WAREHOUSE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": _ITEM_GUID,
                    "displayName": "MyWarehouse",
                    "type": "Warehouse",
                    "workspaceId": _WS_GUID,
                    "properties": {
                        "connectionString": _VERIFIED_HOST,
                    },
                },
            )
        )

        async with client:
            await resolver._fetch_item_detail(UUID(_WS_GUID), UUID(_ITEM_GUID))

    # set_tenant_id must NOT have been called — the existing token override must remain.
    assert recorded_tenant == [], (
        f"set_tenant_id should not be called when override is already set; got {recorded_tenant!r}"
    )
