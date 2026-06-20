import asyncio
import contextlib
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID

if TYPE_CHECKING:
    import httpx as _httpx

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

# Maximum time (seconds) to wait for seeded lakehouse tables to become queryable
# via TDS on the SQL analytics endpoint after refresh_metadata completes.
# Metadata propagation typically takes a few seconds; 120s is a generous margin.
_SQL_ENDPOINT_SEED_VISIBLE_TIMEOUT_S = 120  # 2 min — seed-tables TDS visibility lag

# Polling interval for seed-table visibility checks.
_SQL_ENDPOINT_SEED_POLL_INTERVAL_S = 5

# OneLake DFS endpoint for file uploads.
_ONELAKE_DFS_BASE = "https://onelake.dfs.fabric.microsoft.com"
# ADLS Gen2 DFS API version used for OneLake file uploads.
_DFS_API_VERSION = "2021-06-08"

# Maximum time to wait for a SQL analytics endpoint to provision on a new lakehouse.
# Provisioning is variable (observed as low as ~18s but can run longer); 240s gives
# a generous margin while staying well under the old 600s ceiling.
_SQL_ENDPOINT_PROVISION_TIMEOUT_S = 240  # 4 min — provisioning observed at ~18s, variable
# Polling interval between provisioning status checks.
_SQL_ENDPOINT_POLL_INTERVAL_S = 5
# Maximum time (seconds) to wait for a freshly-provisioned SQL analytics endpoint
# item to become visible via GET /workspaces/{ws}/sqlEndpoints/{id}.  Even after
# provisioningStatus=Success the item API can return 404 EntityNotFound for a short
# eventual-consistency window; this poll absorbs that window before yielding.
_SQL_ENDPOINT_ITEM_VISIBLE_TIMEOUT_S = 120  # 2 min — item-API visibility lag

# Maximum time (seconds) to wait for a fresh warehouse/endpoint SQL database
# to become connectable after creation.
# 10 min — warm-up window for Fabric preview SQL engine
# (DB provisioning + SP permission propagation)
_SQL_READINESS_TIMEOUT_S = 600
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
# Dual-target mutating tests MUST use the ``mutable_schema_target`` fixture;
# ``warehouse_schema`` is for DWH-only DDL (e.g. tables) only.
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


class SharedSqlEndpointTarget(NamedTuple):
    """Container yielded by ``shared_sql_endpoint``.

    Attributes:
        endpoint: The live :class:`~fabric_dw.models.Warehouse` item (kind=SQL_ENDPOINT).
        sql_target: A :class:`~fabric_dw.sql.SqlTarget` pointing at the endpoint.
        workspace_id: The workspace UUID.
        lakehouse_id: The UUID string of the parent Lakehouse.
    """

    endpoint: Warehouse
    sql_target: SqlTarget
    workspace_id: UUID
    lakehouse_id: str


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


def _build_delta_log_entry(table_name: str, parquet_size: int, row_count: int) -> str:
    """Return the content of the initial Delta log commit file (JSON lines).

    A minimal Delta Lake log commit contains four action entries in a single
    ``00000000000000000000.json`` file (one JSON object per line):

    - ``protocol``  — minimum reader/writer version requirements.
    - ``metaData`` — table schema (Arrow schema serialised as Delta string) and format.
    - ``commitInfo`` — operation provenance (not required by readers but conventional).
    - ``add``       — one entry per Parquet data file in this commit.

    OneLake / Fabric honours this layout and projects the table into the SQL
    analytics endpoint's metadata catalog after a ``refresh_metadata`` call.

    Args:
        table_name: Name of the table (used only in schema string for reference).
        parquet_size: Byte size of the single Parquet data file.
        row_count: Number of rows in the data file (encoded in ``stats``).

    Returns:
        A newline-separated string of JSON objects forming the commit file.
    """
    import json  # noqa: PLC0415

    # Schema strings vary per table; both tables use INT32 + STRING/INT32.
    _schema_strings: dict[str, str] = {
        "colors": (
            '{"type":"struct","fields":['
            '{"name":"id","type":"integer","nullable":true,"metadata":{}},'
            '{"name":"name","type":"string","nullable":true,"metadata":{}}'
            "]}"
        ),
        "numbers": (
            '{"type":"struct","fields":['
            '{"name":"id","type":"integer","nullable":true,"metadata":{}},'
            '{"name":"value","type":"integer","nullable":true,"metadata":{}}'
            "]}"
        ),
    }
    if table_name not in _schema_strings:
        msg = f"unknown seed table: {table_name!r} — add a schema string entry to _schema_strings"
        raise ValueError(msg)
    schema_string = _schema_strings[table_name]

    # Fabric validates that metaData.id is a canonical UUID (Guid) string.
    # Use uuid5 seeded from the table name so the value is deterministic and
    # stable across fixture re-runs while still satisfying the GUID constraint.
    import uuid as _uuid  # noqa: PLC0415

    table_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"pytest-seed-{table_name}"))

    protocol = {"protocol": {"minReaderVersion": 1, "minWriterVersion": 2}}
    metadata = {
        "metaData": {
            "id": table_uuid,
            "format": {"provider": "parquet", "options": {}},
            "schemaString": schema_string,
            "partitionColumns": [],
            "configuration": {},
            "createdTime": 1700000000000,
        }
    }
    commit_info = {
        "commitInfo": {
            "timestamp": 1700000000000,
            "operation": "CREATE TABLE",
            "operationParameters": {},
        }
    }
    add = {
        "add": {
            "path": "part-00000.parquet",
            "partitionValues": {},
            "size": parquet_size,
            "dataChange": True,
            "stats": json.dumps({"numRecords": row_count}),
            "modificationTime": 1700000000000,
        }
    }
    return "\n".join(json.dumps(entry) for entry in [protocol, metadata, commit_info, add])


async def _dfs_upload_bytes(
    client: "_httpx.AsyncClient",
    dfs_url: str,
    data: bytes,
    headers: dict[str, str],
) -> None:
    """Upload *data* to *dfs_url* via ADLS Gen2 DFS create/append/flush.

    Creates the file resource at 0 bytes, appends the full content in a single
    patch, then flushes.  Designed for small payloads (a few KiB) where a
    single append chunk is sufficient.

    Args:
        client: An open :class:`httpx.AsyncClient`.
        dfs_url: Full DFS endpoint URL for the file.
        data: Raw bytes to write.
        headers: Base DFS headers (Authorization + x-ms-version).
    """
    size = len(data)

    create_resp = await client.put(
        dfs_url,
        params={"resource": "file"},
        headers={**headers, "Content-Length": "0"},
        content=b"",
    )
    create_resp.raise_for_status()

    append_resp = await client.patch(
        dfs_url,
        params={"action": "append", "position": 0},
        content=data,
        headers={
            **headers,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size),
        },
    )
    append_resp.raise_for_status()

    flush_resp = await client.patch(
        dfs_url,
        params={"action": "flush", "position": size},
        headers={**headers, "Content-Length": "0"},
        content=b"",
    )
    flush_resp.raise_for_status()


async def _seed_lakehouse_sample_data(
    http: FabricHttpClient,
    workspace_id: UUID,
    lakehouse_id: str,
    endpoint_id: UUID,
) -> None:
    """Write ``sample.colors`` and ``sample.numbers`` into a schema-enabled Lakehouse.

    Strategy
    --------
    For a schema-enabled Lakehouse the Fabric Tables Load API rejects Parquet
    ingestion (``UnsupportedOperationForSchemasEnabledLakehouse``).  Instead,
    we write a minimal Delta Lake layout directly to the ``Tables/`` area of the
    Lakehouse via the OneLake ADLS Gen2 DFS API:

    1. Build small :class:`pyarrow.Table` objects in memory.
    2. Write each table as a single Parquet data file locally.
    3. Build a minimal Delta log commit file (protocol + metadata + add action).
    4. Upload both the Parquet file and the ``_delta_log/`` commit to the path
       ``Tables/<schema>/<table>/`` using the DFS create/append/flush flow.
    5. Call :func:`~fabric_dw.services.sql_endpoints.refresh_metadata` on the
       paired SQL analytics endpoint so the Delta tables are projected onto the
       endpoint's TDS surface.

    No third-party Delta library (``deltalake``, ``adlfs``, etc.) is required;
    only ``pyarrow`` (already a runtime dependency) and ``httpx`` (already in the
    virtual environment) are used.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: Workspace UUID.
        lakehouse_id: UUID string of the parent Lakehouse.
        endpoint_id: UUID of the SQL analytics endpoint (for refresh_metadata).
    """
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    import httpx  # noqa: PLC0415
    import pyarrow as pa  # noqa: PLC0415 — runtime dep, always available
    import pyarrow.parquet as pq  # noqa: PLC0415

    from fabric_dw.auth import STORAGE_SCOPE  # noqa: PLC0415
    from fabric_dw.services.sql_endpoints import refresh_metadata  # noqa: PLC0415

    # Acquire a storage-scoped token once for all DFS uploads.
    cred = get_credential()
    token_obj = await cred.get_token(STORAGE_SCOPE)
    token = token_obj.token
    dfs_base_headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "x-ms-version": _DFS_API_VERSION,
    }

    # Build the seed tables — same shape as the warehouse seed.
    seed_tables: dict[str, pa.Table] = {
        "colors": pa.table(
            {
                "id": pa.array([1, 2, 3], type=pa.int32()),
                "name": pa.array(["red", "green", "blue"], type=pa.string()),
            }
        ),
        "numbers": pa.table(
            {
                "id": pa.array([1, 2, 3], type=pa.int32()),
                "value": pa.array([10, 20, 30], type=pa.int32()),
            }
        ),
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        async with httpx.AsyncClient(timeout=300.0) as client:
            for table_name, pa_table in seed_tables.items():
                # Write Parquet locally.
                parquet_file = tmp_path / f"{table_name}.parquet"
                pq.write_table(pa_table, parquet_file)
                parquet_bytes = parquet_file.read_bytes()
                parquet_size = len(parquet_bytes)
                row_count = pa_table.num_rows

                # Delta table path inside the schema-enabled Lakehouse:
                # Tables/<schema>/<table>/
                table_prefix = (
                    f"{_ONELAKE_DFS_BASE}/{workspace_id}/{lakehouse_id}"
                    f"/Tables/{SEED_SCHEMA_NAME}/{table_name}"
                )

                # Upload the Parquet data file.
                parquet_dfs_url = f"{table_prefix}/part-00000.parquet"
                await _dfs_upload_bytes(client, parquet_dfs_url, parquet_bytes, dfs_base_headers)
                logger.debug(
                    "_seed_lakehouse_sample_data: uploaded %s data file (%d bytes)",
                    table_name,
                    parquet_size,
                )

                # Ensure the _delta_log/ directory exists before writing the
                # commit file.  OneLake's auto-create of parent directories is
                # undocumented; an explicit PUT ?resource=directory guarantees it.
                delta_log_dir_url = f"{table_prefix}/_delta_log"
                dir_resp = await client.put(
                    delta_log_dir_url,
                    params={"resource": "directory"},
                    headers={**dfs_base_headers, "Content-Length": "0"},
                    content=b"",
                )
                dir_resp.raise_for_status()

                # Upload the Delta log commit file.
                delta_log_content = _build_delta_log_entry(table_name, parquet_size, row_count)
                delta_log_bytes = delta_log_content.encode()
                delta_log_dfs_url = f"{table_prefix}/_delta_log/00000000000000000000.json"
                await _dfs_upload_bytes(
                    client, delta_log_dfs_url, delta_log_bytes, dfs_base_headers
                )
                logger.debug(
                    "_seed_lakehouse_sample_data: uploaded %s delta log (%d bytes)",
                    table_name,
                    len(delta_log_bytes),
                )

    # Refresh SQL endpoint metadata so the Delta tables are projected onto TDS.
    await refresh_metadata(http, workspace_id, endpoint_id, recreate_tables=False)
    logger.info(
        "_seed_lakehouse_sample_data: metadata refreshed for endpoint %s",
        endpoint_id,
    )


async def _create_schema_enabled_lakehouse(
    http: FabricHttpClient,
    workspace_id: UUID,
    name: str,
) -> str:
    """Create a schema-enabled Lakehouse and return its ID string.

    Handles both the synchronous 201 path and the asynchronous 202 LRO path.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: Target workspace UUID.
        name: Display name for the new Lakehouse.

    Returns:
        The Lakehouse item ID as a string.

    Raises:
        pytest.skip.Exception: If the LRO completes without a usable item ID.
    """
    body: dict[str, object] = {
        "displayName": name,
        "description": "shared integration-test lakehouse (sql_endpoint fixture)",
        "creationPayload": {"enableSchemas": True},
    }
    resp = await http.request(
        "POST",
        HttpBase.FABRIC,
        f"/workspaces/{workspace_id}/lakehouses",
        json=body,
    )

    location = resp.headers.get("Location")
    if location:
        lro_result = await http.poll_operation(location)
        resource_location = lro_result.get("resourceLocation")
        if isinstance(resource_location, str) and resource_location:
            return resource_location.rsplit("/", 1)[-1]
        # Fall back: GET /operations/{id}/result
        op_id = location.rsplit("/", 1)[-1]
        result_resp = await http.request("GET", HttpBase.FABRIC, f"/operations/{op_id}/result")
        result_body = result_resp.json()
        raw_id = result_body.get("id")
        fallback = result_body.get("resourceLocation", "").rsplit("/", 1)[-1]
        lakehouse_id: str | None = raw_id or fallback or None
        if not lakehouse_id:
            pytest.skip(f"create lakehouse LRO completed but could not resolve id: {lro_result}")
        return lakehouse_id  # type: ignore[return-value]  # pytest.skip() above is an exit

    lh_dict = resp.json()
    item_id: str | None = lh_dict.get("id") or None
    if not item_id:
        pytest.skip(f"create lakehouse returned 201 but no id in body: {lh_dict}")
    return item_id  # type: ignore[return-value]


async def _poll_endpoint_until_provisioned(
    http: FabricHttpClient,
    workspace_id: UUID,
    lakehouse_id: str,
    *,
    deadline: float,
) -> tuple[str, str, str]:
    """Poll the Lakehouse until its SQL endpoint reaches ``provisioningStatus=Success``.

    Returns ``(ep_id_raw, ep_conn, ep_name)`` once successful.  Calls
    ``pytest.skip`` if the deadline is reached or provisioning fails.

    Args:
        http: Authenticated Fabric HTTP client.
        workspace_id: Workspace UUID.
        lakehouse_id: Lakehouse item ID string to poll.
        deadline: Monotonic time deadline (``time.monotonic()`` value).

    Returns:
        Tuple of (endpoint_id_str, connection_string, display_name).
    """
    while True:
        if time.monotonic() >= deadline:
            pytest.skip(
                f"SQL analytics endpoint for lakehouse {lakehouse_id} did not provision "
                f"within {_SQL_ENDPOINT_PROVISION_TIMEOUT_S}s — skipping sql_endpoint tests"
            )

        get_resp = await http.request(
            "GET",
            HttpBase.FABRIC,
            f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}",
        )
        lh_body = get_resp.json()
        props = lh_body.get("properties") or {}
        sql_ep_props = props.get("sqlEndpointProperties") or {}
        status = sql_ep_props.get("provisioningStatus", "")

        if status == "Success":
            return (
                str(sql_ep_props.get("id", "")),
                str(sql_ep_props.get("connectionString", "")),
                str(lh_body.get("displayName", "")),
            )

        if status == "Failed":
            pytest.skip(f"SQL analytics endpoint provisioning failed for lakehouse {lakehouse_id}")

        await asyncio.sleep(_SQL_ENDPOINT_POLL_INTERVAL_S)


async def _wait_for_seeded_tables_visible(
    sql_target: SqlTarget,
    *,
    timeout_s: float = _SQL_ENDPOINT_SEED_VISIBLE_TIMEOUT_S,
) -> None:
    """Poll the SQL endpoint via TDS until ``sample.colors`` and ``sample.numbers`` are visible.

    After :func:`_seed_lakehouse_sample_data` uploads the Parquet files and
    calls ``refresh_metadata``, the tables may not be immediately queryable via
    TDS.  This function polls until a ``SELECT TOP 1 1 FROM [sample].[colors]``
    succeeds, or skips the session if the window is exceeded.

    Args:
        sql_target: The SQL analytics endpoint target to probe.
        timeout_s: Maximum seconds to wait before skipping.

    Raises:
        pytest.skip.Exception: If the seed tables do not become visible within *timeout_s*.
    """

    def _probe() -> None:
        # A successful SELECT proves the table is visible via TDS.
        run_query(sql_target, "SELECT TOP 1 1 FROM [sample].[colors]", fetch="none")

    deadline = time.monotonic() + timeout_s
    delay = _SQL_READINESS_BACKOFF_INITIAL_S
    while True:
        try:
            await asyncio.to_thread(_probe)
        except Exception as exc:
            msg_lower = str(exc).lower()
            logger.debug(
                "_wait_for_seeded_tables_visible: probe failed on %r: %s",
                sql_target.database,
                exc,
            )
            # Retry on transient connection errors and the specific "object not
            # found" messages that appear while Delta metadata is still
            # propagating to the SQL analytics endpoint after refresh_metadata.
            # Keep the match narrow: broad strings like "schema" would also
            # swallow genuine permission/config errors and retry them for 120s
            # before a misleading skip.
            is_transient = is_transient_connection_error(exc)
            is_not_found = any(
                kw in msg_lower
                for kw in (
                    # TDS "Invalid object name 'sample.colors'" — table not yet visible.
                    "invalid object name 'sample.",
                    # Fabric DB warm-up: database itself not yet reachable.
                    "database was not found",
                )
            )
            is_warmup = is_auth_failed_message(str(exc))
            if not (is_transient or is_not_found or is_warmup):
                raise
        else:
            logger.info(
                "_wait_for_seeded_tables_visible: sample.colors visible on %s",
                sql_target.database,
            )
            return

        if time.monotonic() >= deadline:
            pytest.skip(
                f"seeded tables (sample.colors / sample.numbers) did not become visible "
                f"via TDS on {sql_target.database!r} within {timeout_s:.0f}s after "
                "refresh_metadata — Fabric metadata propagation exceeded the CI window"
            )

        await asyncio.sleep(delay)
        delay = min(delay * 1.5, _SQL_READINESS_BACKOFF_MAX_S)


# ---------------------------------------------------------------------------
# Shared warm SQL analytics endpoint (session-scoped, one per xdist worker)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def shared_sql_endpoint(
    _session_http: FabricHttpClient,
    _session_workspace_id: UUID,
) -> AsyncIterator[SharedSqlEndpointTarget]:
    """Create ONE warm, seeded SQL analytics endpoint per session (per xdist worker).

    Mirrors ``shared_warehouse`` but provisions a schema-enabled Lakehouse,
    seeds ``sample.colors`` and ``sample.numbers`` through the parent Lakehouse
    via a direct Delta Lake layout upload, and derives the paired SQL analytics
    endpoint.  The endpoint is reused for all ``sql_endpoint``-marked tests in
    the session.

    Seed schema
    -----------
    The Lakehouse has ``enableSchemas=true``.  Seed data is written directly to
    ``Tables/sample/<table>/`` as a minimal Delta Lake layout (Parquet data file
    + ``_delta_log/00000000000000000000.json``) via the OneLake ADLS Gen2 DFS
    API.  The Fabric Tables Load API is intentionally not used here — it rejects
    Parquet ingestion for schema-enabled lakehouses with
    ``UnsupportedOperationForSchemasEnabledLakehouse``.  After the DFS uploads,
    ``refresh_metadata`` is called on the SQL endpoint, and this fixture polls
    until both tables are visible via TDS before yielding.

    **Tests MUST NOT mutate the seed schema.**  Mutating tests must use the
    ``mutable_schema_target`` fixture (added in PR 3) which creates a uniquely-named
    schema per test on the endpoint and cascade-drops it on teardown.

    xdist compatibility
    -------------------
    Session-scoped, one per xdist worker — exactly like ``shared_warehouse``.
    The ``loop_scope="session"`` argument aligns the asyncio event loop lifetime
    with the session scope.

    Cost
    ----
    Endpoint provisioning is slow and 429-sensitive.  By session-scoping it here,
    provisioning cost is O(workers), independent of test count.  The ``sql_endpoint``
    marker gates this fixture on CI only (local runs deselect it by default).
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    uid = uuid.uuid4().hex[:8]
    lh_name = f"pytest_{worker}_{uid}_lh"

    # ``lakehouse_id`` is set as soon as we know the id so teardown can always
    # clean up, even if a pytest.skip() fires before the yield.
    lakehouse_id: str | None = None

    try:
        # 1. Create a schema-enabled Lakehouse.
        lakehouse_id = await _create_schema_enabled_lakehouse(
            _session_http, _session_workspace_id, lh_name
        )

        # 2. Poll until the SQL analytics endpoint provisions.
        deadline = time.monotonic() + _SQL_ENDPOINT_PROVISION_TIMEOUT_S
        ep_id_raw, ep_conn, ep_name = await _poll_endpoint_until_provisioned(
            _session_http, _session_workspace_id, lakehouse_id, deadline=deadline
        )

        if not ep_id_raw:
            pytest.skip(f"SQL endpoint provisioned but id is missing for lakehouse {lakehouse_id}")

        try:
            ep_uuid = UUID(ep_id_raw)
        except ValueError:
            pytest.skip(
                f"SQL endpoint id is not a valid UUID for lakehouse {lakehouse_id}: {ep_id_raw!r}"
            )

        # Resolve connection string via the endpoint API if not yet populated.
        if not ep_conn:
            from fabric_dw.services.sql_endpoints import (  # noqa: PLC0415
                get_endpoint_connection_string,
            )

            try:
                ep_conn = await get_endpoint_connection_string(
                    _session_http,
                    _session_workspace_id,
                    ep_uuid,
                    poll_interval=_SQL_ENDPOINT_POLL_INTERVAL_S,
                    timeout=max(1.0, deadline - time.monotonic()),
                )
            except Exception as exc:
                pytest.skip(f"SQL endpoint {ep_uuid} connection_string did not populate: {exc}")

        wh = Warehouse.model_validate(
            {
                "id": ep_id_raw,
                "displayName": ep_name,
                "workspaceId": str(_session_workspace_id),
                "kind": WarehouseKind.SQL_ENDPOINT,
                "connectionString": ep_conn,
            }
        )

        sql_target = SqlTarget(
            workspace_id=str(_session_workspace_id),
            database=ep_name,
            connection_string=ep_conn,
        )

        # 3. Wait for TDS connectivity before seeding.
        await _wait_for_sql_readiness(sql_target)

        # 4. Wait for the endpoint item to be visible via the product API.
        from fabric_dw.services.sql_endpoints import get_endpoint  # noqa: PLC0415

        item_visible_deadline = time.monotonic() + _SQL_ENDPOINT_ITEM_VISIBLE_TIMEOUT_S
        while True:
            try:
                await get_endpoint(_session_http, _session_workspace_id, ep_uuid)
                break
            except NotFoundError:
                if time.monotonic() >= item_visible_deadline:
                    pytest.skip(
                        f"SQL endpoint item {ep_uuid} was not visible via the product API "
                        f"within {_SQL_ENDPOINT_ITEM_VISIBLE_TIMEOUT_S}s after provisioning"
                    )
                await asyncio.sleep(_SQL_ENDPOINT_POLL_INTERVAL_S)

        # 5. Seed sample data into the Lakehouse (and refresh endpoint metadata).
        await _seed_lakehouse_sample_data(
            _session_http,
            _session_workspace_id,
            lakehouse_id,
            ep_uuid,
        )

        # 6. Poll until seed tables are visible via TDS on the endpoint.
        await _wait_for_seeded_tables_visible(sql_target)

        logger.info(
            "shared_sql_endpoint %r ready for worker %r (workspace %s, lakehouse %s)",
            ep_name,
            worker,
            _session_workspace_id,
            lakehouse_id,
        )

        yield SharedSqlEndpointTarget(
            endpoint=wh,
            sql_target=sql_target,
            workspace_id=_session_workspace_id,
            lakehouse_id=lakehouse_id,
        )

    finally:
        if lakehouse_id:
            with contextlib.suppress(Exception):
                await _session_http.request(
                    "DELETE",
                    HttpBase.FABRIC,
                    f"/workspaces/{_session_workspace_id}/lakehouses/{lakehouse_id}",
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
# Dual-target read fixture (warehouse + SQL analytics endpoint)
# ---------------------------------------------------------------------------

# Symbolic constants for the two read_target param values.  Reused in both
# pytest.param() calls and the if-branch so a typo is caught at definition
# time rather than silently routing tests to the wrong target.
_PARAM_WAREHOUSE = "warehouse"
_PARAM_SQL_ENDPOINT = "sql_endpoint"


@pytest.fixture(
    params=[
        pytest.param(_PARAM_WAREHOUSE),
        pytest.param(_PARAM_SQL_ENDPOINT, marks=pytest.mark.sql_endpoint),
    ]
)
def read_target(
    request: pytest.FixtureRequest,
    shared_warehouse: SharedWarehouseTarget,
) -> SqlTarget:
    """Parametrized fixture that returns the read :class:`~fabric_dw.sql.SqlTarget`.

    Runs each requesting test TWICE: once against the shared warm warehouse and
    once against the shared SQL analytics endpoint.  The endpoint leg carries
    ``pytest.mark.sql_endpoint`` so it is excluded from local runs (via
    ``addopts = "-m 'not … sql_endpoint'"`` in pyproject.toml) and opted-in
    explicitly on CI.

    Both targets expose the same read-only seed schema (``sample``) with
    ``sample.colors`` and ``sample.numbers`` — use :data:`SEED_SCHEMA_NAME`
    to refer to it in assertions.

    **Tests that request this fixture MUST NOT mutate the seed schema.**
    Mutating dual-target tests use the ``mutable_schema_target`` fixture instead.

    Implementation note
    -------------------
    ``shared_sql_endpoint`` is resolved LAZILY via ``request.getfixturevalue``
    only on the ``sql_endpoint`` leg.  It is intentionally NOT declared as a
    signature parameter — that would cause pytest to provision the SQL analytics
    endpoint (~6 min Lakehouse create + poll + seed) even for the ``[warehouse]``
    leg and during local ``-m "not sql_endpoint"`` runs, which must stay fast and
    free of endpoint provisioning.
    """
    if request.param == _PARAM_WAREHOUSE:
        return shared_warehouse.sql_target
    if request.param == _PARAM_SQL_ENDPOINT:
        ep: SharedSqlEndpointTarget = request.getfixturevalue("shared_sql_endpoint")
        return ep.sql_target
    msg = f"Unknown read_target param: {request.param!r}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Dual-target mutable schema fixture (warehouse + SQL analytics endpoint)
# ---------------------------------------------------------------------------


@pytest.fixture(
    params=[
        pytest.param(_PARAM_WAREHOUSE),
        pytest.param(_PARAM_SQL_ENDPOINT, marks=pytest.mark.sql_endpoint),
    ]
)
def _mutable_schema_sql_target(
    request: pytest.FixtureRequest,
    shared_warehouse: SharedWarehouseTarget,
) -> SqlTarget:
    """Sync indirection that routes ``mutable_schema_target`` to the right SQL target.

    Carries the ``[warehouse]`` / ``[sql_endpoint]`` parametrisation (and the
    ``pytest.mark.sql_endpoint`` mark on the endpoint leg) and resolves the
    ``shared_sql_endpoint`` session fixture LAZILY via ``request.getfixturevalue``
    — mirroring the ``read_target`` path exactly.

    Why this exists as a separate **sync** fixture
    ----------------------------------------------
    ``mutable_schema_target`` is an ``async`` fixture, so its setup runs inside a
    running event loop.  Calling ``request.getfixturevalue("shared_sql_endpoint")``
    (itself an async fixture) from there makes pytest-asyncio call ``runner.run()``
    nested inside that loop, raising
    ``RuntimeError: Runner.run() cannot be called from a running event loop``.
    Resolving the endpoint here, in a **sync** fixture that the async fixture
    depends on, materialises it OUTSIDE the loop and avoids the nesting.

    The lazy-provisioning optimisation is preserved: ``shared_sql_endpoint`` is
    intentionally NOT a signature parameter, so the ~6-min endpoint provisioning
    never runs on the ``[warehouse]`` leg or during local ``-m "not sql_endpoint"``
    runs.  The parametrisation marks propagate to the requesting test through the
    fixture dependency closure.
    """
    if request.param == _PARAM_WAREHOUSE:
        return shared_warehouse.sql_target
    if request.param == _PARAM_SQL_ENDPOINT:
        ep: SharedSqlEndpointTarget = request.getfixturevalue("shared_sql_endpoint")
        return ep.sql_target
    msg = f"Unknown mutable_schema_target param: {request.param!r}"
    raise ValueError(msg)


@pytest_asyncio.fixture
async def mutable_schema_target(
    _mutable_schema_sql_target: SqlTarget,
) -> AsyncIterator[tuple[SqlTarget, str]]:
    """Parametrized fixture that creates an isolated schema on each SQL target.

    Runs each requesting test TWICE: once on the shared warm warehouse and once
    on the shared SQL analytics endpoint.  The endpoint leg carries
    ``pytest.mark.sql_endpoint`` so it is excluded from local runs (via
    ``addopts = "-m 'not … sql_endpoint'"`` in pyproject.toml) and opted-in
    explicitly on CI.

    Yields ``(sql_target, schema_name)`` where *schema_name* is a uniquely-named
    schema (``pytest_<8-hex-chars>``) that has already been created on *sql_target*.
    Teardown cascade-drops the schema on the same target, so any views/procedures/
    functions created inside it are cleaned up automatically.

    Implementation note
    -------------------
    The parametrisation and lazy ``shared_sql_endpoint`` resolution live on the
    sync :func:`_mutable_schema_sql_target` indirection fixture this depends on.
    That keeps the async setup from resolving another async fixture from inside
    the running event loop (which raised ``RuntimeError: Runner.run() cannot be
    called from a running event loop``) while preserving the lazy-provisioning
    optimisation.

    Both ``create_schema`` and ``delete_schema(cascade=True)`` are themselves
    dual-target (no ``_assert_not_sql_endpoint`` guard), so the isolation and
    teardown mechanism works identically on both targets.
    """
    sql_target = _mutable_schema_sql_target
    schema_name = f"pytest_{uuid.uuid4().hex[:8]}"
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

            # The Lakehouse body already has connectionString at
            # properties.sqlEndpointProperties.connectionString when
            # provisioningStatus=Success.  Use it directly — it is available
            # immediately at that point.  Only fall back to polling via
            # get_endpoint_connection_string if the value happens to be absent
            # (defensive guard; should not occur in practice).
            if not ep_conn:
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

            # Wait for the SQL-endpoint ITEM to become visible via the product API.
            # Even after provisioningStatus=Success (and TDS is reachable), the
            # GET /workspaces/{ws}/sqlEndpoints/{id} call can return 404
            # EntityNotFound for a short eventual-consistency window.  Tests that
            # call get_endpoint() immediately (e.g. test_get_endpoint_by_id) would
            # otherwise hit that window and fail spuriously.
            from fabric_dw.services.sql_endpoints import get_endpoint  # noqa: PLC0415

            item_visible_deadline = time.monotonic() + _SQL_ENDPOINT_ITEM_VISIBLE_TIMEOUT_S
            while True:
                try:
                    await get_endpoint(http, workspace_id, ep_uuid)
                    break  # item is visible — proceed to yield
                except NotFoundError:
                    if time.monotonic() >= item_visible_deadline:
                        pytest.skip(
                            f"SQL endpoint item {ep_uuid} was not visible via the product API "
                            f"within {_SQL_ENDPOINT_ITEM_VISIBLE_TIMEOUT_S}s after provisioning "
                            "— Fabric eventual-consistency window exceeded CI budget; "
                            "get_endpoint logic is unit-tested"
                        )
                    logger.debug(
                        "SQL endpoint item %s not yet visible (404 EntityNotFound); "
                        "waiting %ds before retry …",
                        ep_uuid,
                        _SQL_ENDPOINT_POLL_INTERVAL_S,
                    )
                    await asyncio.sleep(_SQL_ENDPOINT_POLL_INTERVAL_S)

            yield wh
            return

        if status == "Failed":
            pytest.skip(
                f"SQL analytics endpoint provisioning failed for lakehouse {lh_id} — skipping"
            )

        await asyncio.sleep(_SQL_ENDPOINT_POLL_INTERVAL_S)
