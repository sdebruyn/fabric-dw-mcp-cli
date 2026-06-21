"""Tests for tid-from-SQL-token telemetry on the non-OIDC auth path (issue #653).

TDD: these tests are written FIRST and must fail until the implementation is added.

The fix: on the normal (non-OIDC) SQL path, ``open_connection`` calls
``auth.try_cache_sql_tenant_for_telemetry()`` after a pool miss so the tenant
ID is decoded from the SQL access token and forwarded to the telemetry layer.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from azure.core.credentials import AccessToken

import fabric_dw.auth as auth_module
import fabric_dw.telemetry as telemetry_module
from fabric_dw.auth import SQL_SCOPE, CredentialMode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KNOWN_TID = "aaaabbbb-0000-1111-2222-333344445555"


def _make_jwt(tid: str) -> str:
    """Return a minimal JWT whose payload contains the given ``tid`` claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    payload_bytes = json.dumps({"tid": tid, "aud": "https://database.windows.net/"}).encode()
    payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"fakesig").rstrip(b"=").decode()
    return f"{header}.{payload}.{sig}"


def _reset_telemetry_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reset telemetry module-level state so tests do not bleed into each other.

    Uses a dummy connection string pointing to localhost so no real network
    calls are ever made, and ``tmp_path`` for ``XDG_CONFIG_HOME`` to avoid
    touching the real user's config directory.
    """
    monkeypatch.setenv(
        "FABRIC_TELEMETRY_CONNECTION_STRING",
        "InstrumentationKey=00000000-0000-0000-0000-000000000000;"
        "IngestionEndpoint=https://localhost/",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("JENKINS_URL", raising=False)
    monkeypatch.delenv("TRAVIS", raising=False)
    monkeypatch.delenv("CIRCLECI", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("TF_BUILD", raising=False)
    # Reset the module-level override so state from previous tests doesn't leak.
    monkeypatch.setattr(telemetry_module, "_tenant_id_override", None)
    # Reset process-level suppression (set by --help paths or other tests).
    monkeypatch.setattr(telemetry_module, "_SUPPRESSED", False)
    # Ensure telemetry is enabled for these tests.
    monkeypatch.delenv("FABRIC_TELEMETRY", raising=False)
    monkeypatch.delenv("FABRIC_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)


# ---------------------------------------------------------------------------
# try_cache_sql_tenant_for_telemetry — normal (non-OIDC) path
# ---------------------------------------------------------------------------


def test_try_cache_sql_tenant_decodes_tid_into_tenant_id_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """On the non-OIDC path, calling try_cache_sql_tenant_for_telemetry with a sync
    credential whose SQL-scope token has a known tid must populate _tenant_id_override.
    """
    _reset_telemetry_state(monkeypatch, tmp_path)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)

    jwt_token = _make_jwt(_KNOWN_TID)
    mock_inner = MagicMock()
    mock_inner.get_token.return_value = AccessToken(jwt_token, 9999999999)

    auth_module.try_cache_sql_tenant_for_telemetry(mock_inner)

    assert telemetry_module._tenant_id_override == _KNOWN_TID
    mock_inner.get_token.assert_called_once_with(SQL_SCOPE)


def test_try_cache_sql_tenant_is_noop_when_tenant_already_known(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When _tenant_id_override is already set, try_cache_sql_tenant_for_telemetry
    must skip the token acquisition entirely (idempotent).
    """
    _reset_telemetry_state(monkeypatch, tmp_path)
    monkeypatch.setattr(telemetry_module, "_tenant_id_override", "existing-tenant")

    mock_inner = MagicMock()
    auth_module.try_cache_sql_tenant_for_telemetry(mock_inner)

    # The credential must NOT be touched — tenant is already known.
    mock_inner.get_token.assert_not_called()
    assert telemetry_module._tenant_id_override == "existing-tenant"


def test_try_cache_sql_tenant_is_noop_when_telemetry_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When telemetry is disabled, try_cache_sql_tenant_for_telemetry is a no-op."""
    _reset_telemetry_state(monkeypatch, tmp_path)
    monkeypatch.setenv("FABRIC_DISABLE_TELEMETRY", "1")

    mock_inner = MagicMock()
    auth_module.try_cache_sql_tenant_for_telemetry(mock_inner)

    mock_inner.get_token.assert_not_called()


def test_try_cache_sql_tenant_never_raises_on_token_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If the credential raises, try_cache_sql_tenant_for_telemetry must not propagate
    the exception — telemetry must never break the SQL connection path.
    """
    _reset_telemetry_state(monkeypatch, tmp_path)

    mock_inner = MagicMock()
    mock_inner.get_token.side_effect = RuntimeError("token acquisition failed")

    # Must not raise.
    auth_module.try_cache_sql_tenant_for_telemetry(mock_inner)

    assert telemetry_module._tenant_id_override is None


def test_try_cache_sql_tenant_is_noop_when_credential_is_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Passing None as the credential must be a silent no-op."""
    _reset_telemetry_state(monkeypatch, tmp_path)

    # Must not raise.
    auth_module.try_cache_sql_tenant_for_telemetry(None)

    assert telemetry_module._tenant_id_override is None


def test_try_cache_sql_tenant_envelope_reflects_tid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """After calling try_cache_sql_tenant_for_telemetry, _build_envelope()["tenant_id"]
    must reflect the tid decoded from the SQL token.
    """
    _reset_telemetry_state(monkeypatch, tmp_path)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("FABRIC_INTERACTIVE_TENANT_ID", raising=False)

    jwt_token = _make_jwt(_KNOWN_TID)
    mock_inner = MagicMock()
    mock_inner.get_token.return_value = AccessToken(jwt_token, 9999999999)

    auth_module.try_cache_sql_tenant_for_telemetry(mock_inner)

    envelope = telemetry_module._build_envelope()
    assert envelope["tenant_id"] == _KNOWN_TID


# ---------------------------------------------------------------------------
# open_connection integration — non-OIDC pool-miss path calls telemetry hook
# ---------------------------------------------------------------------------


def test_open_connection_calls_telemetry_hook_on_pool_miss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """open_connection on a non-OIDC pool miss must call try_cache_sql_tenant_for_telemetry.

    This verifies the wiring between sql.open_connection and auth, not the
    full token-decode logic (covered by the tests above).
    """
    import fabric_dw.sql as sql_module  # noqa: PLC0415

    _reset_telemetry_state(monkeypatch, tmp_path)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)

    # Stub mssql_python.connect so no real TDS connection is made.
    mock_conn = MagicMock()
    mock_mssql = MagicMock()
    mock_mssql.connect.return_value = mock_conn
    monkeypatch.setattr(sql_module, "_mssql", mock_mssql)

    # Disable pooling so we always hit the pool-miss path.
    monkeypatch.setenv("FABRIC_SQL_POOL", "0")

    from fabric_dw.sql import SqlTarget, open_connection  # noqa: PLC0415

    target = SqlTarget(
        workspace_id="ws-1",
        database="dw-1",
        connection_string="my-dw.datawarehouse.fabric.microsoft.com",
    )

    calls: list[object] = []

    def fake_try_cache(inner: object) -> None:
        calls.append(inner)

    with patch.object(
        auth_module, "try_cache_sql_tenant_for_telemetry", side_effect=fake_try_cache
    ):
        conn = open_connection(target, mode=CredentialMode.DEFAULT)
        conn.close()

    # The hook must have been called exactly once on the pool-miss path.
    assert len(calls) == 1


def test_open_connection_skips_telemetry_hook_on_pool_hit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """open_connection on a pool HIT must NOT call try_cache_sql_tenant_for_telemetry.

    Token acquisition (and therefore telemetry) is skipped on pool hits to
    avoid unnecessary credential work.
    """
    import fabric_dw.sql as sql_module  # noqa: PLC0415

    _reset_telemetry_state(monkeypatch, tmp_path)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    monkeypatch.setenv("FABRIC_SQL_POOL", "1")

    mock_conn = MagicMock()
    mock_conn.closed = 0  # alive
    mock_mssql = MagicMock()
    mock_mssql.connect.return_value = mock_conn
    monkeypatch.setattr(sql_module, "_mssql", mock_mssql)

    from fabric_dw.sql import SqlTarget, _pool, _pool_lock, open_connection  # noqa: PLC0415

    target = SqlTarget(
        workspace_id="ws-poolhit",
        database="dw-poolhit",
        connection_string="my-dw.datawarehouse.fabric.microsoft.com",
    )

    # Pre-seed the pool with a live connection so the first open_connection
    # call sees a pool HIT.
    import time  # noqa: PLC0415

    key = ("ws-poolhit", "dw-poolhit", CredentialMode.DEFAULT.value)
    with _pool_lock:
        _pool[key] = [(mock_conn, time.monotonic())]

    calls: list[object] = []

    def fake_try_cache(inner: object) -> None:
        calls.append(inner)

    with patch.object(
        auth_module, "try_cache_sql_tenant_for_telemetry", side_effect=fake_try_cache
    ):
        conn = open_connection(target, mode=CredentialMode.DEFAULT)
        conn.close()

    # No telemetry hook on a pool hit.
    assert len(calls) == 0

    # Clean up pool state.
    with _pool_lock:
        _pool.pop(key, None)
