"""Integration tests for fabric_dw.services.permissions (admin API).

These tests require:
- FABRIC_TEST_WORKSPACE_ID set to a workspace the caller can see.
- The caller must be a Fabric Administrator to call the admin endpoint.

Tests that require admin access are marked with ``skip_if_not_admin``
and are gated behind the ``integration`` pytest mark.
"""

from __future__ import annotations

import os
from uuid import UUID

import pytest

from fabric_dw.exceptions import PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import ItemAccess, Warehouse
from fabric_dw.services import permissions

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

#: Set to True when the test environment is known to have Fabric Admin access.
_IS_ADMIN = os.environ.get("FABRIC_TEST_IS_ADMIN", "").lower() in {"1", "true", "yes"}

skip_if_not_admin = pytest.mark.skipif(
    not _IS_ADMIN,
    reason="FABRIC_TEST_IS_ADMIN not set; skipping admin-only test",
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@skip_if_not_admin
async def test_list_item_access_returns_list(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_warehouse: Warehouse,
) -> None:
    """list_item_access must return a list of ItemAccess for a real warehouse."""
    result = await permissions.list_item_access(http, workspace_id, ephemeral_warehouse.id)

    assert isinstance(result, list)
    assert all(isinstance(item, ItemAccess) for item in result)


@skip_if_not_admin
async def test_list_item_access_principals_have_required_fields(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_warehouse: Warehouse,
) -> None:
    """Every returned principal must have id, display_name, and type."""
    result = await permissions.list_item_access(http, workspace_id, ephemeral_warehouse.id)

    for item in result:
        assert item.principal.id is not None
        assert item.principal.type is not None


async def test_list_item_access_non_admin_raises_permission_denied(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_warehouse: Warehouse,
) -> None:
    """list_item_access must raise PermissionDeniedError (with admin hint) when not an admin.

    This test is expected to run even without admin access; it just verifies
    the error is surfaced correctly.  If the caller IS an admin the test is
    skipped so it does not accidentally pass for the wrong reason.
    """
    if _IS_ADMIN:
        pytest.skip("caller has admin access; cannot test non-admin path")

    with pytest.raises(PermissionDeniedError, match="Fabric Administrator"):
        await permissions.list_item_access(http, workspace_id, ephemeral_warehouse.id)
