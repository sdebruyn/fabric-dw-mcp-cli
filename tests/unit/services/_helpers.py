"""Shared test helpers for tests/unit/services/.

These helpers are *not* pytest fixtures — they are plain module-level
callables imported directly by each service test module.  This avoids
duplicate definitions while preserving the calling convention used by
every test (``client = await _make_client()`` followed by
``async with client:``).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential

from fabric_dw.http_client import FabricHttpClient

# A long-lived fake token shared across all service unit tests.  The
# ``expires_on`` is set 1 h into the future so token-refresh logic is
# not triggered during normal test runs.
_FAKE_TOKEN = AccessToken(token="fake-token", expires_on=int(time.time()) + 3600)  # noqa: S106


def _make_credential(token: AccessToken = _FAKE_TOKEN) -> AsyncTokenCredential:
    """Return a mock ``AsyncTokenCredential`` that yields *token*."""
    cred = MagicMock(spec=AsyncTokenCredential)
    cred.get_token = AsyncMock(return_value=token)
    return cred


async def _make_client(rps: int = 100) -> FabricHttpClient:
    """Return a ``FabricHttpClient`` backed by a fake credential.

    The *rps* default is 100 (generous) so that rate-limiting does not
    slow down unit tests.  Pass a smaller value when testing throttling
    behaviour explicitly.
    """
    return FabricHttpClient(credential=_make_credential(), rps=rps)
