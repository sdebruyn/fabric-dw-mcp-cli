"""Tests for telemetry shutdown behaviour in the MCP server entry point.

Verifies that ``run()`` in ``fabric_dw.mcp.server``:
- calls ``record_app_exited`` with the right ``exit_status`` on every exit path,
- always calls ``shutdown_telemetry()`` exactly once (even when
  ``record_app_exited`` raises), and
- re-raises the original exception so the process exit code is correct.

``mcp.run`` is mocked throughout — no real server is started.

The tests need telemetry *enabled* to assert on emission, so they manage the
``FABRIC_DW_TELEMETRY_OPT_OUT`` env var themselves and patch
``configure_azure_monitor`` to a no-op (mirrors the pattern used by
``test_telemetry.py``).
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

import fabric_dw.mcp.server as _srv

# ---------------------------------------------------------------------------
# Module-level fixtures — enable telemetry and stub the Azure SDK
# ---------------------------------------------------------------------------


@pytest.fixture
def _enable_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable telemetry for the duration of the test."""
    monkeypatch.delenv("FABRIC_DW_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)


@pytest.fixture
def _patch_azure_monitor() -> Generator[None, None, None]:
    """Prevent any real Azure Monitor SDK calls."""
    with patch("azure.monitor.opentelemetry.configure_azure_monitor"):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Captured record_app_exited call kwargs.
_AppExitedCall = dict[str, object]


def _run(transport: str = "stdio") -> None:
    """Call ``fabric_dw.mcp.server.run`` with a minimal argv."""
    _srv.run(["--transport", transport])


# ---------------------------------------------------------------------------
# Normal return path
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_enable_telemetry", "_patch_azure_monitor")
def test_normal_return_emits_ok_and_shuts_down() -> None:
    """Normal mcp.run() return → exit_status 'ok', shutdown_telemetry called once."""
    calls: list[_AppExitedCall] = []
    shutdown_calls: list[None] = []

    def fake_record_app_exited(
        *,
        duration_ms: float,  # noqa: ARG001
        exit_status: str,
        error_category: str | None,
    ) -> None:
        calls.append({"exit_status": exit_status, "error_category": error_category})

    def fake_shutdown(**kwargs: object) -> None:  # noqa: ARG001
        shutdown_calls.append(None)

    with (
        patch("fabric_dw.mcp.server.mcp") as mock_mcp,
        patch("fabric_dw.mcp.server.record_app_started"),
        patch("fabric_dw.mcp.server.record_mcp_server_started"),
        patch("fabric_dw.mcp.server.maybe_print_first_run_notice"),
        patch("fabric_dw.mcp.server.record_app_exited", side_effect=fake_record_app_exited),
        patch("fabric_dw.mcp.server.shutdown_telemetry", side_effect=fake_shutdown),
    ):
        mock_mcp.run.return_value = None
        mock_mcp.settings = MagicMock()
        _run()

    # record_app_exited should be called exactly once with exit_status="ok"
    assert len(calls) == 1
    assert calls[0]["exit_status"] == "ok"

    # shutdown_telemetry must be called exactly once
    assert len(shutdown_calls) == 1


# ---------------------------------------------------------------------------
# KeyboardInterrupt path
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_enable_telemetry", "_patch_azure_monitor")
def test_keyboard_interrupt_emits_ok_and_shuts_down_and_reraises() -> None:
    """KeyboardInterrupt from mcp.run → exit_status 'ok', shutdown called, re-raised."""
    calls: list[_AppExitedCall] = []
    shutdown_calls: list[None] = []

    def fake_record_app_exited(
        *,
        duration_ms: float,  # noqa: ARG001
        exit_status: str,
        error_category: str | None,
    ) -> None:
        calls.append({"exit_status": exit_status, "error_category": error_category})

    def fake_shutdown(**kwargs: object) -> None:  # noqa: ARG001
        shutdown_calls.append(None)

    with (
        patch("fabric_dw.mcp.server.mcp") as mock_mcp,
        patch("fabric_dw.mcp.server.record_app_started"),
        patch("fabric_dw.mcp.server.record_mcp_server_started"),
        patch("fabric_dw.mcp.server.maybe_print_first_run_notice"),
        patch("fabric_dw.mcp.server.record_app_exited", side_effect=fake_record_app_exited),
        patch("fabric_dw.mcp.server.shutdown_telemetry", side_effect=fake_shutdown),
    ):
        mock_mcp.run.side_effect = KeyboardInterrupt()
        mock_mcp.settings = MagicMock()

        with pytest.raises(KeyboardInterrupt):
            _run()

    assert len(calls) == 1
    assert calls[0]["exit_status"] == "ok"

    assert len(shutdown_calls) == 1


# ---------------------------------------------------------------------------
# Unexpected exception path
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_enable_telemetry", "_patch_azure_monitor")
def test_unexpected_exception_emits_api_error_and_shuts_down_and_reraises() -> None:
    """Unexpected Exception from mcp.run → exit_status 'api_error', shutdown called, re-raised."""
    calls: list[_AppExitedCall] = []
    shutdown_calls: list[None] = []

    def fake_record_app_exited(
        *,
        duration_ms: float,  # noqa: ARG001
        exit_status: str,
        error_category: str | None,
    ) -> None:
        calls.append({"exit_status": exit_status, "error_category": error_category})

    def fake_shutdown(**kwargs: object) -> None:  # noqa: ARG001
        shutdown_calls.append(None)

    with (
        patch("fabric_dw.mcp.server.mcp") as mock_mcp,
        patch("fabric_dw.mcp.server.record_app_started"),
        patch("fabric_dw.mcp.server.record_mcp_server_started"),
        patch("fabric_dw.mcp.server.maybe_print_first_run_notice"),
        patch("fabric_dw.mcp.server.record_app_exited", side_effect=fake_record_app_exited),
        patch("fabric_dw.mcp.server.shutdown_telemetry", side_effect=fake_shutdown),
    ):
        mock_mcp.run.side_effect = RuntimeError("boom")
        mock_mcp.settings = MagicMock()

        with pytest.raises(RuntimeError, match="boom"):
            _run()

    assert len(calls) == 1
    assert calls[0]["exit_status"] == "api_error"

    assert len(shutdown_calls) == 1


# ---------------------------------------------------------------------------
# SystemExit paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [0, None])
@pytest.mark.usefixtures("_enable_telemetry", "_patch_azure_monitor")
def test_system_exit_zero_emits_ok(code: int | None) -> None:
    """SystemExit(0) / SystemExit(None) from mcp.run → exit_status 'ok'."""
    calls: list[_AppExitedCall] = []

    def fake_record_app_exited(
        *,
        duration_ms: float,  # noqa: ARG001
        exit_status: str,
        error_category: str | None,
    ) -> None:
        calls.append({"exit_status": exit_status, "error_category": error_category})

    with (
        patch("fabric_dw.mcp.server.mcp") as mock_mcp,
        patch("fabric_dw.mcp.server.record_app_started"),
        patch("fabric_dw.mcp.server.record_mcp_server_started"),
        patch("fabric_dw.mcp.server.maybe_print_first_run_notice"),
        patch("fabric_dw.mcp.server.record_app_exited", side_effect=fake_record_app_exited),
        patch("fabric_dw.mcp.server.shutdown_telemetry"),
    ):
        mock_mcp.run.side_effect = SystemExit(code)
        mock_mcp.settings = MagicMock()

        with pytest.raises(SystemExit):
            _run()

    assert len(calls) == 1
    assert calls[0]["exit_status"] == "ok"


@pytest.mark.usefixtures("_enable_telemetry", "_patch_azure_monitor")
def test_system_exit_nonzero_emits_api_error() -> None:
    """SystemExit(1) from mcp.run → exit_status 'api_error'."""
    calls: list[_AppExitedCall] = []

    def fake_record_app_exited(
        *,
        duration_ms: float,  # noqa: ARG001
        exit_status: str,
        error_category: str | None,
    ) -> None:
        calls.append({"exit_status": exit_status, "error_category": error_category})

    with (
        patch("fabric_dw.mcp.server.mcp") as mock_mcp,
        patch("fabric_dw.mcp.server.record_app_started"),
        patch("fabric_dw.mcp.server.record_mcp_server_started"),
        patch("fabric_dw.mcp.server.maybe_print_first_run_notice"),
        patch("fabric_dw.mcp.server.record_app_exited", side_effect=fake_record_app_exited),
        patch("fabric_dw.mcp.server.shutdown_telemetry"),
    ):
        mock_mcp.run.side_effect = SystemExit(1)
        mock_mcp.settings = MagicMock()

        with pytest.raises(SystemExit):
            _run()

    assert len(calls) == 1
    assert calls[0]["exit_status"] == "api_error"


# ---------------------------------------------------------------------------
# shutdown_telemetry always runs even when record_app_exited raises
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_enable_telemetry", "_patch_azure_monitor")
def test_shutdown_runs_even_if_record_app_exited_raises() -> None:
    """shutdown_telemetry() is called exactly once even if record_app_exited raises."""
    shutdown_calls: list[None] = []

    def fake_shutdown(**kwargs: object) -> None:  # noqa: ARG001
        shutdown_calls.append(None)

    with (
        patch("fabric_dw.mcp.server.mcp") as mock_mcp,
        patch("fabric_dw.mcp.server.record_app_started"),
        patch("fabric_dw.mcp.server.record_mcp_server_started"),
        patch("fabric_dw.mcp.server.maybe_print_first_run_notice"),
        patch(
            "fabric_dw.mcp.server.record_app_exited",
            side_effect=RuntimeError("telemetry failure"),
        ),
        patch("fabric_dw.mcp.server.shutdown_telemetry", side_effect=fake_shutdown),
    ):
        mock_mcp.run.return_value = None
        mock_mcp.settings = MagicMock()
        # The telemetry error must be swallowed; run() should not propagate it.
        _run()

    assert len(shutdown_calls) == 1


# ---------------------------------------------------------------------------
# Call ordering: record_app_exited before shutdown_telemetry
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_enable_telemetry", "_patch_azure_monitor")
def test_call_order_record_before_shutdown() -> None:
    """record_app_exited must be called before shutdown_telemetry."""
    manager = MagicMock()
    manager.attach_mock(MagicMock(), "record")
    manager.attach_mock(MagicMock(), "shutdown")

    with (
        patch("fabric_dw.mcp.server.mcp") as mock_mcp,
        patch("fabric_dw.mcp.server.record_app_started"),
        patch("fabric_dw.mcp.server.record_mcp_server_started"),
        patch("fabric_dw.mcp.server.maybe_print_first_run_notice"),
        patch("fabric_dw.mcp.server.record_app_exited", new=manager.record),
        patch("fabric_dw.mcp.server.shutdown_telemetry", new=manager.shutdown),
    ):
        mock_mcp.run.return_value = None
        mock_mcp.settings = MagicMock()
        _run()

    call_names = [c[0] for c in manager.mock_calls]
    assert "record" in call_names
    assert "shutdown" in call_names
    assert call_names.index("record") < call_names.index("shutdown")


# ---------------------------------------------------------------------------
# No hang: shutdown_telemetry is not called multiple times
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_enable_telemetry", "_patch_azure_monitor")
def test_shutdown_called_exactly_once_on_normal_return() -> None:
    """shutdown_telemetry() is called exactly once — idempotency guard check."""
    with (
        patch("fabric_dw.mcp.server.mcp") as mock_mcp,
        patch("fabric_dw.mcp.server.record_app_started"),
        patch("fabric_dw.mcp.server.record_mcp_server_started"),
        patch("fabric_dw.mcp.server.maybe_print_first_run_notice"),
        patch("fabric_dw.mcp.server.record_app_exited"),
        patch("fabric_dw.mcp.server.shutdown_telemetry") as mock_shutdown,
    ):
        mock_mcp.run.return_value = None
        mock_mcp.settings = MagicMock()
        _run()

    mock_shutdown.assert_called_once()
