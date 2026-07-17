"""Integration tests for services.queries — requires real Fabric credentials.

Fixture note: uses ``read_target`` from conftest.  These tests only read DMV
data (running queries, connections) — no schema mutations — so they are safe to
run against either target via the parametrized ``read_target`` fixture.

The ``read_target`` fixture is parametrized over two targets:
  - ``[warehouse]``     — Data Warehouse (always runs)
  - ``[sql_endpoint]``  — SQL Analytics Endpoint (``pytest.mark.sql_endpoint``, CI only)
"""

from __future__ import annotations

import pytest

from fabric_dw.models import Connection, QueryLock
from fabric_dw.services import queries
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration


async def test_list_running_returns_a_list(
    read_target: SqlTarget,
) -> None:
    running = await queries.list_running(read_target)
    assert isinstance(running, list)


async def test_kill_invalid_session_id_raises(
    read_target: SqlTarget,
) -> None:
    with pytest.raises(ValueError, match="session_id must be a positive integer"):
        await queries.kill(read_target, 0)
    with pytest.raises(ValueError, match="session_id must be a positive integer"):
        await queries.kill(read_target, -1)


async def test_list_connections_returns_connection_models(
    read_target: SqlTarget,
    request: pytest.FixtureRequest,
) -> None:
    """list_connections returns a list of Connection instances.

    On a Data Warehouse, issuing this query creates at least one active
    connection (the current session), so the result must be non-empty and
    every element must be a fully-validated Connection model.

    On a SQL Analytics Endpoint, ``sys.dm_exec_connections`` is a DMV over a
    read-only Lakehouse projection and may return 0 rows; the endpoint leg
    therefore only asserts shape (``isinstance(connections, list)``) and
    skips the non-empty and field-content assertions.
    """
    from tests.integration.conftest import _PARAM_WAREHOUSE  # noqa: PLC0415

    connections = await queries.list_connections(read_target)

    assert isinstance(connections, list)

    if request.node.callspec.params.get("read_target") == _PARAM_WAREHOUSE:
        # On a warehouse the current session is always visible in the DMV.
        assert len(connections) >= 1, "expected at least the current session in dm_exec_connections"
        for conn in connections:
            assert isinstance(conn, Connection)
            # net_transport is NOT NULL in the DMV schema — verify it is always populated
            assert isinstance(conn.net_transport, str)
            assert conn.net_transport != ""
            # connect_time is NOT NULL in the DMV schema — verify it is always populated
            assert conn.connect_time is not None
    else:
        # SQL analytics endpoint: dm_exec_connections may return 0 rows; only
        # assert that any returned rows parse cleanly as Connection models.
        if not connections:
            pytest.skip("dm_exec_connections returned no rows on this SQL analytics endpoint")
        for conn in connections:
            assert isinstance(conn, Connection)


async def test_list_locks_returns_list(
    read_target: SqlTarget,
) -> None:
    locks = await queries.list_locks(read_target)
    assert isinstance(locks, list)
    for lock in locks:
        assert isinstance(lock, QueryLock)
