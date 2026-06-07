import asyncio
import time
from pathlib import Path
from uuid import UUID

import pytest

from fabric_dw.cache import LookupCache
from fabric_dw.http_client import FabricHttpClient
from fabric_dw.resolver import Resolver
from fabric_dw.services import workspaces

pytestmark = pytest.mark.integration


async def test_resolver_caches_workspace_lookup(
    http: FabricHttpClient, workspace_id: UUID, tmp_path: Path
) -> None:
    cache = LookupCache(path=tmp_path / "cache.json")
    resolver = Resolver(http, cache)

    # GUID input: bypasses API + cache
    resolved = await resolver.workspace_id(str(workspace_id))
    assert resolved == workspace_id

    # Fetch the canonical name, then resolve by name twice; second call should hit cache
    ws = await workspaces.get(http, workspace_id)
    name = ws.name

    first = await resolver.workspace_id(name)
    assert first == workspace_id
    cached = cache.get_workspace(name)
    assert cached is not None
    assert cached.id == workspace_id

    second = await resolver.workspace_id(name)
    assert second == workspace_id


async def test_rps_limiter_paces_concurrent_requests(
    http: FabricHttpClient, workspace_id: UUID
) -> None:
    start = time.monotonic()
    await asyncio.gather(*(workspaces.get(http, workspace_id) for _ in range(6)))
    elapsed = time.monotonic() - start
    # 6 requests at 2 RPS bucket → roughly 2-3s; allow wide tolerance for network.
    assert elapsed >= 1.5, f"expected ≥1.5s elapsed (RPS pacing), got {elapsed:.2f}s"
