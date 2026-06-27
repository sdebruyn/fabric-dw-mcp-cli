"""Tests for services.sql_pools — written TDD."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx
from pydantic import ValidationError

from fabric_dw.exceptions import (
    AlreadyExistsError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from fabric_dw.models import SqlPool, SqlPoolsConfiguration
from fabric_dw.services import sql_pools
from tests.unit.services._helpers import _make_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
_BASE_URL = "https://api.fabric.microsoft.com/v1"
_CONFIG_URL = f"{_BASE_URL}/workspaces/{_WS_ID}/warehouses/sqlPoolsConfiguration"

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


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

# A response payload where a nested pool entry is missing required fields
# ('name' and 'maxResourcePercentage').  model_validate on SqlPoolsConfiguration
# would raise ValidationError here, but get_status must succeed regardless.
POOLS_ENABLED_BROKEN_POOL_PAYLOAD: dict[str, object] = {
    "customSQLPoolsEnabled": True,
    "customSQLPools": [
        {
            # 'name' and 'maxResourcePercentage' are intentionally absent
            # to simulate beta API schema drift.
            "isDefault": True,
            "optimizeForReads": False,
        }
    ],
}

POOLS_DISABLED_BROKEN_POOL_PAYLOAD: dict[str, object] = {
    "customSQLPoolsEnabled": False,
    "customSQLPools": [
        {
            # Same broken pool entry as above.
            "isDefault": False,
        }
    ],
}


async def test_get_status_returns_true_when_enabled() -> None:
    """get_status returns True when customSQLPoolsEnabled is true."""
    with respx.mock:
        route = respx.get(_CONFIG_URL).mock(
            return_value=httpx.Response(200, json=POOLS_ENABLED_PAYLOAD)
        )
        client = await _make_client()
        async with client:
            result = await sql_pools.get_status(client, _WS_ID)

    assert route.called
    assert "beta=True" in str(route.calls[0].request.url)
    assert result is True


async def test_get_status_returns_false_when_disabled() -> None:
    """get_status returns False when customSQLPoolsEnabled is false."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(200, json=POOLS_DISABLED_PAYLOAD))
        client = await _make_client()
        async with client:
            result = await sql_pools.get_status(client, _WS_ID)

    assert result is False


async def test_get_status_ignores_broken_nested_pool_fields_when_enabled() -> None:
    """get_status does not raise ValidationError when nested pool fields are missing.

    This is the key regression test for issue #905: if the beta API renames or
    drops a required pool field, full SqlPoolsConfiguration.model_validate would
    raise ValidationError.  get_status reads only the top-level flag and must
    therefore succeed even when the pool list is malformed.
    """
    with respx.mock:
        respx.get(_CONFIG_URL).mock(
            return_value=httpx.Response(200, json=POOLS_ENABLED_BROKEN_POOL_PAYLOAD)
        )
        client = await _make_client()
        async with client:
            result = await sql_pools.get_status(client, _WS_ID)

    # The flag is readable even though the nested pool entry is missing fields.
    assert result is True


async def test_get_status_ignores_broken_nested_pool_fields_when_disabled() -> None:
    """get_status returns False and does not raise when pool entries are malformed."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(
            return_value=httpx.Response(200, json=POOLS_DISABLED_BROKEN_POOL_PAYLOAD)
        )
        client = await _make_client()
        async with client:
            result = await sql_pools.get_status(client, _WS_ID)

    assert result is False


async def test_get_status_raises_fabric_error_when_flag_key_missing() -> None:
    """get_status raises FabricError when customSQLPoolsEnabled is absent."""
    from fabric_dw.exceptions import FabricError  # noqa: PLC0415

    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(200, json={"customSQLPools": []}))
        client = await _make_client()
        async with client:
            with pytest.raises(FabricError, match="customSQLPoolsEnabled"):
                await sql_pools.get_status(client, _WS_ID)


async def test_get_status_403_raises_permission_denied() -> None:
    """get_status propagates PermissionDeniedError on 403."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await sql_pools.get_status(client, _WS_ID)


# ---------------------------------------------------------------------------
# get_configuration
# ---------------------------------------------------------------------------


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


async def test_get_configuration_disabled_workspace() -> None:
    """get_configuration handles disabled pools (customSQLPoolsEnabled=false)."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(200, json=POOLS_DISABLED_PAYLOAD))
        client = await _make_client()
        async with client:
            result = await sql_pools.get_configuration(client, _WS_ID)

    assert result.custom_sql_pools_enabled is False
    assert len(result.custom_sql_pools) == 1


async def test_get_configuration_403_raises_permission_denied() -> None:
    """get_configuration propagates PermissionDeniedError on 403 with hint."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await sql_pools.get_configuration(client, _WS_ID)


# ---------------------------------------------------------------------------
# update_configuration
# ---------------------------------------------------------------------------


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


async def test_update_configuration_403_raises_permission_denied() -> None:
    """update_configuration propagates PermissionDeniedError on 403."""
    config = SqlPoolsConfiguration.model_validate(POOLS_ENABLED_PAYLOAD)

    with respx.mock:
        respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
        client = await _make_client()
        async with client:
            with pytest.raises(PermissionDeniedError):
                await sql_pools.update_configuration(client, _WS_ID, config)


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


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


async def test_enable_raises_bad_request_when_no_pools_defined() -> None:
    """enable raises BadRequestError when customSQLPools is empty.

    The Fabric API rejects customSQLPoolsEnabled=True with an empty pool list
    (HTTP 400 "Array item count 0 is less than minimum count of 1").  The
    service must surface this as a typed BadRequestError *before* the PATCH so
    callers (CLI and MCP) never see a raw HTTP 400.
    """
    with respx.mock:
        get_route = respx.get(_CONFIG_URL).mock(
            return_value=httpx.Response(200, json=POOLS_EMPTY_PAYLOAD)
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            with pytest.raises(BadRequestError, match="sql-pools create"):
                await sql_pools.enable(client, _WS_ID)

    # The GET must have been called, but the PATCH must NOT — no round-trip to the API.
    assert get_route.call_count == 1
    assert not patch_route.called


async def test_enable_propagates_not_found_on_404() -> None:
    """enable propagates NotFoundError when get_configuration returns 404.

    There is no fallback that PATCHes an empty configuration — if the workspace
    configuration endpoint is absent, the error surfaces to the caller.
    """
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(404, json={"error": "not found"}))
        client = await _make_client()
        async with client:
            with pytest.raises(NotFoundError):
                await sql_pools.enable(client, _WS_ID)


async def test_disable_propagates_not_found_on_404() -> None:
    """disable propagates NotFoundError when get_configuration returns 404.

    There is no fallback that PATCHes an empty configuration — if the workspace
    configuration endpoint is absent, the error surfaces to the caller.
    """
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(404, json={"error": "not found"}))
        client = await _make_client()
        async with client:
            with pytest.raises(NotFoundError):
                await sql_pools.disable(client, _WS_ID)


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


# Pool payload with headroom for adding another pool (total = 80%)
_POOLS_WITH_HEADROOM: dict[str, object] = {
    "customSQLPoolsEnabled": True,
    "customSQLPools": [
        {
            "name": "ETL",
            "isDefault": False,
            "maxResourcePercentage": 40,
            "optimizeForReads": False,
            "classifier": {"type": "Application Name", "value": ["ETL", "Load"]},
        },
        {
            "name": "Reporting",
            "isDefault": True,
            "maxResourcePercentage": 40,
            "optimizeForReads": True,
            "classifier": {"type": "Application Name", "value": ["Reports"]},
        },
    ],
}


# ---------------------------------------------------------------------------
# create_pool
# ---------------------------------------------------------------------------


async def test_create_pool_appends_and_patches() -> None:
    """create_pool should GET, append the new pool, and PATCH the full list."""
    new_pool = SqlPool.model_validate(
        {
            "name": "NewPool",
            "isDefault": False,
            "maxResourcePercentage": 20,
            "optimizeForReads": True,
            "classifier": {"type": "Application Name", "value": ["App1"]},
        }
    )
    with respx.mock:
        respx.get(_CONFIG_URL).mock(
            side_effect=[
                httpx.Response(200, json=_POOLS_WITH_HEADROOM),
                httpx.Response(200, json=_POOLS_WITH_HEADROOM),
            ]
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            await sql_pools.create_pool(client, _WS_ID, new_pool)

    assert patch_route.called
    sent_body = json.loads(patch_route.calls[0].request.content)
    pool_names = [p["name"] for p in sent_body["customSQLPools"]]
    assert "NewPool" in pool_names
    assert len(pool_names) == 3  # 2 original + 1 new


async def test_create_pool_raises_already_exists_on_duplicate() -> None:
    """create_pool should raise AlreadyExistsError when the pool name already exists."""
    duplicate_pool = SqlPool.model_validate(
        {
            "name": "ETL",
            "isDefault": False,
            "maxResourcePercentage": 10,
        }
    )
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(200, json=POOLS_ENABLED_PAYLOAD))
        client = await _make_client()
        async with client:
            with pytest.raises(AlreadyExistsError, match="ETL"):
                await sql_pools.create_pool(client, _WS_ID, duplicate_pool)


async def test_create_pool_single_classifier_value() -> None:
    """create_pool should handle a classifier with a single value list."""
    new_pool = SqlPool.model_validate(
        {
            "name": "SingleValPool",
            "isDefault": False,
            "maxResourcePercentage": 10,
            "classifier": {"type": "Application Name", "value": ["OnlyApp"]},
        }
    )
    with respx.mock:
        respx.get(_CONFIG_URL).mock(
            side_effect=[
                httpx.Response(200, json=_POOLS_WITH_HEADROOM),
                httpx.Response(200, json=_POOLS_WITH_HEADROOM),
            ]
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            await sql_pools.create_pool(client, _WS_ID, new_pool)

    sent_body = json.loads(patch_route.calls[0].request.content)
    new = next(p for p in sent_body["customSQLPools"] if p["name"] == "SingleValPool")
    assert new["classifier"]["value"] == ["OnlyApp"]


async def test_create_pool_multiple_classifier_values() -> None:
    """create_pool should preserve multiple classifier values."""
    new_pool = SqlPool.model_validate(
        {
            "name": "MultiValPool",
            "isDefault": False,
            "maxResourcePercentage": 10,
            "classifier": {"type": "Application Name", "value": ["App1", "App2", "App3"]},
        }
    )
    with respx.mock:
        respx.get(_CONFIG_URL).mock(
            side_effect=[
                httpx.Response(200, json=_POOLS_WITH_HEADROOM),
                httpx.Response(200, json=_POOLS_WITH_HEADROOM),
            ]
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            await sql_pools.create_pool(client, _WS_ID, new_pool)

    sent_body = json.loads(patch_route.calls[0].request.content)
    new = next(p for p in sent_body["customSQLPools"] if p["name"] == "MultiValPool")
    assert new["classifier"]["value"] == ["App1", "App2", "App3"]


# ---------------------------------------------------------------------------
# update_pool
# ---------------------------------------------------------------------------


async def test_update_pool_raises_not_found_for_missing_name() -> None:
    """update_pool should raise NotFoundError when the pool name does not exist."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(200, json=POOLS_ENABLED_PAYLOAD))
        client = await _make_client()
        async with client:
            with pytest.raises(NotFoundError, match="NonExistent"):
                await sql_pools.update_pool(
                    client, _WS_ID, "NonExistent", max_resource_percentage=50
                )


async def test_update_pool_partial_update_preserves_other_fields() -> None:
    """update_pool should preserve omitted fields unchanged."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(
            side_effect=[
                httpx.Response(200, json=_POOLS_WITH_HEADROOM),
                httpx.Response(200, json=_POOLS_WITH_HEADROOM),
            ]
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            await sql_pools.update_pool(client, _WS_ID, "ETL", max_resource_percentage=50)

    sent_body = json.loads(patch_route.calls[0].request.content)
    etl = next(p for p in sent_body["customSQLPools"] if p["name"] == "ETL")
    assert etl["maxResourcePercentage"] == 50
    assert etl["isDefault"] is False
    assert etl["optimizeForReads"] is False
    assert etl["classifier"]["value"] == ["ETL", "Load"]


async def test_update_pool_toggle_is_default() -> None:
    """update_pool can toggle the is_default flag."""
    base_payload: dict[str, object] = {
        "customSQLPoolsEnabled": True,
        "customSQLPools": [
            {"name": "A", "isDefault": False, "maxResourcePercentage": 50},
            {"name": "B", "isDefault": False, "maxResourcePercentage": 50},
        ],
    }
    with respx.mock:
        respx.get(_CONFIG_URL).mock(
            side_effect=[
                httpx.Response(200, json=base_payload),
                httpx.Response(200, json=base_payload),
            ]
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            await sql_pools.update_pool(client, _WS_ID, "A", is_default=True)

    sent_body = json.loads(patch_route.calls[0].request.content)
    pool_a = next(p for p in sent_body["customSQLPools"] if p["name"] == "A")
    pool_b = next(p for p in sent_body["customSQLPools"] if p["name"] == "B")
    assert pool_a["isDefault"] is True
    assert pool_b["isDefault"] is False


async def test_update_pool_classifier_both_fields() -> None:
    """update_pool replaces classifier type and values when both are supplied."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(
            side_effect=[
                httpx.Response(200, json=POOLS_ENABLED_PAYLOAD),
                httpx.Response(200, json=POOLS_ENABLED_PAYLOAD),
            ]
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            await sql_pools.update_pool(
                client,
                _WS_ID,
                "ETL",
                classifier_type="Application Name",
                classifier_values=["NewApp"],
            )

    sent_body = json.loads(patch_route.calls[0].request.content)
    etl = next(p for p in sent_body["customSQLPools"] if p["name"] == "ETL")
    assert etl["classifier"]["value"] == ["NewApp"]
    assert etl["classifier"]["type"] == "Application Name"


# ---------------------------------------------------------------------------
# delete_pool
# ---------------------------------------------------------------------------


async def test_delete_pool_removes_named_pool() -> None:
    """delete_pool should GET, drop the named pool, and PATCH."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(
            side_effect=[
                httpx.Response(200, json=POOLS_ENABLED_PAYLOAD),
                httpx.Response(200, json=POOLS_ENABLED_PAYLOAD),
            ]
        )
        patch_route = respx.patch(_CONFIG_URL).mock(return_value=httpx.Response(200))
        client = await _make_client()
        async with client:
            await sql_pools.delete_pool(client, _WS_ID, "ETL")

    sent_body = json.loads(patch_route.calls[0].request.content)
    pool_names = [p["name"] for p in sent_body["customSQLPools"]]
    assert "ETL" not in pool_names
    assert len(pool_names) == 2


async def test_delete_pool_raises_not_found_for_missing_name() -> None:
    """delete_pool should raise NotFoundError when the pool name does not exist."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(200, json=POOLS_ENABLED_PAYLOAD))
        client = await _make_client()
        async with client:
            with pytest.raises(NotFoundError, match="DoesNotExist"):
                await sql_pools.delete_pool(client, _WS_ID, "DoesNotExist")


# ---------------------------------------------------------------------------
# update_pool — classifier partial validation
# ---------------------------------------------------------------------------


async def test_update_pool_classifier_type_without_values_raises() -> None:
    """update_pool raises ValueError when classifier_type is given but classifier_values is not."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(200, json=POOLS_ENABLED_PAYLOAD))
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="classifier_type and classifier_values"):
                await sql_pools.update_pool(
                    client, _WS_ID, "ETL", classifier_type="Application Name"
                )


async def test_update_pool_classifier_values_without_type_raises() -> None:
    """update_pool raises ValueError when classifier_values is given but classifier_type is not."""
    with respx.mock:
        respx.get(_CONFIG_URL).mock(return_value=httpx.Response(200, json=POOLS_ENABLED_PAYLOAD))
        client = await _make_client()
        async with client:
            with pytest.raises(ValueError, match="classifier_type and classifier_values"):
                await sql_pools.update_pool(client, _WS_ID, "ETL", classifier_values=["App1"])
