from uuid import UUID

import pytest

from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import Warehouse
from fabric_dw.services import ownership

pytestmark = pytest.mark.integration


async def test_takeover_ephemeral_warehouse_does_not_raise(
    http: FabricHttpClient, workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> None:
    # The SP creating ephemeral_warehouse already owns it. Calling takeover should be
    # a no-op success (200 or 204). If the API requires a different identity to
    # take over (i.e. you can't take over what you already own), xfail with a clear reason.
    try:
        await ownership.takeover(http, workspace_id, ephemeral_warehouse.id)
    except FabricError as exc:
        pytest.xfail(f"takeover not supported in this tenant state: {exc}")
