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
    # The SP creating ephemeral_warehouse already owns it. Fabric returns HTTP 403
    # ArtifactTakeOverNotAllowedByOwner in this case; our service maps it to a
    # clear "already owner" message without the generic role hint.
    with pytest.raises(PermissionDeniedError, match="already the owner"):
        await ownership.takeover(http, workspace_id, ephemeral_warehouse.id)
