import uuid

import pytest
import tenacity

from fabric_dw.exceptions import NotFoundError
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.models import Warehouse
from fabric_dw.services import warehouses

pytestmark = pytest.mark.integration


async def test_ephemeral_warehouse_appears_in_list(
    http: FabricHttpClient, workspace_id: uuid.UUID, ephemeral_warehouse: Warehouse
) -> None:
    # The Fabric REST API occasionally returns a transient 404 on the
    # workspace warehouses endpoint; retry briefly to ride out the blip.
    @tenacity.retry(
        retry=tenacity.retry_if_exception_type(NotFoundError),
        stop=tenacity.stop_after_attempt(4),
        wait=tenacity.wait_exponential(multiplier=2, min=2, max=15),
        reraise=True,
    )
    async def _list() -> list:
        return await warehouses.list_warehouses(http, workspace_id)

    items = await _list()
    assert ephemeral_warehouse.id in {w.id for w in items}


async def test_get_ephemeral_warehouse(
    http: FabricHttpClient, workspace_id: uuid.UUID, ephemeral_warehouse: Warehouse
) -> None:
    fetched = await warehouses.get_warehouse(http, workspace_id, ephemeral_warehouse.id)
    assert fetched.id == ephemeral_warehouse.id
    assert fetched.name == ephemeral_warehouse.name
    assert fetched.connection_string


async def test_rename_ephemeral_warehouse(
    http: FabricHttpClient, workspace_id: uuid.UUID, ephemeral_warehouse: Warehouse
) -> None:
    new_name = f"{ephemeral_warehouse.name}-renamed"
    updated = await warehouses.rename(http, workspace_id, ephemeral_warehouse.id, new_name)
    assert updated.name == new_name


async def test_delete_nonexistent_warehouse_raises(
    http: FabricHttpClient, workspace_id: uuid.UUID
) -> None:
    bogus = uuid.uuid4()
    with pytest.raises(NotFoundError):
        await warehouses.delete(http, workspace_id, bogus)
