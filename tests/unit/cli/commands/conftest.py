"""Shared pytest fixtures for CLI command unit tests."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from azure.core.credentials import AccessToken
from click.testing import CliRunner

from fabric_dw.http_client import FabricHttpClient


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cache_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# respx / wire-validation helpers (used by test_warehouses_respx.py and any
# future respx-based test modules in this directory)
# ---------------------------------------------------------------------------


def _fake_token() -> AccessToken:
    return AccessToken(token="fake-bearer", expires_on=int(time.time()) + 3600)  # noqa: S106


def _make_fake_credential() -> MagicMock:
    """Return a mock AsyncTokenCredential that yields a long-lived fake token."""
    cred = MagicMock()
    cred.get_token = AsyncMock(return_value=_fake_token())
    return cred


@asynccontextmanager
async def _real_http_client_cm(_ctx: object) -> AsyncIterator[FabricHttpClient]:
    """Context manager that yields a *real* FabricHttpClient backed by a fake credential.

    Use this as the replacement for ``build_http_client`` in respx-based tests.
    The returned client is fully functional but its HTTP calls are intercepted by
    whichever ``respx.mock`` context is active at call time.
    """
    cred = _make_fake_credential()
    async with FabricHttpClient(credential=cred, rps=100) as client:
        yield client
