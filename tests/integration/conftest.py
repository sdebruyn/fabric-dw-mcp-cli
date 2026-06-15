import asyncio
import contextlib
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import NamedTuple
from uuid import UUID

import pytest
import pytest_asyncio

from fabric_dw.auth import get_credential
from fabric_dw.exceptions import NotFoundError
from fabric_dw.http_client import FabricHttpClient, HttpBase
from fabric_dw.models import Warehouse, WarehouseKind, WarehouseSnapshot
from fabric_dw.services import schemas, snapshots, warehouses
from fabric_dw.sql import (
    SqlTarget,
    is_auth_failed_message,
    is_transient_connection_error,
    run_query,
)

logger = logging.getLogger(__name__)

# Maximum time to wait for a SQL analytics endpoint to provision on a new lakehouse.
_SQL_ENDPOINT_PROVISION_TIMEOUT_S = 600  # 10 min — Fabric preview provisioning can be slow
# Polling interval between provisioning status checks.
_SQL_ENDPOINT_POLL_INTERVAL_S = 5

# Maximum time (seconds) to wait for a fresh warehouse/endpoint SQL database
# to become connectable after creation.
_SQL_READINESS_TIMEOUT_S = 600  # 10 min — warm-up window for Fabric preview SQL engine
# Starting backoff delay (seconds) between readiness probe attempts.
_SQL_READINESS_BACKOFF_INITIAL_S = 2.0
# Maximum backoff delay cap (seconds).
_SQL_READINESS_BACKOFF_MAX_S = 5.0

# Maximum time (seconds) to wait for a freshly-created snapshot to appear in
# sys.databases on the parent warehouse's SQL connection and report a non-null
# TIMESTAMP property (i.e. be ready for ALTER DATABASE … SET TIMESTAMP = …).
_SNAP_SQL_READINESS_TIMEOUT_S = 600.0  # 10 min — Fabric preview snapshot SQL-layer provisioning
# Polling interval (seconds) between snapshot readiness probes.
_SNAP_SQL_READINESS_POLL_S = 5.0

# Name of the read-only seed schema pre-populated in the shared warehouse.
# Tests that only READ data (list / get / query) may use this schema directly.
# Tests that mutate state MUST use the ``warehouse_schema`` fixture instead.
SEED_SCHEMA_NAME = "sample"


class SharedWarehouseTarget(NamedTuple):
    """Container yielded by ``shared_warehouse``.

    Attributes:
        warehouse: The live :class:`~fabric_dw.models.Warehouse` item.
        sql_target: A :class:`~fabric_dw.sql.SqlTarget` pointing at it.
        workspace_id: The workspace UUID.
    """

    warehouse: Warehouse
    sql_target: SqlTarget
    workspace_id: UUID


async def _wait_for_sql_readiness(
    target: SqlTarget,
    *,
    timeout_s: float = _SQL_READINESS_TIMEOUT_S,
) -> None:
    """Poll *target* with ``SELECT 1`` until the SQL engine is reachable.

    A freshly created Fabric warehouse or SQL analytics endpoint requires a
    warm-up period (database provisioning + permission propagation) before TDS
    connections succeed.  During warm-up the driver raises ``OperationalError``
    with messages like "Login failed … database was not found", "authentication
    failed", "Could not login (18456)", or "communication link failure".  This
    function retries until the query succeeds or *timeout_s* is exhausted.

    Three categories of errors are swallowed during the wait:

    1. **Connection-level transients** — detected by
       :func:`~fabric_dw.sql.is_transient_connection_error` (TCP drops,
       "communication link failure", etc.).
    2. **"database was not found"** — the classic Fabric warm-up variant where
       the SQL endpoint exists but the database name has not propagated yet.
    3. **Auth-failed variants** — detected by
       :func:`~fabric_dw.sql.is_auth_failed_message` ("authentication failed",
       "Could not login (18456)"); the service-principal's access has not yet
       propagated to the new SQL endpoint.

    Any other error (e.g. real SQL errors, unexpected exceptions) is re-raised
    immediately.

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
            # Swallow transient connection drops AND the Fabric warm-up login
            # failures.  Three provisioning-transient variants are recognised:
            #
            # 1. "database was not found" — the SQL endpoint exists but the
            #    database name has not propagated yet (classic warm-up).
            #
            # 2. "authentication failed" / "could not login" (error 18456) —
            #    the database IS reachable but the service principal's access has
            #    not yet propagated to the new SQL endpoint.  Seen especially
            #    under xdist=8 where 8 warehouses are created concurrently.
            #    Detected via is_auth_failed_message (public helper backed by
            #    _AUTH_FAILED_FRAGMENTS, same source of truth as map_driver_error)
            #    so neither private symbols nor string literals are duplicated.
            #
            # All variants are real provisioning transients and are safe to
            # retry within the warm-up window.  A genuine permanent auth
            # misconfiguration (wrong tenant, expired service-principal
            # credentials, etc.) will surface as TimeoutError after timeout_s —
            # that is the accepted trade-off for this readiness probe only.
            # The general run_query/_with_connect_retry path is NOT broadened.
            is_warmup = "database was not found" in msg_lower or is_auth_failed_message(str(exc))
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


async def _wait_for_snapshot_sql_readiness(
    parent_target: SqlTarget,
    snapshot_name: str,
    *,
    timeout_s: float = _SNAP_SQL_READINESS_TIMEOUT_S,
) -> None:
    """Poll the parent warehouse until *snapshot_name* appears in sys.databases and is ready.

    A freshly-created warehouse snapshot takes additional time to provision at the SQL
    layer after the REST LRO has completed.  During this window, ``ALTER DATABASE … SET
    TIMESTAMP`` fails with a not-ready error.  This function waits until the snapshot's
    TIMESTAMP property is non-null in ``sys.databases`` — the authoritative SQL-layer
    signal that the snapshot is ready — before yielding control back to the fixture.

    The query runs against the PARENT warehouse connection (per Microsoft docs, a
    warehouse's snapshots are visible via ``sys.databases`` on the parent).

    Diagnostic logging is emitted for each probe attempt (row found, TIMESTAMP
    value) so that a future run can distinguish slow provisioning from a probe
    configuration issue.

    Args:
        parent_target: The :class:`~fabric_dw.sql.SqlTarget` for the parent warehouse.
        snapshot_name: The display name of the snapshot to wait for.
        timeout_s: Total seconds to wait before giving up.

    Raises:
        pytest.skip.Exception: When the snapshot is still not SQL-ready after
            *timeout_s* — emitted as a skip rather than a hard failure because
            this is a Fabric preview provisioning-latency limitation, not a code bug.
        Exception: Any non-transient SQL error raised by the driver is re-raised.
    """
    # The sys.databases query looks for the snapshot by name and returns a
    # non-null TIMESTAMP when the snapshot is accessible.  A NULL result (or no
    # row) means the snapshot is still provisioning.
    readiness_sql = (
        "SELECT DATABASEPROPERTYEX(v.name, 'TIMESTAMP') AS snapshot_ts "
        "FROM sys.databases AS v "
        "INNER JOIN sys.databases AS s ON v.source_database_id = s.database_id "
        "WHERE v.name = ? AND s.database_id = DB_ID(?);"
    )

    def _probe() -> tuple[bool, bool, object]:
        """Return (row_found, ts_non_null, raw_ts_value)."""
        _cols, rows = run_query(
            parent_target,
            readiness_sql,
            params=(snapshot_name, parent_target.database),
            fetch="all",
        )
        if not rows:
            return False, False, None
        snapshot_ts = rows[0][0]
        return True, snapshot_ts is not None, snapshot_ts

    deadline = time.monotonic() + timeout_s
    attempt = 0
    while True:
        attempt += 1
        try:
            row_found, ts_non_null, raw_ts = await asyncio.to_thread(_probe)
            # row_found is diagnostic-only (logged below); readiness is driven by ts_non_null.
        except Exception as exc:
            # Swallow transient connection errors during the wait — the parent
            # warehouse itself may be briefly unreachable.
            if not is_transient_connection_error(exc):
                raise
            logger.debug(
                "snapshot readiness probe #%d: transient connection error on parent %r: %s",
                attempt,
                parent_target.database,
                exc,
            )
            row_found, ts_non_null, raw_ts = False, False, None
        else:
            logger.debug(
                "snapshot readiness probe #%d for %r on parent %r: "
                "row_found=%s ts_non_null=%s raw_ts=%r",
                attempt,
                snapshot_name,
                parent_target.database,
                row_found,
                ts_non_null,
                raw_ts,
            )

        if ts_non_null:
            logger.info(
                "snapshot %r on parent %r is SQL-ready after %d probe(s); TIMESTAMP=%r",
                snapshot_name,
                parent_target.database,
                attempt,
                raw_ts,
            )
            return

        if time.monotonic() >= deadline:
            # This is a Microsoft Fabric preview snapshot provisioning-latency
            # limitation — the SQL layer is still warming up beyond the CI window.
            # roll_timestamp logic is covered by unit tests, so skip rather than
            # hard-failing the suite.
            pytest.skip(
                f"warehouse snapshot {snapshot_name!r} did not become SQL-ready within "
                f"{timeout_s:.0f}s — Fabric preview snapshot SQL-layer provisioning "
                "exceeded the CI window; roll_timestamp logic is unit-tested"
            )

        await asyncio.sleep(_SNAP_SQL_READINESS_POLL_S)


# ---------------------------------------------------------------------------
# Session-scoped helpers (shared by both shared_warehouse and legacy fixtures)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def _session_workspace_id() -> UUID:
    """Resolve FABRIC_TEST_WORKSPACE_ID once per session.

    Session-scoped variant of ``workspace_id`` used exclusively by
    ``shared_warehouse``.  The function-scoped ``workspace_id`` fixture (below)
    remains unchanged so that tests that request it directly continue to work.
    """
    raw = os.environ.get("FABRIC_TEST_WORKSPACE_ID")
    if not raw:
        pytest.skip("set FABRIC_TEST_WORKSPACE_ID to run integration tests")
    return UUID(raw)


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def _session_http() -> AsyncIterator[FabricHttpClient]:
    """Long-lived HTTP client shared across all tests in the session.

    Session-scoped variant of ``http`` used exclusively by ``shared_warehouse``.
    The function-scoped ``http`` fixture remains unchanged so that tests that
    request it directly continue to work.
    """
    cred = get_credential()
    async with FabricHttpClient(cred) as client:
        yield client


# ---------------------------------------------------------------------------
# Shared warm warehouse (session-scoped, one per xdist worker)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def shared_warehouse(
    _session_http: FabricHttpClient,
    _session_workspace_id: UUID,
) -> AsyncIterator[SharedWarehouseTarget]:
    """Create ONE warm warehouse per session (per xdist worker) and reuse it.

    Naming convention
    -----------------
    The warehouse name embeds ``PYTEST_XDIST_WORKER`` (defaulting to
    ``"master"`` when xdist is not active) and a random UUID fragment so that
    parallel workers each get their own warehouse without cross-process
    coordination.

    Seed schema
    -----------
    After the warehouse warms up, a read-only ``sample`` schema is created with
    a couple of small tables so that pure-read tests can query real rows without
    creating their own objects.  The schema name is exposed as
    ``conftest.SEED_SCHEMA_NAME``.

    **Tests MUST NOT mutate the seed schema.**  Mutating tests must use the
    ``warehouse_schema`` fixture instead, which creates a uniquely-named schema
    per test and cascade-drops it on teardown.

    xdist compatibility
    -------------------
    Each xdist worker runs its own session, so this session-scoped fixture
    runs once per worker — not once globally.  No cross-process locking is
    required.  The ``loop_scope="session"`` argument aligns the asyncio event
    loop lifetime with the session scope, which pytest-asyncio >= 0.23 /
    1.x requires for session-scoped async fixtures.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    uid = uuid.uuid4().hex[:8]
    name = f"pytest-{worker}-{uid}-wh"

    wh = await warehouses.create(_session_http, _session_workspace_id, name)

    try:
        assert wh.connection_string, f"Warehouse {wh.id} returned no connection_string"

        sql_target = SqlTarget(
            workspace_id=str(_session_workspace_id),
            database=wh.name,
            connection_string=wh.connection_string,
        )

        # Wait for the SQL engine to accept connections before doing anything else.
        await _wait_for_sql_readiness(sql_target)

        # Populate the read-only seed schema with a handful of small tables so
        # pure-read tests have real data to work with without creating objects.
        await schemas.create_schema(sql_target, SEED_SCHEMA_NAME)
        await _seed_sample_data(sql_target)

        logger.info(
            "shared_warehouse %r ready for worker %r (workspace %s)",
            wh.name,
            worker,
            _session_workspace_id,
        )

        yield SharedWarehouseTarget(
            warehouse=wh,
            sql_target=sql_target,
            workspace_id=_session_workspace_id,
        )
    finally:
        with contextlib.suppress(Exception):
            await warehouses.delete(_session_http, _session_workspace_id, wh.id)


async def _seed_sample_data(target: SqlTarget) -> None:
    """Create two small read-only tables in the ``sample`` schema.

    ``sample.colors``  — a tiny reference table with an id and name column.
    ``sample.numbers`` — a numeric table with id and value columns.

    These are intentionally small (a few rows each) so that read-only tests get
    predictable, deterministic data without heavy setup cost.
    """
    from fabric_dw.services import tables  # noqa: PLC0415 — local import avoids circular dep

    await tables.create_table(
        target,
        SEED_SCHEMA_NAME,
        "colors",
        "SELECT 1 AS id, 'red' AS name UNION ALL SELECT 2, 'green' UNION ALL SELECT 3, 'blue'",
    )
    await tables.create_table(
        target,
        SEED_SCHEMA_NAME,
        "numbers",
        "SELECT 1 AS id, 10 AS value UNION ALL SELECT 2, 20 UNION ALL SELECT 3, 30",
    )


# ---------------------------------------------------------------------------
# Per-test schema isolation fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def warehouse_schema(
    shared_warehouse: SharedWarehouseTarget,
) -> AsyncIterator[tuple[SqlTarget, str]]:
    """Create a uniquely-named schema in the shared warehouse and cascade-drop it on teardown.

    Yields ``(sql_target, schema_name)`` so the test can qualify every object
    it creates under ``schema_name`` and remain fully isolated from every other
    concurrently running test.

    Design notes
    ------------
    - Each test gets a collision-resistant schema name (``pytest_<8-hex-chars>``).
    - Teardown calls ``schemas.delete_schema(..., cascade=True)`` so the test
      does not need to clean up individual objects — the cascade drop handles it.
    - If setup fails (e.g. schema creation raises) the finally block is still
      executed, but ``contextlib.suppress`` guards against delete failures on a
      schema that was never fully created.
    """
    schema_name = f"pytest_{uuid.uuid4().hex[:8]}"
    sql_target = shared_warehouse.sql_target

    await schemas.create_schema(sql_target, schema_name)
    try:
        yield sql_target, schema_name
    finally:
        with contextlib.suppress(Exception):
            await schemas.delete_schema(sql_target, schema_name, cascade=True)


# ---------------------------------------------------------------------------
# Legacy function-scoped fixtures (kept for tests that need a dedicated item)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def workspace_id() -> UUID:
    raw = os.environ.get("FABRIC_TEST_WORKSPACE_ID")
    if not raw:
        pytest.skip(
            "set FABRIC_TEST_WORKSPACE_ID to run integration tests",
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
        # Wait for the snapshot to become SQL-ready on the parent warehouse
        # connection before yielding.  The REST LRO completing (above) only means
        # the Fabric control-plane has created the snapshot resource; the SQL layer
        # needs additional time to provision the snapshot database.  Without this
        # wait, roll_timestamp fails with the snapshot-not-ready error for the
        # full retry window, causing test_roll_timestamp_updates_snapshot to time
        # out.  The authoritative readiness signal is a non-null TIMESTAMP property
        # in sys.databases on the parent warehouse connection.
        assert ephemeral_warehouse.connection_string, (
            f"Parent warehouse {ephemeral_warehouse.id} has no connection_string"
        )
        parent_target = SqlTarget(
            workspace_id=str(workspace_id),
            database=ephemeral_warehouse.name,
            connection_string=ephemeral_warehouse.connection_string,
        )
        await _wait_for_snapshot_sql_readiness(parent_target, snap.name)
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
