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

from fabric_dw.models import Connection
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
) -> None:
    """list_connections returns a non-empty list of Connection instances.

    Issuing this query creates at least one active connection (the current
    session), so the result list must be non-empty and every element must be
    a fully-validated Connection model with the expected scalar fields.
    """
    connections = await queries.list_connections(read_target)

    assert isinstance(connections, list)
    assert len(connections) >= 1, "expected at least the current session in dm_exec_connections"

    for conn in connections:
        assert isinstance(conn, Connection)
        # net_transport is NOT NULL in the DMV schema — verify it is always populated
        assert isinstance(conn.net_transport, str)
        assert conn.net_transport != ""
        # connect_time is NOT NULL in the DMV schema — verify it is always populated
        assert conn.connect_time is not None
