import asyncio
import contextlib
import os
import time
import uuid
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio

from fabric_dw.auth import get_credential
from fabric_dw.exceptions import NotFoundError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import Warehouse, WarehouseKind, WarehouseSnapshot
from fabric_dw.services import snapshots, warehouses
from fabric_dw.sql import SqlTarget, is_transient_connection_error, run_query

# Maximum time to wait for a SQL analytics endpoint to provision on a new lakehouse.
_SQL_ENDPOINT_PROVISION_TIMEOUT_S = 300
# Polling interval between provisioning status checks.
_SQL_ENDPOINT_POLL_INTERVAL_S = 5

# Maximum time (seconds) to wait for a fresh warehouse/endpoint SQL database
# to become connectable after creation.
_SQL_READINESS_TIMEOUT_S = 240
# Starting backoff delay (seconds) between readiness probe attempts.
_SQL_READINESS_BACKOFF_INITIAL_S = 2.0
# Maximum backoff delay cap (seconds).
_SQL_READINESS_BACKOFF_MAX_S = 5.0


async def _wait_for_sql_readiness(
    target: SqlTarget,
    *,
    timeout_s: float = _SQL_READINESS_TIMEOUT_S,
) -> None:
    """Poll *target* with ``SELECT 1`` until the SQL engine is reachable.

    A freshly created Fabric warehouse or SQL analytics endpoint requires a
    warm-up period (database provisioning + permission propagation) before TDS
    connections succeed.  During warm-up the driver raises ``OperationalError``
    with messages like "Login failed … database was not found" or "communication
    link failure".  This function retries until the query succeeds or *timeout_s*
    is exhausted.

    Only connection-level transient errors (detected by
    :func:`~fabric_dw.sql.is_transient_connection_error`) and login-failed /
    database-not-found errors are swallowed during the wait.  Any other error
    (e.g. real SQL errors, unexpected exceptions) is re-raised immediately.

    The underlying :func:`~fabric_dw.sql.run_query` is synchronous (TDS), so
    each probe is offloaded to a thread via ``asyncio.to_thread``, mirroring
    how the service layer calls it in ``sql_exec.py``.

    Args:
        target: The :class:`~fabric_dw.sql.SqlTarget` to probe.
        timeout_s: Total seconds to wait before giving up.

    Raises:
        TimeoutError: When the SQL endpoint is still not reachable after *timeout_s*.
        Exception: Any non-transient error raised by the driver.
    """

    def _probe() -> None:
        run_query(target, "SELECT 1", fetch="none")

    deadline = time.monotonic() + timeout_s
    delay = _SQL_READINESS_BACKOFF_INITIAL_S
    while True:
        try:
            await asyncio.to_thread(_probe)
        except Exception as exc:
            msg_lower = str(exc).lower()
            # Swallow transient connection drops AND the Fabric warm-up
            # "database was not found" flavour (AuthError from map_driver_error).
            # We do NOT swallow all "login failed" messages — that is too broad
            # and would hide genuine permanent auth misconfiguration (wrong tenant,
            # expired service principal, etc.) for the full 240 s timeout.
            # "database was not found" uniquely identifies the provisioning-transient
            # variant of login-failed (error 18456), so it is the right signal here.
            is_warmup = "database was not found" in msg_lower
            if not (is_transient_connection_error(exc) or is_warmup):
                # Unexpected error — surface it immediately.
                raise
        else:
            # SUCCESS — the database is reachable.
            return

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"SQL endpoint for {target.database!r} (workspace {target.workspace_id}) "
                f"did not become reachable within {timeout_s:.0f}s. "
                "The warehouse may still be provisioning."
            )

        await asyncio.sleep(delay)
        delay = min(delay * 1.5, _SQL_READINESS_BACKOFF_MAX_S)


@pytest_asyncio.fixture
async def workspace_id() -> UUID:
    raw = os.environ.get("FABRIC_TEST_WORKSPACE_ID")
    if not raw:
        pytest.skip(
            "set FABRIC_TEST_WORKSPACE_ID to run integration tests",
            allow_module_level=True,
        )
    return UUID(raw)


@pytest_asyncio.fixture
async def http() -> AsyncIterator[FabricHttpClient]:
    cred = get_credential()
    async with FabricHttpClient(cred) as client:
        yield client


@pytest_asyncio.fixture
async def ephemeral_warehouse(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> AsyncIterator[Warehouse]:
    name = f"pytest-{uuid.uuid4().hex[:8]}-wh"
    wh = await warehouses.create(http, workspace_id, name)
    try:
        # Wait for the new warehouse's SQL engine to become reachable before
        # yielding.  Fresh Fabric warehouses need warm-up (database provisioning
        # + owner permission propagation) before TDS connections succeed; without
        # this poll essentially every TDS test fails with "database was not found
        # or insufficient permissions".
        assert wh.connection_string, f"Warehouse {wh.id} returned no connection_string"
        sql_target = SqlTarget(
            workspace_id=str(workspace_id),
            database=wh.name,
            connection_string=wh.connection_string,
        )
        await _wait_for_sql_readiness(sql_target)
        yield wh
    finally:
        await warehouses.delete(http, workspace_id, wh.id)


@pytest_asyncio.fixture
async def ephemeral_snapshot(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_warehouse: Warehouse,
) -> AsyncIterator[WarehouseSnapshot]:
    name = f"pytest-{uuid.uuid4().hex[:8]}-snap"
    snap = await snapshots.create(http, workspace_id, ephemeral_warehouse.id, name)
    try:
        yield snap
    finally:
        await snapshots.delete(http, workspace_id, snap.id)


@pytest_asyncio.fixture
async def ephemeral_sql_target(
    workspace_id: UUID, ephemeral_warehouse: Warehouse
) -> AsyncIterator[SqlTarget]:
    assert ephemeral_warehouse.connection_string
    yield SqlTarget(
        workspace_id=str(workspace_id),
        database=ephemeral_warehouse.name,
        connection_string=ephemeral_warehouse.connection_string,
    )


@pytest_asyncio.fixture
async def ephemeral_lakehouse(
    http: FabricHttpClient,
    workspace_id: UUID,
) -> AsyncIterator[dict[str, object]]:
    """Create a schema-enabled Lakehouse and delete it after the test.

    Yields the raw API response dict from the POST (or the GET after LRO), which
    contains at minimum ``id``, ``displayName``, and ``workspaceId``.

    The Lakehouse is created with ``creationPayload.enableSchemas=true`` so that
    the auto-provisioned SQL analytics endpoint is fully functional.  Fabric
    automatically provisions a paired SQL analytics endpoint alongside every
    Lakehouse — no extra API call is needed to create it.

    The fixture does NOT wait for the SQL endpoint to provision; use
    ``ephemeral_sql_endpoint`` if you need a ready endpoint.
    """
    name = f"pytest_{uuid.uuid4().hex[:8]}_lh"
    body: dict[str, object] = {
        "displayName": name,
        "description": "ephemeral integration-test lakehouse",
        "creationPayload": {"enableSchemas": True},
    }

    # ``lakehouse_id`` is set as soon as we know the created resource's id so
    # that the finally block can clean up on every exit path — including any
    # pytest.skip() that fires after creation but before the yield.
    lakehouse_id: str | None = None
    lakehouse: dict[str, object] = {}

    try:
        resp = await http.request(
            "POST",
            HttpBase.FABRIC,
            f"/workspaces/{workspace_id}/lakehouses",
            json=body,
        )

        location = resp.headers.get("Location")
        if location:
            # 202 Accepted — poll the LRO then fetch the created item
            lro_result = await http.poll_operation(location)
            resource_location = lro_result.get("resourceLocation")
            if isinstance(resource_location, str) and resource_location:
                lakehouse_id = resource_location.rsplit("/", 1)[-1]
            else:
                # resourceLocation may be absent; fall back to GET /result
                # which returns the created resource directly.
                result_resp = await http.request(
                    "GET",
                    HttpBase.FABRIC,
                    f"{location}/result",
                )
                result_body = result_resp.json()
                raw_id = result_body.get("id")
                fallback = result_body.get("resourceLocation", "").rsplit("/", 1)[-1]
                lakehouse_id = raw_id or fallback or None
            if not lakehouse_id:
                pytest.skip(
                    "create lakehouse LRO completed but could not resolve "
                    f"lakehouse id: {lro_result}"
                )
            get_resp = await http.request(
                "GET",
                HttpBase.FABRIC,
                f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}",
            )
            lakehouse = get_resp.json()
        else:
            # 201 Created — body contains the new item directly
            lakehouse = resp.json()
            lakehouse_id = lakehouse.get("id") or None  # type: ignore[assignment]

        yield lakehouse
    finally:
        if lakehouse_id:
            with contextlib.suppress(NotFoundError):
                await http.request(
                    "DELETE",
                    HttpBase.FABRIC,
                    f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}",
                )


@pytest_asyncio.fixture
async def ephemeral_sql_endpoint(
    http: FabricHttpClient,
    workspace_id: UUID,
    ephemeral_lakehouse: dict[str, object],
) -> AsyncIterator[Warehouse]:
    """Derive the SQL Analytics Endpoint from ``ephemeral_lakehouse`` and wait for it to provision.

    Polls ``GET /workspaces/{ws}/lakehouses/{id}`` until
    ``properties.sqlEndpointProperties.provisioningStatus`` reaches ``Success``.
    Skips the test (rather than failing) if provisioning does not complete
    within ``_SQL_ENDPOINT_PROVISION_TIMEOUT_S`` seconds or if it fails.

    Yields a :class:`~fabric_dw.models.Warehouse` instance typed as
    ``WarehouseKind.SQL_ENDPOINT`` with the endpoint ID and connection string
    populated.
    """
    lh_id = ephemeral_lakehouse.get("id")
    if not lh_id:
        pytest.skip("ephemeral_lakehouse fixture returned no id")

    deadline = time.monotonic() + _SQL_ENDPOINT_PROVISION_TIMEOUT_S

    while True:
        if time.monotonic() >= deadline:
            pytest.skip(
                f"SQL analytics endpoint for lakehouse {lh_id} did not provision within "
                f"{_SQL_ENDPOINT_PROVISION_TIMEOUT_S}s — skipping endpoint-specific tests"
            )

        get_resp = await http.request(
            "GET",
            HttpBase.FABRIC,
            f"/workspaces/{workspace_id}/lakehouses/{lh_id}",
        )
        lh_body = get_resp.json()
        props = lh_body.get("properties") or {}
        sql_ep_props = props.get("sqlEndpointProperties") or {}

        status = sql_ep_props.get("provisioningStatus", "")
        if status == "Success":
            ep_id_raw = sql_ep_props.get("id", "")
            ep_conn = sql_ep_props.get("connectionString", "")
            if not ep_id_raw:
                pytest.skip(f"SQL endpoint provisioned but id is missing for lakehouse {lh_id}")
            # Construct a Warehouse-shaped dict so we can use model_validate
            try:
                ep_uuid = UUID(str(ep_id_raw))
            except ValueError:
                pytest.skip(
                    f"SQL endpoint provisioned but id is not a valid UUID "
                    f"for lakehouse {lh_id}: {ep_id_raw!r}"
                )

            # Even after provisioningStatus=Success, the connectionString on the
            # SQL endpoint resource itself can be empty due to eventual consistency.
            # Poll GET /sqlEndpoints/{id} until connection_string is non-empty so
            # that tests fetching the endpoint directly get a populated value.
            from fabric_dw.services.sql_endpoints import (  # noqa: PLC0415
                get_endpoint_connection_string,
            )

            try:
                ep_conn = await get_endpoint_connection_string(
                    http,
                    workspace_id,
                    ep_uuid,
                    poll_interval=_SQL_ENDPOINT_POLL_INTERVAL_S,
                    timeout=max(1.0, deadline - time.monotonic()),
                )
            except Exception as exc:
                pytest.skip(
                    f"SQL endpoint {ep_uuid} connection_string did not populate "
                    f"within timeout: {exc}"
                )

            wh = Warehouse.model_validate(
                {
                    "id": str(ep_uuid),
                    "displayName": lh_body.get("displayName", ""),
                    "workspaceId": str(workspace_id),
                    "kind": WarehouseKind.SQL_ENDPOINT,
                    "connectionString": ep_conn,
                }
            )
            # Even after the Fabric API reports provisioningStatus=Success, the
            # SQL analytics endpoint may not yet accept TDS connections (the DB
            # engine needs an additional warm-up window).  Poll until reachable.
            if ep_conn:
                sql_target = SqlTarget(
                    workspace_id=str(workspace_id),
                    database=wh.name,
                    connection_string=ep_conn,
                )
                await _wait_for_sql_readiness(sql_target)
            yield wh
            return

        if status == "Failed":
            pytest.skip(
                f"SQL analytics endpoint provisioning failed for lakehouse {lh_id} — skipping"
            )

        await asyncio.sleep(_SQL_ENDPOINT_POLL_INTERVAL_S)
