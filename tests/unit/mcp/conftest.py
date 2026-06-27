"""Shared pytest fixtures for MCP server unit tests.

The central fixture is ``mock_ctx`` — a :class:`ServerContext` with fully
mocked service objects (http, cache, resolver).  Tests inject this context by
patching :data:`fabric_dw.mcp._context._SERVER_CTX` directly.

Usage
-----
```python
async def test_something(mock_ctx):
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=UUID("..."))
    with patch("fabric_dw.mcp._context._SERVER_CTX", mock_ctx):
        result = await mcp._tool_manager.call_tool("some_tool", {...})
    ...
```

Or use the ``ctx_patch`` fixture for the patch context manager directly::

```python
async def test_something(mock_ctx, ctx_patch):
    mock_ctx.resolver.workspace_id = AsyncMock(return_value=_WS_ID)
    with ctx_patch:
        result = await mcp._tool_manager.call_tool("some_tool", {...})
```
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from fabric_dw import auth as _auth
from fabric_dw.cache import ItemEntry
from fabric_dw.mcp._context import ServerContext
from fabric_dw.models import WarehouseKind

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

WS_ID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
WH_ID = UUID("d4e5f6a7-b8c9-0123-def0-123456789abc")
SNAP_ID = UUID("e5f6a7b8-c9d0-1234-ef01-23456789abcd")

WS_NAME = "my-workspace"
WH_NAME = "my-warehouse"


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def make_item_entry(
    *,
    item_id: UUID = WH_ID,
    connection_string: str | None = "wh.fabric.microsoft.com",
    display_name: str = WH_NAME,
    kind: WarehouseKind = WarehouseKind.WAREHOUSE,
) -> ItemEntry:
    return ItemEntry(
        id=item_id,
        kind=kind,
        connection_string=connection_string,
        fetched_at=datetime.now(tz=UTC),
        display_name=display_name,
    )


def make_sql_endpoint_entry(
    *,
    item_id: UUID = WH_ID,
    connection_string: str | None = "ep.fabric.microsoft.com",
    display_name: str = "MySqlEndpoint",
) -> ItemEntry:
    return ItemEntry(
        id=item_id,
        kind=WarehouseKind.SQL_ENDPOINT,
        connection_string=connection_string,
        fetched_at=datetime.now(tz=UTC),
        display_name=display_name,
    )


# ---------------------------------------------------------------------------
# Core fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ctx() -> ServerContext:
    """Return a :class:`ServerContext` with mocked service objects.

    The resolver is a plain :class:`~unittest.mock.AsyncMock`; callers can
    configure ``mock_ctx.resolver.workspace_id`` / ``.item`` per test.
    """
    mock_http = AsyncMock()
    mock_cache = MagicMock()
    mock_resolver = AsyncMock()

    # Sensible defaults so tests that don't care about internals still work.
    mock_resolver.workspace_id = AsyncMock(return_value=WS_ID)
    mock_resolver.item = AsyncMock(return_value=make_item_entry())
    return ServerContext(
        http=mock_http,
        cache=mock_cache,
        resolver=mock_resolver,
        auth_mode=_auth.CredentialMode.DEFAULT,
    )


@pytest.fixture
def ctx_patch(mock_ctx: ServerContext):
    """Return a :func:`unittest.mock.patch` context manager that sets ``_SERVER_CTX``."""
    return patch("fabric_dw.mcp._context._SERVER_CTX", mock_ctx)
