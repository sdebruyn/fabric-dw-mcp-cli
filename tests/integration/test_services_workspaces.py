from uuid import UUID

import pytest

from fabric_dw.exceptions import FabricError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.services import workspaces

pytestmark = pytest.mark.integration


async def test_list_all_includes_test_workspace(http: FabricHttpClient, workspace_id: UUID) -> None:
    items = await workspaces.list_all(http)
    assert workspace_id in {w.id for w in items}


async def test_get_workspace(http: FabricHttpClient, workspace_id: UUID) -> None:
    ws = await workspaces.get(http, workspace_id)
    assert ws.id == workspace_id
    assert ws.name


async def test_get_collation_returns_string_or_none(
    http: FabricHttpClient, workspace_id: UUID
) -> None:
    result = await workspaces.get_collation(http, workspace_id)
    assert result is None or isinstance(result, str)


async def test_set_collation_invalid_value_raises(
    http: FabricHttpClient, workspace_id: UUID
) -> None:
    with pytest.raises(ValueError, match="Unsupported collation"):
        await workspaces.set_collation(http, workspace_id, "BOGUS_COLLATION")


async def test_set_collation_happy_path_does_not_crash(
    http: FabricHttpClient, workspace_id: UUID
) -> None:
    # Best-effort PATCH; API may 404 if the route is not exposed yet. We accept FabricError
    # but not other exceptions.
    try:
        await workspaces.set_collation(http, workspace_id, "Latin1_General_100_BIN2_UTF8")
    except FabricError:
        pytest.skip("workspace collation PATCH not supported on this tenant")
