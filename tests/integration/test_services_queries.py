import pytest

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
