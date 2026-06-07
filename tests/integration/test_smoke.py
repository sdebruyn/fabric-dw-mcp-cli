from uuid import UUID

import pytest

from fabric_dw.http_client import FabricHttpClient
from fabric_dw.services import workspaces

pytestmark = pytest.mark.integration


async def test_list_workspaces_includes_test_workspace(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> None:
    items = await workspaces.list_all(http)
    ids = {w.id for w in items}
    assert workspace_id in ids
