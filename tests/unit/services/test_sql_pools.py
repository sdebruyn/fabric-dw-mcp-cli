"""Tests for services.sql_pools — written TDD."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
import respx
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from pydantic import ValidationError

from fabric_dw.exceptions import NotFound, PermissionDenied
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import SqlPoolsConfiguration
from fabric_dw.services import sql_pools

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_BASE_URL = "https://api.fabric.microsoft.com/v1"
_CONFIG_URL = f"{_BASE_URL}/workspaces/{_WS_ID}/warehouses/sqlPoolsConfiguration"

_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106

POOLS_ENABLED_PAYLOAD: dict[str, Any] = {
    "customSQLPoolsEnabled": True,
    "customSQLPools": [
        {
            "name": "ETL",
            "isDefault": False,
            "maxResourcePercentage": 30,
            "optimizeForReads": False,
            "classifier": {
                "type": "Application Name",
                "value": ["ETL", "Load"],
            },
        },
        {
            "name": "Reporting",
            "isDefault": False,
            "maxResourcePercentage": 30,
            "optimizeForReads": True,
            "classifier": {
                "type": "Application Name",
                "value": ["Reports"],
            },
        },
        {
            "name": "Default",
            "isDefault": True,
            "maxResourcePercentage": 40,
            "optimizeForReads": False,
            "classifier": {
                "type": "Application Name",
                "value": ["Default"],
            },
        },
    ],
}

POOLS_DISABLED_PAYLOAD: dict[str, Any] = {
    "customSQLPoolsEnabled": False,
    "customSQLPools": [
        {
            "name": "Default",
            "isDefault": True,
            "maxResourcePercentage": 100,
            "optimizeForReads": True,
        }
    ],
}

POOLS_EMPTY_PAYLOAD: dict[str, Any] = {
    "customSQLPoolsEnabled": False,
    "customSQLPools": [],
}


def _make_credential() -> AsyncTokenCredential:
    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(return_value=_FAKE_TOKEN)
    return cred


async def _make_client() -> FabricHttpClient:
    return FabricHttpClient(credential=_make_credential(), rps=100)


# ---------------------------------------------------------------------------
# get_configuration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_configuration_returns_model() -> None:
    """get_configuration should GET the endpoint with ?beta=True and return a model."""
    with respx.mock:
        route = respx.get(_CONFIG_URL).mock(
            return_value=httpx.Response(200, json=POOLS_ENABLED_PAYLOAD)
        )
        client = await _make_client()
        async with client:
            result = await sql_pools.get_configuration(client, _WS_ID)

    assert route.called
    assert "beta=True" in str(route.calls[0].request.url)
    assert isinstance(result, SqlPoolsConfiguration)
    assert result.custom_sql_pools_enabled is True
    assert len(result.custom_sql_pools) == 3
    assert result.custom_sql_pools[0].name == "ETL"
    assert result.custom_sql_pools[0].max_resource_percentage == 30
    assert result.custom_sql_pools[0].classifier is not None
    assert result.custom_sql_pools[0].classifier.type == "Application Name"
    assert result.custom_sql_pools[0].classifier.value == ["ETL", "Load"]


@pytest.mark.asyncio
async def test_get_configuration_disabled_workspace() -> None:
    """get_configuration handles disabled pools (customSQLPoolsEnabled=false)."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(200, json=POOLS_DISABLED_PAYLOAD))
        client = await _make_client()
        async with client:
            result = await sql_pools.get_configuration(client, _WS_ID)

    assert result.custom_sql_pools_enabled is False
    assert len(result.custom_sql_pools) == 1


@pytest.mark.asyncio
async def test_get_configuration_403_raises_permission_denied() -> None:
    """get_configuration propagates PermissionDenied on 403 with hint."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await sql_pools.get_configuration(client, _WS_ID)


# ---------------------------------------------------------------------------
# update_configuration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_configuration_patches_full_body() -> None:
    """update_configuration should PATCH the full config and then GET fresh state."""
    config = SqlPoolsConfiguration.model_validate(POOLS_ENABLED_PAYLOAD)

    with respx.mock:
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        get_route = respx.get(_CONFIG_URL).mock(
            return_value=httpx.Response(200, json=POOLS_ENABLED_PAYLOAD)
        )
        client = await _make_client()
        async with client:
            result = await sql_pools.update_configuration(client, _WS_ID, config)

    assert patch_route.called
    assert "beta=True" in str(patch_route.calls[0].request.url)

    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body["customSQLPoolsEnabled"] is True
    assert len(sent_body["customSQLPools"]) == 3

    assert get_route.called
    assert isinstance(result, SqlPoolsConfiguration)


@pytest.mark.asyncio
async def test_update_configuration_destructive_semantics_reflected_in_body() -> None:
    """Pools absent from the config model must not be in the PATCH body (destructive semantics)."""
    single_pool_config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {
                    "name": "OnlyPool",
                    "isDefault": True,
                    "maxResourcePercentage": 100,
                    "optimizeForReads": False,
                }
            ],
        }
    )

    with respx.mock:
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(200, json=POOLS_ENABLED_PAYLOAD))
        client = await _make_client()
        async with client:
            await sql_pools.update_configuration(client, _WS_ID, single_pool_config)

    sent_body = json.loads(patch_route.calls[0].request.content)
    pool_names = [p["name"] for p in sent_body["customSQLPools"]]
    assert pool_names == ["OnlyPool"]


@pytest.mark.asyncio
async def test_update_configuration_403_raises_permission_denied() -> None:
    """update_configuration propagates PermissionDenied on 403."""
    config = SqlPoolsConfiguration.model_validate(POOLS_ENABLED_PAYLOAD)

    with respx.mock:
        respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDenied):
                await sql_pools.update_configuration(client, _WS_ID, config)


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_patches_enabled_true_preserving_pools() -> None:
    """enable should PATCH customSQLPoolsEnabled=true and keep existing pools."""
    with respx.mock:
        get_route = respx.get(_CONFIG_URL).mock(
            side_effect=[
                httpx.Response(200, json=POOLS_DISABLED_PAYLOAD),
                httpx.Response(200, json=POOLS_ENABLED_PAYLOAD),
            ]
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            result = await sql_pools.enable(client, _WS_ID)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body["customSQLPoolsEnabled"] is True
    assert "customSQLPools" in sent_body

    assert get_route.call_count == 2
    assert isinstance(result, SqlPoolsConfiguration)
    assert result.custom_sql_pools_enabled is True


@pytest.mark.asyncio
async def test_enable_is_no_op_when_already_enabled() -> None:
    """enable should return current config without PATCH when already enabled."""
    with respx.mock:
        get_route = respx.get(_CONFIG_URL).mock(
            return_value=httpx.Response(200, json=POOLS_ENABLED_PAYLOAD)
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            result = await sql_pools.enable(client, _WS_ID)

    assert get_route.call_count == 1
    assert not patch_route.called
    assert result.custom_sql_pools_enabled is True


@pytest.mark.asyncio
async def test_disable_patches_enabled_false_preserving_pools() -> None:
    """disable should PATCH customSQLPoolsEnabled=false and keep existing pools."""
    with respx.mock:
        get_route = respx.get(_CONFIG_URL).mock(
            side_effect=[
                httpx.Response(200, json=POOLS_ENABLED_PAYLOAD),
                httpx.Response(200, json=POOLS_DISABLED_PAYLOAD),
            ]
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            result = await sql_pools.disable(client, _WS_ID)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    assert sent_body["customSQLPoolsEnabled"] is False
    assert "customSQLPools" in sent_body
    pool_names = [p["name"] for p in sent_body["customSQLPools"]]
    assert "Default" in pool_names

    assert get_route.call_count == 2
    assert isinstance(result, SqlPoolsConfiguration)


@pytest.mark.asyncio
async def test_enable_propagates_not_found_on_404() -> None:
    """enable propagates NotFound when get_configuration returns 404.

    There is no fallback that PATCHes an empty configuration — if the workspace
    configuration endpoint is absent, the error surfaces to the caller.
    """
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(404, json={"error": "not found"}))
        client = await _make_client()
        async with client:
            with pytest.raises(NotFound):
                await sql_pools.enable(client, _WS_ID)


@pytest.mark.asyncio
async def test_disable_propagates_not_found_on_404() -> None:
    """disable propagates NotFound when get_configuration returns 404.

    There is no fallback that PATCHes an empty configuration — if the workspace
    configuration endpoint is absent, the error surfaces to the caller.
    """
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(404, json={"error": "not found"}))
        client = await _make_client()
        async with client:
            with pytest.raises(NotFound):
                await sql_pools.disable(client, _WS_ID)


@pytest.mark.asyncio
async def test_disable_is_no_op_when_already_disabled() -> None:
    """disable should return current config without PATCH when already disabled."""
    with respx.mock:
        get_route = respx.get(_CONFIG_URL).mock(
            return_value=httpx.Response(200, json=POOLS_DISABLED_PAYLOAD)
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            result = await sql_pools.disable(client, _WS_ID)

    assert get_route.call_count == 1
    assert not patch_route.called
    assert result.custom_sql_pools_enabled is False


# ---------------------------------------------------------------------------
# Model validation (client-side constraints via validate_for_patch)
# ---------------------------------------------------------------------------


def test_validate_for_patch_rejects_sum_over_100() -> None:
    """validate_for_patch should raise ValueError when sum > 100."""
    config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {"name": "A", "isDefault": False, "maxResourcePercentage": 60},
                {"name": "B", "isDefault": True, "maxResourcePercentage": 60},
            ],
        }
    )
    with pytest.raises(ValueError, match="Sum of maxResourcePercentage"):
        config.validate_for_patch()


def test_validate_for_patch_rejects_multiple_defaults() -> None:
    """validate_for_patch should raise ValueError when > 1 pool is default."""
    config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {"name": "A", "isDefault": True, "maxResourcePercentage": 40},
                {"name": "B", "isDefault": True, "maxResourcePercentage": 40},
            ],
        }
    )
    with pytest.raises(ValueError, match="Exactly one SQL pool may be marked as default"):
        config.validate_for_patch()


def test_model_validate_does_not_raise_for_invalid_patch_state() -> None:
    """model_validate should NOT raise for state that violates patch constraints.

    GET responses may contain server-side state that violates client constraints
    (beta API drift, race conditions).  Deserialisation must not fail.
    """
    # sum > 100 — should parse fine via model_validate
    config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {"name": "A", "isDefault": False, "maxResourcePercentage": 60},
                {"name": "B", "isDefault": True, "maxResourcePercentage": 60},
            ],
        }
    )
    assert len(config.custom_sql_pools) == 2


def test_model_accepts_exactly_one_default() -> None:
    """SqlPoolsConfiguration with exactly one default pool should be valid."""
    config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {"name": "A", "isDefault": False, "maxResourcePercentage": 40},
                {"name": "B", "isDefault": True, "maxResourcePercentage": 60},
            ],
        }
    )
    assert config.custom_sql_pools[1].is_default is True


def test_model_accepts_no_defaults() -> None:
    """SqlPoolsConfiguration with zero default pools should be valid."""
    config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {"name": "A", "isDefault": False, "maxResourcePercentage": 50},
                {"name": "B", "isDefault": False, "maxResourcePercentage": 50},
            ],
        }
    )
    assert len(config.custom_sql_pools) == 2


def test_model_accepts_sum_exactly_100() -> None:
    """SqlPoolsConfiguration where sum == 100 should be valid."""
    config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {"name": "A", "isDefault": False, "maxResourcePercentage": 50},
                {"name": "B", "isDefault": True, "maxResourcePercentage": 50},
            ],
        }
    )
    assert config.custom_sql_pools[0].max_resource_percentage == 50


def test_model_rejects_max_resource_percentage_below_1() -> None:
    """SqlPool should reject maxResourcePercentage < 1."""
    with pytest.raises(ValidationError):
        SqlPoolsConfiguration.model_validate(
            {
                "customSQLPoolsEnabled": True,
                "customSQLPools": [
                    {"name": "A", "isDefault": True, "maxResourcePercentage": 0},
                ],
            }
        )


def test_model_rejects_max_resource_percentage_above_100() -> None:
    """SqlPool should reject maxResourcePercentage > 100."""
    with pytest.raises(ValidationError):
        SqlPoolsConfiguration.model_validate(
            {
                "customSQLPoolsEnabled": True,
                "customSQLPools": [
                    {"name": "A", "isDefault": True, "maxResourcePercentage": 101},
                ],
            }
        )


def test_model_accepts_empty_pool_list() -> None:
    """SqlPoolsConfiguration with empty customSQLPools should be valid."""
    config = SqlPoolsConfiguration.model_validate(POOLS_EMPTY_PAYLOAD)
    assert config.custom_sql_pools == []


def test_model_handles_open_classifier_type() -> None:
    """SqlPoolClassifier should accept unknown type values (open enum)."""
    config = SqlPoolsConfiguration.model_validate(
        {
            "customSQLPoolsEnabled": True,
            "customSQLPools": [
                {
                    "name": "X",
                    "isDefault": True,
                    "maxResourcePercentage": 100,
                    "classifier": {
                        "type": "Future Type Not Yet In SDK",
                        "value": ["v"],
                    },
                }
            ],
        }
    )
    assert config.custom_sql_pools[0].classifier is not None
    assert config.custom_sql_pools[0].classifier.type == "Future Type Not Yet In SDK"
