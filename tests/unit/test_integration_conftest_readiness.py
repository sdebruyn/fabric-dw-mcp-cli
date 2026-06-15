"""Tests for the SQL readiness probe retry-decision in tests/integration/conftest.py.

These tests exercise ``_wait_for_sql_readiness`` in isolation — no live Fabric
connection is required.  The ``_probe`` inner function is replaced via
monkeypatching ``tests.integration.conftest.run_query`` so that the retry
logic can be driven entirely through synthetic exceptions.
"""

from __future__ import annotations

import pytest

import tests.integration.conftest as _conftest
from fabric_dw.sql import _AUTH_FAILED_FRAGMENTS, SqlTarget
from tests.integration.conftest import _wait_for_sql_readiness

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_target() -> SqlTarget:
    return SqlTarget(
        workspace_id="ws-1234",
        database="test-warehouse",
        connection_string="server.database.windows.net",
    )


def _make_error(message: str) -> Exception:
    """Return a plain Exception whose str() contains *message*."""
    return Exception(message)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWaitForSqlReadinessAuthFailedRetry:
    """Auth-failed / login-18456 transient is retried in the warm-up window."""

    async def test_auth_failed_is_retried_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Probe raising 'authentication failed' N times then succeeds returns normally."""
        call_count = 0

        def _probe(
            _target: SqlTarget, _sql: str, **_kwargs: object
        ) -> tuple[list[str], list[tuple[object, ...]]]:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise _make_error(
                    "Could not login because the authentication failed. (error 18456)"
                )
            return [], []

        monkeypatch.setattr(_conftest, "run_query", _probe)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_INITIAL_S", 0.0)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_MAX_S", 0.0)

        # Should NOT raise — auth-failed is treated as warm-up transient.
        await _wait_for_sql_readiness(_make_target(), timeout_s=10.0)
        assert call_count == 3

    async def test_auth_failed_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fragment match is case-insensitive (str().lower() applied before comparison)."""
        call_count = 0

        def _probe(
            _target: SqlTarget, _sql: str, **_kwargs: object
        ) -> tuple[list[str], list[tuple[object, ...]]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Mixed-case — lower() should still match _AUTH_FAILED_FRAGMENTS.
                raise _make_error("Authentication Failed (SQLSTATE 28000)")
            return [], []

        monkeypatch.setattr(_conftest, "run_query", _probe)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_INITIAL_S", 0.0)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_MAX_S", 0.0)

        await _wait_for_sql_readiness(_make_target(), timeout_s=10.0)
        assert call_count == 2

    async def test_persistent_auth_failed_raises_timeout_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If auth-failed never clears, TimeoutError is raised after timeout_s elapses."""

        def _always_fail(
            _target: SqlTarget, _sql: str, **_kwargs: object
        ) -> tuple[list[str], list[tuple[object, ...]]]:
            raise _make_error("authentication failed (18456)")

        monkeypatch.setattr(_conftest, "run_query", _always_fail)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_INITIAL_S", 0.0)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_MAX_S", 0.0)

        with pytest.raises(TimeoutError, match="did not become reachable"):
            # Very short timeout so the test finishes quickly.
            await _wait_for_sql_readiness(_make_target(), timeout_s=0.05)

    async def test_unexpected_error_raises_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An error that is NOT a warm-up transient is re-raised on the first probe."""
        call_count = 0

        def _unexpected(
            _target: SqlTarget, _sql: str, **_kwargs: object
        ) -> tuple[list[str], list[tuple[object, ...]]]:
            nonlocal call_count
            call_count += 1
            raise ValueError("syntax error near 'SELECT'")

        monkeypatch.setattr(_conftest, "run_query", _unexpected)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_INITIAL_S", 0.0)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_MAX_S", 0.0)

        # Unexpected error must propagate immediately — only one probe attempt.
        with pytest.raises(ValueError, match="syntax error"):
            await _wait_for_sql_readiness(_make_target(), timeout_s=60.0)

        assert call_count == 1, "Should have probed exactly once before re-raising"

    async def test_database_was_not_found_still_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Existing 'database was not found' warm-up path still works after the change."""
        call_count = 0

        def _probe(
            _target: SqlTarget, _sql: str, **_kwargs: object
        ) -> tuple[list[str], list[tuple[object, ...]]]:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise _make_error("Login failed: database was not found (18456)")
            return [], []

        monkeypatch.setattr(_conftest, "run_query", _probe)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_INITIAL_S", 0.0)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_MAX_S", 0.0)

        await _wait_for_sql_readiness(_make_target(), timeout_s=10.0)
        assert call_count == 2

    async def test_first_attempt_success_no_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: endpoint responds immediately, no retries needed."""
        call_count = 0

        def _probe(
            _target: SqlTarget, _sql: str, **_kwargs: object
        ) -> tuple[list[str], list[tuple[object, ...]]]:
            nonlocal call_count
            call_count += 1
            return [], []

        monkeypatch.setattr(_conftest, "run_query", _probe)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_INITIAL_S", 0.0)
        monkeypatch.setattr(_conftest, "_SQL_READINESS_BACKOFF_MAX_S", 0.0)

        await _wait_for_sql_readiness(_make_target(), timeout_s=10.0)
        assert call_count == 1

    def test_auth_failed_fragments_constant_matches_expected(self) -> None:
        """Sanity check: _AUTH_FAILED_FRAGMENTS includes 'authentication failed'."""
        assert any("authentication failed" in frag for frag in _AUTH_FAILED_FRAGMENTS), (
            "_AUTH_FAILED_FRAGMENTS must contain 'authentication failed'"
        )
