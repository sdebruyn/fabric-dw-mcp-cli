import pytest

from fabric_dw.models import Connection
from fabric_dw.services import queries
from fabric_dw.sql import SqlTarget

pytestmark = pytest.mark.integration


async def test_list_running_returns_a_list(ephemeral_sql_target: SqlTarget) -> None:
    running = await queries.list_running(ephemeral_sql_target)
    assert isinstance(running, list)


async def test_kill_invalid_session_id_raises(ephemeral_sql_target: SqlTarget) -> None:
    with pytest.raises(ValueError, match="session_id must be a positive integer"):
        await queries.kill(ephemeral_sql_target, 0)
    with pytest.raises(ValueError, match="session_id must be a positive integer"):
        await queries.kill(ephemeral_sql_target, -1)


async def test_list_connections_returns_connection_models(
    ephemeral_sql_target: SqlTarget,
) -> None:
    """list_connections returns a non-empty list of Connection instances.

    Issuing this query creates at least one active connection (the current
    session), so the result list must be non-empty and every element must be
    a fully-validated Connection model with the expected scalar fields.
    """
    connections = await queries.list_connections(ephemeral_sql_target)

    assert isinstance(connections, list)
    assert len(connections) >= 1, "expected at least the current session in dm_exec_connections"

    for conn in connections:
        assert isinstance(conn, Connection)
        # net_transport is NOT NULL in the DMV schema — verify it is always populated
        assert isinstance(conn.net_transport, str)
        assert conn.net_transport != ""
        # connect_time is NOT NULL in the DMV schema — verify it is always populated
        assert conn.connect_time is not None
