from uuid import UUID

import pytest

from fabric_dw.exceptions import PermissionDeniedError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import Warehouse
from fabric_dw.services import ownership

pytestmark = pytest.mark.integration


async def test_takeover_ephemeral_warehouse_already_owner(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    # The SP creating ephemeral_warehouse already owns it. Fabric may return either:
    #   - HTTP 403 ArtifactTakeOverNotAllowedByOwner → our service maps this to a
    #     clear "already the owner" message without the generic role hint, OR
    #   - HTTP 2xx (success) → Fabric treats self-takeover as an idempotent no-op,
    #     or ownership propagation is asynchronous so the SP is not yet recorded as
    #     owner at takeover time.
    # Both are legitimate outcomes; the test accepts either.
    try:
        await ownership.takeover(http, workspace_id, ephemeral_warehouse.id)
    except PermissionDeniedError as exc:
        assert "already the owner" in str(exc)  # noqa: PT017
